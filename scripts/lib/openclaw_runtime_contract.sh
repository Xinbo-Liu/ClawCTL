#!/usr/bin/env bash
# 用途：统一读取 OpenClaw 运行时合同真源，供镜像 pin、release gate、doctor 与文档治理共用。
set -euo pipefail

OPENCLAW_RUNTIME_CONTRACT_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=repo_root.sh
source "$OPENCLAW_RUNTIME_CONTRACT_LIB_DIR/repo_root.sh"
OPENCLAW_RUNTIME_CONTRACT_ROOT_DIR="$(openclaw_repo_root_from "$OPENCLAW_RUNTIME_CONTRACT_LIB_DIR")"
# shellcheck source=repo_contracts.sh
source "$OPENCLAW_RUNTIME_CONTRACT_ROOT_DIR/scripts/lib/repo_contracts.sh"
unset OPENCLAW_RUNTIME_CONTRACT_LIB_DIR

# 返回运行时合同真源所属的仓库根目录。
openclaw_runtime_contract_root_dir() {
  printf '%s\n' "$OPENCLAW_RUNTIME_CONTRACT_ROOT_DIR"
}

# 返回运行时合同 JSON 的默认路径。
openclaw_runtime_contract_file() {
  local root_dir="${1:-$(openclaw_runtime_contract_root_dir)}"
  printf '%s/%s\n' "$root_dir" "$(repo_contract_relpath runtime.runtime_contract)"
}

# 确认当前环境具备 jq，以便解析合同真源。
openclaw_runtime_contract_require_jq() {
  command -v jq >/dev/null 2>&1 || {
    echo '[openclaw_runtime_contract] 缺少 jq；请先执行 sudo bash ./scripts/setup/prepare_docker_host.sh --install-base-tools' >&2
    return 20
  }
}

openclaw_runtime_contract_trim_cr() {
  local value="${1-}"
  value="${value%$'\r'}"
  printf '%s' "$value"
}

# 加载运行时合同，并将核心字段导出为环境变量。
openclaw_runtime_contract_load() {
  local root_dir="${1:-$(openclaw_runtime_contract_root_dir)}"
  local contract_path="${OPENCLAW_RUNTIME_CONTRACT_PATH:-$(openclaw_runtime_contract_file "$root_dir")}"
  [[ -f "$contract_path" ]] || {
    echo "[openclaw_runtime_contract] 缺少合同真源：$contract_path" >&2
    return 2
  }

  openclaw_runtime_contract_require_jq || return $?

  local parsed=''
  parsed="$(jq -r '
    def scalar($value):
      if $value == null then ""
      elif ($value | type) == "string" then $value
      else ($value | tostring)
      end;
    def csv($value):
      if ($value | type) == "array" then ($value | map(tostring) | join(","))
      else ""
      end;
    [
      "OPENCLAW_RUNTIME_CONTRACT_GITHUB_LATEST_RELEASE_API=" + scalar(.upstream_release.release_discovery.github_latest_release_api),
      "OPENCLAW_RUNTIME_CONTRACT_GITHUB_RELEASE_URL_TEMPLATE=" + scalar(.upstream_release.release_discovery.github_release_url_template),
      "OPENCLAW_RUNTIME_CONTRACT_PACKAGE_URL=" + scalar(.upstream_release.release_discovery.package_url),
      "OPENCLAW_RUNTIME_CONTRACT_OFFICIAL_RELEASE_IMAGE_REPO=" + scalar(.upstream_release.image_repositories.official_release_image_repo),
      "OPENCLAW_RUNTIME_CONTRACT_DEFAULT_OFFICIAL_GATEWAY_IMAGE_REPO=" + scalar(.upstream_release.image_repositories.default_official_gateway_image_repo),
      "OPENCLAW_RUNTIME_CONTRACT_ALLOWED_CANDIDATE_IMAGE_REPOS_CSV=" + csv(.upstream_release.image_repositories.allowed_candidate_image_repos),
      "OPENCLAW_RUNTIME_CONTRACT_HAS_MODEL_RUNTIME=" + (if ((.model_runtime // null) | type) == "object" and ((.model_runtime.defaults.primary // "") | tostring | length) > 0 then "1" else "0" end),
      "OPENCLAW_RUNTIME_CONTRACT_MODEL_PRIMARY=" + scalar(.model_runtime.defaults.primary),
      "OPENCLAW_RUNTIME_CONTRACT_MODEL_CATALOG_IDS_CSV=" + ((.model_runtime.catalog // []) | map(select(type == "object") | .id // empty | tostring) | join(","))
    ] | .[]
  ' "$contract_path")"

  local line='' key='' value=''
  while IFS= read -r line; do
    line="$(openclaw_runtime_contract_trim_cr "$line")"
    [[ -n "$line" ]] || continue
    key="${line%%=*}"
    value="${line#*=}"
    key="$(openclaw_runtime_contract_trim_cr "$key")"
    value="$(openclaw_runtime_contract_trim_cr "$value")"
    printf -v "$key" '%s' "$value"
    export "${key?}"
  done <<< "$parsed"

  [[ -n "${OPENCLAW_RUNTIME_CONTRACT_GITHUB_LATEST_RELEASE_API:-}" ]] || {
    echo '[openclaw_runtime_contract] 缺少 upstream_release.release_discovery.github_latest_release_api' >&2
    return 2
  }
  [[ -n "${OPENCLAW_RUNTIME_CONTRACT_GITHUB_RELEASE_URL_TEMPLATE:-}" ]] || {
    echo '[openclaw_runtime_contract] 缺少 upstream_release.release_discovery.github_release_url_template' >&2
    return 2
  }
  [[ -n "${OPENCLAW_RUNTIME_CONTRACT_OFFICIAL_RELEASE_IMAGE_REPO:-}" ]] || {
    echo '[openclaw_runtime_contract] 缺少 upstream_release.image_repositories.official_release_image_repo' >&2
    return 2
  }
  [[ -n "${OPENCLAW_RUNTIME_CONTRACT_DEFAULT_OFFICIAL_GATEWAY_IMAGE_REPO:-}" ]] || {
    echo '[openclaw_runtime_contract] 缺少 upstream_release.image_repositories.default_official_gateway_image_repo' >&2
    return 2
  }
  : "${OPENCLAW_RUNTIME_CONTRACT_HAS_MODEL_RUNTIME:=0}"
  : "${OPENCLAW_RUNTIME_CONTRACT_MODEL_PRIMARY:=}"
  : "${OPENCLAW_RUNTIME_CONTRACT_MODEL_CATALOG_IDS_CSV:=}"
  export OPENCLAW_RUNTIME_CONTRACT_HAS_MODEL_RUNTIME OPENCLAW_RUNTIME_CONTRACT_MODEL_PRIMARY OPENCLAW_RUNTIME_CONTRACT_MODEL_CATALOG_IDS_CSV
  return 0
}

# 判断合同中是否声明了 model runtime 默认值。
openclaw_runtime_contract_has_model_runtime() {
  [[ "${OPENCLAW_RUNTIME_CONTRACT_HAS_MODEL_RUNTIME:-0}" == "1" ]]
}

# 判断指定仓库是否允许作为 candidate 源。
openclaw_runtime_contract_repo_allowed_for_candidate() {
  local repo="$1"
  local allowed_csv="${OPENCLAW_RUNTIME_CONTRACT_ALLOWED_CANDIDATE_IMAGE_REPOS_CSV:-}"
  local item=""
  IFS=',' read -r -a __allowed <<< "$allowed_csv"
  for item in "${__allowed[@]}"; do
    [[ -n "$item" ]] || continue
    [[ "$repo" == "$item" ]] && return 0
  done
  return 1
}

# 要求当前仓库必须属于 candidate 白名单。
openclaw_runtime_contract_require_candidate_repo() {
  local repo="$1"
  openclaw_runtime_contract_repo_allowed_for_candidate "$repo" && return 0
  echo "[openclaw_runtime_contract] candidate 仓库未列入 upstream_release.image_repositories.allowed_candidate_image_repos：$repo" >&2
  return 2
}

# 要求 promote 操作只能面向默认官方仓库。
openclaw_runtime_contract_require_promote_repo() {
  local repo="$1"
  if [[ "$repo" != "$OPENCLAW_RUNTIME_CONTRACT_DEFAULT_OFFICIAL_GATEWAY_IMAGE_REPO" ]]; then
    echo "[openclaw_runtime_contract] promote 只允许提升默认 pin 仓库：$OPENCLAW_RUNTIME_CONTRACT_DEFAULT_OFFICIAL_GATEWAY_IMAGE_REPO；当前收到：$repo" >&2
    return 2
  fi
  return 0
}

# 按 release URL 模板渲染指定 tag 的发布页地址。
openclaw_runtime_contract_render_release_url() {
  local tag="$1"
  local template="${OPENCLAW_RUNTIME_CONTRACT_GITHUB_RELEASE_URL_TEMPLATE:-}"
  printf '%s\n' "${template//\{tag\}/$tag}"
}

# 返回官方 Gateway 的 canonical 仓库名。
openclaw_runtime_contract_gateway_canonical_repo() {
  printf '%s\n' "${OPENCLAW_RUNTIME_CONTRACT_OFFICIAL_RELEASE_IMAGE_REPO:-}"
}

# 返回默认 pin 所使用的官方 Gateway 仓库。
openclaw_runtime_contract_gateway_default_repo() {
  printf '%s\n' "${OPENCLAW_RUNTIME_CONTRACT_DEFAULT_OFFICIAL_GATEWAY_IMAGE_REPO:-}"
}

# 列出允许用于 candidate 的 Gateway 仓库。
openclaw_runtime_contract_gateway_candidate_repos() {
  local allowed_csv="${OPENCLAW_RUNTIME_CONTRACT_ALLOWED_CANDIDATE_IMAGE_REPOS_CSV:-}"
  local item=""
  IFS=',' read -r -a __allowed <<< "$allowed_csv"
  for item in "${__allowed[@]}"; do
    [[ -n "$item" ]] || continue
    printf '%s\n' "$item"
  done
}

# 列出除 canonical 之外可用于加速/镜像站的 Gateway 仓库。
openclaw_runtime_contract_gateway_acceleration_repos() {
  local canonical_repo="${OPENCLAW_RUNTIME_CONTRACT_OFFICIAL_RELEASE_IMAGE_REPO:-}"
  local repo=""
  while IFS= read -r repo; do
    [[ -n "$repo" ]] || continue
    [[ "$repo" == "$canonical_repo" ]] && continue
    printf '%s\n' "$repo"
  done < <(openclaw_runtime_contract_gateway_candidate_repos)
}
