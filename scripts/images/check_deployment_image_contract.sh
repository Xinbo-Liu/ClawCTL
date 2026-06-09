#!/usr/bin/env bash
# 用途：校验 compose 运行镜像、部署镜像归档与 host 控制面准备入口是否保持单一部署镜像合同。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir

ENV_FILE="$ROOT_DIR/deploy/.env"
COMPOSE_FILE="$ROOT_DIR/deploy/docker-compose.yml"
TEMPLATE_COMPOSE_FILE="$ROOT_DIR/deploy/docker-compose.yml"
COMPOSE_FILE_EXPLICIT=0
REQUIRE_LOCAL=0
RUN_PYTHON_CONTAINER_PATH="$ROOT_DIR/scripts/runtime/run_python_container.sh"
RUN_OPENCLAW_PYTHON_TOOL_PATH="$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh"
ENSURE_CONTROL_PLANE_IMAGE_PATH="$ROOT_DIR/scripts/images/ensure_control_plane_image.sh"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      [[ $# -ge 2 ]] || { echo '[FAIL] --env-file 缺少路径参数' >&2; exit 2; }
      ENV_FILE="$2"
      shift 2
      ;;
    --compose-file)
      [[ $# -ge 2 ]] || { echo '[FAIL] --compose-file 缺少路径参数' >&2; exit 2; }
      COMPOSE_FILE="$2"
      COMPOSE_FILE_EXPLICIT=1
      shift 2
      ;;
    --require-local)
      REQUIRE_LOCAL=1
      shift
      ;;
    --help|-h)
      cat <<'USAGE'
用法：
  bash ./scripts/images/check_deployment_image_contract.sh [--env-file <path>] [--compose-file <path>] [--require-local]

说明：
  --compose-file    指向已渲染 effective compose 时，校验最终 compose 实际 image refs 与 source_strategy/deploy env selected refs 一致。
  --require-local   要求最终 compose 中每个 image ref 都能被 docker image inspect 命中，并校验 tag@digest 的 RepoDigest 或 verified local refs 中的合同 image id。
USAGE
      exit 0
      ;;
    *)
      echo "[FAIL] 未知参数：$1" >&2
      exit 2
      ;;
  esac
done

IMAGE_ENV_DEPLOY_ENV_PATH="$ENV_FILE"
export IMAGE_ENV_DEPLOY_ENV_PATH
source "$ROOT_DIR/scripts/lib/image_env.sh"
source "$ROOT_DIR/scripts/runtime/runtime_compose_lib.sh"
image_env_load

fail() {
  echo "[FAIL] $1" >&2
  exit 2
}

expected_vars_text="$(image_env_runtime_service_image_vars)" || fail '无法从 source_strategy 解析 compose 运行镜像变量集合'
deployment_images_text="$(image_env_deployment_images)" || fail '无法从 source_strategy 解析部署镜像合同集合'
runtime_service_images_text="$(image_env_runtime_service_images)" || fail '无法从 source_strategy 解析 compose 运行镜像集合'
mapfile -t expected_vars <<< "$expected_vars_text"
mapfile -t deployment_images <<< "$deployment_images_text"
mapfile -t runtime_service_images <<< "$runtime_service_images_text"
trim_compose_image_value() {
  local value="$1"
  value="${value%%#*}"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  if [[ ${#value} -ge 2 ]]; then
    if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; then
      value="${value:1:${#value}-2}"
    elif [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
      value="${value:1:${#value}-2}"
    fi
  fi
  printf '%s\n' "$value"
}

compose_template_image_vars() {
  local raw_ref='' image_ref='' env_key=''
  sed -nE 's/^[[:space:]]*image:[[:space:]]*([^#]+).*$/\1/p' "$TEMPLATE_COMPOSE_FILE" |
    while IFS= read -r raw_ref; do
      image_ref="$(trim_compose_image_value "$raw_ref")"
      if [[ "$image_ref" =~ ^\$\{([A-Z0-9_]+):\?[^}]+\}$ ]]; then
        env_key="${BASH_REMATCH[1]}"
        printf '%s\n' "$env_key"
        continue
      fi
      printf '[FAIL] deploy/docker-compose.yml 的 image 必须使用 ${KEY:?required} 形式，实际：%s\n' "$image_ref" >&2
      return 2
    done |
    awk '!seen[$0]++'
}

compose_vars_text="$(compose_template_image_vars)" || exit $?
if [[ -n "$compose_vars_text" ]]; then
  mapfile -t compose_vars <<< "$compose_vars_text"
else
  compose_vars=()
fi

expected_joined="$(printf '%s\n' "${expected_vars[@]}" | sort | paste -sd ',' -)"
compose_joined="$(printf '%s\n' "${compose_vars[@]}" | sort | paste -sd ',' -)"
[[ "$compose_joined" == "$expected_joined" ]] || fail "deploy/docker-compose.yml 的 image 变量集合已漂移；期望=${expected_joined:-<none>}，实际=${compose_joined:-<none>}"

contract_joined="$(printf '%s\n' "${deployment_images[@]}" | sort -u | paste -sd ',' -)"
service_joined="$(printf '%s\n' "${runtime_service_images[@]}" | sort -u | paste -sd ',' -)"
for image in "${runtime_service_images[@]}"; do
  printf '%s\n' "${deployment_images[@]}" | grep -Fx -- "$image" >/dev/null || fail "镜像准备覆盖不完整；缺少 compose 运行镜像：$image"
done

[[ -f "$RUN_PYTHON_CONTAINER_PATH" ]] || fail '缺少 runtime Python 容器执行入口脚本'
[[ -f "$RUN_OPENCLAW_PYTHON_TOOL_PATH" ]] || fail '缺少 runtime OpenClaw Python 控制面入口脚本'
[[ -f "$ENSURE_CONTROL_PLANE_IMAGE_PATH" ]] || fail '缺少 host 控制面 Python 镜像准备脚本'
grep -Fq 'prepare_control_plane_medium.sh' "$RUN_OPENCLAW_PYTHON_TOOL_PATH" || fail 'run_openclaw_python_tool.sh 帮助面必须显式提示 prepare_control_plane_medium.sh'
grep -Fq 'scripts/images/ensure_control_plane_image.sh' "$ROOT_DIR/scripts/setup/prepare_control_plane_medium.sh" || fail 'prepare_control_plane_medium.sh 必须固定委托 ensure_control_plane_image.sh'
grep -Fq '不支持 docker run 隐式拉取' "$RUN_PYTHON_CONTAINER_PATH" || fail 'run_python_container.sh 必须显式禁止 docker run 隐式拉取'

compose_declared_image_refs() {
  sed -nE 's/^[[:space:]]*image:[[:space:]]*"?([^"#]+)"?[[:space:]]*$/\1/p' "$COMPOSE_FILE" |
    sed -E 's/[[:space:]]+$//' |
    while IFS= read -r image_ref; do
      image_ref="$(trim_compose_image_value "$image_ref")"
      if [[ "$image_ref" =~ ^\$\{([A-Z0-9_]+)(:\?[^}]*)?\}$ ]]; then
        env_key="${BASH_REMATCH[1]}"
        resolved_ref="${!env_key:-}"
        [[ -n "$resolved_ref" ]] && { printf '%s\n' "$resolved_ref"; continue; }
      fi
      printf '%s\n' "$image_ref"
    done |
    awk 'NF && !seen[$0]++'
}

compose_actual_image_refs() {
  local rendered='' refs=''
  if command -v docker >/dev/null 2>&1; then
    rendered="$(runtime_compose_command "$ENV_FILE" "$COMPOSE_FILE" config --format json)" || return 2
    refs="$(printf '%s\n' "$rendered" | jq -r '.services[]?.image // empty' | awk 'NF && !seen[$0]++')" || return 2
    printf '%s\n' "$refs"
    return 0
  fi
  compose_declared_image_refs
}

image_ref_digest() {
  local ref="$1"
  [[ "$ref" == *@* ]] || { printf ''; return 0; }
  printf '%s\n' "${ref#*@}"
}

image_ref_repo() {
  local ref="$1"
  local without_digest="${ref%@*}"
  local last_segment="${without_digest##*/}"
  if [[ "$last_segment" == *:* ]]; then
    printf '%s\n' "${without_digest%:*}"
  else
    printf '%s\n' "$without_digest"
  fi
}

gateway_selection_value() {
  local key="$1"
  local selection_file="$ROOT_DIR/state/image_pull/gateway_source_selection.json"
  [[ -f "$selection_file" && -r "$selection_file" ]] || { printf ''; return 0; }
  command -v jq >/dev/null 2>&1 || { printf ''; return 0; }
  jq -r --arg key "$key" '.[$key] // empty' "$selection_file" 2>/dev/null || true
}

verified_local_ref_for_expected() {
  local expected="$1"
  local refs_file='' env_key='' recorded_pin='' local_ref=''
  refs_file="$(runtime_compose_local_refs_env_file "$ROOT_DIR")"
  [[ -f "$refs_file" ]] || return 1
  for env_key in "${expected_vars[@]}"; do
    [[ "${!env_key:-}" == "$expected" ]] || continue
    recorded_pin="$(image_env_read_key_from_file "$refs_file" "${env_key}_PIN_REF")"
    [[ "$recorded_pin" == "$expected" ]] || continue
    local_ref="$(image_env_read_key_from_file "$refs_file" "${env_key}_LOCAL_REF")"
    [[ -n "$local_ref" ]] || continue
    printf '%s\n' "$local_ref"
    return 0
  done
  return 1
}

verified_local_image_id_for_expected() {
  local expected="$1"
  local refs_file='' env_key='' recorded_pin='' local_ref='' recorded_image_id=''
  refs_file="$(runtime_compose_local_refs_env_file "$ROOT_DIR")"
  [[ -f "$refs_file" ]] || return 1
  for env_key in "${expected_vars[@]}"; do
    [[ "${!env_key:-}" == "$expected" ]] || continue
    recorded_pin="$(image_env_read_key_from_file "$refs_file" "${env_key}_PIN_REF")"
    [[ "$recorded_pin" == "$expected" ]] || continue
    local_ref="$(image_env_read_key_from_file "$refs_file" "${env_key}_LOCAL_REF")"
    [[ -n "$local_ref" ]] || continue
    recorded_image_id="$(image_env_read_key_from_file "$refs_file" "${env_key}_IMAGE_ID")"
    [[ -n "$recorded_image_id" ]] || return 1
    printf '%s\n' "$recorded_image_id"
    return 0
  done
  return 1
}

image_ref_matches_expected() {
  local actual="$1"
  local expected="$2"
  local local_ref=''
  [[ "$actual" == "$expected" ]] && return 0
  local_ref="$(verified_local_ref_for_expected "$expected" || true)"
  [[ -n "$local_ref" && "$actual" == "$local_ref" ]]
}

expected_image_for_actual() {
  local actual="$1"
  local expected=''
  for expected in "${runtime_service_images[@]}"; do
    if image_ref_matches_expected "$actual" "$expected"; then
      printf '%s\n' "$expected"
      return 0
    fi
  done
  return 1
}

classify_compose_mismatch() {
  local expected="$1"
  local actual_joined="$2"
  local selected='' official='' env_rewritten=''
  selected="$(gateway_selection_value selected)"
  official="$(gateway_selection_value official)"
  env_rewritten="$(gateway_selection_value envRewritten)"
  if [[ "$env_rewritten" == 'true' && -n "$selected" && "$expected" == "$selected" && -n "$official" ]]; then
    if printf '%s\n' "$actual_joined" | tr ',' '\n' | grep -Fxq "$official"; then
      fail "Gateway candidate 已选中并写入 deploy env，但 effective compose 仍指向 canonical：selected=$selected actual=$official。请在 pull_images 改写 env 后重新加载镜像 env，并重渲染 state/openclaw/control_plane/setup/docker-compose.effective.yml。"
    fi
  fi
  local expected_digest='' actual='' actual_digest=''
  expected_digest="$(image_ref_digest "$expected")"
  if [[ -n "$expected_digest" ]]; then
    while IFS= read -r actual; do
      [[ -n "$actual" ]] || continue
      actual_digest="$(image_ref_digest "$actual")"
      if [[ "$actual_digest" == "$expected_digest" && "$actual" != "$expected" ]]; then
        fail "effective compose 引用的镜像 digest 与 selected ref 相同但仓库/tag 不一致：selected=$expected actual=$actual。若这是 Gateway candidate 自动切换，请确认 compose 已按改写后的 deploy env 重新渲染。"
      fi
    done < <(printf '%s\n' "$actual_joined" | tr ',' '\n')
  fi
  fail "effective compose 未使用 source_strategy/deploy env 的 selected image ref：缺少 $expected；实际=${actual_joined:-<none>}"
}

require_local_image_ref() {
  local image="$1"
  local expected="${2:-$1}"
  local expected_digest='' repo_digest_lines='' repo_digest_match='' image_id='' recorded_image_id=''
  if ! image_id="$(docker image inspect "$image" --format '{{.Id}}' 2>/dev/null)"; then
    local selected='' candidate='' official='' env_rewritten=''
    selected="$(gateway_selection_value selected)"
    candidate="$(gateway_selection_value candidate)"
    official="$(gateway_selection_value official)"
    env_rewritten="$(gateway_selection_value envRewritten)"
    if [[ "$env_rewritten" == 'true' && -n "$candidate" && "$image" == "$official" ]] && docker image inspect "$candidate" >/dev/null 2>&1; then
      fail "Gateway candidate 已拉取但 compose 仍指 canonical：compose=$image candidate=$candidate。请在 pull_images 改写 env 后重新加载镜像 env、重渲染 effective compose，再执行 compose up。"
    fi
    if [[ -n "$selected" && "$image" == "$selected" ]]; then
      fail "selected ref 未拉取或本地不可见：$image。请先执行 bash ./scripts/images/pull_images.sh，或使用 load_deployment_images.sh 导入同一 pin 的离线归档。"
    fi
    fail "final compose image ref 本地不存在：$image。docker_compose_up 前必须先完成部署镜像准备；若 registry 不可达，先运行 check_docker_host_readiness.sh 定位 selected/candidate 链路，或改用离线归档。"
  fi
  if [[ "$image" != "$expected" ]] && image_ref_matches_expected "$image" "$expected"; then
    expected_digest="$(image_ref_digest "$expected")"
    recorded_image_id="$(verified_local_image_id_for_expected "$expected" || true)"
    if [[ -n "$recorded_image_id" && "$image_id" != "$recorded_image_id" ]]; then
      fail "verified local ref 的 image ID 与合同记录不一致：local=$image pin=$expected expected_id=$recorded_image_id actual_id=$image_id"
    fi
    repo_digest_lines="$(docker image inspect "$image" --format '{{range .RepoDigests}}{{println .}}{{end}}' 2>/dev/null || true)"
    if [[ -n "$repo_digest_lines" && -n "$expected_digest" ]]; then
      repo_digest_match="$(printf '%s\n' "$repo_digest_lines" | grep -F "@$expected_digest" || true)"
      [[ -n "$repo_digest_match" ]] || fail "verified local ref 的 RepoDigest 与 pin digest 不一致：local=$image pin=$expected；RepoDigests=$(printf '%s' "$repo_digest_lines" | paste -sd, -)"
    fi
    if [[ -z "$repo_digest_lines" && -z "$recorded_image_id" ]]; then
      fail "verified local ref 缺少 image ID 与 RepoDigest 证明：local=$image pin=$expected。请重新导入包含 *_IMAGE_ID 的 deployment image bundle。"
    fi
    return 0
  fi
  expected_digest="$(image_ref_digest "$expected")"
  [[ -n "$expected_digest" ]] || return 0
  repo_digest_lines="$(docker image inspect "$image" --format '{{range .RepoDigests}}{{println .}}{{end}}' 2>/dev/null || true)"
  if [[ -z "$repo_digest_lines" ]]; then
    fail "本地镜像缺少 RepoDigests，无法证明 final compose image ref 的 digest：$image（image_id=$image_id）。请重新 docker pull 完整 tag@digest 引用或导入包含 RepoDigest 的部署镜像归档。"
  fi
  repo_digest_match="$(printf '%s\n' "$repo_digest_lines" | grep -F "@$expected_digest" || true)"
  [[ -n "$repo_digest_match" ]] || fail "本地镜像 RepoDigest 与 final compose image ref digest 不一致：$image；RepoDigests=$(printf '%s' "$repo_digest_lines" | paste -sd, -)"
}

if [[ "$COMPOSE_FILE_EXPLICIT" == '1' ]]; then
  [[ -f "$COMPOSE_FILE" ]] || fail "effective compose 文件不存在：$COMPOSE_FILE"
  actual_images_text="$(compose_actual_image_refs)" || fail "无法解析 effective compose 最终镜像集合；请确认 docker compose config 可执行且 env/local refs 可解析。"
  if [[ -n "$actual_images_text" ]]; then
    mapfile -t actual_images <<< "$actual_images_text"
  else
    actual_images=()
  fi
  actual_joined="$(printf '%s\n' "${actual_images[@]}" | sort -u | paste -sd ',' -)"
  for image in "${runtime_service_images[@]}"; do
    actual_match=''
    for actual_image in "${actual_images[@]}"; do
      if image_ref_matches_expected "$actual_image" "$image"; then
        actual_match="$actual_image"
        break
      fi
    done
    [[ -n "$actual_match" ]] || classify_compose_mismatch "$image" "$actual_joined"
  done
  for image in "${actual_images[@]}"; do
    expected_for_actual="$(expected_image_for_actual "$image" || true)"
    [[ -n "$expected_for_actual" ]] || fail "effective compose 引用了 source_strategy compose_runtime 之外的镜像：$image"
  done
  if [[ "$REQUIRE_LOCAL" == '1' ]]; then
    command -v docker >/dev/null 2>&1 || fail '--require-local 需要 docker CLI'
    for image in "${actual_images[@]}"; do
      expected_for_actual="$(expected_image_for_actual "$image" || true)"
      [[ -n "$expected_for_actual" ]] || fail "effective compose 引用了 source_strategy compose_runtime 之外的镜像：$image"
      require_local_image_ref "$image" "$expected_for_actual"
    done
  fi
fi

echo "[INFO] compose 运行镜像变量：${compose_joined}"
echo "[INFO] compose 运行镜像：${service_joined}"
echo "[INFO] 部署镜像合同：${contract_joined}"
if [[ "$COMPOSE_FILE_EXPLICIT" == '1' ]]; then
  echo "[INFO] effective compose 运行镜像：${actual_joined}"
fi
echo '[INFO] 当前部署镜像合同校验通过。'
