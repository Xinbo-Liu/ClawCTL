#!/usr/bin/env bash
# 用途：检查控制平面 registry、scheduler heartbeat 与 internal-api 控制平面只读接口是否一致可用。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/doctor/check_control_plane_runtime.sh

说明：
  - 检查控制平面 registry、scheduler heartbeat 与 internal-api 控制平面只读接口是否一致可用；
  - 脚本本体不接受业务参数；如只查看说明，请使用 --help。
USAGE
}

if [[ $# -gt 0 ]]; then
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[check_control_plane_runtime][FAIL] 未知参数：$1" >&2
      exit 2
      ;;
  esac
fi

PYTHON_TOOL="$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh"
RUNNER="$ROOT_DIR/scripts/runtime/run_runtime_container_command.sh"
[[ -f "$PYTHON_TOOL" && -r "$PYTHON_TOOL" ]] || { echo "[check_control_plane_runtime][FAIL] 缺少统一 Python 工具入口：$PYTHON_TOOL" >&2; exit 2; }
[[ -f "$RUNNER" && -r "$RUNNER" ]] || { echo "[check_control_plane_runtime][FAIL] 缺少统一容器入口：$RUNNER" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "[check_control_plane_runtime][FAIL] 缺少 jq，无法合并运行态摘要" >&2; exit 2; }

registry_json="$(bash "$PYTHON_TOOL" control-plane validate registry)" || {
  echo '[check_control_plane_runtime][FAIL] 控制平面 registry 校验失败' >&2
  exit 3
}

bash "$RUNNER" --target scheduler -- \
  python3 -m openclaw.cli control-plane scheduler-runtime \
    --healthcheck \
    --max-stale-seconds 900 >/dev/null

CONTROL_PLANE_API_CHECK_PY="$(cat <<'PY'
from __future__ import annotations
import json
import os
import sys
import urllib.request
from openclaw.internal_api.contract import route_surface

routes = route_surface()
DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
try:
    MAX_RESPONSE_BYTES = max(
        1024,
        int(os.environ.get("OPENCLAW_INTERNAL_API_CHECK_MAX_RESPONSE_BYTES", str(DEFAULT_MAX_RESPONSE_BYTES)) or str(DEFAULT_MAX_RESPONSE_BYTES)),
    )
except ValueError:
    MAX_RESPONSE_BYTES = DEFAULT_MAX_RESPONSE_BYTES
port = os.environ.get("OPENCLAW_INTERNAL_API_PORT", "18081").strip() or "18081"
token = os.environ.get("OPENCLAW_INTERNAL_API_TOKEN", "").strip()
headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}

def read_limited_json(resp, path: str) -> dict:
    body = resp.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise RuntimeError(f"{path} response too large: exceeds {MAX_RESPONSE_BYTES} bytes")
    return json.loads(body.decode("utf-8") or "{}")

def fetch(path: str) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
    with urllib.request.urlopen(req, timeout=8) as resp:
        return read_limited_json(resp, path)

summary = fetch(routes["control_plane_summary"])
jobs = fetch(routes["control_plane_jobs"])
payload = {"summary": summary, "jobs": jobs}
print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
summary_scheduler = summary.get("scheduler") if isinstance(summary.get("scheduler"), dict) else {}
counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
items = jobs.get("items") if isinstance(jobs.get("items"), list) else []
healthy = bool(summary_scheduler.get("healthy"))
jobs_consistent = int(counts.get("jobs") or 0) == len(items)
sys.exit(0 if healthy and jobs_consistent else 1)
PY
)"

api_json="$(bash "$RUNNER" --target internal-api -- python3 -c "$CONTROL_PLANE_API_CHECK_PY"
)" || {
  echo '[check_control_plane_runtime][FAIL] internal-api 控制平面只读接口异常' >&2
  exit 4
}

CONTROL_PLANE_STATE_CHECK_PY="$(cat <<'PY'
from __future__ import annotations
import json
from openclaw.lib.runtime.state import resolve_state_root

root = resolve_state_root(view="host")
required = {
    "status": root / "control_plane_scheduler_status.json",
    "heartbeat": root / "control_plane_scheduler_heartbeat.json",
    "state": root / "control_plane_scheduler" / "state.json",
}
optional = {
    "history": root / "control_plane_scheduler_history.jsonl",
}
payload = {
    "items": {
        key: {"path": str(path), "exists": path.exists(), "isFile": path.is_file()}
        for key, path in required.items()
    },
    "optionalItems": {
        key: {"path": str(path), "exists": path.exists(), "isFile": path.is_file()}
        for key, path in optional.items()
    },
}
print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
if all(item["exists"] and item["isFile"] for item in payload["items"].values()):
    raise SystemExit(0)
raise SystemExit(1)
PY
)"

state_check_json="$(bash "$RUNNER" --target scheduler -- python3 -c "$CONTROL_PLANE_STATE_CHECK_PY"
)" || {
  echo '[check_control_plane_runtime][FAIL] scheduler 运行态文件不完整' >&2
  exit 5
}

summary_tmp_root="$ROOT_DIR/state/openclaw/control_plane/tmp"
mkdir -p "$summary_tmp_root"
summary_tmp_dir="$(mktemp -d "$summary_tmp_root/check-control-plane-runtime.XXXXXX")"
cleanup_control_plane_runtime_summary_tmp() {
  rm -rf "$summary_tmp_dir"
}
trap cleanup_control_plane_runtime_summary_tmp EXIT
registry_json_file="$summary_tmp_dir/registry.json"
api_json_file="$summary_tmp_dir/api.json"
state_check_json_file="$summary_tmp_dir/state-files.json"
printf '%s\n' "$registry_json" >"$registry_json_file"
printf '%s\n' "$api_json" >"$api_json_file"
printf '%s\n' "$state_check_json" >"$state_check_json_file"

jq -n \
  --slurpfile registry "$registry_json_file" \
  --slurpfile api "$api_json_file" \
  --slurpfile stateFiles "$state_check_json_file" \
  '{registry: $registry[0], api: $api[0], stateFiles: $stateFiles[0]}'
