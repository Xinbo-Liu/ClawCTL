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
  bash ./scripts/gateway/run_gateway_probe.sh

说明：
  - 等价于在 gateway target 内执行 official CLI：`gateway probe --json`
  - 适合部署完成后的首轮活性核对，或与 `run_gateway_status_deep.sh` 组合使用。
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
    echo "[run_gateway_probe][FAIL] 未知参数：$1" >&2
    usage >&2
    exit 2
    ;;
esac

bash "$ROOT_DIR/scripts/runtime/run_openclaw_official_cli.sh" --target gateway -- gateway probe --json
