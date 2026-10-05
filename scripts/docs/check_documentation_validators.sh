#!/usr/bin/env bash
# 用途：在一个 Python 进程中共享文档注册表、JSON 和 Markdown 缓存并执行全部文档子检查。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir

RUNNER_ARGS=(--workdir "$ROOT_DIR")
# shellcheck source=../lib/docs_inventory_env.sh
source "$ROOT_DIR/scripts/lib/docs_inventory_env.sh"
openclaw_docs_inventory_prepare_runner_args "$ROOT_DIR"
RUNNER_ARGS+=("${OPENCLAW_DOCS_INVENTORY_RUNNER_ARGS[@]}")

exec bash "$ROOT_DIR/scripts/lib/run_static_python.sh" "${RUNNER_ARGS[@]}" -- \
  -m openclaw.docs.validators.batch "$@"
