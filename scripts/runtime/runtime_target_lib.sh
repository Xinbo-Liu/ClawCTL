#!/usr/bin/env bash
# 用途：统一从 runtime service registry 解析 runtime target -> compose service / docker container 的真源映射；现行唯一入口为 official gateway + private ingress，并按 active profile 叠加 extension 服务。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
PYTHON_RUNNER="$ROOT_DIR/scripts/runtime/run_python_container.sh"
# shellcheck source=../lib/repo_contracts.sh
source "$ROOT_DIR/scripts/lib/repo_contracts.sh"
repo_contract_assign_path SERVICE_REGISTRY_PATH runtime.service_registry
# shellcheck source=../lib/control_plane_config_paths.sh
source "$ROOT_DIR/scripts/lib/control_plane_config_paths.sh"
# shellcheck source=../lib/repo_python_env.sh
source "$ROOT_DIR/scripts/lib/repo_python_env.sh"
RUNTIME_TARGET_RESOLVED_CONFIG_PATH="${RUNTIME_TARGET_RESOLVED_CONFIG_PATH:-}"
RUNTIME_TARGET_REGISTRY_CACHE="${RUNTIME_TARGET_REGISTRY_CACHE:-}"

# 统一输出 runtime target 解析失败信息，并返回标准错误码。
runtime_target_fail() {
  echo "[runtime_target][FAIL] $*" >&2
  return 2
}

# 标准化 runtime target 别名，保证查询入口一致。
runtime_target_normalize_target() {
  case "${1:-}" in
    *) printf '%s\n' "${1:-}" ;;
  esac
}

# 按需解析 runtime target 所依赖的 control-plane config，避免 source 阶段触发容器化 Python。
runtime_target_control_plane_config_path() {
  local requested_config_path='' profile_id='agent_platform' explicit_profile='0'
  if [[ -z "$RUNTIME_TARGET_RESOLVED_CONFIG_PATH" ]]; then
    openclaw_control_plane_apply_default_selection_from_env_files \
      requested_config_path \
      profile_id \
      explicit_profile \
      "$ROOT_DIR/deploy/.env|deploy/.env" \
      "$ROOT_DIR/deploy/site.env|deploy/site.env" || return $?
    RUNTIME_TARGET_RESOLVED_CONFIG_PATH="$(openclaw_control_plane_resolve_config_path "$profile_id" "$requested_config_path" "$explicit_profile")" || return 1
  fi
  printf '%s\n' "$RUNTIME_TARGET_RESOLVED_CONFIG_PATH"
}

runtime_target_resolve_path_from_dir() {
  local base_dir="$1"
  local value="$2"
  local rel_dir="" rel_base=""
  [[ -n "$value" ]] || return 1
  if [[ "$value" == /* ]]; then
    printf '%s\n' "$value"
    return 0
  fi
  if [[ "$value" == @repo/* ]]; then
    runtime_target_resolve_path_from_dir "$ROOT_DIR" "${value#@repo/}"
    return $?
  fi
  rel_dir="$(dirname "$value")"
  rel_base="$(basename "$value")"
  (
    cd "$base_dir/$rel_dir" 2>/dev/null || exit 1
    printf '%s/%s\n' "$(pwd -P)" "$rel_base"
  )
}

runtime_target_config_declares_service_registry_fragments() {
  local config_path="$1"
  local config_dir="" enabled_ids="" raw_dir="" manifests_dir="" manifest="" extension_id="" fragment_path=""
  command -v jq >/dev/null 2>&1 || return 0
  [[ -f "$config_path" ]] || return 0
  config_dir="$(cd "$(dirname "$config_path")" && pwd -P)" || return 0
  enabled_ids="$(jq -r '.extensions.enabledExtensionIds[]? // empty' "$config_path" 2>/dev/null || true)"
  [[ -n "$enabled_ids" ]] || return 1
  # 非平台扩展的 manifest 目录可能通过 @extension 声明；shell 快速路径不展开
  # 该占位符，直接切到 Python 合并器，避免漏掉扩展 runtime service。
  if jq -e '.extensions.enabledExtensionIds[]? | select(. != "agent_platform")' "$config_path" >/dev/null 2>&1; then
    return 0
  fi
  while IFS= read -r raw_dir; do
    [[ -n "$raw_dir" ]] || continue
    manifests_dir="$(runtime_target_resolve_path_from_dir "$config_dir" "$raw_dir" 2>/dev/null || true)"
    [[ -n "$manifests_dir" && -d "$manifests_dir" ]] || continue
    for manifest in "$manifests_dir"/*.json; do
      [[ -f "$manifest" ]] || continue
      extension_id="$(jq -r '.id // empty' "$manifest" 2>/dev/null || true)"
      [[ -n "$extension_id" ]] || continue
      if ! grep -Fxq "$extension_id" <<< "$enabled_ids"; then
        continue
      fi
      fragment_path="$(jq -r '.surfaceFragments.runtimeServiceRegistryPath // empty' "$manifest" 2>/dev/null || true)"
      [[ -z "$fragment_path" ]] || return 0
    done
  done < <(jq -r '.extensions.manifestsDirs[]? // empty' "$config_path" 2>/dev/null || true)
  return 1
}

runtime_target_load_registry_cache_fast() {
  local config_path=""
  command -v jq >/dev/null 2>&1 || return 1
  [[ -f "$SERVICE_REGISTRY_PATH" ]] || return 1
  config_path="$(runtime_target_control_plane_config_path)" || return 1
  if runtime_target_config_declares_service_registry_fragments "$config_path"; then
    return 1
  fi
  RUNTIME_TARGET_REGISTRY_CACHE="$(jq -r '
    .targets[]?
    | select((.target // "") != "" and (.service // "") != "" and (.container // "") != "")
    | [.target, .service, .container]
    | @tsv
  ' "$SERVICE_REGISTRY_PATH" | awk -F '\t' '{print $1 "|" $2 "|" $3}')" || return 1
  [[ -n "$RUNTIME_TARGET_REGISTRY_CACHE" ]] || return 1
}

# 加载并缓存 runtime service registry，供后续解析复用。
runtime_target_load_registry_cache() {
  [[ -n "$RUNTIME_TARGET_REGISTRY_CACHE" ]] && return 0
  if runtime_target_load_registry_cache_fast; then
    return 0
  fi
  [[ -f "$SERVICE_REGISTRY_PATH" ]] || runtime_target_fail "缺少 runtime service registry：$SERVICE_REGISTRY_PATH" || return 2
  [[ -f "$PYTHON_RUNNER" && -r "$PYTHON_RUNNER" ]] || runtime_target_fail "缺少统一 Python runner：$PYTHON_RUNNER" || return 2
  local config_path=''
  local -a repo_python_env_args=()
  config_path="$(runtime_target_control_plane_config_path)" || return 1
  while IFS= read -r -d '' item; do
    repo_python_env_args+=("$item")
  done < <(openclaw_repo_python_env_args "$ROOT_DIR")
  RUNTIME_TARGET_REGISTRY_CACHE="$(bash "$PYTHON_RUNNER" --workdir "$ROOT_DIR" "${repo_python_env_args[@]+"${repo_python_env_args[@]}"}" --env "OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH=$config_path" -- - "$SERVICE_REGISTRY_PATH" "$config_path" <<'PY'
import sys
from pathlib import Path

from openclaw.control_plane.surfaces import load_runtime_service_registry

registry_path = Path(sys.argv[1]).resolve()
config_path = Path(sys.argv[2]).resolve()
payload = load_runtime_service_registry(registry_path, config_path=config_path)
rows = payload.get('targets') if isinstance(payload, dict) else None
if not isinstance(rows, list):
    raise SystemExit('service_registry.targets 必须为数组')
for item in rows:
    if not isinstance(item, dict):
        continue
    target = str(item.get('target') or '').strip()
    service = str(item.get('service') or '').strip()
    container = str(item.get('container') or '').strip()
    if target and service and container:
        print(f'{target}|{service}|{container}')
PY
)" || runtime_target_fail "读取 runtime service registry 失败：$SERVICE_REGISTRY_PATH" || return 2
  [[ -n "$RUNTIME_TARGET_REGISTRY_CACHE" ]] || runtime_target_fail "runtime service registry 为空：$SERVICE_REGISTRY_PATH" || return 2
}

# 列出 registry 中声明的全部 runtime target。
runtime_target_known_targets() {
  runtime_target_load_registry_cache || return 1
  printf '%s\n' "$RUNTIME_TARGET_REGISTRY_CACHE" | cut -d'|' -f1
}

# 按 target 返回 registry 记录。
runtime_target_record_for_target() {
  local target=""
  target="$(runtime_target_normalize_target "$1")"
  runtime_target_load_registry_cache || return 1
  printf '%s\n' "$RUNTIME_TARGET_REGISTRY_CACHE" | awk -F'|' -v target="$target" '$1 == target { print; found=1; exit } END { exit(found ? 0 : 1) }'
}

# 根据 target 解析 compose service 名称。
runtime_target_service_name_for_target() {
  local record=""
  record="$(runtime_target_record_for_target "$1")" || return 1
  printf '%s\n' "$record" | cut -d'|' -f2
}

# 根据 target 解析 Docker 容器名。
runtime_target_container_name_for_target() {
  local record=""
  record="$(runtime_target_record_for_target "$1")" || return 1
  printf '%s\n' "$record" | cut -d'|' -f3
}

# 按 compose service 名称反查 registry 记录。
runtime_target_resolve_by_service_name() {
  local service_name="$1"
  runtime_target_load_registry_cache || return 1
  printf '%s\n' "$RUNTIME_TARGET_REGISTRY_CACHE" | awk -F'|' -v service_name="$service_name" '$2 == service_name { print; found=1; exit } END { exit(found ? 0 : 1) }'
}

# 按容器名反查 registry 记录。
runtime_target_resolve_by_container_name() {
  local container_name="$1"
  runtime_target_load_registry_cache || return 1
  printf '%s\n' "$RUNTIME_TARGET_REGISTRY_CACHE" | awk -F'|' -v container_name="$container_name" '$3 == container_name { print; found=1; exit } END { exit(found ? 0 : 1) }'
}

# 对文本行做去重并保持首次出现顺序。
runtime_target_dedupe_lines() {
  awk '!seen[$0]++'
}
