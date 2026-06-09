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
