#!/usr/bin/env bash
# 用途：为 scripts/setup/lib 下的库脚本定位并载入仓库根解析器。

openclaw_setup_lib_source_repo_root() {
  local start_dir="$1"
  local search_dir="$start_dir"
  local candidate=""
  while [[ "$search_dir" != "/" ]]; do
    candidate="$search_dir/scripts/lib/repo_root.sh"
    if [[ -f "$candidate" ]]; then
      # shellcheck source=scripts/lib/repo_root.sh
      source "$candidate"
      return 0
    fi
    search_dir="$(cd "$search_dir/.." && pwd -P)" || return 1
  done
  echo "[openclaw][FAIL] cannot locate scripts/lib/repo_root.sh from $start_dir" >&2
  return 2
}
