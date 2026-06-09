#!/usr/bin/env bash
# 用途：为 Python 控制面配置选择面提供轻量 shell 包装。

if [[ -n "${OPENCLAW_CONTROL_PLANE_CONFIG_PATHS_SH_LOADED:-}" ]]; then
  return 0 2>/dev/null || exit 0
fi
OPENCLAW_CONTROL_PLANE_CONFIG_PATHS_SH_LOADED=1

OPENCLAW_CONTROL_PLANE_CONFIG_PATHS_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=repo_root.sh
source "$OPENCLAW_CONTROL_PLANE_CONFIG_PATHS_LIB_DIR/repo_root.sh"
OPENCLAW_CONTROL_PLANE_CONFIG_PATHS_ROOT="$(openclaw_repo_root_from "$OPENCLAW_CONTROL_PLANE_CONFIG_PATHS_LIB_DIR")"
unset OPENCLAW_CONTROL_PLANE_CONFIG_PATHS_LIB_DIR
OPENCLAW_CONTROL_PLANE_DEFAULT_PROFILE_ID='agent_platform'
OPENCLAW_CONTROL_PLANE_CONTAINER_REPO_ROOT='/opt/openclaw-tools'

openclaw_control_plane_is_windows_abs_path() {
  local value="$1"
  [[ "$value" =~ ^[A-Za-z]:[\\/].* ]]
}

openclaw_control_plane_surface() {
  local proxy_python="${OPENCLAW_CONTROL_PLANE_CONFIG_PROXY_PYTHON:-}"
  local registry_override="${OPENCLAW_CONTROL_PLANE_PROFILE_REGISTRY_PATH:-}"
  local public_config_path="${OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH:-}"
  local public_profile="${OPENCLAW_CONTROL_PLANE_PROFILE:-}"
  local container_config_path="${CONTROL_PLANE_CONTAINER_CONFIG_PATH:-}"
  local -a forwarded_env=()
  if [[ -n "$public_config_path" ]]; then
    forwarded_env+=("OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH=$public_config_path")
  fi
  if [[ -n "$public_profile" ]]; then
    forwarded_env+=("OPENCLAW_CONTROL_PLANE_PROFILE=$public_profile")
  fi
  if [[ -n "$container_config_path" ]]; then
    forwarded_env+=("CONTROL_PLANE_CONTAINER_CONFIG_PATH=$container_config_path")
  fi
  if [[ -n "$registry_override" ]]; then
    forwarded_env+=("OPENCLAW_CONTROL_PLANE_PROFILE_REGISTRY_PATH=$registry_override")
  fi
  if [[ -n "$proxy_python" ]]; then
    (
      cd "$OPENCLAW_CONTROL_PLANE_CONFIG_PATHS_ROOT" && \
      env "${forwarded_env[@]+"${forwarded_env[@]}"}" \
      "$proxy_python" -B -m openclaw.lib.repo.control_plane_config_surface "$@" --repo-root "$OPENCLAW_CONTROL_PLANE_CONFIG_PATHS_ROOT"
    )
    return $?
  fi
  local -a runner=(
    bash "$OPENCLAW_CONTROL_PLANE_CONFIG_PATHS_ROOT/scripts/runtime/run_python_container.sh"
    --workdir "$OPENCLAW_CONTROL_PLANE_CONFIG_PATHS_ROOT" \
    --env "OPENCLAW_REPO_ROOT=$OPENCLAW_CONTROL_PLANE_CONFIG_PATHS_ROOT" \
    --env "OPENCLAW_TOOLS_ROOT=$OPENCLAW_CONTROL_PLANE_CONFIG_PATHS_ROOT" \
  )
  local forwarded=''
  for forwarded in "${forwarded_env[@]+"${forwarded_env[@]}"}"; do
    runner+=(--env "$forwarded")
  done
  runner+=(-- -m openclaw.lib.repo.control_plane_config_surface "$@" --repo-root "$OPENCLAW_CONTROL_PLANE_CONFIG_PATHS_ROOT")
  "${runner[@]}"
}

openclaw_control_plane_selected_host_path() {
  local requested_path="${1:-}"
  if [[ -n "$requested_path" ]]; then
    printf '%s\n' "$requested_path"
    return 0
  fi
  if [[ -n "${OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH:-}" ]]; then
    printf '%s\n' "$OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH"
    return 0
  fi
  return 1
}

openclaw_control_plane_selected_profile_id() {
  if [[ -n "${OPENCLAW_CONTROL_PLANE_PROFILE:-}" ]]; then
    printf '%s\n' "$OPENCLAW_CONTROL_PLANE_PROFILE"
    return 0
  fi
  return 1
}

openclaw_control_plane_read_env_key() {
  local env_file="$1"
  local expected_key="$2"
  local raw_line='' line='' key='' value=''
  [[ -f "$env_file" ]] || return 1
  while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    line="${raw_line%$'\r'}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -n "$line" && "${line:0:1}" != "#" ]] || continue
    if [[ "$line" == export[[:space:]]* ]]; then
      line="${line#export}"
      line="${line#"${line%%[![:space:]]*}"}"
    fi
    [[ "$line" == *"="* ]] || continue
    key="${line%%=*}"
    value="${line#*=}"
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    [[ "$key" == "$expected_key" ]] || continue
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
    return 0
  done < "$env_file"
  return 1
}

openclaw_control_plane_validate_env_file_selection() {
  local env_file="$1"
  local env_label="${2:-$env_file}"
  local env_config_path='' env_profile='' normalized_config_path='' config_profile=''
  [[ -f "$env_file" ]] || return 0
  env_config_path="$(openclaw_control_plane_read_env_key "$env_file" OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH 2>/dev/null || true)"
  env_profile="$(openclaw_control_plane_read_env_key "$env_file" OPENCLAW_CONTROL_PLANE_PROFILE 2>/dev/null || true)"

  if [[ -n "$env_config_path" ]]; then
    normalized_config_path="$(openclaw_control_plane_normalize_host_config_path "$env_config_path")" || return 1
    if [[ ! -f "$normalized_config_path" ]]; then
      echo "[control_plane_config_paths][FAIL] $env_label 中 OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH 指向的配置文件不存在：$normalized_config_path" >&2
      return 2
    fi
  fi
  if [[ -n "$normalized_config_path" && -n "$env_profile" ]]; then
    config_profile="$(openclaw_control_plane_profile_id_for_path "$normalized_config_path")" || return 1
    if [[ "$config_profile" != "$env_profile" ]]; then
      echo "[control_plane_config_paths][FAIL] $env_label 中 OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH 与 OPENCLAW_CONTROL_PLANE_PROFILE 不一致：path -> $config_profile, profile=$env_profile" >&2
      return 2
    fi
  fi
}

openclaw_control_plane_apply_selection_from_env_file() {
  local env_file="$1"
  local requested_config_var="$2"
  local profile_var="$3"
  local explicit_profile_var="$4"
  local env_label="${5:-$env_file}"
  local env_config_path='' env_profile=''
  [[ -f "$env_file" ]] || return 0
  openclaw_control_plane_validate_env_file_selection "$env_file" "$env_label" || return $?
  env_config_path="$(openclaw_control_plane_read_env_key "$env_file" OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH 2>/dev/null || true)"
  env_profile="$(openclaw_control_plane_read_env_key "$env_file" OPENCLAW_CONTROL_PLANE_PROFILE 2>/dev/null || true)"

  if [[ -z "${!requested_config_var}" && "${!explicit_profile_var}" != "1" && -n "$env_config_path" ]]; then
    printf -v "$requested_config_var" '%s' "$env_config_path"
  fi
  if [[ "${!explicit_profile_var}" != "1" && -n "$env_profile" ]]; then
    printf -v "$profile_var" '%s' "$env_profile"
    printf -v "$explicit_profile_var" '%s' 1
  fi
}

openclaw_control_plane_apply_default_selection_from_env_files() {
  local requested_config_var="$1"
  local profile_var="$2"
  local explicit_profile_var="$3"
  shift 3
  local env_file='' env_label='' spec='' env_config_path='' env_profile=''
  [[ -z "${OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH:-}" && -z "${OPENCLAW_CONTROL_PLANE_PROFILE:-}" ]] || return 0
  for spec in "$@"; do
    env_file="${spec%%|*}"
    env_label="${spec#*|}"
    [[ "$env_label" != "$spec" ]] || env_label="$env_file"
    [[ -f "$env_file" ]] || continue
    env_config_path="$(openclaw_control_plane_read_env_key "$env_file" OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH 2>/dev/null || true)"
    env_profile="$(openclaw_control_plane_read_env_key "$env_file" OPENCLAW_CONTROL_PLANE_PROFILE 2>/dev/null || true)"
    [[ -n "$env_config_path" || -n "$env_profile" ]] || continue
    openclaw_control_plane_apply_selection_from_env_file \
      "$env_file" \
      "$requested_config_var" \
      "$profile_var" \
      "$explicit_profile_var" \
      "$env_label" || return $?
    return 0
  done
}

openclaw_control_plane_apply_env_file_active_selection() {
  # Active 部署入口以 env 文件为准，避免调用者环境中的 ambient profile 抢占
  # deploy/.env。显式 --config-path 仍保留调用者路径，但 env 文件自身必须可解析且自洽。
  local env_file="$1"
  local requested_config_var="$2"
  local profile_var="$3"
  local explicit_profile_var="$4"
  local config_path_explicit="${5:-0}"
  local env_label="${6:-$env_file}"
  local env_config_path='' env_profile=''
  [[ -f "$env_file" ]] || return 0
  env_config_path="$(openclaw_control_plane_read_env_key "$env_file" OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH 2>/dev/null || true)"
  env_profile="$(openclaw_control_plane_read_env_key "$env_file" OPENCLAW_CONTROL_PLANE_PROFILE 2>/dev/null || true)"
  openclaw_control_plane_validate_env_file_selection "$env_file" "$env_label" || return $?
  if [[ "$config_path_explicit" == '1' || "$config_path_explicit" == 'true' || "$config_path_explicit" == 'yes' ]]; then
    return 0
  fi
  if [[ -n "$env_config_path" ]]; then
    printf -v "$requested_config_var" '%s' "$env_config_path"
  elif [[ -n "$env_profile" ]]; then
    printf -v "$requested_config_var" '%s' ''
  fi
  if [[ -n "$env_profile" ]]; then
    printf -v "$profile_var" '%s' "$env_profile"
    printf -v "$explicit_profile_var" '%s' 1
  fi
}

openclaw_control_plane_profile_config_path() {
  local profile_id="${1:-$OPENCLAW_CONTROL_PLANE_DEFAULT_PROFILE_ID}"
  openclaw_control_plane_surface host-path --control-plane-profile "$profile_id"
}

openclaw_control_plane_profile_id_for_path() {
  local requested_path="${1:?requested_path is required}"
  openclaw_control_plane_surface profile-id --config-path "$requested_path"
}

openclaw_control_plane_assert_public_selection_consistent() {
  local selected_path="${1:-}"
  local selected_profile="${OPENCLAW_CONTROL_PLANE_PROFILE:-}"
  local path_profile=''
  if [[ -z "$selected_path" || -z "$selected_profile" ]]; then
    return 0
  fi
  path_profile="$(openclaw_control_plane_profile_id_for_path "$selected_path")" || return 1
  if [[ "$path_profile" != "$selected_profile" ]]; then
    echo "[control_plane_config_paths][FAIL] OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH 与 OPENCLAW_CONTROL_PLANE_PROFILE 不一致：path -> $path_profile, profile=$selected_profile" >&2
    return 2
  fi
}

openclaw_control_plane_agent_config_path() {
  local agent_ref="${1:?agent_ref is required}"
  openclaw_control_plane_surface agent-host-path --agent-ref "$agent_ref"
}

openclaw_control_plane_normalize_host_config_path() {
  local selected_path="${1:?selected_path is required}"
  local selected_dir='' selected_base='' normalized_path=''
  if openclaw_control_plane_is_windows_abs_path "$selected_path"; then
    printf '%s\n' "${selected_path//\\//}"
    return 0
  fi
  case "$selected_path" in
    "$OPENCLAW_CONTROL_PLANE_CONFIG_PATHS_ROOT"/*)
      if [[ -f "$selected_path" ]]; then
        selected_dir="$(cd "$(dirname "$selected_path")" && pwd -P)" || return 1
        selected_base="$(basename "$selected_path")"
        normalized_path="$selected_dir/$selected_base"
        case "$normalized_path" in
          "$OPENCLAW_CONTROL_PLANE_CONFIG_PATHS_ROOT"/*)
            printf '%s\n' "$normalized_path"
            return 0
            ;;
        esac
      fi
      ;;
    "$OPENCLAW_CONTROL_PLANE_CONTAINER_REPO_ROOT"/*)
      printf '%s\n' "$OPENCLAW_CONTROL_PLANE_CONFIG_PATHS_ROOT/${selected_path#"$OPENCLAW_CONTROL_PLANE_CONTAINER_REPO_ROOT"/}"
      return 0
      ;;
  esac
  openclaw_control_plane_surface host-path --config-path "$selected_path"
}

openclaw_control_plane_resolve_config_path() {
  local profile_id="${1:-$OPENCLAW_CONTROL_PLANE_DEFAULT_PROFILE_ID}"
  local requested_path="${2:-}"
  local explicit_profile="${3:-0}"
  local selected_path='' selected_profile=''
  if [[ -n "$requested_path" ]]; then
    openclaw_control_plane_normalize_host_config_path "$requested_path"
    return $?
  fi
  if [[ "$explicit_profile" == "1" || "$explicit_profile" == "true" || "$explicit_profile" == "yes" ]]; then
    openclaw_control_plane_surface host-path --control-plane-profile "$profile_id"
    return $?
  fi
  if selected_path="$(openclaw_control_plane_selected_host_path)"; then
    openclaw_control_plane_assert_public_selection_consistent "$selected_path" || return $?
    openclaw_control_plane_normalize_host_config_path "$selected_path"
    return $?
  fi
  if selected_profile="$(openclaw_control_plane_selected_profile_id)"; then
    openclaw_control_plane_surface host-path --control-plane-profile "$selected_profile"
    return $?
  fi
  openclaw_control_plane_surface host-path --control-plane-profile "$profile_id"
}

openclaw_control_plane_container_profile_config_path() {
  local profile_id="${1:-$OPENCLAW_CONTROL_PLANE_DEFAULT_PROFILE_ID}"
  local selected_path='' selected_profile=''
  if [[ -n "${CONTROL_PLANE_CONTAINER_CONFIG_PATH:-}" ]]; then
    printf '%s\n' "$CONTROL_PLANE_CONTAINER_CONFIG_PATH"
    return 0
  fi
  if selected_path="$(openclaw_control_plane_selected_host_path)"; then
    openclaw_control_plane_assert_public_selection_consistent "$selected_path" || return $?
    openclaw_control_plane_surface container-path --config-path "$selected_path"
    return $?
  fi
  if selected_profile="$(openclaw_control_plane_selected_profile_id)"; then
    openclaw_control_plane_surface container-path --control-plane-profile "$selected_profile"
    return $?
  fi
  openclaw_control_plane_surface container-path --control-plane-profile "$profile_id"
}

openclaw_control_plane_container_config_path() {
  local profile_id="${1:-$OPENCLAW_CONTROL_PLANE_DEFAULT_PROFILE_ID}"
  local requested_path="${2:-}"
  local selected_path='' selected_profile=''
  if [[ -z "$requested_path" && -n "${CONTROL_PLANE_CONTAINER_CONFIG_PATH:-}" ]]; then
    printf '%s\n' "$CONTROL_PLANE_CONTAINER_CONFIG_PATH"
    return 0
  fi
  if [[ -n "$requested_path" ]]; then
    openclaw_control_plane_surface container-path --config-path "$requested_path"
    return $?
  fi
  if selected_path="$(openclaw_control_plane_selected_host_path)"; then
    openclaw_control_plane_assert_public_selection_consistent "$selected_path" || return $?
    openclaw_control_plane_surface container-path --config-path "$selected_path"
    return $?
  fi
  if selected_profile="$(openclaw_control_plane_selected_profile_id)"; then
    openclaw_control_plane_surface container-path --control-plane-profile "$selected_profile"
    return $?
  fi
  openclaw_control_plane_surface container-path --control-plane-profile "$profile_id"
}

openclaw_control_plane_profile_id() {
  local profile_id="${1:-$OPENCLAW_CONTROL_PLANE_DEFAULT_PROFILE_ID}"
  local requested_path="${2:-}"
  local selected_path='' selected_profile=''
  if [[ -n "$requested_path" ]]; then
    openclaw_control_plane_profile_id_for_path "$requested_path"
    return $?
  fi
  if selected_path="$(openclaw_control_plane_selected_host_path)"; then
    openclaw_control_plane_assert_public_selection_consistent "$selected_path" || return $?
    openclaw_control_plane_profile_id_for_path "$selected_path"
    return $?
  fi
  if selected_profile="$(openclaw_control_plane_selected_profile_id)"; then
    printf '%s\n' "$selected_profile"
    return 0
  fi
  printf '%s\n' "$profile_id"
  return 0
}

openclaw_control_plane_has_explicit_selection() {
  local arg=''
  for arg in "$@"; do
    case "$arg" in
      --config-path|--control-plane-profile|--config-path=*|--control-plane-profile=*)
        return 0
        ;;
    esac
  done
  return 1
}

openclaw_control_plane_wrapper_args() {
  local default_profile="${1:-$OPENCLAW_CONTROL_PLANE_DEFAULT_PROFILE_ID}"
  shift || true
  if ! openclaw_control_plane_has_explicit_selection "$@"; then
    printf '%s\0' --control-plane-profile "$default_profile"
  fi
  if [[ $# -gt 0 ]]; then
    printf '%s\0' "$@"
  fi
}
