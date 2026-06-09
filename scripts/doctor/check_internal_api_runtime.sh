#!/usr/bin/env bash
# 用途：检查 internal-api 容器是否 healthy，并输出 readyz、控制平面摘要与 job 列表。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/doctor/check_internal_api_runtime.sh

说明：
  - 检查 internal-api 容器是否 ready，并输出 readyz、控制平面摘要与 job 列表；
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
      echo "[check_internal_api_runtime][FAIL] 未知参数：$1" >&2
      exit 2
      ;;
  esac
fi

RUNNER="$ROOT_DIR/scripts/runtime/run_runtime_container_command.sh"
[[ -f "$RUNNER" && -r "$RUNNER" ]] || { echo "[check_internal_api_runtime][FAIL] 缺少统一容器入口：$RUNNER" >&2; exit 2; }

INTERNAL_API_CHECK_PY="$(cat <<'PY'
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
try:
    FETCH_TIMEOUT_SECONDS = max(1, int(os.environ.get("OPENCLAW_INTERNAL_API_CHECK_TIMEOUT_SECONDS", "20") or "20"))
except ValueError:
    FETCH_TIMEOUT_SECONDS = 20


def read_limited_json(resp, path: str) -> dict:
    body = resp.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise RuntimeError(f"{path} response too large: exceeds {MAX_RESPONSE_BYTES} bytes")
    return json.loads(body.decode("utf-8") or "{}")

def fetch(path: str, *, auth: bool) -> dict:
    headers = {"Accept": "application/json"}
    token = os.environ.get("OPENCLAW_INTERNAL_API_TOKEN", "").strip()
    if auth and token:
        headers["Authorization"] = f"Bearer {token}"
    port = os.environ.get("OPENCLAW_INTERNAL_API_PORT", "18081").strip() or "18081"
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SECONDS) as resp:
        return read_limited_json(resp, path)

payload = {
    "healthz": fetch(routes["healthz"], auth=False),
    "readyz": fetch(routes["readyz"], auth=False),
    "controlPlaneSummary": fetch(routes["control_plane_summary"], auth=True),
    "controlPlaneJobs": fetch(routes["control_plane_jobs"], auth=True),
}
print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
ready_status = str((payload.get("readyz") or {}).get("status") or "").lower()
summary = payload.get("controlPlaneSummary") if isinstance(payload.get("controlPlaneSummary"), dict) else {}
scheduler = summary.get("scheduler") if isinstance(summary.get("scheduler"), dict) else {}
items = (payload.get("controlPlaneJobs") or {}).get("items") if isinstance(payload.get("controlPlaneJobs"), dict) else []
internal_ready = ready_status == "ready"
control_plane_ready = bool(scheduler.get("healthy")) and isinstance(items, list)
sys.exit(0 if internal_ready and control_plane_ready else 1)
PY
)"

bash "$RUNNER" --target internal-api -- python3 -c "$INTERNAL_API_CHECK_PY"
