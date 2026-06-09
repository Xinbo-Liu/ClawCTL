#!/usr/bin/env bash
# 用途：集中管理项目内镜像变量，避免镜像名散落硬编码。
# 约束：
# - 只按 key 定向读取 deploy/.env 与 pin 文件；
# - 部署镜像角色与 compose 运行镜像集合统一由 runtime.source_strategy 声明；
# - internal-api、scheduler 以及扩展贡献的 runtime Python 服务统一复用 OPENCLAW_RUNTIME_PYTHON_IMAGE；
# - host 控制面容器执行入口固定使用 OPENCLAW_CONTROL_PLANE_IMAGE，且不接受 deploy/.env 覆盖。

set -euo pipefail

IMAGE_ENV_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=repo_root.sh
source "$IMAGE_ENV_LIB_DIR/repo_root.sh"
IMAGE_ENV_ROOT_DIR="$(openclaw_repo_root_from "$IMAGE_ENV_LIB_DIR")"
unset IMAGE_ENV_LIB_DIR
ROOT_DIR="${ROOT_DIR:-$IMAGE_ENV_ROOT_DIR}"
# shellcheck source=repo_contracts.sh
source "$IMAGE_ENV_ROOT_DIR/scripts/lib/repo_contracts.sh"
IMAGE_ENV_DEPLOY_ENV_PATH="${IMAGE_ENV_DEPLOY_ENV_PATH:-$IMAGE_ENV_ROOT_DIR/deploy/.env}"
repo_contract_default_path IMAGE_ENV_PIN_FILE image_pins.openclaw
repo_contract_default_path IMAGE_ENV_RUNTIME_PIN_FILE image_pins.runtime
IMAGE_ENV_LOADED=0
IMAGE_ENV_LOADED_DEPLOY_ENV_PATH=''
IMAGE_ENV_LOADED_PIN_FILE=''
IMAGE_ENV_LOADED_RUNTIME_PIN_FILE=''

# 从指定 env/pin 文件读取 key 的最后一次赋值，兼容引号包裹和注释行。
image_env_read_key_from_file() {
  local file_path="$1" key="$2" raw_line="" line="" current_key="" value=""
  [[ -f "$file_path" ]] || { printf ''; return 0; }
  while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    line="$raw_line"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" || "${line:0:1}" == "#" || "$line" != *"="* ]] && continue
    current_key="${line%%=*}"
    value="${line#*=}"
    current_key="${current_key#"${current_key%%[![:space:]]*}"}"
    current_key="${current_key%"${current_key##*[![:space:]]}"}"
    [[ "$current_key" == "$key" ]] || continue
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    if [[ ${#value} -ge 2 ]]; then
      if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; then value="${value:1:${#value}-2}"; fi
      if [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then value="${value:1:${#value}-2}"; fi
    fi
    printf '%s' "$value"
    return 0
  done < "$file_path"
  printf ''
}

# 提供 bootstrap 侧同名读取入口，实际读取逻辑统一收口到 image_env_read_key_from_file。
image_env_read_key_from_file_bootstrap() {
  image_env_read_key_from_file "$@"
}

# 将 image:tag@digest 拆分为 repository、tag、digest 三段。
image_env_split_image_ref() {
  local image_ref="$1" without_digest="$1" repo="" tag="latest" digest="" last_segment=""
  if [[ "$without_digest" == *@* ]]; then
    digest="${without_digest#*@}"
    without_digest="${without_digest%@*}"
  fi
  last_segment="${without_digest##*/}"
  if [[ "$last_segment" == *:* ]]; then
    repo="${without_digest%:*}"
    tag="${without_digest##*:}"
  else
    repo="$without_digest"
  fi
  printf '%s
%s
%s
' "$repo" "$tag" "$digest"
}

# 校验镜像引用必须是完整 tag@sha256 pin，禁止 latest 或缺 digest。
image_env_require_tag_digest_ref() {
  local image_ref="$1" key="$2" file_path="$3" repo="" tag="" digest=""
  mapfile -t __image_parts < <(image_env_split_image_ref "$image_ref")
  repo="${__image_parts[0]}"; tag="${__image_parts[1]}"; digest="${__image_parts[2]}"
  [[ -n "$repo" ]] || { printf '[image_env] %s 必须包含 repository：%s（%s）
' "$key" "$image_ref" "$file_path" >&2; return 1; }
  [[ -n "$tag" && "$tag" != "latest" ]] || { printf '[image_env] %s 必须包含明确 tag，且不得为 latest：%s（%s）
' "$key" "$image_ref" "$file_path" >&2; return 1; }
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || { printf '[image_env] %s 必须为完整 tag@sha256 pin：%s（%s）
' "$key" "$image_ref" "$file_path" >&2; return 1; }
}

# 优先读取 deploy/.env 中的当前选择，缺省回退到 canonical pin 默认值。
image_env_get_value() {
  local key="$1" default_value="${2:-}" value=""
  value="$(image_env_read_key_from_file "$IMAGE_ENV_DEPLOY_ENV_PATH" "$key")"
  [[ -n "$value" ]] && { printf '%s
' "$value"; return 0; }
  printf '%s
' "$default_value"
}

# 对镜像引用列表按首次出现顺序去重。
image_env_print_unique() {
  awk 'NF && !seen[$0]++ { print }'
}

# 返回运行来源策略真源路径，供镜像角色集合从统一配置派生。
image_env_source_strategy_path() {
  repo_contract_path runtime.source_strategy
}

# 校验 jq 可用；镜像角色集合解析依赖 JSON 真源，缺失时必须显式失败。
image_env_require_strategy_jq() {
  command -v jq >/dev/null 2>&1 || {
    printf '[image_env] 缺少 jq；无法解析 runtime.source_strategy 中的镜像角色集合。\n' >&2
    return 1
  }
}

# 按用途从 source_strategy.json 列出 env key，避免把部署镜像数量写进脚本。
image_env_strategy_env_keys() {
  local purpose="$1"
  local strategy_path=''
  strategy_path="$(image_env_source_strategy_path)"
  [[ -f "$strategy_path" ]] || {
    printf '[image_env] 缺少运行来源策略真源：%s\n' "$strategy_path" >&2
    return 1
  }
  image_env_require_strategy_jq || return $?
  jq -r --arg purpose "$purpose" '
    (.images // {}) | to_entries[] |
    .value as $image |
    ($image.selected_runtime_source.ref_env // "") as $env_key |
    if $purpose == "deployment_contract" then
      ($image.deployment_contract // {}) as $contract |
      select($env_key != "" and ($contract.enabled == true)) |
      $env_key
    elif $purpose == "compose_runtime" then
      ($image.compose_runtime // {}) as $runtime |
      select($env_key != "" and (($runtime.enabled // false) == true)) |
      $env_key
    else
      empty
    end
  ' "$strategy_path" | tr -d '\r'
}

# 输出部署镜像合同覆盖的 env key 列表。
image_env_deployment_image_vars() {
  image_env_strategy_env_keys deployment_contract
}

# 按 env key 列表输出当前 image ref，并保持唯一化。
image_env_values_for_vars() {
  local env_key='' value=''
  for env_key in "$@"; do
    [[ -n "$env_key" ]] || continue
    value="${!env_key:-}"
    [[ -n "$value" ]] && printf '%s\n' "$value"
  done | image_env_print_unique
}

# 加载当前部署镜像 env，固定服务入口从 pin 真源读取，部署/运行集合另由 source_strategy 派生。
image_env_load() {
  local default_gateway default_control_plane default_runtime_python default_nginx
  if [[ "${IMAGE_ENV_LOADED:-0}" == "1" &&
    "${IMAGE_ENV_LOADED_DEPLOY_ENV_PATH:-}" == "$IMAGE_ENV_DEPLOY_ENV_PATH" &&
    "${IMAGE_ENV_LOADED_PIN_FILE:-}" == "$IMAGE_ENV_PIN_FILE" &&
    "${IMAGE_ENV_LOADED_RUNTIME_PIN_FILE:-}" == "$IMAGE_ENV_RUNTIME_PIN_FILE" ]]; then
    return 0
  fi

  default_gateway="$(image_env_read_key_from_file "$IMAGE_ENV_PIN_FILE" OPENCLAW_OFFICIAL_GATEWAY_IMAGE)"
  default_control_plane="$(image_env_read_key_from_file "$IMAGE_ENV_RUNTIME_PIN_FILE" OPENCLAW_CONTROL_PLANE_IMAGE)"
  default_runtime_python="$(image_env_read_key_from_file "$IMAGE_ENV_RUNTIME_PIN_FILE" OPENCLAW_RUNTIME_PYTHON_IMAGE)"
  default_nginx="$(image_env_read_key_from_file "$IMAGE_ENV_RUNTIME_PIN_FILE" NGINX_IMAGE)"

  OPENCLAW_OFFICIAL_GATEWAY_IMAGE="$(image_env_get_value OPENCLAW_OFFICIAL_GATEWAY_IMAGE "$default_gateway")"
  OPENCLAW_CONTROL_PLANE_IMAGE="$default_control_plane"
  OPENCLAW_RUNTIME_PYTHON_IMAGE="$(image_env_get_value OPENCLAW_RUNTIME_PYTHON_IMAGE "$default_runtime_python")"
  NGINX_IMAGE="$(image_env_get_value NGINX_IMAGE "$default_nginx")"

  image_env_require_tag_digest_ref "$OPENCLAW_OFFICIAL_GATEWAY_IMAGE" OPENCLAW_OFFICIAL_GATEWAY_IMAGE runtime
  image_env_require_tag_digest_ref "$OPENCLAW_CONTROL_PLANE_IMAGE" OPENCLAW_CONTROL_PLANE_IMAGE runtime
  image_env_require_tag_digest_ref "$OPENCLAW_RUNTIME_PYTHON_IMAGE" OPENCLAW_RUNTIME_PYTHON_IMAGE runtime
  image_env_require_tag_digest_ref "$NGINX_IMAGE" NGINX_IMAGE runtime

  mapfile -t __cp_parts < <(image_env_split_image_ref "$OPENCLAW_CONTROL_PLANE_IMAGE")
  OPENCLAW_CONTROL_PLANE_IMAGE_TAG="${__cp_parts[1]}"; OPENCLAW_CONTROL_PLANE_IMAGE_DIGEST="${__cp_parts[2]}"
  mapfile -t __py_parts < <(image_env_split_image_ref "$OPENCLAW_RUNTIME_PYTHON_IMAGE")
  OPENCLAW_RUNTIME_PYTHON_IMAGE_TAG="${__py_parts[1]}"; OPENCLAW_RUNTIME_PYTHON_IMAGE_DIGEST="${__py_parts[2]}"
  mapfile -t __ng_parts < <(image_env_split_image_ref "$NGINX_IMAGE")
  NGINX_IMAGE_TAG="${__ng_parts[1]}"; NGINX_IMAGE_DIGEST="${__ng_parts[2]}"
  mapfile -t __gw_parts < <(image_env_split_image_ref "$OPENCLAW_OFFICIAL_GATEWAY_IMAGE")
  OPENCLAW_OFFICIAL_GATEWAY_TAG="${__gw_parts[1]}"; OPENCLAW_OFFICIAL_GATEWAY_DIGEST="${__gw_parts[2]}"
  OPENCLAW_OFFICIAL_GATEWAY_RELEASE_VERSION="${OPENCLAW_OFFICIAL_GATEWAY_TAG%%-*}"

  export OPENCLAW_OFFICIAL_GATEWAY_IMAGE OPENCLAW_OFFICIAL_GATEWAY_TAG OPENCLAW_OFFICIAL_GATEWAY_DIGEST OPENCLAW_OFFICIAL_GATEWAY_RELEASE_VERSION
  export OPENCLAW_CONTROL_PLANE_IMAGE OPENCLAW_CONTROL_PLANE_IMAGE_TAG OPENCLAW_CONTROL_PLANE_IMAGE_DIGEST
  export OPENCLAW_RUNTIME_PYTHON_IMAGE OPENCLAW_RUNTIME_PYTHON_IMAGE_TAG OPENCLAW_RUNTIME_PYTHON_IMAGE_DIGEST
  export NGINX_IMAGE NGINX_IMAGE_TAG NGINX_IMAGE_DIGEST
  IMAGE_ENV_LOADED=1
  IMAGE_ENV_LOADED_DEPLOY_ENV_PATH="$IMAGE_ENV_DEPLOY_ENV_PATH"
  IMAGE_ENV_LOADED_PIN_FILE="$IMAGE_ENV_PIN_FILE"
  IMAGE_ENV_LOADED_RUNTIME_PIN_FILE="$IMAGE_ENV_RUNTIME_PIN_FILE"
}

# 返回 Gateway 当前运行镜像引用，供依赖该函数名的脚本使用。
official_gateway_runtime_image() { image_env_load; printf '%s
' "$OPENCLAW_OFFICIAL_GATEWAY_IMAGE"; }
# 返回控制面 Python 镜像引用。
image_env_control_plane_image() { image_env_load; printf '%s
' "$OPENCLAW_CONTROL_PLANE_IMAGE"; }
# 返回 runtime Python 镜像引用。
image_env_runtime_python_image() { image_env_load; printf '%s
' "$OPENCLAW_RUNTIME_PYTHON_IMAGE"; }

# 返回部署镜像合同覆盖的全部当前镜像引用。
image_env_deployment_images() {
  local image_vars=() image_vars_text=''
  image_env_load
  image_vars_text="$(image_env_deployment_image_vars)" || return $?
  [[ -n "$image_vars_text" ]] || {
    printf '[image_env] source_strategy 未声明部署镜像合同角色。\n' >&2
    return 1
  }
  mapfile -t image_vars <<< "$image_vars_text"
  image_env_values_for_vars "${image_vars[@]}"
}

# 返回构建输入需要预拉取的镜像集合；该集合是构建路径专用，不代表部署合同数量。
image_env_build_input_images() {
  image_env_load
  printf '%s
' "$OPENCLAW_OFFICIAL_GATEWAY_IMAGE" "$OPENCLAW_RUNTIME_PYTHON_IMAGE" | image_env_print_unique
}

# 返回 compose 运行服务实际引用的当前镜像集合。
image_env_runtime_service_images() {
  local image_vars=() image_vars_text=''
  image_env_load
  image_vars_text="$(image_env_runtime_service_image_vars)" || return $?
  [[ -n "$image_vars_text" ]] || {
    printf '[image_env] source_strategy 未声明 compose 运行镜像集合。\n' >&2
    return 1
  }
  mapfile -t image_vars <<< "$image_vars_text"
  image_env_values_for_vars "${image_vars[@]}"
}

# 返回 Gateway 当前完整 image ref。
image_env_openclaw_image_ref() { image_env_load; printf '%s
' "$OPENCLAW_OFFICIAL_GATEWAY_IMAGE"; }
# 返回 Gateway 镜像 repository。
image_env_openclaw_image_repo() { image_env_load; mapfile -t __gw_parts < <(image_env_split_image_ref "$OPENCLAW_OFFICIAL_GATEWAY_IMAGE"); printf '%s
' "${__gw_parts[0]}"; }
# 返回 Gateway 镜像 tag。
image_env_openclaw_image_tag() { image_env_load; printf '%s
' "$OPENCLAW_OFFICIAL_GATEWAY_TAG"; }
# 返回 Gateway 镜像 digest。
image_env_openclaw_image_digest() { image_env_load; printf '%s
' "$OPENCLAW_OFFICIAL_GATEWAY_DIGEST"; }
# 返回 Gateway release version，供 release gate 与 proof key 使用。
image_env_openclaw_release_version() { image_env_load; printf '%s
' "$OPENCLAW_OFFICIAL_GATEWAY_RELEASE_VERSION"; }

# 输出 compose 运行服务镜像 env key 列表。
image_env_runtime_service_image_vars() {
  image_env_strategy_env_keys compose_runtime
}
