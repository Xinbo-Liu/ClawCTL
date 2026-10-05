#!/usr/bin/env bash
# 用途：从任意脚本位置统一发现仓库根目录，避免脚本各自硬编码目录跳级。

if [[ -n "${OPENCLAW_REPO_ROOT_SH_LOADED:-}" ]]; then
  return 0 2>/dev/null || exit 0
fi
OPENCLAW_REPO_ROOT_SH_LOADED=1
openclaw_repo_root_shell_path() {
  local raw_path="${1-}"
  if command -v cygpath >/dev/null 2>&1 && [[ "$raw_path" =~ ^[A-Za-z]:[\\/].*$ ]]; then
    cygpath -u "$raw_path"
    return 0
  fi
  printf '%s\n' "$raw_path"
}

# 职责：将仓库相对路径或绝对路径规范化为可比较的绝对路径。
openclaw_repo_abs_path_for_compare() {
  local root_dir="${1:?root_dir is required}"
  local raw_path="${2:?path is required}"
  local shell_path="" full_path="" dir="" base=""
  root_dir="$(openclaw_repo_root_shell_path "$root_dir")"
  shell_path="$(openclaw_repo_root_shell_path "$raw_path")"
  case "$shell_path" in
    /*) full_path="$shell_path" ;;
    *) full_path="$root_dir/$shell_path" ;;
  esac
  dir="$(dirname "$full_path")"
  base="$(basename "$full_path")"
  if [[ -d "$dir" ]]; then
    (cd "$dir" 2>/dev/null && printf '%s/%s\n' "$(pwd -P)" "$base")
  else
    printf '%s\n' "$full_path"
  fi
}
openclaw_repo_root_has_markers() {
  local candidate="${1:?candidate is required}"
  [[ -d "$candidate/python/openclaw" ]] &&
    [[ -f "$candidate/scripts/runtime/run_openclaw_python_tool.sh" ]] &&
    return 0
  [[ -f "$candidate/config/governance/support/repo_contracts.json" ]] &&
    [[ -f "$candidate/scripts/lib/repo_contracts.sh" ]]
}
openclaw_repo_root_from() {
  local start_path="${1:-${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}}"
  local current_dir='' parent_dir=''
  if [[ -d "$start_path" ]]; then
    current_dir="$(cd "$start_path" && pwd -P)" || return $?
  else
    current_dir="$(cd "$(dirname "$start_path")" && pwd -P)" || return $?
  fi
  while [[ -n "$current_dir" && "$current_dir" != "/" ]]; do
    if openclaw_repo_root_has_markers "$current_dir"; then
      openclaw_repo_root_shell_path "$current_dir"
      return 0
    fi
    parent_dir="$(dirname "$current_dir")"
    [[ "$parent_dir" != "$current_dir" ]] || break
    current_dir="$parent_dir"
  done
  echo "[repo_root][FAIL] 无法从路径发现仓库根：$start_path" >&2
  return 97
}
