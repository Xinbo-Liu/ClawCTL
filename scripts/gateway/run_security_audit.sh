#!/usr/bin/env bash
set -euo pipefail
__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/gateway/run_security_audit.sh

说明：
  - 等价于在 gateway target 内执行 official CLI：`security audit --deep --json`
  - 适合部署后治理巡检，或作为 acceptance 补充人工核对入口。
USAGE
}

case "${1:-}" in
  -h|--help)
    usage
    exit 0
    ;;
  '')
    ;;
  *)
    echo "[run_security_audit][FAIL] 未知参数：$1" >&2
    usage >&2
    exit 2
    ;;
esac

bash "$ROOT_DIR/scripts/runtime/run_openclaw_official_cli.sh" --target gateway -- security audit --deep --json
