#!/usr/bin/env bash
# 用途：统一检查运行态派生产物、acceptance state、official CLI 产物与 shadow verify 证据前提，避免 runtime evidence 与运行态现状脱节。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
# shellcheck source=../lib/control_plane_config_paths.sh
source "$ROOT_DIR/scripts/lib/control_plane_config_paths.sh"
# shellcheck source=../lib/repo_contracts.sh
source "$ROOT_DIR/scripts/lib/repo_contracts.sh"
PYTHON_RUNNER="$ROOT_DIR/scripts/runtime/run_python_container.sh"
repo_contract_assign_path RUNTIME_CONTRACT_PATH runtime.runtime_contract
repo_contract_assign_relpath RUNTIME_CONTRACT_REL_PATH runtime.runtime_contract

if [[ ${1:-} == "-h" || ${1:-} == "--help" ]]; then
  cat <<'USAGE'
用法：
  bash ./scripts/runtime/check_runtime_evidence_prereqs.sh [选项]

说明：
  - 统一检查运行态派生产物、deployment acceptance、official CLI 产物与 shadow verify 证据前提；
  - 默认从 --env-file 指向的 deploy env 读取 active control-plane profile；
  - 只做本地文件与目录存在性/可读性判断，不会自动执行 Docker、one_click_test_full、export_runtime_acceptance_evidence 或 shadow verify；
  - 当前脚本不会自动 sudo、提权或 chown；只会报告缺口。

scope：
  runtime-prepare   只检查 deploy/.env 与运行态派生产物（openclaw.json、runtime.*.env、nginx.gateway.conf、exec-approvals.json）
  evidence-export   在 runtime-prepare 基础上额外要求 deployment_acceptance.json 与 official_cli 产物齐全；通过后即可导出 runtime acceptance
  clean-release     在 evidence-export 基础上额外要求 shadow_verify/summary.json 与 compare.json 齐全；通过后即可导出带候选实例对照的交付材料
USAGE
  exit 0
fi

source "$ROOT_DIR/scripts/setup/lib/runtime_permissions.sh"
source "$ROOT_DIR/scripts/setup/lib/deploy_env_shell.sh"
source "$ROOT_DIR/scripts/runtime/runtime_target_lib.sh"
CONTROL_PLANE_OBJECTS_CMD=("$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh" control-plane objects)
DEPLOYMENT_ACCEPTANCE_STATE=""
HOST_STATE_DIR=""
HOST_GATEWAY_DIR=""
HOST_CONTROL_PLANE_DIR=""
RESOLVED_CONFIG_PATH=""

SCOPE="runtime-prepare"
ENV_FILE="$ROOT_DIR/deploy/.env"
SUMMARY_JSON=""
FAILURES=0
WARNINGS=0

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/runtime/check_runtime_evidence_prereqs.sh [选项]

说明：
  - 统一检查运行态派生产物、deployment acceptance、official CLI 产物与 shadow verify 证据前提；
  - 默认从 --env-file 指向的 deploy env 读取 active control-plane profile；
  - 只做本地文件与目录存在性/可读性判断，不会自动执行 Docker、one_click_test_full、export_runtime_acceptance_evidence 或 shadow verify；
  - 当前脚本不会自动 sudo、提权或 chown；只会报告缺口。

scope：
  runtime-prepare   只检查 deploy/.env 与运行态派生产物（openclaw.json、runtime.*.env、nginx.gateway.conf、exec-approvals.json）
  evidence-export   在 runtime-prepare 基础上额外要求 deployment_acceptance.json 与 official_cli 产物齐全；通过后即可导出 runtime acceptance
  clean-release     在 evidence-export 基础上额外要求 shadow_verify/summary.json 与 compare.json 齐全；通过后即可导出带候选实例对照的交付材料

选项：
  --scope <scope>         取值：runtime-prepare | evidence-export | clean-release
                           例如：--scope runtime-prepare | evidence-export | clean-release
  --env-file <path>       覆盖默认 env 文件路径（默认：deploy/.env）
  --summary-json <path>   可选；写出结构化检查摘要
  -h, --help              显示帮助
USAGE
}

note() { printf '[INFO] %s\n' "$*"; }
warn() { WARNINGS=1; printf '[WARN] %s\n' "$*"; }
fail() { FAILURES=1; printf '[FAIL] %s\n' "$*" >&2; }

model_runtime_enabled() {
  local expected_json=""
  local -a repo_python_env_args=()
  while IFS= read -r -d '' item; do
    repo_python_env_args+=("$item")
  done < <(openclaw_repo_python_env_args "$ROOT_DIR")
  if ! expected_json="$(OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH="$RESOLVED_CONFIG_PATH" bash "$PYTHON_RUNNER" --workdir "$ROOT_DIR" "${repo_python_env_args[@]}" --env "OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH=$RESOLVED_CONFIG_PATH" -- - "$RESOLVED_CONFIG_PATH" "$RUNTIME_CONTRACT_PATH" <<'PY'
import json
import sys
from pathlib import Path

config_path = Path(sys.argv[1]).resolve()
contract_path = Path(sys.argv[2]).resolve()
from openclaw.control_plane.runtime.truth_surfaces import load_runtime_contract

contract = load_runtime_contract(
    contract_path,
    config_path=config_path,
)
runtime = contract.get('model_runtime') or {}
defaults = runtime.get('defaults') or {}
primary = defaults.get('primary')
print(json.dumps({'enabled': isinstance(primary, str) and bool(primary)}, ensure_ascii=False))
PY
)"; then
    fail "读取 merged runtime contract 期望值失败：$RUNTIME_CONTRACT_REL_PATH"
    printf 'false\n'
    return 0
  fi

  if ! bash "$PYTHON_RUNNER" --workdir "$ROOT_DIR" -- - "$expected_json" <<'PY'
import json
import sys
print('true' if json.loads(sys.argv[1]).get('enabled') else 'false')
PY
  then
    fail "解析 model_runtime enabled 状态失败"
    printf 'false\n'
  fi
}

check_readable_file() {
  local path="$1"
  local label="$2"
  if [[ ! -f "$path" ]]; then
    fail "$label 缺失：$path"
    return 0
  fi
  [[ -r "$path" ]] || fail "$label 不可读：$path"
}

check_readable_dir() {
  local path="$1"
  local label="$2"
  if [[ ! -d "$path" ]]; then
    fail "$label 缺失：$path"
    return 0
  fi
  [[ -r "$path" && -x "$path" ]] || fail "$label 不可读/不可遍历：$path"
}

resolve_deployment_acceptance_state() {
  local resolved=""
  if [[ -n "$DEPLOYMENT_ACCEPTANCE_STATE" ]]; then
    return 0
  fi
  if ! resolved="$("${CONTROL_PLANE_OBJECTS_CMD[@]}" entry-path --family acceptance_state --entry deployment_acceptance)"; then
    fail "解析 deployment acceptance 状态文件路径失败"
    return 0
  fi
  if [[ "$resolved" = /* ]]; then
    DEPLOYMENT_ACCEPTANCE_STATE="$resolved"
  else
    DEPLOYMENT_ACCEPTANCE_STATE="$ROOT_DIR/$resolved"
  fi
}

runtime_prepare_checks() {
  check_readable_file "$ENV_FILE" "运行态 env 文件"
  check_readable_file "$HOST_GATEWAY_DIR/openclaw.json" "official Gateway 运行态 JSON"
  check_readable_file "$HOST_GATEWAY_DIR/runtime.gateway.env" "official Gateway 运行态 env"
  check_readable_file "$HOST_CONTROL_PLANE_DIR/runtime.internal-api.env" "internal-api 路径 env"
  check_readable_file "$HOST_CONTROL_PLANE_DIR/runtime.internal-api.app.env" "internal-api 应用 env"
  check_readable_file "$HOST_CONTROL_PLANE_DIR/runtime.scheduler.env" "scheduler 路径 env"
  check_readable_file "$HOST_CONTROL_PLANE_DIR/runtime.scheduler.app.env" "scheduler 应用 env"
  check_readable_file "$HOST_GATEWAY_DIR/nginx.gateway.conf" "private ingress Nginx 运行态配置"
  check_readable_file "$HOST_GATEWAY_DIR/exec-approvals.json" "official Gateway exec approvals 运行态副本"
}

evidence_export_checks() {
  runtime_prepare_checks
  resolve_deployment_acceptance_state
  check_readable_file "$DEPLOYMENT_ACCEPTANCE_STATE" "deployment acceptance 状态文件"
  check_readable_dir "$HOST_CONTROL_PLANE_DIR/setup/official_cli" "official CLI 产物目录"
  check_readable_file "$HOST_CONTROL_PLANE_DIR/setup/official_cli/doctor.log" "official CLI doctor 日志"
  check_readable_file "$HOST_CONTROL_PLANE_DIR/setup/official_cli/security_audit_deep.json" "official CLI security audit JSON"
  if [[ "$(model_runtime_enabled)" == 'true' ]]; then
    check_readable_file "$HOST_CONTROL_PLANE_DIR/setup/official_cli/models_status_probe.json" "official CLI models status --probe JSON"
  else
    note "当前 active profile 未声明 model_runtime，跳过 models status --probe JSON 前提"
  fi
}

clean_release_checks() {
  evidence_export_checks
  check_readable_dir "$HOST_CONTROL_PLANE_DIR/setup/shadow_verify" "shadow verify 产物目录"
  check_readable_file "$HOST_CONTROL_PLANE_DIR/setup/shadow_verify/summary.json" "shadow verify summary"
  check_readable_file "$HOST_CONTROL_PLANE_DIR/setup/shadow_verify/compare.json" "shadow verify compare"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scope)
      [[ $# -ge 2 ]] || { echo '[check_runtime_evidence_prereqs][FAIL] --scope 缺少参数' >&2; exit 2; }
      SCOPE="$2"
      shift 2
      ;;
    --env-file)
      [[ $# -ge 2 ]] || { echo '[check_runtime_evidence_prereqs][FAIL] --env-file 缺少参数' >&2; exit 2; }
      ENV_FILE="$2"
      shift 2
      ;;
    --summary-json)
      [[ $# -ge 2 ]] || { echo '[check_runtime_evidence_prereqs][FAIL] --summary-json 缺少参数' >&2; exit 2; }
      SUMMARY_JSON="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[check_runtime_evidence_prereqs][FAIL] 未知参数：$1" >&2
      exit 2
      ;;
  esac
done

if [[ -f "$ENV_FILE" ]]; then
  deploy_env_shell_load_keys "$ENV_FILE" OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH \
    || { echo "[check_runtime_evidence_prereqs][FAIL] 无法从 env 读取 OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH：$ENV_FILE" >&2; exit 2; }
  export OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH
fi

HOST_STATE_DIR="$(runtime_permissions_host_state_root "$ROOT_DIR")"
HOST_GATEWAY_DIR="$(runtime_permissions_host_gateway_state_dir "$ROOT_DIR")"
HOST_CONTROL_PLANE_DIR="$(runtime_permissions_host_control_plane_state_dir "$ROOT_DIR")"
RESOLVED_CONFIG_PATH="$(openclaw_control_plane_resolve_config_path agent_platform)"

case "$SCOPE" in
  runtime-prepare) runtime_prepare_checks ;;
  evidence-export) evidence_export_checks ;;
  clean-release) clean_release_checks ;;
  *) echo "[check_runtime_evidence_prereqs][FAIL] 不支持的 scope：$SCOPE" >&2; exit 2 ;;
esac

if [[ -n "$SUMMARY_JSON" ]]; then
  mkdir -p "$(dirname "$SUMMARY_JSON")"
  bash "$PYTHON_RUNNER" --workdir "$ROOT_DIR" -- - "$SUMMARY_JSON" "$SCOPE" "$ENV_FILE" "$FAILURES" "$WARNINGS" <<'PY'
import json, sys
from datetime import datetime, timezone
out, scope, env_file, failures, warnings = sys.argv[1:6]
payload = {
    "schema_version": 1,
    "generated_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    "scope": scope,
    "env_file": env_file,
    "ok": failures == '0',
    "warnings_present": warnings == '1',
}
with open(out, 'w', encoding='utf-8') as fh:
    json.dump(payload, fh, ensure_ascii=False, indent=2)
    fh.write('\n')
PY
  note "已写出摘要：$SUMMARY_JSON"
fi

if [[ "$FAILURES" != "0" ]]; then
  case "$SCOPE" in
    runtime-prepare)
      note '建议先执行：bash ./scripts/setup/bootstrap.sh && bash ./scripts/runtime/run_openclaw_python_tool.sh setup ingress render-nginx --env-file deploy/.env；分阶段补跑与恢复入口统一查看 docs/getting-started/quickstart.md'
      ;;
    evidence-export)
      note '建议先执行：bash ./scripts/setup/one_click_test_full.sh，并确保 official_cli 产物已生成后再导出 runtime evidence'
      ;;
    clean-release)
      note '建议先执行：bash ./scripts/gateway/run_shadow_upgrade_verify.sh --require-candidate-runtime，然后重新执行 bash ./scripts/runtime/export_runtime_acceptance_evidence.sh'
      ;;
  esac
  echo '[check_runtime_evidence_prereqs][FAIL] 运行态 evidence 前提未闭合；当前脚本不会自动 sudo、提权或 chown。' >&2
  exit 3
fi

note "scope=$SCOPE 前提已闭合"
if [[ "$WARNINGS" == "1" ]]; then
  exit 4
fi
