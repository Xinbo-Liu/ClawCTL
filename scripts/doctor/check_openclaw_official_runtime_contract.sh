#!/usr/bin/env bash
# 用途：执行 OpenClaw 官方安全审计与可选模型探测，作为单一官方 Gateway 的统一运行态契约检查入口。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
PYTHON_RUNNER="$ROOT_DIR/scripts/runtime/run_python_container.sh"
# shellcheck source=../lib/control_plane_config_paths.sh
source "$ROOT_DIR/scripts/lib/control_plane_config_paths.sh"
# shellcheck source=../lib/repo_contracts.sh
source "$ROOT_DIR/scripts/lib/repo_contracts.sh"
source "$ROOT_DIR/scripts/runtime/runtime_target_lib.sh"
source "$ROOT_DIR/scripts/runtime/runtime_docker_lib.sh"
source "$ROOT_DIR/scripts/setup/lib/runtime_permissions.sh"
source "$ROOT_DIR/scripts/setup/lib/deploy_env_shell.sh"
TARGET="gateway"
JSON_STDOUT=0
NO_CACHE=0
PROBE_TIMEOUT_MS="${OPENCLAW_MODELS_PROBE_TIMEOUT_MS:-15000}"
COMMAND_TIMEOUT_SECONDS="${OPENCLAW_OFFICIAL_CLI_COMMAND_TIMEOUT_SECONDS:-300}"
RESOLVED_CONFIG_PATH=""
repo_contract_assign_path RUNTIME_CONTRACT_PATH runtime.runtime_contract
repo_contract_assign_relpath RUNTIME_CONTRACT_REL_PATH runtime.runtime_contract

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/doctor/check_openclaw_official_runtime_contract.sh [选项]

说明：
  - 通过统一容器 CLI 入口在唯一官方 Gateway 容器内执行 `openclaw doctor`；
  - 执行 `openclaw security audit --deep --json`；
  - 当 active profile 声明 `model_runtime` 时，额外执行 `openclaw models status --check` 与 `openclaw models status --probe --json`；
  - 原始证据写入 `HOST_CONTROL_PLANE_DIR/setup/official_cli/`（当前 host state root 由 runtime_paths 真源派生）；
  - 校验标准固定为：doctor 通过、安全审计不存在 high/critical finding；若启用模型运行合同，则模型探测结果还必须与 merged runtime contract 一致。

选项：
  --root-dir <path>         指定仓库根目录
  --target <gateway>        只允许 gateway（默认：gateway）
  --probe-timeout-ms <ms>   传给 `openclaw models status --probe-timeout`（默认：15000）
  --command-timeout-seconds <seconds>
                           单条官方 CLI 命令最大执行秒数（默认：300）
  --no-cache                忽略同一容器/配置指纹下的已通过官方 CLI 证据，强制重新深查
  --json                    输出机器可读摘要
  -h, --help                显示帮助
USAGE
}

python_json_eval() {
  local inline_script="$1"
  shift
  bash "$PYTHON_RUNNER" --workdir "$ROOT_DIR" -- - "$@" <<PYINLINE
$inline_script
PYINLINE
}

fail() {
  local msg="$1" code="${2:-2}"
  if [[ "$JSON_STDOUT" == "1" ]]; then
    python_json_eval $'import json, sys\nprint(json.dumps({"ok": False, "error": sys.argv[1]}, ensure_ascii=False, indent=2))' "$msg"
  else
    echo "[check_openclaw_official_runtime_contract][FAIL] $msg" >&2
  fi
  exit "$code"
}

note() {
  [[ "$JSON_STDOUT" == "1" ]] && return 0
  echo "[check_openclaw_official_runtime_contract] $*"
}

run_official_cli_command() {
  local label="$1"
  local stdout_path="$2"
  local stderr_path="$3"
  local fail_code="$4"
  shift 4
  local rc=0
  local evidence_paths="$stdout_path"
  [[ "$stderr_path" != "$stdout_path" ]] && evidence_paths="$stdout_path 与 $stderr_path"
  set +e
  if command -v timeout >/dev/null 2>&1; then
    if [[ "$stdout_path" == "$stderr_path" ]]; then
      timeout "$COMMAND_TIMEOUT_SECONDS" "$@" >"$stdout_path" 2>&1
      rc=$?
    else
      timeout "$COMMAND_TIMEOUT_SECONDS" "$@" >"$stdout_path" 2>"$stderr_path"
      rc=$?
    fi
  else
    if [[ "$stdout_path" == "$stderr_path" ]]; then
      "$@" >"$stdout_path" 2>&1
      rc=$?
    else
      "$@" >"$stdout_path" 2>"$stderr_path"
      rc=$?
    fi
  fi
  set -e
  if [[ "$rc" -eq 124 ]]; then
    fail "$label 超过 ${COMMAND_TIMEOUT_SECONDS}s；请查看 $evidence_paths" "$fail_code"
  fi
  [[ "$rc" -eq 0 ]] || fail "$label 失败；请查看 $evidence_paths" "$fail_code"
}

sha256_file_or_empty() {
  local path="$1"
  [[ -f "$path" ]] || {
    printf '\n'
    return 0
  }
  sha256sum "$path" 2>/dev/null | awk '{print $1}'
}

sha256_text() {
  printf '%s' "$1" | sha256sum | awk '{print $1}'
}

json_bool_field() {
  local payload="$1"
  local field="$2"
  if command -v jq >/dev/null 2>&1; then
    jq -r --arg field "$field" '.[$field] // false' <<<"$payload"
    return $?
  fi
  python_json_eval $'import json, sys\nprint("true" if json.loads(sys.argv[1]).get(sys.argv[2]) else "false")' "$payload" "$field"
}

official_cli_write_summary() {
  local summary_path="$1"
  local container_id="$2"
  local container_image="$3"
  local container_started_at="$4"
  local config_sha="$5"
  local runtime_contract_sha="$6"
  local service_config_sha="$7"
  local expected_sha="$8"
  local doctor_log_path="$9"
  local security_json_path="${10}"
  local security_stderr_path="${11}"
  local models_check_path="${12}"
  local models_probe_json_path="${13}"
  local models_probe_stderr_path="${14}"
  local security_summary="${15}"
  local models_summary="${16}"
  command -v jq >/dev/null 2>&1 || return 0
  jq -n \
    --arg target "$TARGET" \
    --arg generated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --arg container_name "$container_name" \
    --arg container_id "$container_id" \
    --arg container_image "$container_image" \
    --arg container_started_at "$container_started_at" \
    --arg config_sha "$config_sha" \
    --arg runtime_contract_sha "$runtime_contract_sha" \
    --arg service_config_sha "$service_config_sha" \
    --arg expected_sha "$expected_sha" \
    --arg doctor_log "$doctor_log_path" \
    --arg security_json "$security_json_path" \
    --arg security_stderr "$security_stderr_path" \
    --arg models_check "$models_check_path" \
    --arg models_probe_json "$models_probe_json_path" \
    --arg models_probe_stderr "$models_probe_stderr_path" \
    --argjson security "$security_summary" \
    --argjson models "$models_summary" \
    '{
      ok: true,
      target: $target,
      generated_at: $generated_at,
      fingerprint: {
        container_name: $container_name,
        container_id: $container_id,
        container_image: $container_image,
        container_started_at: $container_started_at,
        config_sha256: $config_sha,
        runtime_contract_sha256: $runtime_contract_sha,
        service_config_sha256: $service_config_sha,
        expected_runtime_sha256: $expected_sha
      },
      evidence: {
        doctor_log: $doctor_log,
        security_json: $security_json,
        security_stderr: $security_stderr,
        models_check: $models_check,
        models_probe_json: $models_probe_json,
        models_probe_stderr: $models_probe_stderr
      },
      security: $security,
      models: $models
    }' > "$summary_path"
}

official_cli_cache_valid() {
  local summary_path="$1"
  local container_id="$2"
  local container_image="$3"
  local container_started_at="$4"
  local config_sha="$5"
  local runtime_contract_sha="$6"
  local service_config_sha="$7"
  local expected_sha="$8"
  local path=""
  [[ "$NO_CACHE" != "1" ]] || return 1
  command -v jq >/dev/null 2>&1 || return 1
  [[ -r "$summary_path" ]] || return 1
  jq -e \
    --arg container_id "$container_id" \
    --arg container_image "$container_image" \
    --arg container_started_at "$container_started_at" \
    --arg config_sha "$config_sha" \
    --arg runtime_contract_sha "$runtime_contract_sha" \
    --arg service_config_sha "$service_config_sha" \
    --arg expected_sha "$expected_sha" \
    '
      .ok == true
      and .fingerprint.container_id == $container_id
      and .fingerprint.container_image == $container_image
      and .fingerprint.container_started_at == $container_started_at
      and .fingerprint.config_sha256 == $config_sha
      and .fingerprint.runtime_contract_sha256 == $runtime_contract_sha
      and .fingerprint.service_config_sha256 == $service_config_sha
      and .fingerprint.expected_runtime_sha256 == $expected_sha
    ' "$summary_path" >/dev/null || return 1
  while IFS= read -r path; do
    [[ -r "$path" ]] || return 1
  done < <(jq -r '.evidence | to_entries[]?.value // empty' "$summary_path")
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root-dir)
      [[ $# -ge 2 ]] || fail "--root-dir 缺少参数" 2
      ROOT_DIR="$2"
      shift 2
      ;;
    --target)
      [[ $# -ge 2 ]] || fail "--target 缺少参数" 2
      TARGET="$2"
      shift 2
      ;;
    --probe-timeout-ms)
      [[ $# -ge 2 ]] || fail "--probe-timeout-ms 缺少参数" 2
      PROBE_TIMEOUT_MS="$2"
      shift 2
      ;;
    --command-timeout-seconds)
      [[ $# -ge 2 ]] || fail "--command-timeout-seconds 缺少参数" 2
      COMMAND_TIMEOUT_SECONDS="$2"
      shift 2
      ;;
    --no-cache)
      NO_CACHE=1
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
      fail "未知参数：$1" 2
      ;;
  esac
done

ROOT_DIR="$(cd "$ROOT_DIR" && pwd)"
PYTHON_RUNNER="$ROOT_DIR/scripts/runtime/run_python_container.sh"
OPENCLAW_CONTROL_PLANE_CONFIG_PATHS_ROOT="$ROOT_DIR"
if [[ -z "${OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH:-}" && -f "$ROOT_DIR/deploy/.env" ]]; then
  deploy_env_shell_load_keys "$ROOT_DIR/deploy/.env" OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH \
    || fail "无法从 deploy/.env 读取 OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH" 2
  export OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH
fi
RESOLVED_CONFIG_PATH="$(openclaw_control_plane_resolve_config_path agent_platform)"
repo_contract_assign_path RUNTIME_CONTRACT_PATH runtime.runtime_contract
runtime_permissions_assert_access_mode "$ROOT_DIR" rx "仓库根目录" || fail "仓库根目录缺少读取/执行权限：$ROOT_DIR；当前脚本不会自动提权或 chown" 4
HOST_CONTROL_PLANE_DIR="$(runtime_permissions_host_control_plane_state_dir "$ROOT_DIR")"
runtime_docker_require_cli >/dev/null || fail "未检测到 docker" 3
OFFICIAL_CLI_RUNNER="$ROOT_DIR/scripts/runtime/run_openclaw_official_cli.sh"
[[ -f "$OFFICIAL_CLI_RUNNER" && -r "$OFFICIAL_CLI_RUNNER" ]] || fail "缺少统一容器 CLI 入口：$OFFICIAL_CLI_RUNNER" 3
[[ -f "$PYTHON_RUNNER" && -r "$PYTHON_RUNNER" ]] || fail "缺少固定 Python runner：$PYTHON_RUNNER" 3
[[ "$PROBE_TIMEOUT_MS" =~ ^[1-9][0-9]*$ ]] || fail "--probe-timeout-ms 必须为正整数" 2
[[ "$COMMAND_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || fail "--command-timeout-seconds 必须为正整数" 2
[[ "$TARGET" == 'gateway' ]] || fail "当前只支持 --target gateway" 2

container_name="$(runtime_target_container_name_for_target gateway)" || fail "gateway target 不存在" 2
config_path="$ROOT_DIR/config/gateway/openclaw.gateway.json"
state_dir="$HOST_CONTROL_PLANE_DIR/setup/official_cli"
runtime_permissions_assert_dir_manageable_or_creatable "$state_dir" "gateway 官方 CLI 证据目录" || fail "gateway 官方 CLI 证据目录不可管理：$state_dir；当前脚本不会自动提权或 chown" 4
mkdir -p "$state_dir"
runtime_permissions_assert_access_mode "$state_dir" rwx "gateway 官方 CLI 证据目录" || fail "gateway 官方 CLI 证据目录缺少读取/写入/执行权限：$state_dir；当前脚本不会自动提权或 chown" 4
[[ -f "$config_path" ]] || fail "缺少真源配置：$config_path" 4

REPO_PYTHON_ENV_ARGS=()
while IFS= read -r -d '' item; do
  REPO_PYTHON_ENV_ARGS+=("$item")
done < <(openclaw_repo_python_env_args "$ROOT_DIR")

expected_json="$(OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH="$RESOLVED_CONFIG_PATH" bash "$PYTHON_RUNNER" --workdir "$ROOT_DIR" "${REPO_PYTHON_ENV_ARGS[@]}" -- - "$RESOLVED_CONFIG_PATH" "$RUNTIME_CONTRACT_PATH" <<'PY'
import json, pathlib, sys

config_path = pathlib.Path(sys.argv[1]).resolve()
contract_path = pathlib.Path(sys.argv[2]).resolve()
from openclaw.control_plane.runtime.truth_surfaces import load_runtime_contract
contract = load_runtime_contract(contract_path, config_path=config_path)
runtime = contract.get('model_runtime') or {}
defaults = runtime.get('defaults') or {}
primary = defaults.get('primary')
enabled = isinstance(primary, str) and bool(primary)
print(json.dumps({'enabled': enabled, 'primary': primary if enabled else ''}, ensure_ascii=False))
PY
)" || fail "读取 merged runtime contract 期望值失败：$RUNTIME_CONTRACT_REL_PATH" 4

models_enabled="$(json_bool_field "$expected_json" enabled)"

runtime_docker_inspect_exists "$container_name" || fail "未找到容器：$container_name；请先完成部署并确认容器已创建" 10

# doctor 日志 / security audit JSON / models status 证据都会写入官方 CLI 证据目录。
doctor_log_path="$state_dir/doctor.log"
security_json_path="$state_dir/security_audit_deep.json"
security_stderr_path="$state_dir/security_audit_deep.stderr.log"
models_check_path="$state_dir/models_status_check.log"
models_probe_json_path="$state_dir/models_status_probe.json"
models_probe_stderr_path="$state_dir/models_status_probe.stderr.log"
summary_json_path="$state_dir/summary.json"
container_id="$(runtime_docker_inspect_format "$container_name" '{{.Id}}' 2>/dev/null || true)"
container_image="$(runtime_docker_inspect_format "$container_name" '{{.Image}}' 2>/dev/null || true)"
container_started_at="$(runtime_docker_container_started_at "$container_name" 2>/dev/null || true)"
config_sha="$(sha256_file_or_empty "$config_path")"
runtime_contract_sha="$(sha256_file_or_empty "$RUNTIME_CONTRACT_PATH")"
service_config_sha="$(sha256_file_or_empty "$RESOLVED_CONFIG_PATH")"
expected_sha="$(sha256_text "$expected_json")"

if official_cli_cache_valid \
  "$summary_json_path" \
  "$container_id" \
  "$container_image" \
  "$container_started_at" \
  "$config_sha" \
  "$runtime_contract_sha" \
  "$service_config_sha" \
  "$expected_sha"; then
  if [[ "$JSON_STDOUT" == '1' ]]; then
    cat "$summary_json_path"
  else
    note "复用已通过的官方 CLI 证据：$summary_json_path"
  fi
  exit 0
fi

rm -f "$doctor_log_path" "$security_json_path" "$security_stderr_path" "$models_check_path" "$models_probe_json_path" "$models_probe_stderr_path"

note '执行 openclaw doctor'
run_official_cli_command 'gateway 执行 openclaw doctor' "$doctor_log_path" "$doctor_log_path" 11 \
  bash "$OFFICIAL_CLI_RUNNER" --target gateway -- doctor

note '执行 openclaw security audit --deep --json'
run_official_cli_command 'gateway 执行 openclaw security audit --deep --json' "$security_json_path" "$security_stderr_path" 12 \
  bash "$OFFICIAL_CLI_RUNNER" --target gateway -- security audit --deep --json

security_summary="$(bash "$PYTHON_RUNNER" --workdir "$ROOT_DIR" -- - "$security_json_path" <<'PY'
import json, pathlib, sys
payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
report = payload.get('report') if isinstance(payload, dict) and isinstance(payload.get('report'), dict) else payload
if not isinstance(report, dict):
    raise SystemExit('security audit JSON 根对象不合法')
findings = report.get('findings') if isinstance(report.get('findings'), list) else []
blocking = []
for item in findings:
    if not isinstance(item, dict):
        continue
    severity = str(item.get('severity', '')).lower()
    if severity in {'critical', 'high'}:
        blocking.append({'checkId': item.get('checkId') or item.get('id') or 'unknown', 'severity': severity, 'message': item.get('message') or item.get('title') or ''})
summary = report.get('summary') if isinstance(report.get('summary'), dict) else {}
print(json.dumps({'ok': len(blocking) == 0, 'status': report.get('status') or payload.get('status') or 'unknown', 'total_findings': len(findings), 'blocking_findings': blocking, 'summary': summary}, ensure_ascii=False))
PY
)" || fail "gateway 安全审计 JSON 解析失败：$security_json_path" 12

security_ok="$(bash "$PYTHON_RUNNER" --workdir "$ROOT_DIR" -- - "$security_summary" <<'PY'
import json, sys
print('true' if json.loads(sys.argv[1]).get('ok') else 'false')
PY
)"
[[ "$security_ok" == 'true' ]] || fail "gateway 安全审计存在 high/critical findings；详情见 $security_json_path" 13

if [[ "$models_enabled" == 'true' ]]; then
  note 'active profile 已声明 model_runtime；执行 openclaw models status --check'
  run_official_cli_command 'gateway 执行 openclaw models status --check' "$models_check_path" "$models_check_path" 14 \
    bash "$OFFICIAL_CLI_RUNNER" --target gateway -- models status --check

  note '执行 openclaw models status --probe --json'
  run_official_cli_command 'gateway 执行 openclaw models status --probe --json' "$models_probe_json_path" "$models_probe_stderr_path" 15 \
    bash "$OFFICIAL_CLI_RUNNER" --target gateway -- models status --probe --probe-timeout "$PROBE_TIMEOUT_MS" --json

  models_summary="$(bash "$PYTHON_RUNNER" --workdir "$ROOT_DIR" -- - "$models_probe_json_path" "$expected_json" <<'PY'
import json, pathlib, sys
payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
expected = json.loads(sys.argv[2])
if not isinstance(payload, dict):
    raise SystemExit('models status JSON 根对象不合法')

def pick_first(paths):
    for path in paths:
        cur = payload
        ok = True
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                ok = False
                break
            cur = cur[key]
        if ok:
            return cur
    return None

primary = pick_first([('resolved', 'primary'), ('resolved', 'model', 'primary'), ('models', 'resolved', 'primary'), ('models', 'resolved', 'model', 'primary'), ('models', 'primary')])
result = {
    'ok': primary == expected.get('primary'),
    'enabled': True,
    'skipped': False,
    'resolved_primary': primary,
    'expected_primary': expected.get('primary'),
}
print(json.dumps(result, ensure_ascii=False))
PY
)" || fail "gateway 模型探测 JSON 解析失败：$models_probe_json_path" 15

  models_ok="$(bash "$PYTHON_RUNNER" --workdir "$ROOT_DIR" -- - "$models_summary" <<'PY'
import json, sys
print('true' if json.loads(sys.argv[1]).get('ok') else 'false')
PY
)"
  [[ "$models_ok" == 'true' ]] || fail "gateway 模型探测结果与 merged runtime contract 不一致；详情见 $models_probe_json_path" 16
else
  note '当前 active profile 未声明 model_runtime，跳过 models status 探针'
  printf '%s\n' '[check_openclaw_official_runtime_contract] skipped: active profile declares no model_runtime' >"$models_check_path"
  : >"$models_probe_stderr_path"
  models_summary="$(bash "$PYTHON_RUNNER" --workdir "$ROOT_DIR" -- - "$expected_json" <<'PY'
import json, sys
expected = json.loads(sys.argv[1])
print(json.dumps({
    'ok': True,
    'enabled': False,
    'skipped': True,
    'reason': 'active profile declares no model_runtime',
    'expected_primary': expected.get('primary') or '',
}, ensure_ascii=False))
PY
)"
  printf '%s\n' "$models_summary" >"$models_probe_json_path"
fi

official_cli_write_summary \
  "$summary_json_path" \
  "$container_id" \
  "$container_image" \
  "$container_started_at" \
  "$config_sha" \
  "$runtime_contract_sha" \
  "$service_config_sha" \
  "$expected_sha" \
  "$doctor_log_path" \
  "$security_json_path" \
  "$security_stderr_path" \
  "$models_check_path" \
  "$models_probe_json_path" \
  "$models_probe_stderr_path" \
  "$security_summary" \
  "$models_summary"

if [[ "$JSON_STDOUT" == '1' ]]; then
  if [[ -r "$summary_json_path" ]]; then
    cat "$summary_json_path"
  else
    python_json_eval $'import json, sys\nprint(json.dumps({"ok": True, "target": "gateway", "doctor_log": sys.argv[1], "security": json.loads(sys.argv[2]), "models": json.loads(sys.argv[3])}, ensure_ascii=False, indent=2))' "$doctor_log_path" "$security_summary" "$models_summary"
  fi
else
  echo "[check_openclaw_official_runtime_contract] gateway 通过：doctor / security audit 已符合运行时合同；models.enabled=${models_enabled}"
fi
