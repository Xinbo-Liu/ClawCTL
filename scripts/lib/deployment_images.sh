#!/usr/bin/env bash
# 用途：统一管理部署镜像角色集合的本地就绪、Docker daemon 前提与离线归档装载逻辑。
set -euo pipefail

DEPLOYMENT_IMAGES_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=repo_root.sh
source "$DEPLOYMENT_IMAGES_LIB_DIR/repo_root.sh"
ROOT_DIR="${ROOT_DIR:-$(openclaw_repo_root_from "$DEPLOYMENT_IMAGES_LIB_DIR")}"
# shellcheck source=scripts/lib/image_env.sh
source "$ROOT_DIR/scripts/lib/image_env.sh"
image_env_load

DEPLOYMENT_IMAGE_ARTIFACT_DIR="${DEPLOYMENT_IMAGE_ARTIFACT_DIR:-$ROOT_DIR/state/image_artifacts}"
DEPLOYMENT_IMAGE_ARCHIVE_GLOB='deployment_images_*.tar'
DEPLOYMENT_IMAGE_BUNDLE_CONTRACT='deployment-images.contract.json'
DEPLOYMENT_IMAGE_BUNDLE_DOCKER_ARCHIVE='deployment-images.docker.tar'
DEPLOYMENT_IMAGE_BUNDLE_SHA256='deployment-images.sha256'
DEPLOYMENT_IMAGE_LOCAL_REFS_ENV="${DEPLOYMENT_IMAGE_LOCAL_REFS_ENV:-$DEPLOYMENT_IMAGE_ARTIFACT_DIR/deployment-images.local-refs.env}"

# 输出部署镜像相关的普通提示信息。
deployment_images_note() {
  printf '[INFO] %s\n' "$*"
}

# 输出部署镜像相关的告警信息。
deployment_images_warn() {
  printf '[WARN] %s\n' "$*"
}

# 输出部署镜像相关的错误信息，并返回标准错误码。
deployment_images_fail() {
  printf '[FAIL] %s\n' "$*" >&2
  return 2
}

# 确认当前环境具备 jq。
deployment_images_require_jq() {
  command -v jq >/dev/null 2>&1 || deployment_images_fail '缺少 jq；请先执行 sudo bash ./scripts/setup/prepare_docker_host.sh --install-base-tools。'
}

# 确认当前环境具备 tar。
deployment_images_require_tar() {
  command -v tar >/dev/null 2>&1 || deployment_images_fail '缺少 tar；请先安装 tar 后再处理部署镜像归档。'
}

# 确认当前环境具备 sha256sum。
deployment_images_require_sha256sum() {
  command -v sha256sum >/dev/null 2>&1 || deployment_images_fail '缺少 sha256sum；无法生成或校验部署镜像 bundle 摘要。'
}

# 确认当前环境具备 docker CLI。
deployment_images_require_docker_cli() {
  command -v docker >/dev/null 2>&1 || deployment_images_fail '未检测到 docker CLI；请先安装 Docker。'
}

# 确认当前用户可以连接 Docker daemon。
deployment_images_require_docker_daemon() {
  if docker info >/dev/null 2>&1; then
    return 0
  fi
  local docker_info_err=''
  docker_info_err="$(docker info 2>&1 || true)"
  if printf '%s' "$docker_info_err" | grep -qiE 'permission denied|/var/run/docker\.sock|got permission denied'; then
    deployment_images_fail '当前用户无法访问 Docker daemon（通常是 /var/run/docker.sock 权限不足）；请先修复 docker 组 / sudo / daemon 权限。'
  fi
  deployment_images_fail '当前无法连接 Docker daemon；请先确认 dockerd 已启动，且当前用户具备访问 Docker daemon 的权限。'
}

# 组合校验 docker CLI 与 daemon 是否都已就绪。
deployment_images_require_docker_ready() {
  deployment_images_require_docker_cli || return $?
  deployment_images_require_docker_daemon || return $?
}

# 列出当前部署链要求的全部镜像引用。
deployment_images_list_images() {
  image_env_deployment_images
}

# 返回控制面 Python 镜像引用。
deployment_images_control_plane_image() {
  printf '%s\n' "$OPENCLAW_CONTROL_PLANE_IMAGE"
}

# 从运行来源策略真源列出部署镜像角色、对应 env key、当前 pin ref 与展示标签。
deployment_images_role_rows() {
  local strategy_path='' role='' env_key='' label='' pin_ref='' role_rows_text=''
  image_env_load
  deployment_images_require_jq || return $?
  strategy_path="$(image_env_source_strategy_path)"
  [[ -f "$strategy_path" ]] || deployment_images_fail "缺少运行来源策略真源：$strategy_path"
  role_rows_text="$(jq -r '
    (.images // {}) | to_entries[] |
    .key as $image_id |
    .value as $image |
    ($image.deployment_contract // {}) as $contract |
    ($image.selected_runtime_source.ref_env // "") as $env_key |
    select($env_key != "" and ($contract.enabled == true)) |
    [
      ($contract.managed_tag_role // $contract.role // $image_id),
      $env_key,
      ($contract.label // $image.summary // $image_id)
    ] | @tsv
  ' "$strategy_path" | tr -d '\r')" || return $?
  [[ -n "$role_rows_text" ]] || deployment_images_fail "运行来源策略未声明部署镜像合同角色：$strategy_path"
  while IFS=$'\t' read -r role env_key label; do
    [[ -n "$role" && -n "$env_key" ]] || deployment_images_fail "运行来源策略缺少部署镜像角色或 env key：$strategy_path"
    pin_ref="${!env_key:-}"
    [[ -n "$pin_ref" ]] || deployment_images_fail "部署镜像角色 $role 对应的 $env_key 未加载。"
    printf '%s|%s|%s|%s\n' "$role" "$env_key" "$pin_ref" "$label"
  done <<< "$role_rows_text"
}

# 从 image:tag@digest 合同引用中还原 Docker save/load 可携带的 source tag。
deployment_images_source_tag_ref() {
  local ref="$1"
  local repo='' tag='' digest=''
  mapfile -t __deployment_image_parts < <(image_env_split_image_ref "$ref")
  repo="${__deployment_image_parts[0]}"
  tag="${__deployment_image_parts[1]}"
  digest="${__deployment_image_parts[2]}"
  [[ -n "$repo" && -n "$tag" && -n "$digest" ]] || deployment_images_fail "镜像 ref 必须是 image:tag@sha256 形式：$ref"
  printf '%s:%s\n' "$repo" "$tag"
}

# 提取 image:tag@digest 中的 digest 部分。
deployment_images_ref_digest() {
  local ref="$1"
  mapfile -t __deployment_image_parts < <(image_env_split_image_ref "$ref")
  printf '%s\n' "${__deployment_image_parts[2]}"
}

# 提取 image:tag@digest 中的 tag 部分。
deployment_images_ref_tag() {
  local ref="$1"
  mapfile -t __deployment_image_parts < <(image_env_split_image_ref "$ref")
  printf '%s\n' "${__deployment_image_parts[1]}"
}

# 为部署镜像角色生成受管本地 tag，便于 docker images 输出可解释且可清理。
deployment_images_managed_tag_for_role() {
  local role="$1"
  local ref="$2"
  local tag='' digest='' digest_hex='' role_path=''
  tag="$(deployment_images_ref_tag "$ref")"
  digest="$(deployment_images_ref_digest "$ref")"
  digest_hex="${digest#sha256:}"
  role_path="${role//_/-}"
  tag="$(printf '%s' "$tag" | sed -E 's/[^A-Za-z0-9_.-]+/-/g; s/^[.-]+//; s/[.-]+$//')"
  [[ -n "$tag" ]] || tag='image'
  printf 'openclaw.local/deployment/%s:%s-sha256-%s\n' "$role_path" "$tag" "${digest_hex:0:16}"
}

# 返回 load bundle 后记录 pin、managed tag 与 image id 证明的 env 文件路径。
deployment_images_local_refs_env_path() {
  printf '%s\n' "$DEPLOYMENT_IMAGE_LOCAL_REFS_ENV"
}

# 校验指定 env key 的 pin 与 managed tag 映射；记录 image id 时必须匹配当前本地镜像。
deployment_images_verified_local_ref_for_key() {
  local env_key="$1"
  local pin_ref="$2"
  local refs_file='' recorded_pin='' local_ref='' recorded_image_id='' actual_image_id=''
  refs_file="$(deployment_images_local_refs_env_path)"
  [[ -f "$refs_file" ]] || return 1
  recorded_pin="$(image_env_read_key_from_file "$refs_file" "${env_key}_PIN_REF")"
  [[ "$recorded_pin" == "$pin_ref" ]] || return 1
  local_ref="$(image_env_read_key_from_file "$refs_file" "${env_key}_LOCAL_REF")"
  [[ -n "$local_ref" ]] || return 1
  recorded_image_id="$(image_env_read_key_from_file "$refs_file" "${env_key}_IMAGE_ID")"
  if [[ -z "$recorded_image_id" ]]; then
    docker image inspect "$local_ref" >/dev/null 2>&1 || return 1
    printf '%s\n' "$local_ref"
    return 0
  fi
  actual_image_id="$(docker image inspect "$local_ref" --format '{{.Id}}' 2>/dev/null || true)"
  if [[ "$actual_image_id" != "$recorded_image_id" ]]; then
    return 1
  fi
  printf '%s\n' "$local_ref"
}

# 从 verified local refs env 直接按 pin ref 查找本地 managed ref。
deployment_images_resolve_verified_local_ref_from_refs_file() {
  local ref="$1"
  local refs_file='' raw_line='' line='' key='' value='' env_key='' local_ref=''
  refs_file="$(deployment_images_local_refs_env_path)"
  [[ -f "$refs_file" ]] || return 1
  while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    line="$raw_line"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" || "${line:0:1}" == "#" || "$line" != *"="* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    [[ "$key" == *_PIN_REF && "$value" == "$ref" ]] || continue
    env_key="${key%_PIN_REF}"
    [[ -n "$env_key" ]] || continue
    local_ref="$(deployment_images_verified_local_ref_for_key "$env_key" "$ref" || true)"
    [[ -n "$local_ref" ]] || return 1
    printf '%s\n' "$local_ref"
    return 0
  done < "$refs_file"
  return 1
}

# 将当前 pin ref 解析为已验证的 managed local ref；记录 image id 时同时校验 image id。
deployment_images_resolve_verified_local_ref() {
  local ref="$1"
  local role='' env_key='' pin_ref='' label='' local_ref='' role_rows_text=''
  local_ref="$(deployment_images_resolve_verified_local_ref_from_refs_file "$ref" || true)"
  if [[ -n "$local_ref" ]]; then
    printf '%s\n' "$local_ref"
    return 0
  fi
  [[ -f "$(deployment_images_local_refs_env_path)" ]] || return 1
  role_rows_text="$(deployment_images_role_rows)" || return $?
  while IFS='|' read -r role env_key pin_ref label; do
    [[ "$pin_ref" == "$ref" ]] || continue
    local_ref="$(deployment_images_verified_local_ref_for_key "$env_key" "$pin_ref" || true)"
    [[ -n "$local_ref" ]] || return 1
    printf '%s\n' "$local_ref"
    return 0
  done <<< "$role_rows_text"
  return 1
}

# 判断指定镜像是否已存在于本地 Docker。
deployment_images_image_present() {
  local ref="$1"
  docker image inspect "$ref" >/dev/null 2>&1 && return 0
  deployment_images_resolve_verified_local_ref "$ref" >/dev/null 2>&1
}

# 从给定镜像列表中过滤出本地缺失项。
deployment_images_collect_missing_images() {
  local ref=''
  for ref in "$@"; do
    deployment_images_image_present "$ref" || printf '%s\n' "$ref"
  done
}

# 按空格拼接非空参数，便于输出人类可读摘要。
deployment_images_join_by_space() {
  local out=''
  local item=''
  for item in "$@"; do
    [[ -n "$item" ]] || continue
    if [[ -n "$out" ]]; then
      out+=" "
    fi
    out+="$item"
  done
  printf '%s\n' "$out"
}

# 确认部署镜像归档存在且可读。
deployment_images_require_archive_readable() {
  local archive_path="$1"
  [[ -n "$archive_path" ]] || deployment_images_fail '部署镜像归档未提供。'
  [[ -f "$archive_path" && -r "$archive_path" ]] || deployment_images_fail "部署镜像归档不可读：$archive_path"
}

# 从归档目录中选择最近生成的 deployment_images_*.tar。
deployment_images_find_latest_archive() {
  local artifacts_dir="$DEPLOYMENT_IMAGE_ARTIFACT_DIR"
  [[ -d "$artifacts_dir" ]] || return 1
  local latest=''
  latest="$(find "$artifacts_dir" -maxdepth 1 -type f -name "$DEPLOYMENT_IMAGE_ARCHIVE_GLOB" -printf '%T@|%p\n' 2>/dev/null | sort -t'|' -k1,1nr | head -n 1 | cut -d'|' -f2-)"
  [[ -n "$latest" ]] || return 1
  [[ -f "$latest" && -r "$latest" ]] || return 1
  printf '%s\n' "$latest"
}

# 优先使用显式路径，否则回退到最近归档。
deployment_images_resolve_archive_path() {
  local requested_path="${1:-}"
  local resolved=''
  if [[ -n "$requested_path" ]]; then
    deployment_images_require_archive_readable "$requested_path" || return $?
    printf '%s\n' "$requested_path"
    return 0
  fi
  resolved="$(deployment_images_find_latest_archive || true)"
  [[ -n "$resolved" ]] || return 1
  printf '%s\n' "$resolved"
}

# 以非致命方式尝试解析归档路径，便于上层分支处理。
deployment_images_try_resolve_archive_path() {
  local requested_path="${1:-}"
  local resolved=''
  local status=0
  set +e
  resolved="$(deployment_images_resolve_archive_path "$requested_path")"
  status=$?
  set -e
  if [[ "$status" -eq 0 ]]; then
    printf '%s\n' "$resolved"
  fi
  return "$status"
}

# 读取镜像归档中的 manifest.json 内容。
deployment_images_archive_manifest_json() {
  local archive_path="$1"
  deployment_images_require_archive_readable "$archive_path" || return $?
  deployment_images_require_tar || return $?
  local manifest_json=''
  manifest_json="$(tar -xOf "$archive_path" manifest.json 2>/dev/null || true)"
  [[ -n "$manifest_json" ]] || {
    deployment_images_fail "部署镜像归档缺少 manifest.json：$archive_path"
    return $?
  }
  printf '%s\n' "$manifest_json"
}

# 判断归档是否为 OpenClaw deployment image bundle。
deployment_images_archive_is_bundle() {
  local archive_path="$1"
  deployment_images_require_archive_readable "$archive_path" || return $?
  deployment_images_require_tar || return $?
  tar -tf "$archive_path" 2>/dev/null | grep -Fxq "$DEPLOYMENT_IMAGE_BUNDLE_CONTRACT"
}

# 从 bundle 中读取 deployment-images.contract.json。
deployment_images_bundle_contract_json() {
  local archive_path="$1"
  deployment_images_require_archive_readable "$archive_path" || return $?
  deployment_images_require_tar || return $?
  local contract_json=''
  contract_json="$(tar -xOf "$archive_path" "$DEPLOYMENT_IMAGE_BUNDLE_CONTRACT" 2>/dev/null || true)"
  [[ -n "$contract_json" ]] || {
    deployment_images_fail "部署镜像 bundle 缺少 $DEPLOYMENT_IMAGE_BUNDLE_CONTRACT：$archive_path"
    return $?
  }
  printf '%s\n' "$contract_json"
}

# 列出 bundle 合同声明的 pin refs。
deployment_images_archive_list_contract_refs() {
  local archive_path="$1"
  deployment_images_require_jq || return $?
  deployment_images_bundle_contract_json "$archive_path" | jq -r '.roles[]? | .pin_ref // empty'
}

# 列出镜像归档内声明的全部 RepoTags。
deployment_images_archive_list_repo_tags() {
  local archive_path="$1"
  deployment_images_require_jq || return $?
  deployment_images_archive_manifest_json "$archive_path" | jq -r '.[]? | .RepoTags[]? // empty'
}

# 校验归档是否覆盖所需镜像引用，并输出缺失项。
deployment_images_archive_verify_required_refs() {
  local archive_path="$1"
  shift || true
  local required_refs=()
  local archive_tags=()
  local ref=''
  local tag=''
  local found=0
  local missing=()
  local required_refs_text='' archive_tags_text=''

  if (( $# > 0 )); then
    required_refs=("$@")
  else
    required_refs_text="$(deployment_images_list_images)" || return $?
    mapfile -t required_refs <<< "$required_refs_text"
  fi

  deployment_images_require_archive_readable "$archive_path" || return $?
  deployment_images_require_jq || return $?
  deployment_images_require_tar || return $?
  if deployment_images_archive_is_bundle "$archive_path"; then
    archive_tags_text="$(deployment_images_archive_list_contract_refs "$archive_path")" || return $?
  else
    archive_tags_text="$(deployment_images_archive_list_repo_tags "$archive_path")" || return $?
  fi
  mapfile -t archive_tags <<< "$archive_tags_text"

  for ref in "${required_refs[@]}"; do
    [[ -n "$ref" ]] || continue
    found=0
    for tag in "${archive_tags[@]}"; do
      if [[ "$tag" == "$ref" ]]; then
        found=1
        break
      fi
    done
    (( found == 1 )) || missing+=("$ref")
  done

  if (( ${#missing[@]} == 0 )); then
    return 0
  fi
  printf '%s\n' "$(deployment_images_join_by_space "${missing[@]}")"
  return 1
}

# 要求给定归档满足当前 pin 所需的镜像合同。
deployment_images_require_archive_contract() {
  local archive_path="$1"
  shift || true
  local missing_refs=''
  if missing_refs="$(deployment_images_archive_verify_required_refs "$archive_path" "$@" || true)"; [[ -n "$missing_refs" ]]; then
    deployment_images_fail "部署镜像归档与当前 pin 不一致，缺少镜像：$missing_refs（archive=$archive_path）"
    return $?
  fi
  return 0
}

# 将 bundle 合同转换为运行态可消费的 verified local refs env，包含 pin、managed tag 与 image id。
deployment_images_contract_json_to_local_refs_env() {
  local contract_json="$1"
  local refs_file='' tmp_file=''
  refs_file="$(deployment_images_local_refs_env_path)"
  mkdir -p "$(dirname "$refs_file")"
  tmp_file="${refs_file}.tmp.$$"
  {
    printf '# Generated by load_deployment_images.sh; do not edit.\n'
    printf 'OPENCLAW_DEPLOYMENT_IMAGE_LOCAL_REFS_KIND=openclaw_deployment_image_local_refs\n'
    printf '%s\n' "$contract_json" | jq -r '
      .roles[]? |
      select((.env_key // "") != "" and (.pin_ref // "") != "" and (.managed_tag // "") != "") |
      "\(.env_key)_PIN_REF=\(.pin_ref)\n\(.env_key)_LOCAL_REF=\(.managed_tag)\n\(.env_key)_IMAGE_ID=\(.image_id // "")"
    '
  } > "$tmp_file"
  chmod 600 "$tmp_file"
  mv "$tmp_file" "$refs_file"
  deployment_images_note "已写出 verified local image refs：$refs_file"
}

# bundle 导入后补齐每个角色的 managed tag，并写出带 image id 证明的本地 ref 映射。
deployment_images_tag_loaded_bundle_roles() {
  local archive_path="$1"
  local contract_json=''
  local role='' env_key='' pin_ref='' managed_tag='' source_tag='' image_id=''
  deployment_images_archive_is_bundle "$archive_path" || return 0
  deployment_images_require_jq || return $?
  contract_json="$(deployment_images_bundle_contract_json "$archive_path")"
  while IFS='|' read -r role env_key pin_ref managed_tag source_tag image_id; do
    [[ -n "$managed_tag" ]] || continue
    if docker image inspect "$managed_tag" >/dev/null 2>&1; then
      :
    elif [[ -n "$source_tag" ]] && docker image inspect "$source_tag" >/dev/null 2>&1; then
      docker tag "$source_tag" "$managed_tag"
    elif [[ -n "$image_id" ]] && docker image inspect "$image_id" >/dev/null 2>&1; then
      docker tag "$image_id" "$managed_tag"
    else
      deployment_images_fail "bundle 导入后无法定位角色镜像：role=$role managed=$managed_tag source=$source_tag image_id=$image_id"
      return $?
    fi
  done < <(printf '%s\n' "$contract_json" | jq -r '.roles[]? | [.role, .env_key, .pin_ref, .managed_tag, .source_tag, .image_id] | @tsv' | tr '\t' '|')
  deployment_images_contract_json_to_local_refs_env "$contract_json"
}

# 根据当前部署镜像角色 pin 和本地 Docker inspect 结果生成 bundle 合同 JSON。
deployment_images_write_contract_json() {
  local out_file="$1"
  local generated_at="$2"
  local role='' env_key='' pin_ref='' label=''
  local source_tag='' managed_tag='' digest='' image_id='' repo_digests='' inspect_ref='' first=1
  local role_rows_text=''
  deployment_images_require_jq || return $?
  role_rows_text="$(deployment_images_role_rows)" || return $?
  {
    printf '{\n'
    printf '  "schemaVersion": 1,\n'
    printf '  "kind": "openclaw_deployment_image_bundle",\n'
    printf '  "generatedAt": %s,\n' "$(jq -Rn --arg v "$generated_at" '$v')"
    printf '  "dockerArchive": %s,\n' "$(jq -Rn --arg v "$DEPLOYMENT_IMAGE_BUNDLE_DOCKER_ARCHIVE" '$v')"
    printf '  "roles": [\n'
    while IFS='|' read -r role env_key pin_ref label; do
      source_tag="$(deployment_images_source_tag_ref "$pin_ref")"
      managed_tag="$(deployment_images_managed_tag_for_role "$role" "$pin_ref")"
      digest="$(deployment_images_ref_digest "$pin_ref")"
      inspect_ref="$pin_ref"
      if ! docker image inspect "$inspect_ref" >/dev/null 2>&1; then
        inspect_ref="$(deployment_images_resolve_verified_local_ref "$pin_ref" || true)"
      fi
      [[ -n "$inspect_ref" ]] || {
        deployment_images_fail "无法为部署镜像合同定位本地镜像：role=$role pin=$pin_ref"
        return $?
      }
      image_id="$(docker image inspect "$inspect_ref" --format '{{.Id}}')"
      repo_digests="$(docker image inspect "$inspect_ref" --format '{{json .RepoDigests}}')"
      if [[ "$first" != "1" ]]; then
        printf ',\n'
      fi
      first=0
      jq -cn \
        --arg role "$role" \
        --arg env_key "$env_key" \
        --arg role_label "$label" \
        --arg pin_ref "$pin_ref" \
        --arg source_tag "$source_tag" \
        --arg managed_tag "$managed_tag" \
        --arg digest "$digest" \
        --arg image_id "$image_id" \
        --argjson repo_digests "$repo_digests" \
        '{"role":$role, "env_key":$env_key, "label":$role_label, "pin_ref":$pin_ref, "source_tag":$source_tag, "managed_tag":$managed_tag, "digest":$digest, "image_id":$image_id, "repo_digests":$repo_digests}'
    done <<< "$role_rows_text"
    printf '\n  ]\n'
    printf '}\n'
  } > "$out_file"
}

# 将指定镜像归档导入本地 Docker。
deployment_images_load_archive() {
  local archive_path="$1"
  deployment_images_require_archive_readable "$archive_path" || return $?
  if ! deployment_images_archive_is_bundle "$archive_path"; then
    docker load -i "$archive_path"
    return $?
  fi
  deployment_images_require_tar || return $?
  deployment_images_require_sha256sum || return $?
  local tmp_parent="$DEPLOYMENT_IMAGE_ARTIFACT_DIR/tmp"
  local tmp_dir=''
  mkdir -p "$tmp_parent"
  tmp_dir="$(mktemp -d "$tmp_parent/load-bundle.XXXXXX")" || return 2
  trap '[[ -n "${tmp_dir:-}" ]] && rm -rf "$tmp_dir"' RETURN
  tar -xf "$archive_path" -C "$tmp_dir" "$DEPLOYMENT_IMAGE_BUNDLE_DOCKER_ARCHIVE" "$DEPLOYMENT_IMAGE_BUNDLE_SHA256" || return $?
  if ! (cd "$tmp_dir" && sha256sum -c "$DEPLOYMENT_IMAGE_BUNDLE_SHA256"); then
    deployment_images_fail "部署镜像 bundle 摘要校验失败：$archive_path"
    return $?
  fi
  docker load -i "$tmp_dir/$DEPLOYMENT_IMAGE_BUNDLE_DOCKER_ARCHIVE" || return $?
  rm -rf "$tmp_dir"
  trap - RETURN
}

# 确认本地 Docker 已具备所需镜像集合。
deployment_images_verify_required_images_present() {
  local image_refs=()
  local missing=()
  local image_refs_text='' missing_text=''
  if (( $# > 0 )); then
    image_refs=("$@")
  else
    image_refs_text="$(deployment_images_list_images)" || return $?
    mapfile -t image_refs <<< "$image_refs_text"
  fi
  missing_text="$(deployment_images_collect_missing_images "${image_refs[@]}")"
  if [[ -n "$missing_text" ]]; then
    mapfile -t missing <<< "$missing_text"
  fi
  if (( ${#missing[@]} == 0 )); then
    return 0
  fi
  printf '%s\n' "$(deployment_images_join_by_space "${missing[@]}")"
  return 1
}

# 导入镜像归档并在导入后再次验证镜像集合完整性。
deployment_images_load_archive_and_verify() {
  local archive_path="$1"
  local image_refs=()
  local missing_refs=''
  local image_refs_text=''
  if (( $# > 1 )); then
    shift
    image_refs=("$@")
  else
    image_refs_text="$(deployment_images_list_images)" || return $?
    mapfile -t image_refs <<< "$image_refs_text"
  fi
  deployment_images_require_archive_contract "$archive_path" "${image_refs[@]}" || return $?
  deployment_images_load_archive "$archive_path" || return $?
  deployment_images_tag_loaded_bundle_roles "$archive_path" || return $?
  if missing_refs="$(deployment_images_verify_required_images_present "${image_refs[@]}" || true)"; [[ -n "$missing_refs" ]]; then
    deployment_images_fail "部署镜像归档导入后仍缺少目标镜像：$missing_refs"
    return $?
  fi
  return 0
}
