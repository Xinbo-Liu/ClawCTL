#!/usr/bin/env bash
set -euo pipefail

# 用途：在目标机上检查系统时间、Docker / Compose / DNS / 镜像站连通性是否满足进入 bootstrap 之前的宿主机前提。
# 说明：
# - 该脚本只做只读检查，不修改系统配置。
# - 只验证宿主机与网络前提；不会执行需要 bootstrap 产物的 compose 渲染。
# - compose config 必须在 bootstrap 生成 runtime.*.env 之后再检查。
# - 可用 --offline 跳过外部 DNS/HTTPS 探测，用于纯离线 docker load 场景。

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
source "$ROOT_DIR/scripts/setup/lib/setup_flow_handoff.sh"
source "$ROOT_DIR/scripts/lib/image_env.sh"
source "$ROOT_DIR/scripts/lib/control_plane_config_paths.sh"
source "$ROOT_DIR/scripts/lib/openclaw_runtime_contract.sh"
source "$ROOT_DIR/scripts/lib/registry_manifest_probe.sh"
# shellcheck source=../lib/repo_contracts.sh
source "$ROOT_DIR/scripts/lib/repo_contracts.sh"
# shellcheck source=../lib/docker_host_support_truth.sh
source "$ROOT_DIR/scripts/lib/docker_host_support_truth.sh"

OFFLINE_MODE=0
ENV_FILE="${IMAGE_ENV_DEPLOY_ENV_PATH:-$ROOT_DIR/deploy/.env}"
HOST_SUPPORT_POLICY_PATH="$(docker_host_support_truth_path)"
HOST_SUPPORT_POLICY_REL_PATH="$(docker_host_support_truth_relpath)"
RESOLVED_CONFIG_PATH=''
CONTROL_PLANE_MANIFESTS_DIR=''
CONTROL_PLANE_MANIFESTS_DIRS=()
ENABLED_EXTENSION_IDS=()
ACTIVE_RUNTIME_SOURCE_STRATEGY_PATHS=()
PROVIDER_API_SELECTED_DEFAULTS=()
CENTOS7_MIN_DOCKER_VERSION=''
CENTOS7_RECOMMENDED_DOCKER_VERSION=''
CENTOS7_MIN_COMPOSE_VERSION=''
CENTOS7_RECOMMENDED_COMPOSE_VERSION=''
CENTOS7_REQUIRED_STORAGE_DRIVER=''
CENTOS7_REQUIRED_COMMANDS=()
OFFICIAL_GATEWAY_ACCELERATION_REPOS=()
OFFICIAL_GATEWAY_CANONICAL_REPO=''
PYTHON_RUNTIME_ACCELERATION_REPOS=()
PYTHON_RUNTIME_CANONICAL_REPO=''
NGINX_RUNTIME_ACCELERATION_REPOS=()
NGINX_RUNTIME_CANONICAL_REPO=''
PROVIDER_API_ACCELERATION_BASE_URLS=()
PROVIDER_API_CANONICAL_BASE_URLS=()
DOCKER_REGISTRY_MIRRORS=()
OPENCLAW_SUPPLY_CHAIN_SCRIPT="$ROOT_DIR/scripts/images/check_openclaw_supply_chain.sh"

usage() {
  cat <<'USAGE' | sed "s|__HOST_SUPPORT_POLICY__|$HOST_SUPPORT_POLICY_REL_PATH|g"
用法：
  bash ./scripts/doctor/check_docker_host_readiness.sh [--offline] [--env-file <path>]

说明：
  - 本脚本只检查系统时间、Docker / Compose / DNS / HTTPS 与宿主机基础前提。
  - 若宿主机尚未准备完成，先回 docs/getting-started/environment-setup.md 选择对应在线 / 离线准备命令；不要把 --all 当成所有场景的唯一后续动作。
  - 仓库内 JSON / 供应链真源统一通过 jq + curl 解析；本脚本不准备 host 控制面执行介质。
  - 本脚本不执行容器化 Python 静态治理；进入 host 控制面命令前必须单独执行 prepare_control_plane_medium.sh。
  - 若 CentOS 7 初始机出现 `Cannot find a valid baseurl for repo: base/7/x86_64`，中国国内网络首轮部署先执行：sudo bash ./scripts/setup/prepare_docker_host.sh --all --network-profile cn；仅修复 repo 时执行：sudo bash ./scripts/setup/prepare_docker_host.sh --repair-centos7-vault-repos --network-profile cn
  - 对带 canonical / acceleration 分层的运行来源，脚本会先验证当前 selected runtime source；OpenClaw Gateway 的 digest 与候选仓库判定统一复用 check_openclaw_supply_chain.sh；Python / Nginx 只验证本地缓存与 Docker 传输链；若启用扩展声明了 provider / API 入口，也会按 active runtime source truth 一并探测。若 selected source 或当前传输链不可用，会继续探测候选来源并给出诊断，但仍阻止继续在线部署。
  - Python / Nginx 中国网络默认 pin 固定为 Daocloud tag@digest；宿主机预检只验证本地缓存与当前 selected source 传输链路，不在此阶段复做精确 tag@digest artifact 判定。
  - 候选 acceleration source 或 Docker registry mirror 探测通过只代表宿主机存在可用网络路径；本脚本保持只读，不改写 deploy env。后续在线 pull 由 pull_images.sh 按 PULL_GATEWAY_CANDIDATE_MODE=auto-switch|fail-fast|off 决定是否写入当前 deploy/.env 的候选覆盖值。
  - 运行后的来源限制由 Nginx allowlist 与基础设施边界证据共同闭合；本脚本只负责进入 bootstrap 前的宿主机前提。
  - private ingress 的 Nginx allowlist 与基础设施边界证据统一在部署后通过 check_ingress_boundary_evidence.sh 落盘。
  - CentOS 7 宿主机支持策略真源固定为 __HOST_SUPPORT_POLICY__。
  - 通过后的默认衔接动作统一查看 docs/getting-started/quickstart.md。

选项：
  --offline           跳过外部 DNS / HTTPS 探测；系统时间只做本机可信时间窗口校验。
  --env-file <path>   覆盖镜像与 provider/API 输入读取的 deploy env 文件（默认：deploy/.env）。
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --offline)
      OFFLINE_MODE=1
      ;;
    --env-file)
      [[ $# -ge 2 ]] || { echo "[FAIL] --env-file 缺少路径参数" >&2; exit 2; }
      ENV_FILE="$2"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[FAIL] 未知参数：$1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

IMAGE_ENV_DEPLOY_ENV_PATH="$ENV_FILE"
export IMAGE_ENV_DEPLOY_ENV_PATH
image_env_load

fail() {
  local msg="$1"
  local code="${2:-1}"
  echo "[FAIL] $msg" >&2
  exit "$code"
}

warn() {
  echo "[WARN] $1"
}

note() {
  echo "[INFO] $1"
}

check_cmd() {
  local cmd="$1"
  if host_command_exists "$cmd"; then
    return 0
  fi
  case "$cmd" in
    jq)
      fail "缺少命令：jq；请先执行 sudo bash ./scripts/setup/prepare_docker_host.sh --install-base-tools" 20
      ;;
    docker)
      fail "缺少命令：docker；已安装 Docker/Compose 的环境请修复 PATH 后继续；未安装环境请人工安装 Docker Engine 与 compose plugin 后复跑；离线目标机请准备离线安装包与 deployment_images_*.tar，再按 image-preparation 文档导入。Ubuntu 22.04 推荐基线只做检测与指引，本脚本不会自动 apt 安装 Docker/Compose。" 20
      ;;
    *)
      fail "缺少命令：$cmd" 20
      ;;
  esac
}

host_command_path() {
  local cmd="$1"
  local candidate=''
  if command -v "$cmd" >/dev/null 2>&1; then
    command -v "$cmd"
    return 0
  fi
  for candidate in "/usr/sbin/$cmd" "/sbin/$cmd" "/usr/bin/$cmd" "/bin/$cmd"; do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

host_command_exists() {
  host_command_path "$1" >/dev/null 2>&1
}

firewalld_zone_list_contains() {
  local zones="$1"
  local expected="$2"
  printf '%s\n' "$zones" | tr ' ' '\n' | grep -Fxq "$expected"
}

check_firewalld_docker_zone_contract() {
  local zones=''
  local target=''
  local target_status=0
  command -v firewall-cmd >/dev/null 2>&1 || {
    note '未检测到 firewall-cmd；跳过 firewalld docker zone 合同检查。'
    return 0
  }
  command -v systemctl >/dev/null 2>&1 || {
    warn '未检测到 systemctl；无法只读判断 firewalld 状态。'
    return 0
  }
  if ! systemctl is-active --quiet firewalld; then
    note 'firewalld 未运行；跳过 docker zone 合同检查。'
    return 0
  fi
  zones="$(firewall-cmd --permanent --get-zones 2>/dev/null || true)"
  if [[ -z "$zones" ]]; then
    warn '无法读取 firewalld permanent zone 列表；若后续 Docker bridge / NAT 报 INVALID_ZONE: docker，请先以 root 复跑 prepare_docker_host.sh --open-firewall。'
    return 0
  fi
  if ! firewalld_zone_list_contains "$zones" docker; then
    fail "firewalld 正在运行，但 permanent docker zone 缺失；Docker compose 创建 bridge 网络时可能报 INVALID_ZONE: docker。请执行 sudo bash ./scripts/setup/prepare_docker_host.sh --open-firewall 后重试；若已执行过 apply_ingress_boundary_rules.sh，Docker/firewalld 修复后还需重新物化 ingress 边界证据。" 35
  fi
  set +e
  target="$(firewall-cmd --permanent --zone=docker --get-target 2>/dev/null)"
  target_status=$?
  set -e
  if [[ "$target_status" -ne 0 || -z "$target" ]]; then
    warn '无法读取 firewalld permanent docker zone target；若后续 Docker bridge / NAT 报 INVALID_ZONE: docker，请先以 root 复跑 prepare_docker_host.sh --open-firewall。'
    return 0
  fi
  if [[ "$target" != "ACCEPT" ]]; then
    fail "firewalld permanent docker zone target=${target:-<empty>}，应为 ACCEPT；请执行 sudo bash ./scripts/setup/prepare_docker_host.sh --open-firewall 后重试。" 35
  fi
  note 'firewalld docker zone 合同通过：permanent zone=docker，target=ACCEPT。'
}

probe_dns() {
  local host="$1"
  getent hosts "$host" >/dev/null 2>&1
}

probe_http_status() {
  local url="$1"
  local code=''
  code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 8 --max-time 15 "$url" 2>/dev/null || true)"
  if [[ "$code" =~ ^[1-5][0-9][0-9]$ ]]; then
    printf '%s' "$code"
    return 0
  fi
  return 1
}

http_status_is_endpoint_reachable() {
  local code="$1"
  [[ "$code" =~ ^[2-4][0-9][0-9]$ ]]
}

append_named_array_item() {
  local array_name="$1"
  local value="$2"
  case "$array_name" in
    OFFICIAL_GATEWAY_ACCELERATION_REPOS) OFFICIAL_GATEWAY_ACCELERATION_REPOS+=("$value") ;;
    PYTHON_RUNTIME_ACCELERATION_REPOS) PYTHON_RUNTIME_ACCELERATION_REPOS+=("$value") ;;
    NGINX_RUNTIME_ACCELERATION_REPOS) NGINX_RUNTIME_ACCELERATION_REPOS+=("$value") ;;
    PROVIDER_API_SELECTED_DEFAULTS) PROVIDER_API_SELECTED_DEFAULTS+=("$value") ;;
    PROVIDER_API_ACCELERATION_BASE_URLS) PROVIDER_API_ACCELERATION_BASE_URLS+=("$value") ;;
    PROVIDER_API_CANONICAL_BASE_URLS) PROVIDER_API_CANONICAL_BASE_URLS+=("$value") ;;
    source_candidates_gateway) source_candidates_gateway+=("$value") ;;
    source_candidates_python) source_candidates_python+=("$value") ;;
    source_candidates_nginx) source_candidates_nginx+=("$value") ;;
    source_candidates_provider_api) source_candidates_provider_api+=("$value") ;;
    DOCKER_REGISTRY_MIRRORS) DOCKER_REGISTRY_MIRRORS+=("$value") ;;
    CONTROL_PLANE_MANIFESTS_DIRS) CONTROL_PLANE_MANIFESTS_DIRS+=("$value") ;;
    *) fail "append_named_array_item 不支持的数组名：$array_name" 19 ;;
  esac
}

print_named_array_items() {
  local array_name="$1"
  case "$array_name" in
    OFFICIAL_GATEWAY_ACCELERATION_REPOS) printf '%s\n' "${OFFICIAL_GATEWAY_ACCELERATION_REPOS[@]+"${OFFICIAL_GATEWAY_ACCELERATION_REPOS[@]}"}" ;;
    PYTHON_RUNTIME_ACCELERATION_REPOS) printf '%s\n' "${PYTHON_RUNTIME_ACCELERATION_REPOS[@]+"${PYTHON_RUNTIME_ACCELERATION_REPOS[@]}"}" ;;
    NGINX_RUNTIME_ACCELERATION_REPOS) printf '%s\n' "${NGINX_RUNTIME_ACCELERATION_REPOS[@]+"${NGINX_RUNTIME_ACCELERATION_REPOS[@]}"}" ;;
    PROVIDER_API_SELECTED_DEFAULTS) printf '%s\n' "${PROVIDER_API_SELECTED_DEFAULTS[@]+"${PROVIDER_API_SELECTED_DEFAULTS[@]}"}" ;;
    PROVIDER_API_ACCELERATION_BASE_URLS) printf '%s\n' "${PROVIDER_API_ACCELERATION_BASE_URLS[@]+"${PROVIDER_API_ACCELERATION_BASE_URLS[@]}"}" ;;
    PROVIDER_API_CANONICAL_BASE_URLS) printf '%s\n' "${PROVIDER_API_CANONICAL_BASE_URLS[@]+"${PROVIDER_API_CANONICAL_BASE_URLS[@]}"}" ;;
    source_candidates_gateway) printf '%s\n' "${source_candidates_gateway[@]+"${source_candidates_gateway[@]}"}" ;;
    source_candidates_python) printf '%s\n' "${source_candidates_python[@]+"${source_candidates_python[@]}"}" ;;
    source_candidates_nginx) printf '%s\n' "${source_candidates_nginx[@]+"${source_candidates_nginx[@]}"}" ;;
    source_candidates_provider_api) printf '%s\n' "${source_candidates_provider_api[@]+"${source_candidates_provider_api[@]}"}" ;;
    DOCKER_REGISTRY_MIRRORS) printf '%s\n' "${DOCKER_REGISTRY_MIRRORS[@]+"${DOCKER_REGISTRY_MIRRORS[@]}"}" ;;
    CONTROL_PLANE_MANIFESTS_DIRS) printf '%s\n' "${CONTROL_PLANE_MANIFESTS_DIRS[@]+"${CONTROL_PLANE_MANIFESTS_DIRS[@]}"}" ;;
    *) fail "print_named_array_items 不支持的数组名：$array_name" 19 ;;
  esac
}

append_unique_array_item() {
  local array_name="$1"
  local value="$2"
  local item=''
  while IFS= read -r item; do
    [[ "$item" == "$value" ]] && return 0
  done < <(print_named_array_items "$array_name")
  append_named_array_item "$array_name" "$value"
}

resolve_path_from_dir() {
  local base_dir="$1"
  local rel_path="$2"
  local rel_dir='' rel_base='' extension_root=''
  if [[ "$rel_path" == /* ]]; then
    printf '%s\n' "$rel_path"
    return 0
  fi
  if [[ "$rel_path" == @repo/* ]]; then
    resolve_path_from_dir "$ROOT_DIR" "${rel_path#@repo/}"
    return $?
  fi
  if [[ "$rel_path" == @extension/* ]]; then
    extension_root="$(resolve_extension_root_from_dir "$base_dir")" \
      || fail "无法解析 extension 锚点路径：$rel_path（base=$base_dir）" 19
    resolve_path_from_dir "$extension_root" "${rel_path#@extension/}"
    return $?
  fi
  rel_dir="$(dirname "$rel_path")"
  rel_base="$(basename "$rel_path")"
  (
    cd "$base_dir/$rel_dir" 2>/dev/null || exit 1
    printf '%s/%s\n' "$(pwd)" "$rel_base"
  ) || fail "无法解析相对路径：$rel_path（base=$base_dir）" 19
}

resolve_extension_root_from_dir() {
  local current="$1"
  local extensions_root="$ROOT_DIR/agent/extensions"
  current="$(cd "$current" 2>/dev/null && pwd -P)" || return 1
  extensions_root="$(cd "$extensions_root" 2>/dev/null && pwd -P)" || return 1
  while [[ "$current" == "$ROOT_DIR"* && "$current" != '/' ]]; do
    if [[ "$(dirname "$current")" == "$extensions_root" ]]; then
      printf '%s\n' "$current"
      return 0
    fi
    current="$(dirname "$current")"
  done
  return 1
}

load_control_plane_extension_state_with_jq() {
  check_cmd jq
  local current="$RESOLVED_CONFIG_PATH"
  local parent_rel='' config_path='' manifests_rel='' has_manifests='' has_enabled='' id=''
  local -a chain=()
  while :; do
    [[ -f "$current" ]] || fail "缺少 control-plane config：$current" 19
    chain=("$current" "${chain[@]+"${chain[@]}"}")
    parent_rel="$(jq -r '.extends // empty' "$current")"
    [[ -n "$parent_rel" ]] || break
    current="$(resolve_path_from_dir "$(dirname "$current")" "$parent_rel")"
  done
  local manifests_dir=''
  CONTROL_PLANE_MANIFESTS_DIR=''
  CONTROL_PLANE_MANIFESTS_DIRS=()
  ENABLED_EXTENSION_IDS=()
  for config_path in "${chain[@]}"; do
    has_manifests="$(jq -r 'if ((.extensions // {}) | has("manifestsDirs")) then "1" else "0" end' "$config_path")"
    if [[ "$has_manifests" == '1' ]]; then
      CONTROL_PLANE_MANIFESTS_DIRS=()
      while IFS= read -r manifests_dir; do
        [[ -n "$manifests_dir" ]] || continue
        append_unique_array_item CONTROL_PLANE_MANIFESTS_DIRS "$(resolve_path_from_dir "$(dirname "$config_path")" "$manifests_dir")"
      done < <(jq -r '.extensions.manifestsDirs[]? // empty' "$config_path")
      [[ "${#CONTROL_PLANE_MANIFESTS_DIRS[@]}" -gt 0 ]] || fail "control-plane config manifestsDirs 为空：$config_path" 19
      CONTROL_PLANE_MANIFESTS_DIR="${CONTROL_PLANE_MANIFESTS_DIRS[0]}"
    fi
    has_enabled="$(jq -r 'if ((.extensions // {}) | has("enabledExtensionIds")) then "1" else "0" end' "$config_path")"
    if [[ "$has_enabled" == '1' ]]; then
      ENABLED_EXTENSION_IDS=()
      while IFS= read -r id; do
        [[ -n "$id" ]] || continue
        ENABLED_EXTENSION_IDS+=("$id")
      done < <(jq -r '.extensions.enabledExtensionIds[]? // empty' "$config_path")
    fi
  done
  [[ "${#CONTROL_PLANE_MANIFESTS_DIRS[@]}" -gt 0 ]] || fail "control-plane config 缺少 extensions.manifestsDirs：$RESOLVED_CONFIG_PATH" 19
}

load_active_runtime_source_strategy_paths() {
  local extension_id='' manifest_path='' fragment_rel='' manifests_dir=''
  ACTIVE_RUNTIME_SOURCE_STRATEGY_PATHS=()
  load_control_plane_extension_state_with_jq
  for extension_id in "${ENABLED_EXTENSION_IDS[@]+"${ENABLED_EXTENSION_IDS[@]}"}"; do
    manifest_path=''
    for manifests_dir in "${CONTROL_PLANE_MANIFESTS_DIRS[@]+"${CONTROL_PLANE_MANIFESTS_DIRS[@]}"}"; do
      if [[ -f "$manifests_dir/${extension_id}.json" ]]; then
        manifest_path="$manifests_dir/${extension_id}.json"
        break
      fi
    done
    [[ -f "$manifest_path" ]] || fail "缺少已启用 extension manifest：$extension_id" 19
    fragment_rel="$(jq -r '.surfaceFragments.runtimeSourceStrategyPath // empty' "$manifest_path")"
    [[ -n "$fragment_rel" ]] || continue
    ACTIVE_RUNTIME_SOURCE_STRATEGY_PATHS+=("$(resolve_path_from_dir "$(dirname "$manifest_path")" "$fragment_rel")")
  done
}


load_runtime_source_strategy_with_jq() {
  local strategy_path="$1"
  check_cmd jq
  local strategy_line='' kind='' value=''
  while IFS= read -r strategy_line; do
    [[ -n "$strategy_line" ]] || continue
    kind="${strategy_line%%|*}"
    value="${strategy_line#*|}"
    case "$kind" in
      runtime_python_accel_repo) append_unique_array_item PYTHON_RUNTIME_ACCELERATION_REPOS "$value" ;;&
      runtime_python_canonical_repo) PYTHON_RUNTIME_CANONICAL_REPO="$value" ;;&
      nginx_runtime_accel_repo) append_unique_array_item NGINX_RUNTIME_ACCELERATION_REPOS "$value" ;;&
      nginx_runtime_canonical_repo) NGINX_RUNTIME_CANONICAL_REPO="$value" ;;&
      provider_selected_default) append_unique_array_item PROVIDER_API_SELECTED_DEFAULTS "$value" ;;&
      provider_canonical_base_url) append_unique_array_item PROVIDER_API_CANONICAL_BASE_URLS "$value" ;;&
      provider_accel_base_url) append_unique_array_item PROVIDER_API_ACCELERATION_BASE_URLS "$value" ;;&
    esac
  done < <(jq -r '
    def emit($kind; $value):
      ($value // "") | tostring | gsub("^[[:space:]]+|[[:space:]]+$"; "") | select(length > 0) | "\($kind)|\(.)";
    .images as $images
    | .providers as $providers
    | [
        ($images.runtime_python.acceleration_sources[]? | emit("runtime_python_accel_repo"; .repo)),
        emit("runtime_python_canonical_repo"; $images.runtime_python.canonical_source.repo),
        ($images.nginx_runtime.acceleration_sources[]? | emit("nginx_runtime_accel_repo"; .repo)),
        emit("nginx_runtime_canonical_repo"; $images.nginx_runtime.canonical_source.repo),
        ($providers // {} | to_entries[]? | emit("provider_selected_default"; ((.value.selected_runtime_source.env_key // "") + "\u001f" + (.value.selected_runtime_source.selected_default // "")))),
        ($providers // {} | to_entries[]? | emit("provider_canonical_base_url"; .value.canonical_source.baseUrl)),
        ($providers // {} | to_entries[]? | .value.acceleration_sources[]? | emit("provider_accel_base_url"; .baseUrl))
      ]
    | .[]
  ' "$strategy_path")
  return 0
}

load_runtime_source_strategy() {
  local strategy_path='' runtime_source_strategy_path=''
  runtime_source_strategy_path="$(repo_contract_path runtime.source_strategy)"
  [[ -f "$runtime_source_strategy_path" ]] || fail "缺少运行来源策略真源：$runtime_source_strategy_path" 19
  PYTHON_RUNTIME_ACCELERATION_REPOS=()
  PYTHON_RUNTIME_CANONICAL_REPO=''
  NGINX_RUNTIME_ACCELERATION_REPOS=()
  NGINX_RUNTIME_CANONICAL_REPO=''
  PROVIDER_API_ACCELERATION_BASE_URLS=()
  PROVIDER_API_CANONICAL_BASE_URLS=()
  PROVIDER_API_SELECTED_DEFAULTS=()
  load_active_runtime_source_strategy_paths
  load_runtime_source_strategy_with_jq "$runtime_source_strategy_path"
  for strategy_path in "${ACTIVE_RUNTIME_SOURCE_STRATEGY_PATHS[@]+"${ACTIVE_RUNTIME_SOURCE_STRATEGY_PATHS[@]}"}"; do
    [[ -f "$strategy_path" ]] || fail "缺少已启用 runtime source fragment：$strategy_path" 19
    load_runtime_source_strategy_with_jq "$strategy_path"
  done
}

hydrate_openclaw_gateway_sources_from_contract() {
  OFFICIAL_GATEWAY_CANONICAL_REPO="$(openclaw_runtime_contract_gateway_canonical_repo)"
  OFFICIAL_GATEWAY_ACCELERATION_REPOS=()
  local repo=''
  while IFS= read -r repo; do
    [[ -n "$repo" ]] || continue
    append_unique_array_item OFFICIAL_GATEWAY_ACCELERATION_REPOS "$repo"
  done < <(openclaw_runtime_contract_gateway_acceleration_repos)
}

probe_openclaw_gateway_candidate_repo_detail() {
  local repo="$1"
  local expected_digest="$2"
  local tag="$3"
  local actual_digest='' status=0 host=''
  host="$(extract_host "$repo")"
  set +e
  actual_digest="$(registry_manifest_probe_resolve_digest "$repo" "$tag" 2>/dev/null)"
  status=$?
  set -e
  if [[ "$status" -eq 0 ]]; then
    if [[ -n "$expected_digest" && "$actual_digest" != "$expected_digest" ]]; then
      printf '直连 %s：HTTP 200，但 digest 不一致（expected=%s，actual=%s）' "$host" "$expected_digest" "$actual_digest"
      return 10
    fi
    printf '直连 %s：HTTP 200，digest=%s' "$host" "$actual_digest"
    return 0
  fi
  return "$status"
}

probe_openclaw_gateway_source_group() {
  local label="$1"
  local fail_code="$2"
  local guidance="${3:-}"
  local tmp_json='' current_ref='' current_repo='' current_tag='' current_pinned_digest=''
  local current_mirror_status='' current_mirror_digest='' current_official_repo='' current_official_status='' current_official_digest=''
  local current_host='' current_detail='' repo='' detail='' status=0
  local -a usable_candidates=()
  local -a candidate_diagnostics=()

  [[ -f "$OPENCLAW_SUPPLY_CHAIN_SCRIPT" ]] || fail "缺少 OpenClaw 统一供应链脚本：$OPENCLAW_SUPPLY_CHAIN_SCRIPT" 19
  tmp_json="$(mktemp)"
  if ! bash "$OPENCLAW_SUPPLY_CHAIN_SCRIPT" --scope current-tag > "$tmp_json"; then
    rm -f "$tmp_json"
    fail "$label 无法读取统一供应链事实；请先修复 check_openclaw_supply_chain.sh 链路。" "$fail_code"
  fi

  current_ref="$(jq -r '.current.ref // empty' "$tmp_json")"
  current_repo="$(jq -r '.current.repo // empty' "$tmp_json")"
  current_tag="$(jq -r '.current.tag // empty' "$tmp_json")"
  current_pinned_digest="$(jq -r '.current.pinned_digest // empty' "$tmp_json")"
  current_mirror_status="$(jq -r 'if .current.mirror_digest_status == null then "" else (.current.mirror_digest_status|tostring) end' "$tmp_json")"
  current_mirror_digest="$(jq -r '.current.mirror_digest // empty' "$tmp_json")"
  current_official_repo="$(jq -r '.current.official_repo // empty' "$tmp_json")"
  current_official_status="$(jq -r 'if .current.official_digest_status == null then "" else (.current.official_digest_status|tostring) end' "$tmp_json")"
  current_official_digest="$(jq -r '.current.official_digest // empty' "$tmp_json")"
  rm -f "$tmp_json"

  [[ -n "$current_ref" && -n "$current_repo" && -n "$current_tag" && -n "$current_pinned_digest" ]] || fail "$label 统一供应链事实缺少当前 pin / repo / tag / digest。" "$fail_code"

  current_host="$(extract_host "$current_repo")"
  if [[ "$current_mirror_status" == "0" ]]; then
    if [[ "$current_mirror_digest" == "$current_pinned_digest" ]]; then
      note "$label 正常：$current_ref（直连 $current_host：HTTP 200，digest=$current_mirror_digest）"
      return 0
    fi
    current_detail="直连 $current_host：HTTP 200，但 digest 不一致（expected=$current_pinned_digest，actual=$current_mirror_digest）"
  else
    current_detail="直连 $current_host：不可达或未返回可校验 manifest digest"
  fi

  while IFS= read -r repo; do
    [[ -n "$repo" ]] || continue
    [[ "$repo" == "$current_repo" ]] && continue
    detail=''
    status=0
    if [[ "$repo" == "$current_official_repo" && "$current_official_status" == "0" && -n "$current_official_digest" ]]; then
      if [[ "$current_official_digest" == "$current_pinned_digest" ]]; then
        detail="直连 $(extract_host "$repo")：HTTP 200，digest=$current_official_digest"
        status=0
      else
        detail="直连 $(extract_host "$repo")：HTTP 200，但 digest 不一致（expected=$current_pinned_digest，actual=$current_official_digest）"
        status=10
      fi
    else
      set +e
      detail="$(probe_openclaw_gateway_candidate_repo_detail "$repo" "$current_pinned_digest" "$current_tag")"
      status=$?
      set -e
    fi
    if [[ -n "$detail" ]]; then
      if [[ "$status" -eq 0 ]]; then
        usable_candidates+=("$repo（$detail）")
      else
        candidate_diagnostics+=("$repo（$detail）")
      fi
    fi
  done < <(openclaw_runtime_contract_gateway_candidate_repos)

  if [[ "$current_mirror_status" == "0" ]]; then
    local diag_suffix=''
    if (( ${#usable_candidates[@]} > 0 || ${#candidate_diagnostics[@]} > 0 )); then
      diag_suffix="。其余候选诊断：${usable_candidates[*]} ${candidate_diagnostics[*]}"
      diag_suffix="${diag_suffix% }"
    fi
    fail "$label 当前 selected runtime source 可达，但与当前 pin 不一致：$current_ref（$current_detail）$diag_suffix。继续在线部署前，必须先修正默认 pin 或确认镜像站同步状态。$guidance" "$fail_code"
  fi

  if (( ${#usable_candidates[@]} > 0 )); then
    local diag_suffix=''
    if (( ${#candidate_diagnostics[@]} > 0 )); then
      diag_suffix="；其余候选诊断：${candidate_diagnostics[*]}"
    fi
    fail "$label 当前 selected runtime source 不可达：$current_ref（$current_detail）；已检测到可用候选：${usable_candidates[*]}$diag_suffix。后续 pull_images.sh 可按 PULL_GATEWAY_CANDIDATE_MODE=auto-switch 写入当前 deploy env 的候选覆盖值；或使用离线镜像归档。$guidance" "$fail_code"
  fi

  if (( ${#candidate_diagnostics[@]} > 0 )); then
    fail "$label 当前 selected runtime source 不可达：$current_ref（$current_detail）；其余候选诊断：${candidate_diagnostics[*]}。$guidance" "$fail_code"
  fi
  fail "$label 不可达：$current_ref（$current_detail）。$guidance" "$fail_code"
}

docker_hub_repo_host() {
  local host="$1"
  case "$host" in
    docker.io|index.docker.io|registry-1.docker.io)
      return 0
      ;;
  esac
  return 1
}

load_docker_registry_mirrors() {
  local mirror=''
  while IFS= read -r mirror; do
    mirror="${mirror#"${mirror%%[![:space:]]*}"}"
    mirror="${mirror%"${mirror##*[![:space:]]}"}"
    [[ -n "$mirror" ]] || continue
    append_unique_array_item DOCKER_REGISTRY_MIRRORS "$mirror"
  done < <(docker info --format '{{range .RegistryConfig.Mirrors}}{{println .}}{{end}}' 2>/dev/null || true)
}

image_ref_replace_repo() {
  local image_ref="$1"
  local repo="$2"
  local without_digest="$image_ref"
  local digest=''
  local tag='latest'
  local repo_tag=''
  if [[ "$without_digest" == *@* ]]; then
    digest="${without_digest#*@}"
    without_digest="${without_digest%@*}"
  fi
  repo_tag="${without_digest##*/}"
  if [[ "$repo_tag" == *:* ]]; then
    tag="${repo_tag##*:}"
  fi
  if [[ -n "$digest" ]]; then
    printf '%s:%s@%s' "$repo" "$tag" "$digest"
  else
    printf '%s:%s' "$repo" "$tag"
  fi
}

parse_image_ref() {
  local image_ref="$1"
  local without_digest="$image_ref"
  local repo_with_host=''
  local digest=''
  local tag='latest'
  if [[ "$without_digest" == *@* ]]; then
    digest="${without_digest#*@}"
    without_digest="${without_digest%@*}"
  fi
  repo_with_host="$without_digest"
  local last_segment="${repo_with_host##*/}"
  if [[ "$last_segment" == *:* ]]; then
    tag="${last_segment##*:}"
    repo_with_host="${repo_with_host%:*}"
  fi
  local host="${repo_with_host%%/*}"
  local repo_path="${repo_with_host#*/}"
  printf '%s
%s
%s
%s
' "$host" "$repo_path" "$tag" "$digest"
}

image_present_locally() {
  local image_ref="$1"
  docker image inspect "$image_ref" >/dev/null 2>&1
}

http_status_is_registry_base_reachable() {
  local code="$1"
  [[ "$code" == '200' || "$code" == '401' || "$code" == '403' ]]
}

probe_registry_base_endpoint() {
  local base_url="$1"
  local detail_prefix="$2"
  local host='' code=''
  host="$(extract_host "$base_url")"
  [[ -n "$host" ]] || return 1
  probe_dns "$host" || return 1
  code="$(probe_http_status "${base_url%/}/v2/" || true)"
  if http_status_is_registry_base_reachable "$code"; then
    printf '%s：HTTP %s' "$detail_prefix" "$code"
    return 0
  fi
  return 1
}

probe_runtime_image_transport_candidate() {
  local image_ref="$1"
  local prefer_mirrors="${2:-0}"
  local host='' detail='' mirror='' mirror_host=''
  mapfile -t __image_parts < <(parse_image_ref "$image_ref")
  host="${__image_parts[0]}"
  [[ -n "$host" ]] || return 1
  if docker_hub_repo_host "$host"; then
    if [[ "$prefer_mirrors" == '1' ]]; then
      while IFS= read -r mirror; do
        [[ -n "$mirror" ]] || continue
        mirror_host="$(extract_host "$mirror")"
        [[ -n "$mirror_host" ]] || continue
        detail="$(probe_registry_base_endpoint "$mirror" "经 Docker registry mirror $mirror_host" || true)"
        if [[ -n "$detail" ]]; then
          printf '%s' "$detail"
          return 0
        fi
      done < <(print_named_array_items DOCKER_REGISTRY_MIRRORS)
    fi
    detail="$(probe_registry_base_endpoint 'https://registry-1.docker.io' '直连 registry-1.docker.io' || true)"
    [[ -n "$detail" ]] || return 1
    printf '%s' "$detail"
    return 0
  fi
  detail="$(probe_registry_base_endpoint "https://$host" "直连 $host" || true)"
  [[ -n "$detail" ]] || return 1
  printf '%s' "$detail"
  return 0
}

probe_runtime_image_source_group() {
  local label="$1"
  local fail_code="$2"
  local array_name="$3"
  local guidance="${4:-}"
  local -a candidates=()
  mapfile -t candidates < <(print_named_array_items "$array_name")
  (( ${#candidates[@]} > 0 )) || fail "$label 缺少任何可探测候选。" "$fail_code"

  local first_target=''
  local attempted=()
  local candidate_hits=()
  local entry='' kind='' value='' display='' detail=''
  local idx=0

  for entry in "${candidates[@]}"; do
    kind="${entry%%|*}"
    value="${entry#*|}"
    display="$value"
    [[ -n "$first_target" ]] || first_target="$display"
    attempted+=("$display")

    if [[ "$kind" != 'registry' ]]; then
      idx=$((idx + 1))
      continue
    fi

    if (( idx == 0 )) && image_present_locally "$value"; then
      note "$label 已就绪：$display（本地镜像已存在；后续入口将直接复用本地镜像，不依赖远端拉取。）"
      return 0
    fi

    local prefer_mirrors=0
    if (( idx == 0 )); then
      prefer_mirrors=1
    fi
    detail="$(probe_runtime_image_transport_candidate "$value" "$prefer_mirrors" || true)"
    if [[ -n "$detail" ]]; then
      if (( idx == 0 )); then
        note "$label 正常：$display（$detail）；当前检查只验证本地缓存与 Docker 传输链路，不在宿主机预检阶段复做精确 tag@digest artifact 判定。"
        return 0
      fi
      candidate_hits+=("$display（$detail）")
    fi
    idx=$((idx + 1))
  done

  if (( ${#candidate_hits[@]} > 0 )); then
    fail "$label 当前本地缺少镜像，且 Docker 传输链未就绪：$first_target；已检测到可用候选链路：${candidate_hits[*]}。继续在线部署前，先执行 check_docker_host_readiness.sh 定位 Docker 传输链；中国国内网络首轮部署执行 prepare_docker_host.sh --all --network-profile cn；仍受限时使用离线镜像归档。$guidance" "$fail_code"
  fi
  fail "$label 当前本地缺少镜像，且 Docker 传输链未就绪；已依次尝试：${attempted[*]}。$guidance" "$fail_code"
}

probe_endpoint_candidate() {
  local endpoint="$1"
  local host='' code=''
  host="$(extract_host "$endpoint")"
  [[ -n "$host" ]] || return 1
  probe_dns "$host" || return 1
  code="$(probe_http_status "$endpoint" || true)"
  if http_status_is_endpoint_reachable "$code"; then
    printf 'HTTP %s' "$code"
    return 0
  fi
  return 1
}

probe_source_group() {
  local label="$1"
  local fail_code="$2"
  local array_name="$3"
  local guidance="${4:-}"
  local -a candidates=()
  mapfile -t candidates < <(print_named_array_items "$array_name")
  (( ${#candidates[@]} > 0 )) || fail "$label 缺少任何可探测候选。" "$fail_code"

  local first_target=''
  local attempted=()
  local candidate_hits=()
  local entry='' kind='' value='' display='' detail=''
  local idx=0
  for entry in "${candidates[@]}"; do
    kind="${entry%%|*}"
    value="${entry#*|}"
    display="$value"
    [[ -n "$first_target" ]] || first_target="$display"
    attempted+=("$display")
    detail=''
    if [[ "$kind" == 'endpoint' ]]; then
      detail="$(probe_endpoint_candidate "$value" || true)"
      if [[ -n "$detail" ]]; then
        if (( idx == 0 )); then
          note "$label 连通：$display（$detail）；该检查只验证网络 reachability，不验证业务鉴权。"
          return 0
        fi
        candidate_hits+=("$display（$detail）")
      fi
    else
      fail "$label 包含不支持的候选类型：$kind" "$fail_code"
    fi
    idx=$((idx + 1))
  done
  if (( ${#candidate_hits[@]} > 0 )); then
    fail "$label 当前 selected runtime source 不可达：$first_target；已检测到可用候选：${candidate_hits[*]}。继续在线部署前，必须先把当前运行来源切到候选，或使用离线镜像归档。$guidance" "$fail_code"
  fi
  fail "$label 不可达；已依次尝试：${attempted[*]}。$guidance" "$fail_code"
}

normalize_semver() {
  local raw="$1"
  raw="${raw#v}"
  raw="${raw%%-*}"
  raw="${raw%%+*}"
  echo "$raw"
}

semver_gte() {
  local current="$1"
  local minimum="$2"
  [[ -n "$current" && -n "$minimum" ]] || return 1
  [[ "$(printf '%s\n%s\n' "$minimum" "$current" | sort -V | head -n 1)" == "$minimum" ]]
}

load_host_support_policy() {
  [[ -f "$HOST_SUPPORT_POLICY_PATH" ]] || fail "缺少 CentOS 7 宿主机支持策略真源：$HOST_SUPPORT_POLICY_PATH" 19
  check_cmd jq
  CENTOS7_MIN_DOCKER_VERSION="$(docker_host_support_supported_centos7_section_value "$HOST_SUPPORT_POLICY_PATH" docker_server minimum '')"
  CENTOS7_RECOMMENDED_DOCKER_VERSION="$(docker_host_support_supported_centos7_section_value "$HOST_SUPPORT_POLICY_PATH" docker_server recommended '')"
  CENTOS7_MIN_COMPOSE_VERSION="$(docker_host_support_supported_centos7_section_value "$HOST_SUPPORT_POLICY_PATH" docker_compose minimum '')"
  CENTOS7_RECOMMENDED_COMPOSE_VERSION="$(docker_host_support_supported_centos7_section_value "$HOST_SUPPORT_POLICY_PATH" docker_compose recommended '')"
  CENTOS7_REQUIRED_STORAGE_DRIVER="$(docker_host_support_supported_centos7_scalar "$HOST_SUPPORT_POLICY_PATH" storage_driver_required '')"
  mapfile -t CENTOS7_REQUIRED_COMMANDS < <(jq -r '.policies.supported_centos7.required_commands[]? // empty' "$HOST_SUPPORT_POLICY_PATH")
  [[ -n "$CENTOS7_MIN_DOCKER_VERSION" ]] || fail "CentOS 7 宿主机支持策略缺少 docker_server.minimum：$HOST_SUPPORT_POLICY_PATH" 19
  [[ -n "$CENTOS7_MIN_COMPOSE_VERSION" ]] || fail "CentOS 7 宿主机支持策略缺少 docker_compose.minimum：$HOST_SUPPORT_POLICY_PATH" 19
  [[ -n "$CENTOS7_REQUIRED_STORAGE_DRIVER" ]] || fail "CentOS 7 宿主机支持策略缺少 storage_driver_required：$HOST_SUPPORT_POLICY_PATH" 19
  ((${#CENTOS7_REQUIRED_COMMANDS[@]} > 0)) || fail "CentOS 7 宿主机支持策略缺少 required_commands：$HOST_SUPPORT_POLICY_PATH" 19
}



extract_host() {
  local raw="$1"
  local host="$raw"
  if [[ "$host" == *"://"* ]]; then
    host="${host#*://}"
    host="${host%%/*}"
  else
    host="${host%%/*}"
  fi
  host="${host##*@}"
  echo "${host%%:*}"
}

detect_host_mode() {
  local os_id="" version_id="" pretty=""
  if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    source /etc/os-release
    os_id="${ID:-}"
    version_id="${VERSION_ID:-}"
    pretty="${PRETTY_NAME:-${NAME:-unknown}}"
  elif [[ -f /etc/centos-release ]]; then
    pretty="$(cat /etc/centos-release)"
    if grep -Eq 'CentOS( Linux)? release 7' /etc/centos-release; then
      os_id=centos
      version_id=7
    fi
  fi
  if [[ "$os_id" == "centos" && "$version_id" == 7* ]]; then
    HOST_MODE=supported_centos7
    HOST_OS_PRETTY="${pretty:-CentOS Linux 7}"
    return
  fi
  HOST_MODE=recommended
  HOST_OS_PRETTY="${pretty:-unknown}"
}

require_cmd_for_support_contract() {
  local cmd="$1"
  host_command_exists "$cmd" || fail "CentOS 7 宿主机支持策略要求存在命令：$cmd" 26
}

main() {
detect_host_mode
note "宿主机基线识别：$HOST_OS_PRETTY"
note "HOST_MODE=$HOST_MODE"
if [[ "$HOST_MODE" == "supported_centos7" ]]; then
  note "当前使用 CentOS 7 宿主机支持策略；必须通过额外宿主机预检后方可进入部署。"
else
  note "当前使用推荐宿主机基线。"
fi

note "检查系统时间与证书时钟前提"
system_time_args=(bash "$ROOT_DIR/scripts/doctor/check_system_time.sh")
[[ "$OFFLINE_MODE" == "1" ]] && system_time_args+=(--offline)
"${system_time_args[@]}" || fail "系统时间校验失败；请先执行 sudo bash ./scripts/setup/update_system_time.sh 后重试。" 24

note "检查 Docker / Compose 可用性"
check_cmd docker
RESOLVED_CONFIG_PATH="$(openclaw_control_plane_resolve_config_path agent_platform)"
load_host_support_policy
load_runtime_source_strategy
openclaw_runtime_contract_load "$ROOT_DIR"
hydrate_openclaw_gateway_sources_from_contract

note "跳过控制面 Python 介质检查：宿主机 readiness 只验证 Docker / Compose / 网络 / 镜像来源只读前提；进入 host 控制面命令前执行 prepare_control_plane_medium.sh。"

docker version >/dev/null 2>&1 || fail "docker CLI 存在，但无法执行 docker version；请检查 daemon 是否已启动。" 21

if ! docker info >/dev/null 2>&1; then
  docker_info_err="$(docker info 2>&1 || true)"
  if printf '%s' "$docker_info_err" | grep -qiE 'permission denied|/var/run/docker\.sock|got permission denied'; then
    fail "无法连接 Docker daemon；当前用户无权访问 Docker daemon（通常是 /var/run/docker.sock 权限不足）。请先修复 docker 组 / sudo / daemon 权限。" 22
  fi
  fail "无法连接 Docker daemon；请确认 systemctl status docker 为 active。" 22
fi
check_firewalld_docker_zone_contract

if ! docker compose version >/dev/null 2>&1; then
  fail "缺少 docker compose 插件，或插件不可用。已安装环境请修复 Docker CLI plugin 路径后继续；未安装环境请人工安装 compose plugin；离线环境请先准备 compose plugin 离线包。本脚本不在 Ubuntu 22.04 上自动 apt 安装 Docker/Compose。" 23
fi

load_docker_registry_mirrors
if (( ${#DOCKER_REGISTRY_MIRRORS[@]} > 0 )); then
  note "Docker registry mirrors: ${DOCKER_REGISTRY_MIRRORS[*]}"
else
  warn "当前 Docker daemon 未报告 registry-mirrors；若目标环境依赖中国网络镜像加速，首轮部署先执行 prepare_docker_host.sh --all --network-profile cn，单独修复 daemon 时执行 prepare_docker_host.sh --configure-daemon。"
fi

if [[ "$OFFLINE_MODE" == "0" ]]; then
  note "检查镜像/接口 DNS 与 HTTPS 连通性"

  source_candidates_gateway=(
    "registry|$OPENCLAW_OFFICIAL_GATEWAY_IMAGE"
  )
  if [[ -n "$OFFICIAL_GATEWAY_CANONICAL_REPO" ]]; then
    append_unique_array_item source_candidates_gateway "registry|$(image_ref_replace_repo "$OPENCLAW_OFFICIAL_GATEWAY_IMAGE" "$OFFICIAL_GATEWAY_CANONICAL_REPO")"
  fi
  for repo in "${OFFICIAL_GATEWAY_ACCELERATION_REPOS[@]+"${OFFICIAL_GATEWAY_ACCELERATION_REPOS[@]}"}"; do
    append_unique_array_item source_candidates_gateway "registry|$(image_ref_replace_repo "$OPENCLAW_OFFICIAL_GATEWAY_IMAGE" "$repo")"
  done

  source_candidates_python=(
    "registry|$OPENCLAW_RUNTIME_PYTHON_IMAGE"
  )
  if [[ -n "$PYTHON_RUNTIME_CANONICAL_REPO" ]]; then
    append_unique_array_item source_candidates_python "registry|$(image_ref_replace_repo "$OPENCLAW_RUNTIME_PYTHON_IMAGE" "$PYTHON_RUNTIME_CANONICAL_REPO")"
  fi
  for repo in "${PYTHON_RUNTIME_ACCELERATION_REPOS[@]+"${PYTHON_RUNTIME_ACCELERATION_REPOS[@]}"}"; do
    append_unique_array_item source_candidates_python "registry|$(image_ref_replace_repo "$OPENCLAW_RUNTIME_PYTHON_IMAGE" "$repo")"
  done

  source_candidates_nginx=(
    "registry|$NGINX_IMAGE"
  )
  if [[ -n "$NGINX_RUNTIME_CANONICAL_REPO" ]]; then
    append_unique_array_item source_candidates_nginx "registry|$(image_ref_replace_repo "$NGINX_IMAGE" "$NGINX_RUNTIME_CANONICAL_REPO")"
  fi
  for repo in "${NGINX_RUNTIME_ACCELERATION_REPOS[@]+"${NGINX_RUNTIME_ACCELERATION_REPOS[@]}"}"; do
    append_unique_array_item source_candidates_nginx "registry|$(image_ref_replace_repo "$NGINX_IMAGE" "$repo")"
  done

  source_candidates_provider_api=()
  for provider_binding in "${PROVIDER_API_SELECTED_DEFAULTS[@]+"${PROVIDER_API_SELECTED_DEFAULTS[@]}"}"; do
    provider_env_key="${provider_binding%%$'\x1f'*}"
    provider_default_endpoint="${provider_binding#*$'\x1f'}"
    provider_selected_endpoint="$provider_default_endpoint"
    if [[ -n "$provider_env_key" && -n "${!provider_env_key-}" ]]; then
      provider_selected_endpoint="${!provider_env_key}"
    fi
    if [[ -n "$provider_selected_endpoint" ]]; then
      append_unique_array_item source_candidates_provider_api "endpoint|$provider_selected_endpoint"
    fi
  done
  for endpoint in "${PROVIDER_API_CANONICAL_BASE_URLS[@]+"${PROVIDER_API_CANONICAL_BASE_URLS[@]}"}"; do
    append_unique_array_item source_candidates_provider_api "endpoint|$endpoint"
  done
  for endpoint in "${PROVIDER_API_ACCELERATION_BASE_URLS[@]+"${PROVIDER_API_ACCELERATION_BASE_URLS[@]}"}"; do
    append_unique_array_item source_candidates_provider_api "endpoint|$endpoint"
  done

  probe_openclaw_gateway_source_group "OpenClaw Gateway 镜像来源" 25 "中国国内网络首轮部署先执行 sudo bash ./scripts/setup/prepare_docker_host.sh --all --network-profile cn；readiness 只区分 selected source 不可达、candidate 可用与 digest 不一致。后续 pull_images.sh 默认用 PULL_GATEWAY_CANDIDATE_MODE=auto-switch 只改当前 deploy/.env；selected/candidate 都不可达时使用离线镜像归档。"
  probe_runtime_image_source_group "Python 运行镜像来源" 25 source_candidates_python "若当前本地尚未准备 OPENCLAW_RUNTIME_PYTHON_IMAGE，中国国内网络先确认 sudo bash ./scripts/setup/prepare_docker_host.sh --all --network-profile cn 已完成；在线补齐执行 pull_images.sh，受限网络执行 load_deployment_images.sh 或使用离线镜像归档。"
  probe_runtime_image_source_group "Nginx 运行镜像来源" 25 source_candidates_nginx "若当前本地尚未准备 NGINX_IMAGE，中国国内网络先确认 sudo bash ./scripts/setup/prepare_docker_host.sh --all --network-profile cn 已完成；在线补齐执行 pull_images.sh，受限网络执行 load_deployment_images.sh 或使用离线镜像归档。"
  if (( ${#source_candidates_provider_api[@]} > 0 )); then
    probe_source_group "启用的 provider/API 入口" 25 source_candidates_provider_api "provider/API 入口真源来自当前 active profile 的 deploy env schema 与 extension.env/site.env 输入；本检查只校验网络连通性，不把 base URL 当作业务健康检查接口。"
  else
    note "当前 base kernel 未声明默认 provider / API 入口；跳过外部 provider 连通性探测。"
  fi
else
  note "离线模式：跳过外部 DNS / HTTPS 探测。"
fi

note "检查 Docker 运行时关键字段"
storage_driver="$(docker info --format '{{.Driver}}' 2>/dev/null || true)"
cgroup_driver="$(docker info --format '{{.CgroupDriver}}' 2>/dev/null || true)"
server_version="$(docker version --format '{{.Server.Version}}' 2>/dev/null || true)"

[[ -n "$storage_driver" ]] && note "Storage Driver: $storage_driver"
[[ -n "$cgroup_driver" ]] && note "Cgroup Driver: $cgroup_driver"
[[ -n "$server_version" ]] && note "Docker Server Version: $server_version"

kernel_release="$(uname -r 2>/dev/null || true)"
[[ -n "$kernel_release" ]] && note "Kernel Release: $kernel_release"

compose_version="$(docker compose version --short 2>/dev/null || docker compose version 2>/dev/null | head -n 1 || true)"
[[ -n "$compose_version" ]] && note "Docker Compose Version: $compose_version"

runc_path="$(host_command_path runc || true)"
if [[ -n "$runc_path" ]]; then
  note "runc Version: $("$runc_path" --version 2>/dev/null | head -n 1)"
else
  [[ "$HOST_MODE" == "supported_centos7" ]] && fail "CentOS 7 宿主机支持策略要求已安装 runc" 27
  warn "未检测到 runc；如运行时异常，请补齐。"
fi

containerd_path="$(host_command_path containerd || true)"
if [[ -n "$containerd_path" ]]; then
  note "containerd Version: $("$containerd_path" --version 2>/dev/null | head -n 1)"
else
  [[ "$HOST_MODE" == "supported_centos7" ]] && fail "CentOS 7 宿主机支持策略要求已安装 containerd" 28
  warn "未检测到 containerd；如运行时异常，请补齐。"
fi

iptables_path="$(host_command_path iptables || true)"
if [[ -n "$iptables_path" ]]; then
  note "iptables 已安装：$iptables_path"
else
  if [[ "$HOST_MODE" == "supported_centos7" ]]; then
    fail "CentOS 7 宿主机支持策略要求已安装 iptables" 29
  fi
  warn "未检测到 iptables；若后续 bridge / NAT 行为异常，请先补齐。"
fi

if [[ "$HOST_MODE" == "supported_centos7" ]]; then
  for cmd in "${CENTOS7_REQUIRED_COMMANDS[@]}"; do
    require_cmd_for_support_contract "$cmd"
  done
  [[ "$storage_driver" == "$CENTOS7_REQUIRED_STORAGE_DRIVER" ]] || fail "CentOS 7 宿主机支持策略要求 Docker Storage Driver=${CENTOS7_REQUIRED_STORAGE_DRIVER}，当前：${storage_driver:-<empty>}" 30
  [[ -n "$server_version" ]] || fail "CentOS 7 宿主机支持策略要求可读取 Docker Server Version" 31
  [[ -n "$compose_version" ]] || fail "CentOS 7 宿主机支持策略要求可读取 Docker Compose Version" 32
  normalized_server_version="$(normalize_semver "$server_version")"
  normalized_compose_version="$(normalize_semver "$compose_version")"
  semver_gte "$normalized_server_version" "$CENTOS7_MIN_DOCKER_VERSION" || fail "CentOS 7 宿主机支持策略要求 Docker Server Version >= ${CENTOS7_MIN_DOCKER_VERSION}，当前：${server_version:-<empty>}" 33
  semver_gte "$normalized_compose_version" "$CENTOS7_MIN_COMPOSE_VERSION" || fail "CentOS 7 宿主机支持策略要求 Docker Compose Version >= ${CENTOS7_MIN_COMPOSE_VERSION}，当前：${compose_version:-<empty>}" 34
  if [[ -n "$CENTOS7_RECOMMENDED_DOCKER_VERSION" && "$normalized_server_version" != "$CENTOS7_RECOMMENDED_DOCKER_VERSION" ]]; then
    warn "CentOS 7 宿主机支持策略当前推荐 Docker Server Version=${CENTOS7_RECOMMENDED_DOCKER_VERSION}；当前为 ${server_version:-<empty>}。该版本仍允许继续，但不属于当前建议组合。"
  fi
  if [[ -n "$CENTOS7_RECOMMENDED_COMPOSE_VERSION" && "$normalized_compose_version" != "$CENTOS7_RECOMMENDED_COMPOSE_VERSION" ]]; then
    warn "CentOS 7 宿主机支持策略当前推荐 Docker Compose Version=${CENTOS7_RECOMMENDED_COMPOSE_VERSION}；当前为 ${compose_version:-<empty>}。该版本仍允许继续，但不属于当前建议组合。"
  fi
  note "CentOS 7 宿主机支持策略已按真源校验：policy=$HOST_SUPPORT_POLICY_PATH，Docker>=${CENTOS7_MIN_DOCKER_VERSION}，Compose>=${CENTOS7_MIN_COMPOSE_VERSION}"
  note "CentOS 7 宿主机支持策略预检通过。"
fi

note "宿主机前提检查完成；compose 渲染需在 bootstrap 之后单独执行。"
setup_flow_print_unified_handoff '宿主机 readiness 阶段' '已通过'

echo "[OK] Docker 主机准备检查通过。"
}

main
