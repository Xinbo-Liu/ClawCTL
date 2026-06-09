#!/usr/bin/env bash
# 用途：统一执行部署后/服务启动后的全量测试，验证 service/dispatch/dispatch_targets/external/pipeline 是否真正可用。
# 说明：
# - 既可由 one_click_deploy.sh 自动调用，也可在服务已启动后独立执行 deployment acceptance；
# - 默认检查组顺序由 runtime.testing_manifest 真源派生；
# - full 测试允许 WARN，用于区分本地问题与外部系统波动。
set -Eeuo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
OPENCLAW_PYTHON_TOOL="$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh"
one_click_test_cp() {
  bash "$OPENCLAW_PYTHON_TOOL" setup flow one-click-test "$@"
}
prevalidate_value_args() {
  local -a args=("$@")
  local index=0
  local flag=''
  local message=''
  for (( index = 0; index < ${#args[@]}; index += 1 )); do
    flag="${args[$index]}"
    case "$flag" in
      --env-file)
        message='--env-file 缺少路径参数'
        ;;
      --group)
        message='--group 缺少检查组名称'
        ;;
      --only)
        message='--only 缺少检查项列表'
        ;;
      --skip)
        message='--skip 缺少检查项列表'
        ;;
      *)
        message=''
        ;;
    esac
    [[ -n "$message" ]] || continue
    if (( index + 1 >= ${#args[@]} )) || [[ "${args[$(( index + 1 ))]}" == --* ]]; then
      echo "[one_click_test_full][FAIL] $message" >&2
      exit 2
    fi
  done
}
prevalidate_value_args "$@"
# shellcheck source=scripts/lib/flow_entry_surface_shell.sh
source "$ROOT_DIR/scripts/lib/flow_entry_surface_shell.sh"
# shellcheck source=scripts/lib/flow_preflight_shell.sh
source "$ROOT_DIR/scripts/lib/flow_preflight_shell.sh"
# shellcheck source=scripts/setup/lib/setup_cli_common.sh
source "$ROOT_DIR/scripts/setup/lib/setup_cli_common.sh"
# shellcheck source=scripts/setup/lib/test_gate_common.sh
source "$ROOT_DIR/scripts/setup/lib/test_gate_common.sh"
# shellcheck source=scripts/runtime/runtime_container_lib.sh
source "$ROOT_DIR/scripts/runtime/runtime_container_lib.sh"
# shellcheck source=scripts/lib/run_summary_shell.sh
source "$ROOT_DIR/scripts/lib/run_summary_shell.sh"
# shellcheck source=scripts/setup/lib/full_test_env_shell.sh
source "$ROOT_DIR/scripts/setup/lib/full_test_env_shell.sh"
# shellcheck source=scripts/setup/lib/full_test_summary_shell.sh
source "$ROOT_DIR/scripts/setup/lib/full_test_summary_shell.sh"
# shellcheck source=scripts/setup/lib/full_test_group_runner.sh
source "$ROOT_DIR/scripts/setup/lib/full_test_group_runner.sh"
# shellcheck source=scripts/setup/lib/full_test_acceptance_shell.sh
source "$ROOT_DIR/scripts/setup/lib/full_test_acceptance_shell.sh"
DEFAULT_ENV_FILE=""

ENV_FILE=""
GROUP="all"
ONLY_RAW=""
SKIP_RAW=""
JSON_STDOUT=0
STRICT=0
QUIET=0
HELP_ONLY=0
EXPLAIN_ONLY=0
DEPLOYMENT_ACCEPTANCE_STATE_PATH=""
FULL_TEST_SURFACE_CMD=()
FULL_TEST_LOG_DIR=""
FULL_TEST_RUN_ID=""
FULL_TEST_SUMMARY_JSON_PATH=""
FULL_TEST_SUMMARY_MD_PATH=""

PASS_IDS=()
FAIL_IDS=()
WARN_IDS=()
SKIP_IDS=()
RESULT_LINES=()
NEXT_ACTIONS=()
GENERATED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
FULL_TEST_TMP_DIR="$ROOT_DIR/state/openclaw/control_plane/tmp"
RESULT_LINES_FILE=""
NEXT_ACTIONS_FILE=""
FINAL_EXIT_CODE=0
FULL_TEST_CURRENT_STAGE='control_plane_defaults'
FULL_TEST_SUMMARY_EMITTED=0
FULL_TEST_LAST_ERROR_MESSAGE=""
FULL_TEST_FAILURE_SUMMARY_ACTIVE=0

cleanup() {
  [[ -n "${RESULT_LINES_FILE:-}" ]] && rm -f "$RESULT_LINES_FILE"
  [[ -n "${NEXT_ACTIONS_FILE:-}" ]] && rm -f "$NEXT_ACTIONS_FILE"
  return 0
}
trap cleanup EXIT

full_test_prepare_tmp_files() {
  mkdir -p "$FULL_TEST_TMP_DIR"
  RESULT_LINES_FILE="$(mktemp "$FULL_TEST_TMP_DIR/one-click-full-results.XXXXXX")"
  NEXT_ACTIONS_FILE="$(mktemp "$FULL_TEST_TMP_DIR/one-click-full-actions.XXXXXX")"
}

usage() {
  one_click_test_full_static_help_text
}

explain() {
  one_click_test_full_static_explain_text
}

die() {
  local msg="$1" code="${2:-3}"
  echo "[one_click_test_full][FAIL] $msg" >&2
  FULL_TEST_LAST_ERROR_MESSAGE="$msg"
  if [[ "$FULL_TEST_FAILURE_SUMMARY_ACTIVE" == '1' ]]; then
    full_test_on_error "$code"
  fi
  exit "$code"
}


append_action() {
  setup_gate_append_unique_action NEXT_ACTIONS "$1"
}


record_pass() { full_test_record_pass "$@"; }
record_fail() { full_test_record_fail "$@"; }
record_warn() { full_test_record_warn "$@"; }
record_skip() { full_test_record_skip "$@"; }


run_and_capture() {
  setup_gate_run_and_capture "$@"
}


run_and_capture_with_env() {
  local __outvar="$1"
  shift
  setup_gate_run_and_capture_with_env "$__outvar" "$ENV_FILE" "$DEFAULT_ENV_FILE" "$@"
}


missing_required_keys() {
  setup_gate_missing_required_keys "$1"
}

csv_contains() {
  setup_gate_csv_contains "$1" "$2"
}

load_one_click_test_full_control_plane_defaults() {
  full_test_load_control_plane_defaults
}

run_selected_groups() {
  full_test_run_selected_groups
}

evaluate_deployment_acceptance() {
  full_test_evaluate_deployment_acceptance
}

should_run_group() {
  local group="$1"
  [[ "$GROUP" == "all" || "$GROUP" == "$group" ]]
}

validate_cli_filters() {
  full_test_validate_cli_filters
}

append_default_failure_followups() {
  local scenario='' line=''
  local -a scenarios=()

  has_failed_in_group() {
    local target_group="$1" line='' status='' check_id='' detail='' group=''
    for line in "${RESULT_LINES[@]:-}"; do
      IFS='|' read -r status check_id detail group <<<"$line"
      [[ "$status" == 'FAIL' && "$group" == "$target_group" ]] && return 0
    done
    return 1
  }

  if has_failed_in_group service; then
    scenarios+=(service_failed)
  fi
  if has_failed_in_group dispatch; then
    scenarios+=(dispatch_failed)
  fi
  if has_failed_in_group dispatch_targets; then
    scenarios+=(dispatch_targets_failed)
  fi
  if has_failed_in_group external; then
    scenarios+=(external_failed)
  fi
  if has_failed_in_group pipeline; then
    scenarios+=(pipeline_failed)
  fi
  if [[ "$(check_status_by_id deployment_acceptance_contract)" == 'FAIL' ]]; then
    scenarios+=(acceptance_contract_failed)
  fi

  for scenario in "${scenarios[@]:-}"; do
    while IFS= read -r line; do
      [[ -n "$line" ]] || continue
      append_action "$line"
    done < <(bash "$OPENCLAW_PYTHON_TOOL" setup surface failures show-next-steps --entry one_click_test_full --scenario "$scenario")
  done
}

should_run_check() {
  local id="$1" group="$2"
  if [[ -n "$ONLY_RAW" ]]; then
    csv_contains "$ONLY_RAW" "$id" || return 1
  fi
  if [[ -n "$SKIP_RAW" ]] && csv_contains "$SKIP_RAW" "$id"; then
    return 1
  fi
  should_run_group "$group"
}

load_env_values() {
  full_test_load_env_values "$@"
}

require_prereqs() {
  full_test_require_prereqs
}


check_status_by_id() {
  full_test_check_status_by_id "$1"
}


write_full_test_summary() {
  full_test_write_summary
}

after_full_test() {
  full_test_after_run
}



full_test_on_error() {
  local exit_code="$1"
  trap - ERR
  [[ "$FULL_TEST_SUMMARY_EMITTED" == '1' ]] && exit "$exit_code"
  FULL_TEST_SUMMARY_EMITTED=1
  FINAL_EXIT_CODE="$exit_code"
  if ((${#NEXT_ACTIONS[@]} == 0)); then
    full_test_append_preflight_actions
  fi
  if ! full_test_write_summary; then
    echo "[one_click_test_full][WARN] full test 控制面摘要不可用；请先恢复控制面摘要入口。" >&2
  fi
  if [[ "$QUIET" != '1' ]]; then
    if [[ -f "$FULL_TEST_SUMMARY_JSON_PATH" ]]; then
      "${FULL_TEST_SURFACE_CMD[@]}" print-summary --summary-json "$FULL_TEST_SUMMARY_JSON_PATH" || true
      flow_summary_emit_prefixed_note_paths "[one_click_test_full]" "${FULL_TEST_SUMMARY_MD_PATH#"$ROOT_DIR"/}" "${FULL_TEST_SUMMARY_JSON_PATH#"$ROOT_DIR"/}"
    fi
  fi
  if [[ "$JSON_STDOUT" == '1' && -f "$FULL_TEST_SUMMARY_JSON_PATH" ]]; then
    cat "$FULL_TEST_SUMMARY_JSON_PATH"
  fi
  exit "$exit_code"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      [[ $# -ge 2 ]] || die "--env-file 缺少路径参数" 2
      ENV_FILE="$2"; shift 2 ;;
    --group)
      [[ $# -ge 2 ]] || die "--group 缺少检查组名称" 2
      GROUP="$2"; shift 2 ;;
    --only)
      [[ $# -ge 2 ]] || die "--only 缺少检查项列表" 2
      ONLY_RAW="$2"; shift 2 ;;
    --skip)
      [[ $# -ge 2 ]] || die "--skip 缺少检查项列表" 2
      SKIP_RAW="$2"; shift 2 ;;
    --json) JSON_STDOUT=1; shift ;;
    --strict) STRICT=1; shift ;;
    --quiet) QUIET=1; shift ;;
    --explain) EXPLAIN_ONLY=1; shift ;;
    -h|--help) HELP_ONLY=1; shift ;;
    *)
      flow_entry_handle_unknown_arg "one_click_test_full" "$1" usage
      exit 3 ;;
  esac
done

if flow_entry_maybe_render_static_surface "$HELP_ONLY" "$EXPLAIN_ONLY" usage explain; then
  exit 0
fi

full_test_prepare_tmp_files
trap 'full_test_on_error $?' ERR

FULL_TEST_FAILURE_SUMMARY_ACTIVE=1
FULL_TEST_CURRENT_STAGE='control_plane_defaults'
full_test_preflight_and_load_control_plane_defaults

FULL_TEST_CURRENT_STAGE='cli_filter_validation'
validate_cli_filters
FULL_TEST_CURRENT_STAGE='prereqs'
require_prereqs
FULL_TEST_CURRENT_STAGE='group_execution'
run_selected_groups
FULL_TEST_CURRENT_STAGE='deployment_acceptance'
evaluate_deployment_acceptance

FINAL_EXIT_CODE="$(full_test_calculate_exit_code "$STRICT")"

if [[ "$FINAL_EXIT_CODE" != "0" ]]; then
  FULL_TEST_CURRENT_STAGE='post_failure_followups'
  append_default_failure_followups
fi

FULL_TEST_CURRENT_STAGE='summary_write'
after_full_test

trap - ERR
exit "$FINAL_EXIT_CODE"
