#!/usr/bin/env bash
# 用途：通过 deploy env 控制面安全读取 env key，避免脚本直接 source 用户可编辑的 deploy/.env。
set -euo pipefail

DEPLOY_ENV_SHELL_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=repo_root_bootstrap.sh
source "$DEPLOY_ENV_SHELL_LIB_DIR/repo_root_bootstrap.sh"
openclaw_setup_lib_source_repo_root "$DEPLOY_ENV_SHELL_LIB_DIR" || return 2 2>/dev/null || exit 2
unset -f openclaw_setup_lib_source_repo_root
DEPLOY_ENV_SHELL_ROOT="$(openclaw_repo_root_from "$DEPLOY_ENV_SHELL_LIB_DIR")"

deploy_env_shell_repo_root() {
  printf '%s\n' "$DEPLOY_ENV_SHELL_ROOT"
}

deploy_env_shell_load_keys() {
  local env_file="$1"
  shift
  local root_dir="${ROOT_DIR:-}"
  local output=''
  if [[ -z "$root_dir" ]]; then
    root_dir="$(deploy_env_shell_repo_root)"
  fi
  [[ -f "$env_file" ]] || {
    echo "[deploy_env_shell][FAIL] env 文件不存在：$env_file" >&2
    return 2
  }
  (($# > 0)) || {
    echo '[deploy_env_shell][FAIL] 至少需要一个 env key' >&2
    return 2
  }
  output="$(bash "$root_dir/scripts/runtime/run_openclaw_python_tool.sh" setup env query-env-batch --env-file "$env_file" --format shell "$@")" || return $?
  eval "$output"
}
