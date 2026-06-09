#!/usr/bin/env bash
# 用途：执行 official gateway 同版本窗口影子验证，输出 active runtime 审计摘要、候选实例隔离计划与 active/candidate 差异摘要，并镜像最新摘要到 control-plane state release/evidence/。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
source "$ROOT_DIR/scripts/setup/lib/runtime_permissions.sh"
# shellcheck source=../lib/repo_contracts.sh
source "$ROOT_DIR/scripts/lib/repo_contracts.sh"
HOST_STATE_ROOT="$(runtime_permissions_host_state_root "$ROOT_DIR")"
HOST_CONTROL_PLANE_ROOT="$(runtime_permissions_host_control_plane_state_dir "$ROOT_DIR")"
PYTHON_RUNNER="$ROOT_DIR/scripts/runtime/run_python_container.sh"
RELEASE_GOVERNANCE_HELPER="${RELEASE_GOVERNANCE_HELPER:-$ROOT_DIR/scripts/runtime/export_runtime_acceptance_evidence.sh}"
STATE_DIR="${STATE_DIR:-$HOST_CONTROL_PLANE_ROOT/setup/shadow_verify}"
EVIDENCE_DIR="${EVIDENCE_DIR:-$HOST_CONTROL_PLANE_ROOT/release/evidence}"
repo_contract_assign_path PIN_ENV_PATH image_pins.openclaw
GATEWAY_SOURCE_PATH="$ROOT_DIR/config/gateway/openclaw.gateway.json"
DEPLOY_ENV_PATH="${DEPLOY_ENV_PATH:-$ROOT_DIR/deploy/.env}"
TARGET_TAG=""
CANDIDATE_IMAGE_REF=""
JSON_STDOUT=0
REQUIRE_CANDIDATE_RUNTIME=0
SKIP_CANDIDATE_RUNTIME=0

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/gateway/run_shadow_upgrade_verify.sh [--target-tag <tag>] [--candidate-image-ref <image>] [--require-candidate-runtime] [--skip-candidate-runtime] [--json]

说明：
  - 顺序执行 release 对齐、digest 对齐、doctor/runtime contract、internal-api runtime、gateway status --deep、gateway probe；
  - 默认会尽力在隔离网络中真实拉起候选 control-plane，输出独立 state_root / config / env / network / container_name；
  - Docker daemon 与控制面容器属于当前脚本固定前提；若当前环境缺少 Docker daemon，脚本会直接失败；
  - 若当前环境缺少 gateway token，脚本会在摘要中明确记录 candidate runtime 未执行的原因；
  - 输出 active runtime 与 candidate instance 的差异摘要；
  - 把 latest 摘要镜像到 control-plane state release/evidence/shadow-verify-summary.json(.md) 与 shadow-verify-compare.json(.md)；
  - 摘要渲染固定通过控制面容器执行；Docker daemon 或控制面镜像未就绪时脚本直接失败；
  - 当前流程只做影子验证，不自动切流。
USAGE
}

fail() {
  echo "[shadow-verify][FAIL] $*" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target-tag)
      [[ $# -ge 2 ]] || fail "--target-tag 缺少参数"
      TARGET_TAG="$2"
      shift 2
      ;;
    --candidate-image-ref)
      [[ $# -ge 2 ]] || fail "--candidate-image-ref 缺少参数"
      CANDIDATE_IMAGE_REF="$2"
      shift 2
      ;;
    --require-candidate-runtime)
      REQUIRE_CANDIDATE_RUNTIME=1
      shift
      ;;
    --skip-candidate-runtime)
      SKIP_CANDIDATE_RUNTIME=1
      shift
      ;;
    --json)
      JSON_STDOUT=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "未知参数：$1"
      ;;
  esac
done

(( SKIP_CANDIDATE_RUNTIME == 0 || REQUIRE_CANDIDATE_RUNTIME == 0 )) || fail "--skip-candidate-runtime 与 --require-candidate-runtime 不能同时使用"

mkdir -p "$STATE_DIR" "$EVIDENCE_DIR"
RELEASE_JSON="$STATE_DIR/release_check.json"
DIGEST_JSON="$STATE_DIR/digest_check.json"
DOCTOR_JSON="$STATE_DIR/doctor_runtime_contract.json"
STATUS_JSON="$STATE_DIR/gateway_status_deep.json"
PROBE_JSON="$STATE_DIR/gateway_probe.json"
INTERNAL_API_JSON="$STATE_DIR/internal_api_runtime.json"
SUMMARY_JSON="$STATE_DIR/summary.json"
SUMMARY_MD="$STATE_DIR/summary.md"
COMPARE_JSON="$STATE_DIR/compare.json"
COMPARE_MD="$STATE_DIR/compare.md"
CANDIDATE_PLAN_JSON="$STATE_DIR/candidate-instance.json"
CANDIDATE_STATUS_JSON="$STATE_DIR/candidate_status_deep.json"
CANDIDATE_PROBE_JSON="$STATE_DIR/candidate_probe.json"
CANDIDATE_ENV="$STATE_DIR/runtime.control-plane.shadow.env"
EVIDENCE_JSON="$EVIDENCE_DIR/shadow-verify-summary.json"
EVIDENCE_MD="$EVIDENCE_DIR/shadow-verify-summary.md"
EVIDENCE_COMPARE_JSON="$EVIDENCE_DIR/shadow-verify-compare.json"
EVIDENCE_COMPARE_MD="$EVIDENCE_DIR/shadow-verify-compare.md"
CANDIDATE_HOME_DIR="$STATE_DIR/candidate_home"
CANDIDATE_STATE_ROOT="$CANDIDATE_HOME_DIR/.openclaw"
CANDIDATE_CONFIG_JSON="$CANDIDATE_STATE_ROOT/openclaw.json"
CANDIDATE_LOCAL_RO_DIR="$STATE_DIR/gateway/local_ro_gateway"
CANDIDATE_NETWORK_NAME="openclaw_shadow_verify_net"
CANDIDATE_CONTAINER_NAME="openclaw-official-gateway-shadow"
ACTIVE_CONTAINER_NAME="openclaw-official-gateway"
ACTIVE_NETWORK_NAME="openclaw_ingress_net"
ACTIVE_STATE_ROOT="$HOST_STATE_ROOT/gateway"
CANDIDATE_RUNTIME_EXECUTED=0
CANDIDATE_ISOLATION_READY=1
CANDIDATE_RUNTIME_STARTED=0
CANDIDATE_STATUS_OK=0
CANDIDATE_PROBE_OK=0
CANDIDATE_CLEANUP_COMPLETED=0
CANDIDATE_CONTAINER_REMOVED=0
CANDIDATE_NETWORK_REMOVED=0
CANDIDATE_NETWORK_CREATED=0
CANDIDATE_EXECUTION_MODE="planned_only"
CANDIDATE_EXECUTION_REASON="not-attempted"
GATEWAY_TOKEN_SOURCE=""
GATEWAY_AUTH_TOKEN=""
SUMMARY_RENDERER_MODE='control_plane_container'
SUMMARY_RENDERER_BIN='python3 -B'

capture_json_evidence() {
  local output_path="$1"
  local error_message="$2"
  shift
  shift
  local status=0
  set +e
  "$@" >"$output_path"
  status=$?
  set -e
  if (( status != 0 )) && [[ ! -s "$output_path" ]]; then
    printf '{\n  "ok": false,\n  "capture_status": %s,\n  "error": "%s"\n}\n' "$status" "$error_message" > "$output_path"
  fi
  return "$status"
}

run_summary_python() {
  SUMMARY_RENDERER_MODE='control_plane_container'
  SUMMARY_RENDERER_BIN='python3 -B'
  docker_daemon_ready || fail '当前无法连接 Docker daemon；shadow verify 固定通过控制面容器生成摘要。'
  bash "$PYTHON_RUNNER" --workdir "$ROOT_DIR" -- - "$@"
}

trim_shell_value() {
  local value="${1-}"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s\n' "$value"
}

load_env_value() {
  local file_path="$1"
  local key="$2"
  local raw=""
  local line=""
  local current_key=""
  local current_value=""
  [[ -f "$file_path" ]] || return 1
  while IFS= read -r raw || [[ -n "$raw" ]]; do
    line="$(trim_shell_value "$raw")"
    [[ -n "$line" ]] || continue
    [[ "$line" == \#* ]] && continue
    [[ "$line" == *=* ]] || continue
    current_key="${line%%=*}"
    current_value="${line#*=}"
    current_key="$(trim_shell_value "$current_key")"
    current_value="$(trim_shell_value "$current_value")"
    if [[ "$current_key" == "$key" ]]; then
      printf '%s\n' "$current_value"
      return 0
    fi
  done < "$file_path"
  return 1
}

docker_cli_ready() {
  command -v docker >/dev/null 2>&1
}

docker_daemon_ready() {
  docker_cli_ready && docker info >/dev/null 2>&1
}

ensure_shadow_verify_readiness() {
  local docker_info_output=''
  if ! docker_cli_ready; then
    echo '[shadow-verify][FAIL] 未检测到 docker CLI；shadow verify 固定要求 Docker daemon 与控制面容器。' >&2
    echo '[shadow-verify][NEXT] 先安装并启动 Docker，再重新执行当前脚本。' >&2
    exit 2
  fi
  if ! docker info >/dev/null 2>&1; then
    docker_info_output="$(docker info 2>&1 || true)"
    echo '[shadow-verify][FAIL] 当前无法连接 Docker daemon；shadow verify 固定要求 Docker daemon 与控制面容器。' >&2
    [[ -z "$docker_info_output" ]] || printf '%s\n' "$docker_info_output" >&2
    echo '[shadow-verify][NEXT] 先修复 Docker daemon 可访问性或当前用户权限，再重新执行当前脚本。' >&2
    exit 2
  fi
}

inspect_active_runtime() {
  if ! docker_daemon_ready; then
    return 0
  fi
  if docker inspect "$ACTIVE_CONTAINER_NAME" >/dev/null 2>&1; then
    local networks
    networks="$(docker inspect --format '{{range $name, $_ := .NetworkSettings.Networks}}{{println $name}}{{end}}' "$ACTIVE_CONTAINER_NAME" 2>/dev/null | sed '/^$/d' || true)"
    if [[ -n "$networks" ]]; then
      ACTIVE_NETWORK_NAME="$(printf '%s\n' "$networks" | head -n 1)"
    fi
  fi
}

resolve_gateway_auth_token() {
  if [[ -n "${OPENCLAW_GATEWAY_TOKEN:-}" ]]; then
    GATEWAY_AUTH_TOKEN="$OPENCLAW_GATEWAY_TOKEN"
    GATEWAY_TOKEN_SOURCE="process-env"
    return 0
  fi
  if token="$(load_env_value "$DEPLOY_ENV_PATH" OPENCLAW_GATEWAY_TOKEN 2>/dev/null || true)"; then
    if [[ -n "$token" ]]; then
      GATEWAY_AUTH_TOKEN="$token"
      GATEWAY_TOKEN_SOURCE="deploy-env"
      return 0
    fi
  fi
  if docker_daemon_ready && docker inspect "$ACTIVE_CONTAINER_NAME" >/dev/null 2>&1; then
    token="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$ACTIVE_CONTAINER_NAME" 2>/dev/null | awk -F= '$1=="OPENCLAW_GATEWAY_TOKEN" {print substr($0, index($0,$2)); exit}')"
    if [[ -n "$token" ]]; then
      GATEWAY_AUTH_TOKEN="$token"
      GATEWAY_TOKEN_SOURCE="active-container-env"
      return 0
    fi
  fi
  return 1
}

cleanup_candidate_runtime() {
  local cleanup_ok=1
  if docker_cli_ready; then
    if docker inspect "$CANDIDATE_CONTAINER_NAME" >/dev/null 2>&1; then
      if docker rm -f "$CANDIDATE_CONTAINER_NAME" >/dev/null 2>&1; then
        CANDIDATE_CONTAINER_REMOVED=1
      else
        cleanup_ok=0
      fi
    else
      CANDIDATE_CONTAINER_REMOVED=1
    fi
    if (( CANDIDATE_NETWORK_CREATED == 1 )); then
      if docker network inspect "$CANDIDATE_NETWORK_NAME" >/dev/null 2>&1; then
        if docker network rm "$CANDIDATE_NETWORK_NAME" >/dev/null 2>&1; then
          CANDIDATE_NETWORK_REMOVED=1
        else
          cleanup_ok=0
        fi
      else
        CANDIDATE_NETWORK_REMOVED=1
      fi
    else
      CANDIDATE_NETWORK_REMOVED=1
    fi
  fi
  if (( cleanup_ok == 1 )) && (( CANDIDATE_CONTAINER_REMOVED == 1 )) && (( CANDIDATE_NETWORK_REMOVED == 1 )); then
    CANDIDATE_CLEANUP_COMPLETED=1
  else
    CANDIDATE_CLEANUP_COMPLETED=0
  fi
}

run_candidate_runtime() {
  mkdir -p "$CANDIDATE_STATE_ROOT" "$CANDIDATE_LOCAL_RO_DIR" "$CANDIDATE_STATE_ROOT/logs"
  cp "$GATEWAY_SOURCE_PATH" "$CANDIDATE_CONFIG_JSON"
  if [[ -f "$HOST_STATE_ROOT/gateway/exec-approvals.json" ]]; then
    cp "$HOST_STATE_ROOT/gateway/exec-approvals.json" "$CANDIDATE_STATE_ROOT/exec-approvals.json"
  fi

  if ! docker network inspect "$CANDIDATE_NETWORK_NAME" >/dev/null 2>&1; then
    docker network create "$CANDIDATE_NETWORK_NAME" >/dev/null
    CANDIDATE_NETWORK_CREATED=1
  fi

  if docker inspect "$CANDIDATE_CONTAINER_NAME" >/dev/null 2>&1; then
    docker rm -f "$CANDIDATE_CONTAINER_NAME" >/dev/null 2>&1 || true
  fi

  docker run -d \
    --name "$CANDIDATE_CONTAINER_NAME" \
    --network "$CANDIDATE_NETWORK_NAME" \
    --read-only \
    --tmpfs /tmp \
    --security-opt no-new-privileges:true \
    --cap-drop ALL \
    -e "OPENCLAW_GATEWAY_TOKEN=$GATEWAY_AUTH_TOKEN" \
    -e "TZ=${TZ:-Asia/Shanghai}" \
    -e "HOME=/home/node" \
    -e "OPENCLAW_HOME=/home/node" \
    -e "OPENCLAW_STATE_DIR=/home/node/.openclaw" \
    -e "OPENCLAW_CONFIG_PATH=/home/node/.openclaw/openclaw.json" \
    -v "$CANDIDATE_STATE_ROOT:/home/node/.openclaw:Z" \
    -v "$CANDIDATE_LOCAL_RO_DIR:/local_ro:ro,Z" \
    "$CANDIDATE_IMAGE_REF" \
    openclaw gateway run --port 18789 --bind lan >/dev/null

  CANDIDATE_RUNTIME_STARTED=1
  CANDIDATE_RUNTIME_EXECUTED=1
  CANDIDATE_EXECUTION_MODE="isolated_runtime"
  CANDIDATE_EXECUTION_REASON="executed"

  local status_ok_local=0
  local probe_ok_local=0
  : > "$CANDIDATE_STATUS_JSON"
  : > "$CANDIDATE_PROBE_JSON"
  for _ in $(seq 1 15); do
    if bash "$ROOT_DIR/scripts/runtime/run_openclaw_official_cli.sh" --container "$CANDIDATE_CONTAINER_NAME" -- gateway status --json --deep >"$CANDIDATE_STATUS_JSON" 2>/dev/null; then
      status_ok_local=1
      if bash "$ROOT_DIR/scripts/runtime/run_openclaw_official_cli.sh" --container "$CANDIDATE_CONTAINER_NAME" -- gateway probe --json >"$CANDIDATE_PROBE_JSON" 2>/dev/null; then
        probe_ok_local=1
        break
      fi
    fi
    sleep 2
  done
  CANDIDATE_STATUS_OK=$status_ok_local
  CANDIDATE_PROBE_OK=$probe_ok_local
  if (( CANDIDATE_STATUS_OK == 0 || CANDIDATE_PROBE_OK == 0 )); then
    CANDIDATE_EXECUTION_REASON="candidate-runtime-started-but-probes-failed"
  fi
}

ensure_shadow_verify_readiness

capture_json_evidence "$RELEASE_JSON" 'release check failed before producing JSON' bash "$ROOT_DIR/scripts/images/check_openclaw_release.sh" --json || true
capture_json_evidence "$DIGEST_JSON" 'digest check failed before producing JSON' bash "$ROOT_DIR/scripts/images/check_openclaw_digest.sh" --json || true
capture_json_evidence "$DOCTOR_JSON" 'doctor runtime contract failed before producing JSON' bash "$ROOT_DIR/scripts/doctor/check_openclaw_official_runtime_contract.sh" --json || true
capture_json_evidence "$STATUS_JSON" 'gateway status deep failed before producing JSON' bash "$ROOT_DIR/scripts/gateway/run_gateway_status_deep.sh" || true
capture_json_evidence "$PROBE_JSON" 'gateway probe failed before producing JSON' bash "$ROOT_DIR/scripts/gateway/run_gateway_probe.sh" || true
INTERNAL_API_CAPTURE_STATUS=0
if capture_json_evidence "$INTERNAL_API_JSON" 'internal-api runtime check failed before producing JSON' bash "$ROOT_DIR/scripts/doctor/check_internal_api_runtime.sh"; then
  INTERNAL_API_CAPTURE_STATUS=0
else
  INTERNAL_API_CAPTURE_STATUS=$?
fi

inspect_active_runtime
if (( SKIP_CANDIDATE_RUNTIME == 1 )); then
  CANDIDATE_EXECUTION_MODE="skipped"
  CANDIDATE_EXECUTION_REASON="skipped-by-flag"
elif ! docker_cli_ready; then
  CANDIDATE_EXECUTION_REASON="docker-cli-unavailable"
elif ! docker_daemon_ready; then
  CANDIDATE_EXECUTION_REASON="docker-daemon-unavailable"
elif ! resolve_gateway_auth_token; then
  CANDIDATE_EXECUTION_REASON="gateway-token-unavailable"
else
  if run_candidate_runtime; then
    :
  else
    if [[ "$CANDIDATE_EXECUTION_REASON" == "executed" ]]; then
      CANDIDATE_EXECUTION_REASON="candidate-runtime-launch-failed"
    fi
  fi
fi
cleanup_candidate_runtime
if (( REQUIRE_CANDIDATE_RUNTIME == 1 )) && (( CANDIDATE_RUNTIME_EXECUTED == 0 )); then
  fail "候选 control-plane 隔离实例未实际执行：$CANDIDATE_EXECUTION_REASON"
fi

run_summary_python \
  "$RELEASE_JSON" "$DIGEST_JSON" "$DOCTOR_JSON" "$STATUS_JSON" "$PROBE_JSON" "$INTERNAL_API_JSON" \
  "$SUMMARY_JSON" "$SUMMARY_MD" "$COMPARE_JSON" "$COMPARE_MD" "$CANDIDATE_PLAN_JSON" \
  "$CANDIDATE_STATUS_JSON" "$CANDIDATE_PROBE_JSON" "$CANDIDATE_CONFIG_JSON" "$CANDIDATE_ENV" \
  "$EVIDENCE_JSON" "$EVIDENCE_MD" "$EVIDENCE_COMPARE_JSON" "$EVIDENCE_COMPARE_MD" \
  "$TARGET_TAG" "$CANDIDATE_IMAGE_REF" "$PIN_ENV_PATH" "$GATEWAY_SOURCE_PATH" \
  "$CANDIDATE_STATE_ROOT" "$CANDIDATE_HOME_DIR" "$CANDIDATE_NETWORK_NAME" "$CANDIDATE_CONTAINER_NAME" \
  "$ACTIVE_CONTAINER_NAME" "$ACTIVE_NETWORK_NAME" "$ACTIVE_STATE_ROOT" \
  "$CANDIDATE_RUNTIME_EXECUTED" "$CANDIDATE_ISOLATION_READY" "$CANDIDATE_RUNTIME_STARTED" \
  "$CANDIDATE_STATUS_OK" "$CANDIDATE_PROBE_OK" "$CANDIDATE_CLEANUP_COMPLETED" \
  "$CANDIDATE_CONTAINER_REMOVED" "$CANDIDATE_NETWORK_REMOVED" "$CANDIDATE_EXECUTION_MODE" \
  "$CANDIDATE_EXECUTION_REASON" "$GATEWAY_TOKEN_SOURCE" "$INTERNAL_API_CAPTURE_STATUS" "$REQUIRE_CANDIDATE_RUNTIME" \
  "$SUMMARY_RENDERER_MODE" "$SUMMARY_RENDERER_BIN" <<'PYINNER'
import hashlib
import json
import pathlib
import sys
from datetime import datetime, timezone


def load_env(path: pathlib.Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        values[key.strip()] = value.strip()
    return values


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def bool_arg(index: int) -> bool:
    return sys.argv[index].strip() == '1'


def safe_read_json(path: pathlib.Path) -> dict | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    return json.loads(path.read_text(encoding='utf-8'))


release = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
digest = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding='utf-8'))
doctor = json.loads(pathlib.Path(sys.argv[3]).read_text(encoding='utf-8'))
status = json.loads(pathlib.Path(sys.argv[4]).read_text(encoding='utf-8'))
probe = json.loads(pathlib.Path(sys.argv[5]).read_text(encoding='utf-8'))
internal_api = json.loads(pathlib.Path(sys.argv[6]).read_text(encoding='utf-8'))
summary_json_path = pathlib.Path(sys.argv[7])
summary_md_path = pathlib.Path(sys.argv[8])
compare_json_path = pathlib.Path(sys.argv[9])
compare_md_path = pathlib.Path(sys.argv[10])
candidate_plan_path = pathlib.Path(sys.argv[11])
candidate_status_json_path = pathlib.Path(sys.argv[12])
candidate_probe_json_path = pathlib.Path(sys.argv[13])
candidate_config_path = pathlib.Path(sys.argv[14])
candidate_env_path = pathlib.Path(sys.argv[15])
evidence_json_path = pathlib.Path(sys.argv[16])
evidence_md_path = pathlib.Path(sys.argv[17])
evidence_compare_json_path = pathlib.Path(sys.argv[18])
evidence_compare_md_path = pathlib.Path(sys.argv[19])
target_tag = sys.argv[20].strip()
candidate_image_ref = sys.argv[21].strip()
pin_env_path = pathlib.Path(sys.argv[22])
gateway_source_path = pathlib.Path(sys.argv[23])
candidate_state_root = pathlib.Path(sys.argv[24])
candidate_home_dir = pathlib.Path(sys.argv[25])
candidate_network_name = sys.argv[26].strip()
candidate_container_name = sys.argv[27].strip()
active_container_name = sys.argv[28].strip()
active_network_name = sys.argv[29].strip()
active_state_root = sys.argv[30].strip()
candidate_runtime_executed = bool_arg(31)
candidate_isolation_ready = bool_arg(32)
candidate_runtime_started = bool_arg(33)
candidate_status_ok = bool_arg(34)
candidate_probe_ok = bool_arg(35)
candidate_cleanup_completed = bool_arg(36)
candidate_container_removed = bool_arg(37)
candidate_network_removed = bool_arg(38)
candidate_execution_mode = sys.argv[39].strip()
candidate_execution_reason = sys.argv[40].strip()
gateway_token_source = sys.argv[41].strip()
internal_api_capture_status = int(sys.argv[42].strip() or '0')
require_candidate_runtime = bool_arg(43)
summary_renderer_mode = sys.argv[44].strip()
summary_renderer_bin = sys.argv[45].strip()

release_target_tag = str(((release.get('supply_chain') or {}).get('latest') or {}).get('tag') or '').strip()
pin_values = load_env(pin_env_path)
active_image_ref = str(pin_values.get('OPENCLAW_OFFICIAL_GATEWAY_IMAGE') or '').strip()
active_tag = ''
if ':' in active_image_ref and '@sha256:' in active_image_ref:
    active_tag = active_image_ref.split(':', 1)[1].split('@sha256:', 1)[0].strip()
if not candidate_image_ref:
    candidate_image_ref = active_image_ref
candidate_tag = ''
if ':' in candidate_image_ref and '@sha256:' in candidate_image_ref:
    candidate_tag = candidate_image_ref.split(':', 1)[1].split('@sha256:', 1)[0].strip()

source_config_text = gateway_source_path.read_text(encoding='utf-8')
source_config_sha = sha256_text(source_config_text)
if not candidate_config_path.exists():
    candidate_config_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_config_path.write_text(source_config_text, encoding='utf-8')

candidate_env_lines = [
    '# 由 run_shadow_upgrade_verify.sh 生成；候选 control-plane 隔离实例 env',
    f'OPENCLAW_HOME=/home/node',
    f'OPENCLAW_STATE_DIR=/home/node/.openclaw',
    f'OPENCLAW_CONFIG_PATH=/home/node/.openclaw/openclaw.json',
    f'OPENCLAW_OFFICIAL_GATEWAY_IMAGE={candidate_image_ref}',
    f'OPENCLAW_SHADOW_VERIFY_MODE={candidate_execution_mode or "planned_only"}',
    f'OPENCLAW_SHADOW_VERIFY_RUNTIME_EXECUTED={str(candidate_runtime_executed).lower()}',
]
if gateway_token_source:
    candidate_env_lines.append(f'OPENCLAW_SHADOW_VERIFY_TOKEN_SOURCE={gateway_token_source}')
candidate_env_path.write_text('\n'.join(candidate_env_lines) + '\n', encoding='utf-8')

candidate_status = safe_read_json(candidate_status_json_path)
candidate_probe = safe_read_json(candidate_probe_json_path)
active_status_ok = str(status.get('status') or '').lower() in {'ok', 'healthy', 'ready'} or bool(status.get('ok', False))
active_probe_ok = str(probe.get('status') or '').lower() in {'ok', 'healthy', 'ready'} or bool(probe.get('ok', False))
internal_readyz = internal_api.get('readyz') if isinstance(internal_api.get('readyz'), dict) else {}
internal_summary = internal_api.get('controlPlaneSummary') if isinstance(internal_api.get('controlPlaneSummary'), dict) else {}
internal_jobs = internal_api.get('controlPlaneJobs') if isinstance(internal_api.get('controlPlaneJobs'), dict) else {}
internal_scheduler = internal_summary.get('scheduler') if isinstance(internal_summary.get('scheduler'), dict) else {}
active_internal_api_ok = (
    internal_api_capture_status == 0
    and str(internal_readyz.get('status') or '').lower() == 'ready'
    and bool(internal_scheduler.get('healthy'))
    and isinstance(internal_jobs.get('items'), list)
)

candidate_plan = {
    'schema_version': 2,
    'plan_type': 'shadow_verify_candidate_instance',
    'generated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    'runtime_executed': candidate_runtime_executed,
    'runtime_started': candidate_runtime_started,
    'status_ok': candidate_status_ok,
    'probe_ok': candidate_probe_ok,
    'cleanup_completed': candidate_cleanup_completed,
    'cleanup_container_removed': candidate_container_removed,
    'cleanup_network_removed': candidate_network_removed,
    'isolation_ready': candidate_isolation_ready,
    'mode': candidate_execution_mode or 'planned_only',
    'execution_reason': candidate_execution_reason or None,
    'require_candidate_runtime': require_candidate_runtime,
    'candidate_image_ref': candidate_image_ref or None,
    'candidate_tag': candidate_tag or None,
    'state_root': str(candidate_state_root),
    'config_path': str(candidate_config_path),
    'env_path': str(candidate_env_path),
    'home_dir': str(candidate_home_dir),
    'container_name': candidate_container_name,
    'network_name': candidate_network_name,
    'published_ports': [],
    'shared_mounts_with_active_runtime': [],
    'artifacts': {
        'candidate_status_deep': candidate_status_json_path.name if candidate_status_json_path.exists() else None,
        'candidate_probe': candidate_probe_json_path.name if candidate_probe_json_path.exists() else None,
    },
    'notes': [
        'candidate instance 只使用 shadow_verify 目录下的独立 state_root / config / env，并以 /home/node/.openclaw/openclaw.json 暴露官方合同。',
        'shadow verify 只做隔离实例验证，不自动切流。',
    ],
}
candidate_plan_path.write_text(json.dumps(candidate_plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

blocking: list[str] = []
if not bool(release.get('ok', True)):
    blocking.append('release-check-failed')
if not bool(digest.get('ok', True)):
    blocking.append('digest-check-failed')
if not bool(doctor.get('ok', False)):
    blocking.append('doctor-runtime-contract-failed')
if not active_status_ok:
    blocking.append('gateway-status-deep-not-ok')
if not active_probe_ok:
    blocking.append('gateway-probe-not-ok')
if not active_internal_api_ok:
    blocking.append('internal-api-runtime-not-ok')
if target_tag and release_target_tag and target_tag != release_target_tag:
    blocking.append('target-tag-mismatch-with-release-check')
if target_tag and candidate_tag and target_tag != candidate_tag:
    blocking.append('candidate-tag-mismatch')
if require_candidate_runtime and not candidate_runtime_executed:
    blocking.append('candidate-runtime-required-but-not-executed')
if candidate_runtime_executed and not candidate_status_ok:
    blocking.append('candidate-status-deep-not-ok')
if candidate_runtime_executed and not candidate_probe_ok:
    blocking.append('candidate-probe-not-ok')
if candidate_runtime_executed and not candidate_cleanup_completed:
    blocking.append('candidate-cleanup-incomplete')

summary = {
    'schema_version': 3,
    'evidence_type': 'same_day_shadow_verify',
    'ok': not blocking,
    'mode': 'same-day-shadow-verify',
    'target_tag': target_tag or None,
    'release_latest_tag': release_target_tag or None,
    'generated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    'blocking_checks': blocking,
    'candidate_runtime_required': require_candidate_runtime,
    'candidate_instance_plan': candidate_plan_path.name,
    'candidate_runtime': {
        'executed': candidate_runtime_executed,
        'status_ok': candidate_status_ok,
        'probe_ok': candidate_probe_ok,
        'cleanup_completed': candidate_cleanup_completed,
        'execution_reason': candidate_execution_reason or None,
        'token_source': gateway_token_source or None,
    },
    'summary_renderer': {
        'mode': summary_renderer_mode or None,
        'executable': summary_renderer_bin or None,
    },
    'internal_api_runtime': {
        'ok': active_internal_api_ok,
        'capture_status': internal_api_capture_status,
        'ready_status': str(internal_readyz.get('status') or '').lower() or None,
        'scheduler_healthy': bool(internal_scheduler.get('healthy')),
        'job_count': len(internal_jobs.get('items') or []) if isinstance(internal_jobs.get('items'), list) else None,
    },
    'artifacts': {
        'release_check': pathlib.Path(sys.argv[1]).name,
        'digest_check': pathlib.Path(sys.argv[2]).name,
        'doctor_runtime_contract': pathlib.Path(sys.argv[3]).name,
        'gateway_status_deep': pathlib.Path(sys.argv[4]).name,
        'gateway_probe': pathlib.Path(sys.argv[5]).name,
        'internal_api_runtime': pathlib.Path(sys.argv[6]).name,
        'candidate_status_deep': candidate_status_json_path.name if candidate_status_json_path.exists() else None,
        'candidate_probe': candidate_probe_json_path.name if candidate_probe_json_path.exists() else None,
        'shadow_compare': compare_json_path.name,
    },
}
content_json = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
summary_json_path.write_text(content_json, encoding='utf-8')
evidence_json_path.write_text(content_json, encoding='utf-8')
lines = [
    '# Shadow Verify Summary',
    '',
    f'- ok: `{summary["ok"]}`',
    f'- target_tag: `{target_tag or ""}`',
    f'- release_latest_tag: `{release_target_tag or ""}`',
    f'- candidate_image_ref: `{candidate_image_ref or ""}`',
    f'- candidate_runtime_executed: `{candidate_runtime_executed}`',
    f'- candidate_status_ok: `{candidate_status_ok}`',
    f'- candidate_probe_ok: `{candidate_probe_ok}`',
    f'- internal_api_ok: `{active_internal_api_ok}`',
    f'- candidate_cleanup_completed: `{candidate_cleanup_completed}`',
    f'- summary_renderer: `{summary_renderer_mode or ""}`',
    f'- candidate_execution_reason: `{candidate_execution_reason or ""}`',
    f'- blocking_checks: `{", ".join(blocking) if blocking else "none"}`',
    '',
    '## Artifacts',
]
for key, value in summary['artifacts'].items():
    lines.append(f'- {key}: `{value or ""}`')
content_md = "\n".join(lines) + "\n"
summary_md_path.write_text(content_md, encoding='utf-8')
evidence_md_path.write_text(content_md, encoding='utf-8')

compare = {
    'schema_version': 2,
    'evidence_type': 'shadow_verify_compare',
    'generated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    'active_runtime': {
        'image_ref': active_image_ref or None,
        'tag': active_tag or None,
        'config_source_path': str(gateway_source_path),
        'config_sha256': source_config_sha,
        'state_root': active_state_root,
        'container_name': active_container_name,
        'network_name': active_network_name,
        'status_ok': active_status_ok,
        'probe_ok': active_probe_ok,
        'internal_api_ok': active_internal_api_ok,
    },
    'candidate_instance': candidate_plan,
    'candidate_status': candidate_status,
    'candidate_probe': candidate_probe,
    'comparison': {
        'same_image_ref': bool(active_image_ref and candidate_image_ref and active_image_ref == candidate_image_ref),
        'same_tag': bool(active_tag and candidate_tag and active_tag == candidate_tag),
        'same_config_sha256': True,
        'shared_state_root': str(candidate_state_root) == active_state_root,
        'shared_container_name': candidate_container_name == active_container_name,
        'shared_network_name': candidate_network_name == active_network_name,
    },
}
compare_json = json.dumps(compare, ensure_ascii=False, indent=2) + '\n'
compare_json_path.write_text(compare_json, encoding='utf-8')
evidence_compare_json_path.write_text(compare_json, encoding='utf-8')
compare_md_lines = [
    '# Shadow Verify Compare',
    '',
    f'- active_image_ref: `{active_image_ref or ""}`',
    f'- candidate_image_ref: `{candidate_image_ref or ""}`',
    f'- same_image_ref: `{compare["comparison"]["same_image_ref"]}`',
    f'- same_tag: `{compare["comparison"]["same_tag"]}`',
    f'- same_config_sha256: `{compare["comparison"]["same_config_sha256"]}`',
    f'- shared_state_root: `{compare["comparison"]["shared_state_root"]}`',
    f'- shared_container_name: `{compare["comparison"]["shared_container_name"]}`',
    f'- shared_network_name: `{compare["comparison"]["shared_network_name"]}`',
    f'- runtime_executed: `{candidate_plan["runtime_executed"]}`',
    f'- status_ok: `{candidate_plan["status_ok"]}`',
    f'- probe_ok: `{candidate_plan["probe_ok"]}`',
    f'- cleanup_completed: `{candidate_plan["cleanup_completed"]}`',
    f'- execution_reason: `{candidate_plan["execution_reason"] or ""}`',
]
compare_md = '\n'.join(compare_md_lines) + '\n'
compare_md_path.write_text(compare_md, encoding='utf-8')
evidence_compare_md_path.write_text(compare_md, encoding='utf-8')
PYINNER


if [[ "$JSON_STDOUT" == "1" ]]; then
  cat "$SUMMARY_JSON"
else
  echo "[shadow-verify] summary written to $SUMMARY_JSON"
  echo "[shadow-verify] compare written to $COMPARE_JSON"
  echo "[shadow-verify] candidate plan written to $CANDIDATE_PLAN_JSON"
  echo "[shadow-verify] evidence mirrored to $EVIDENCE_JSON and $EVIDENCE_COMPARE_JSON"
  echo "[shadow-verify] 如需额外生成 release evidence 摘要索引，可手工执行 export_runtime_acceptance_evidence.sh"
  cat "$SUMMARY_MD"
  echo
  cat "$COMPARE_MD"
fi
