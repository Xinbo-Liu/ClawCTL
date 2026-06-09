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
  bash ./scripts/gateway/run_gateway_status_deep.sh

说明：
  - 等价于在 gateway target 内执行 official CLI：`gateway status --json --deep`
  - 适合部署完成后的深度治理核对，或排障时确认 gateway 内部状态。
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
    echo "[run_gateway_status_deep][FAIL] 未知参数：$1" >&2
    usage >&2
    exit 2
    ;;
esac

bash "$ROOT_DIR/scripts/runtime/run_openclaw_official_cli.sh" --target gateway -- gateway status --json --deep
