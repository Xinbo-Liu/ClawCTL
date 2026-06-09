#!/usr/bin/env bash
# 用途：把部署后的 acceptance state、run ledger 与官方 CLI 摘要导出到 control-plane state release/evidence/，形成运行验收证明。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/runtime/export_runtime_acceptance_evidence.sh

推荐顺序：
  bash ./scripts/runtime/check_runtime_evidence_prereqs.sh --scope evidence-export
  sudo bash ./scripts/doctor/check_ingress_boundary_evidence.sh --env-file deploy/.env --require-nginx-policy
  bash ./scripts/runtime/export_runtime_acceptance_evidence.sh

说明：
  - 当前脚本不接受业务参数；输出路径统一由 control-plane object families 派生。
  - 默认从 deploy/.env 读取 active control-plane profile 后再导出 run ledger 与 runtime acceptance。
  - 若当前机器已存在 shadow verify 产物，会一并同步到 control-plane state release/evidence/。
  - deployment_acceptance.accepted=true、ingress_boundary_evidence.accepted=true、nginx_policy.ok=true 与 runtime_acceptance.accepted=true 是硬门禁。
USAGE
}

if [[ $# -gt 0 ]]; then
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[export_runtime_acceptance_evidence][FAIL] 未知参数：$1" >&2
      usage >&2
      exit 2
      ;;
  esac
fi

source "$ROOT_DIR/scripts/setup/lib/runtime_permissions.sh"
source "$ROOT_DIR/scripts/setup/lib/deploy_env_shell.sh"
DEPLOY_ENV_FILE="$ROOT_DIR/deploy/.env"
if [[ -f "$DEPLOY_ENV_FILE" ]]; then
  if deploy_env_shell_load_keys "$DEPLOY_ENV_FILE" OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH; then
    export OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH
  else
    echo "[export_runtime_acceptance_evidence][FAIL] 无法从 deploy/.env 读取 OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH" >&2
    exit 2
  fi
fi
HOST_STATE_DIR="$(runtime_permissions_host_state_root "$ROOT_DIR")"
HOST_CONTROL_PLANE_DIR="$(runtime_permissions_host_control_plane_state_dir "$ROOT_DIR")"
CONTROL_PLANE_OBJECTS_CMD=("$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh" control-plane objects)
DEPLOYMENT_ACCEPTANCE_STATE="${DEPLOYMENT_ACCEPTANCE_STATE:-$("${CONTROL_PLANE_OBJECTS_CMD[@]}" entry-path --family acceptance_state --entry deployment_acceptance)}"
INGRESS_BOUNDARY_EVIDENCE_STATE="${INGRESS_BOUNDARY_EVIDENCE_STATE:-$("${CONTROL_PLANE_OBJECTS_CMD[@]}" entry-path --family acceptance_state --entry ingress_boundary_evidence)}"
CONTROL_PLANE_OFFICIAL_DIR="${CONTROL_PLANE_OFFICIAL_DIR:-$HOST_CONTROL_PLANE_DIR/setup/official_cli}"
RUNTIME_ACCEPTANCE_OUTPUT="${RUNTIME_ACCEPTANCE_OUTPUT:-$("${CONTROL_PLANE_OBJECTS_CMD[@]}" entry-path --family runtime_evidence --entry runtime_acceptance)}"
CONTROL_PLANE_SUMMARY_OUTPUT="${CONTROL_PLANE_SUMMARY_OUTPUT:-$("${CONTROL_PLANE_OBJECTS_CMD[@]}" entry-path --family runtime_evidence --entry official_cli_control_plane)}"
CONTROL_PLANE_RUN_LEDGER_OUTPUT="${CONTROL_PLANE_RUN_LEDGER_OUTPUT:-$("${CONTROL_PLANE_OBJECTS_CMD[@]}" entry-path --family runtime_evidence --entry control_plane_run_ledger)}"
CONTROL_PLANE_AGENT_ACCESS_LOG_OUTPUT="${CONTROL_PLANE_AGENT_ACCESS_LOG_OUTPUT:-$("${CONTROL_PLANE_OBJECTS_CMD[@]}" entry-path --family runtime_evidence --entry control_plane_agent_access_log)}"
CONTROL_PLANE_AGENT_GROUP_ACCESS_OUTPUT="${CONTROL_PLANE_AGENT_GROUP_ACCESS_OUTPUT:-$("${CONTROL_PLANE_OBJECTS_CMD[@]}" entry-path --family runtime_evidence --entry control_plane_agent_group_access)}"
CONTROL_PLANE_AGENT_GROUP_ACCEPTANCE_BINDINGS_OUTPUT="${CONTROL_PLANE_AGENT_GROUP_ACCEPTANCE_BINDINGS_OUTPUT:-$("${CONTROL_PLANE_OBJECTS_CMD[@]}" entry-path --family runtime_evidence --entry control_plane_agent_group_acceptance_bindings)}"
CONTROL_PLANE_AGENT_GROUP_RELEASE_GATES_OUTPUT="${CONTROL_PLANE_AGENT_GROUP_RELEASE_GATES_OUTPUT:-$("${CONTROL_PLANE_OBJECTS_CMD[@]}" entry-path --family runtime_evidence --entry control_plane_agent_group_release_gates)}"
CONTROL_PLANE_ARTIFACT_POLICIES_OUTPUT="${CONTROL_PLANE_ARTIFACT_POLICIES_OUTPUT:-$("${CONTROL_PLANE_OBJECTS_CMD[@]}" entry-path --family runtime_evidence --entry control_plane_job_artifact_policies)}"
DISPATCH_RUNTIME_CHECK_OUTPUT="${DISPATCH_RUNTIME_CHECK_OUTPUT:-$("${CONTROL_PLANE_OBJECTS_CMD[@]}" entry-path --family runtime_evidence --entry dispatch_runtime_check)}"
OUTPUT_DIR="${OUTPUT_DIR:-$(dirname "$RUNTIME_ACCEPTANCE_OUTPUT")}"
SHADOW_VERIFY_STATE_DIR="${SHADOW_VERIFY_STATE_DIR:-$HOST_CONTROL_PLANE_DIR/setup/shadow_verify}"
SHADOW_VERIFY_OUTPUT_JSON="${SHADOW_VERIFY_OUTPUT_JSON:-$("${CONTROL_PLANE_OBJECTS_CMD[@]}" entry-path --family runtime_evidence --entry shadow_verify_summary_json)}"
SHADOW_VERIFY_OUTPUT_MD="${SHADOW_VERIFY_OUTPUT_MD:-$("${CONTROL_PLANE_OBJECTS_CMD[@]}" entry-path --family runtime_evidence --entry shadow_verify_summary_md)}"
SHADOW_COMPARE_OUTPUT_JSON="${SHADOW_COMPARE_OUTPUT_JSON:-$("${CONTROL_PLANE_OBJECTS_CMD[@]}" entry-path --family runtime_evidence --entry shadow_verify_compare_json)}"
SHADOW_COMPARE_OUTPUT_MD="${SHADOW_COMPARE_OUTPUT_MD:-$("${CONTROL_PLANE_OBJECTS_CMD[@]}" entry-path --family runtime_evidence --entry shadow_verify_compare_md)}"
ACCEPTANCE_SURFACE_CMD=("$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh" runtime acceptance)

fail() {
  echo "[export_runtime_acceptance_evidence][FAIL] $1" >&2
  exit "${2:-2}"
}

require_dir_manageable_or_creatable() {
  local path="$1"
  local label="$2"
  if [[ -d "$path" ]]; then
    [[ -r "$path" && -w "$path" && -x "$path" ]] || fail "$label 缺少读取/写入/执行权限：$path；当前脚本不会自动 sudo、提权或 chown，请先修正宿主机权限。" 4
    return 0
  fi
  local parent
  parent="$(dirname "$path")"
  [[ -d "$parent" ]] || fail "$label 的父目录不存在：$parent；当前脚本不会自动补建越级路径。" 4
  [[ -r "$parent" && -w "$parent" && -x "$parent" ]] || fail "$label 的父目录不可写：$parent；当前脚本不会自动 sudo、提权或 chown，请先修正宿主机权限。" 4
}

require_file_manageable_or_creatable() {
  local path="$1"
  local label="$2"
  if [[ -e "$path" ]]; then
    [[ -f "$path" ]] || fail "$label 不是常规文件：$path" 4
    [[ -r "$path" && -w "$path" ]] || fail "$label 缺少读取/写入权限：$path；当前脚本不会自动 sudo、提权或 chown，请先修正宿主机权限。" 4
    return 0
  fi
  local parent
  parent="$(dirname "$path")"
  [[ -d "$parent" ]] || fail "$label 的父目录不存在：$parent；当前脚本不会自动补建越级路径。" 4
  [[ -r "$parent" && -w "$parent" && -x "$parent" ]] || fail "$label 的父目录不可写：$parent；当前脚本不会自动 sudo、提权或 chown，请先修正宿主机权限。" 4
}

require_json_flag_true() {
  local path="$1"
  local jq_expr="$2"
  local label="$3"
  [[ -f "$path" ]] || fail "缺少 $label：$path" 3
  jq -e "$jq_expr" "$path" >/dev/null || fail "$label 未通过硬门禁：$path" 5
}

bash "$ROOT_DIR/scripts/runtime/check_runtime_evidence_prereqs.sh" --scope evidence-export --env-file "$ROOT_DIR/deploy/.env"
[[ -f "$DEPLOYMENT_ACCEPTANCE_STATE" ]] || fail "缺少部署验收状态文件：$DEPLOYMENT_ACCEPTANCE_STATE" 3
[[ -f "$INGRESS_BOUNDARY_EVIDENCE_STATE" ]] || fail "缺少 ingress 边界证据文件：$INGRESS_BOUNDARY_EVIDENCE_STATE" 3
[[ -d "$CONTROL_PLANE_OFFICIAL_DIR" ]] || fail "缺少官方 CLI 产物目录：$CONTROL_PLANE_OFFICIAL_DIR" 3
require_json_flag_true "$DEPLOYMENT_ACCEPTANCE_STATE" ".accepted == true" "deployment acceptance 状态文件"
require_json_flag_true "$INGRESS_BOUNDARY_EVIDENCE_STATE" \
  '.accepted == true and .nginx_policy.required == true and .nginx_policy.checked == true and .nginx_policy.ok == true and .nginx_policy.default_deny == true and .nginx_policy.rewrite_phase_default_deny == true and .nginx_policy.access_phase_default_deny == true and ((.nginx_policy.source_cidrs // []) | length > 0)' \
  "ingress 边界证据文件"
require_dir_manageable_or_creatable "$OUTPUT_DIR" "control-plane state release/evidence 输出目录"
mkdir -p "$OUTPUT_DIR"
require_file_manageable_or_creatable "$RUNTIME_ACCEPTANCE_OUTPUT" "runtime acceptance 证据文件"
require_file_manageable_or_creatable "$CONTROL_PLANE_SUMMARY_OUTPUT" "control-plane 官方 CLI 摘要文件"
require_file_manageable_or_creatable "$CONTROL_PLANE_RUN_LEDGER_OUTPUT" "control-plane run ledger 摘要文件"
require_file_manageable_or_creatable "$CONTROL_PLANE_AGENT_ACCESS_LOG_OUTPUT" "control-plane agent access log 摘要文件"
require_file_manageable_or_creatable "$CONTROL_PLANE_AGENT_GROUP_ACCESS_OUTPUT" "control-plane agent group access 摘要文件"
require_file_manageable_or_creatable "$CONTROL_PLANE_AGENT_GROUP_ACCEPTANCE_BINDINGS_OUTPUT" "control-plane agent group acceptance binding 摘要文件"
require_file_manageable_or_creatable "$CONTROL_PLANE_AGENT_GROUP_RELEASE_GATES_OUTPUT" "control-plane agent group release gate 摘要文件"
require_file_manageable_or_creatable "$CONTROL_PLANE_ARTIFACT_POLICIES_OUTPUT" "control-plane artifact policy 摘要文件"
require_file_manageable_or_creatable "$DISPATCH_RUNTIME_CHECK_OUTPUT" "dispatch runtime 证据文件"

if [[ -f "$SHADOW_VERIFY_STATE_DIR/summary.json" ]]; then
  require_file_manageable_or_creatable "$SHADOW_VERIFY_OUTPUT_JSON" "shadow verify 摘要文件"
fi
if [[ -f "$SHADOW_VERIFY_STATE_DIR/summary.md" ]]; then
  require_file_manageable_or_creatable "$SHADOW_VERIFY_OUTPUT_MD" "shadow verify Markdown 摘要文件"
fi
if [[ -f "$SHADOW_VERIFY_STATE_DIR/compare.json" ]]; then
  require_file_manageable_or_creatable "$SHADOW_COMPARE_OUTPUT_JSON" "shadow verify compare 摘要文件"
fi
if [[ -f "$SHADOW_VERIFY_STATE_DIR/compare.md" ]]; then
  require_file_manageable_or_creatable "$SHADOW_COMPARE_OUTPUT_MD" "shadow verify compare Markdown 摘要文件"
fi

mkdir -p "$HOST_CONTROL_PLANE_DIR/tmp"
tmp_dir="$(mktemp -d "$HOST_CONTROL_PLANE_DIR/tmp/runtime-acceptance.XXXXXX")"
trap 'rm -rf "$tmp_dir"' EXIT
CONTROL_PLANE_RUNTIME_SUMMARY_TMP="$tmp_dir/control-plane-runtime-summary.json"

bash "$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh" control-plane evidence export-agent-group-evidence >/dev/null
bash "$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh" control-plane runtime run-ledger-summary >"$CONTROL_PLANE_RUN_LEDGER_OUTPUT"
bash "$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh" control-plane evidence agent-access-log --limit 200 >"$CONTROL_PLANE_AGENT_ACCESS_LOG_OUTPUT"
bash "$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh" control-plane evidence agent-group-access --limit 200 --timeline-limit 20 >"$CONTROL_PLANE_AGENT_GROUP_ACCESS_OUTPUT"
bash "$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh" control-plane evidence agent-group-acceptance-bindings >"$CONTROL_PLANE_AGENT_GROUP_ACCEPTANCE_BINDINGS_OUTPUT"
bash "$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh" control-plane evidence agent-group-release-gates >"$CONTROL_PLANE_AGENT_GROUP_RELEASE_GATES_OUTPUT"
bash "$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh" control-plane artifacts json >"$CONTROL_PLANE_ARTIFACT_POLICIES_OUTPUT"
bash "$ROOT_DIR/scripts/doctor/check_control_plane_runtime.sh" | jq '.api.summary' >"$CONTROL_PLANE_RUNTIME_SUMMARY_TMP"
bash "$ROOT_DIR/scripts/doctor/check_dispatch_runtime.sh" --json >"$DISPATCH_RUNTIME_CHECK_OUTPUT"
"${ACCEPTANCE_SURFACE_CMD[@]}" write-official-cli-summary --official-dir "$CONTROL_PLANE_OFFICIAL_DIR" --out "$CONTROL_PLANE_SUMMARY_OUTPUT" --target gateway
"${ACCEPTANCE_SURFACE_CMD[@]}" write-runtime-acceptance-summary --acceptance-state "$DEPLOYMENT_ACCEPTANCE_STATE" --control-plane-summary "$CONTROL_PLANE_SUMMARY_OUTPUT" --control-plane-runtime-summary "$CONTROL_PLANE_RUNTIME_SUMMARY_TMP" --control-plane-run-ledger "$CONTROL_PLANE_RUN_LEDGER_OUTPUT" --out "$RUNTIME_ACCEPTANCE_OUTPUT"
require_json_flag_true "$RUNTIME_ACCEPTANCE_OUTPUT" ".accepted == true" "runtime acceptance 证据文件"
"${ACCEPTANCE_SURFACE_CMD[@]}" acceptance-summary >/dev/null

if [[ -f "$SHADOW_VERIFY_STATE_DIR/summary.json" ]]; then
  cp "$SHADOW_VERIFY_STATE_DIR/summary.json" "$SHADOW_VERIFY_OUTPUT_JSON"
  echo "[export_runtime_acceptance_evidence] 已同步：$SHADOW_VERIFY_OUTPUT_JSON"
fi
if [[ -f "$SHADOW_VERIFY_STATE_DIR/summary.md" ]]; then
  cp "$SHADOW_VERIFY_STATE_DIR/summary.md" "$SHADOW_VERIFY_OUTPUT_MD"
  echo "[export_runtime_acceptance_evidence] 已同步：$SHADOW_VERIFY_OUTPUT_MD"
fi
if [[ -f "$SHADOW_VERIFY_STATE_DIR/compare.json" ]]; then
  cp "$SHADOW_VERIFY_STATE_DIR/compare.json" "$SHADOW_COMPARE_OUTPUT_JSON"
  echo "[export_runtime_acceptance_evidence] 已同步：$SHADOW_COMPARE_OUTPUT_JSON"
fi
if [[ -f "$SHADOW_VERIFY_STATE_DIR/compare.md" ]]; then
  cp "$SHADOW_VERIFY_STATE_DIR/compare.md" "$SHADOW_COMPARE_OUTPUT_MD"
  echo "[export_runtime_acceptance_evidence] 已同步：$SHADOW_COMPARE_OUTPUT_MD"
fi

echo "[export_runtime_acceptance_evidence] 已生成：$CONTROL_PLANE_RUN_LEDGER_OUTPUT"
echo "[export_runtime_acceptance_evidence] 已生成：$CONTROL_PLANE_AGENT_ACCESS_LOG_OUTPUT"
echo "[export_runtime_acceptance_evidence] 已生成：$CONTROL_PLANE_AGENT_GROUP_ACCESS_OUTPUT"
echo "[export_runtime_acceptance_evidence] 已生成：$CONTROL_PLANE_AGENT_GROUP_ACCEPTANCE_BINDINGS_OUTPUT"
echo "[export_runtime_acceptance_evidence] 已生成：$CONTROL_PLANE_AGENT_GROUP_RELEASE_GATES_OUTPUT"
echo "[export_runtime_acceptance_evidence] 已生成：$CONTROL_PLANE_ARTIFACT_POLICIES_OUTPUT"
echo "[export_runtime_acceptance_evidence] 已生成：$CONTROL_PLANE_SUMMARY_OUTPUT"
echo "[export_runtime_acceptance_evidence] 已生成：$DISPATCH_RUNTIME_CHECK_OUTPUT"
echo "[export_runtime_acceptance_evidence] 已生成：$RUNTIME_ACCEPTANCE_OUTPUT"
