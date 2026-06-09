#!/usr/bin/env bash
# 用途：统一执行启动前只读门禁，验证配置完成度与 Docker 宿主机前提。
# 说明：
# - 只负责“是否具备进入 bootstrap / 部署阶段的基础条件”；
# - 不执行 bootstrap、runtime 产物检查、compose 渲染或 delivery_adapter 执行能力检查；
# - 支持 --offline，把宿主机检查切换到离线模式，跳过外部 DNS / HTTPS 探测。
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
one_click_test_basic_summary_command() {
  bash "$OPENCLAW_PYTHON_TOOL" setup flow basic-summary "$@"
}
prevalidate_image_archive_args() {
  local -a args=("$@")
  local has_offline=0
  local has_image_archive=0
  local index=0
  local arg=''
  for (( index = 0; index < ${#args[@]}; index += 1 )); do
    arg="${args[$index]}"
    case "$arg" in
      --offline)
        has_offline=1
        ;;
      --image-archive)
        has_image_archive=1
        if (( index + 1 >= ${#args[@]} )) || [[ "${args[$(( index + 1 ))]}" == --* ]]; then
          echo "[one_click_test_basic][FAIL] --image-archive 缺少路径参数" >&2
          exit 2
        fi
        ;;
      --image-archive=)
        echo "[one_click_test_basic][FAIL] --image-archive 缺少路径参数" >&2
        exit 2
        ;;
      --image-archive=*)
        has_image_archive=1
        ;;
    esac
  done
  if (( has_image_archive == 1 && has_offline == 0 )); then
    echo "[one_click_test_basic][FAIL] --image-archive 仅在 --offline 下有效" >&2
    exit 2
  fi
}
prevalidate_image_archive_args "$@"
# shellcheck source=scripts/lib/flow_summary_common_shell.sh
source "$ROOT_DIR/scripts/lib/flow_summary_common_shell.sh"
# shellcheck source=scripts/lib/flow_entry_surface_shell.sh
source "$ROOT_DIR/scripts/lib/flow_entry_surface_shell.sh"
# shellcheck source=scripts/lib/flow_preflight_shell.sh
source "$ROOT_DIR/scripts/lib/flow_preflight_shell.sh"
# shellcheck source=scripts/setup/lib/setup_cli_common.sh
source "$ROOT_DIR/scripts/setup/lib/setup_cli_common.sh"
# shellcheck source=scripts/setup/lib/host_install_defaults.sh
source "$ROOT_DIR/scripts/setup/lib/host_install_defaults.sh"
# shellcheck source=scripts/setup/lib/ingress_boundary_evidence_cache.sh
source "$ROOT_DIR/scripts/setup/lib/ingress_boundary_evidence_cache.sh"
# shellcheck source=scripts/setup/lib/test_gate_common.sh
source "$ROOT_DIR/scripts/setup/lib/test_gate_common.sh"
DEFAULT_ENV_FILE=""

ENV_FILE=""
OFFLINE_MODE=0
JSON_STDOUT=0
QUIET=0
HELP_ONLY=0
EXPLAIN_ONLY=0
IMAGE_ARCHIVE_PATH=""
CHECK_OPENCLAW_RELEASE_SCRIPT=""
RUN_RELEASE_CHECK=1
STRICT_RELEASE_CHECK="${OPENCLAW_STRICT_RELEASE_CHECK:-0}"
RELEASE_POLICY="relaxed_install"

PASS_IDS=()
FAIL_IDS=()
WARN_IDS=()
SKIP_IDS=()
RESULT_LINES=()
CONFIG_FAILURE=0
DOCKER_READY=-1
GENERATED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
FINAL_EXIT_CODE=0
BASIC_CURRENT_STAGE='control_plane_defaults'
BASIC_SUMMARY_EMITTED=0
RESULT_LINES_FILE=''

usage() {
  one_click_test_basic_static_help_text
}

explain() {
  one_click_test_basic_static_explain_text
}

log() {
  [[ "$QUIET" == "1" ]] && return 0
  echo "[one_click_test_basic] $*"
}

die() {
  local msg="$1"
  local code="${2:-3}"
  echo "[one_click_test_basic][FAIL] $msg" >&2
  exit "$code"
}

reject_root_runtime_user() {
  [[ "$(id -u)" != "0" ]] && return 0
  die "one_click_test_basic 拒绝以 root 执行部署前门禁；root 仅用于 prepare_docker_host、prepare_deploy_user、apply_ingress_boundary_rules、fix_permissions 等宿主机步骤。请切换到固定部署用户后重试；若当前保留 root SSH 会话，可执行：runuser -u openclaw -- bash -lc 'cd $ROOT_DIR && bash ./scripts/setup/one_click_test_basic.sh'。" 2
}

record_pass() { basic_test_record_pass "$@"; }
record_fail() { basic_test_record_fail "$@"; }
record_warn() { basic_test_record_warn "$@"; }
record_skip() { basic_test_record_skip "$@"; }

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

load_one_click_test_basic_control_plane_defaults() {
  DEFAULT_ENV_FILE="$ROOT_DIR/$(one_click_test_cp basic-env-file-path)"
  [[ -n "$ENV_FILE" ]] || ENV_FILE="$DEFAULT_ENV_FILE"
  CHECK_OPENCLAW_RELEASE_SCRIPT="$ROOT_DIR/$(one_click_test_cp basic-check-openclaw-release-script-path)"
}

basic_test_cleanup() {
  [[ -n "$RESULT_LINES_FILE" && -f "$RESULT_LINES_FILE" ]] && rm -f "$RESULT_LINES_FILE"
  return 0
}

basic_test_write_result_lines_file() {
  if [[ -z "$RESULT_LINES_FILE" ]]; then
    local tmp_dir="$ROOT_DIR/state/openclaw/control_plane/tmp"
    mkdir -p "$tmp_dir"
    RESULT_LINES_FILE="$(mktemp "$tmp_dir/one-click-basic-results.XXXXXX")"
  fi
  printf '%s\n' "${RESULT_LINES[@]+"${RESULT_LINES[@]}"}" >"$RESULT_LINES_FILE"
}

basic_test_result_lines_count() {
  [[ ${RESULT_LINES+x} ]] && printf '%s\n' "${#RESULT_LINES[@]}" || printf '0\n'
}

basic_test_emit_surface_summary() {
  local format="$1"
  local exit_code="$2"
  local stage="${3:-}"
  local -a args=(
    summary
    --format "$format"
    --generated-at "$GENERATED_AT"
    --env-file "$ENV_FILE"
    --offline "$OFFLINE_MODE"
    --return-code "$exit_code"
    --release-check "$RUN_RELEASE_CHECK"
    --release-policy "$RELEASE_POLICY"
  )
  if [[ -n "$IMAGE_ARCHIVE_PATH" ]]; then
    args+=(--image-archive-path "$IMAGE_ARCHIVE_PATH")
  fi
  if [[ -n "$RESULT_LINES_FILE" && -f "$RESULT_LINES_FILE" && -s "$RESULT_LINES_FILE" ]]; then
    args+=(--result-lines-file "$RESULT_LINES_FILE")
  else
    args+=(--failed-stage "$stage" --failure-detail 'Docker / 控制面 / 静态前置未闭合，basic gate 在正式检查组前提前失败。')
  fi
  one_click_test_basic_summary_command "${args[@]}"
}

basic_test_write_gate_proof() {
  local -a args=(
    write-proof
    --format json
    --generated-at "$GENERATED_AT"
    --env-file "$ENV_FILE"
    --offline "$OFFLINE_MODE"
    --return-code "$FINAL_EXIT_CODE"
    --release-check "$RUN_RELEASE_CHECK"
    --release-policy "$RELEASE_POLICY"
  )
  if [[ -n "$IMAGE_ARCHIVE_PATH" ]]; then
    args+=(--image-archive-path "$IMAGE_ARCHIVE_PATH")
  fi
  if [[ -n "$RESULT_LINES_FILE" && -f "$RESULT_LINES_FILE" && -s "$RESULT_LINES_FILE" ]]; then
    args+=(--result-lines-file "$RESULT_LINES_FILE")
  fi
  one_click_test_basic_summary_command "${args[@]}"
}

basic_test_on_error() {
  local exit_code="$1"
  local helper_output=''
  trap - ERR
  [[ "$BASIC_SUMMARY_EMITTED" == '1' ]] && exit "$exit_code"
  BASIC_SUMMARY_EMITTED=1
  FINAL_EXIT_CODE="$exit_code"
  if (($(basic_test_result_lines_count) > 0)); then
    basic_test_write_result_lines_file
  fi
  if [[ "$QUIET" != '1' ]]; then
    if ! run_and_capture helper_output basic_test_emit_surface_summary text "$exit_code" "$BASIC_CURRENT_STAGE"; then
      [[ -n "$helper_output" ]] && echo "$helper_output" >&2
    else
      printf '%s\n' "$helper_output"
    fi
  fi
  if [[ "$JSON_STDOUT" == '1' ]]; then
    if ! run_and_capture helper_output basic_test_emit_surface_summary json "$exit_code" "$BASIC_CURRENT_STAGE"; then
      [[ -n "$helper_output" ]] && echo "$helper_output" >&2
    else
      printf '%s\n' "$helper_output"
    fi
  fi
  exit "$exit_code"
}

main() {
while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      [[ $# -ge 2 ]] || die "--env-file 缺少路径参数" 2
      ENV_FILE="$2"; shift 2 ;;
    --offline)
      OFFLINE_MODE=1; shift ;;
    --image-archive)
      [[ $# -ge 2 ]] || die "--image-archive 缺少路径参数" 2
      IMAGE_ARCHIVE_PATH="$2"; shift 2 ;;
    --skip-release-check)
      RUN_RELEASE_CHECK=0; RELEASE_POLICY="skipped"; shift ;;
    --strict-release-check)
      RUN_RELEASE_CHECK=1; STRICT_RELEASE_CHECK=1; RELEASE_POLICY="strict_release"; shift ;;
    --json)
      JSON_STDOUT=1; shift ;;
    --quiet)
      QUIET=1; shift ;;
    --explain)
      EXPLAIN_ONLY=1; shift ;;
    -h|--help)
      HELP_ONLY=1; shift ;;
    *)
      flow_entry_handle_unknown_arg "one_click_test_basic" "$1" usage
      exit 3 ;;
  esac
done

if flow_entry_maybe_render_static_surface "$HELP_ONLY" "$EXPLAIN_ONLY" usage explain; then
  exit 0
fi

if [[ -n "$IMAGE_ARCHIVE_PATH" && "$OFFLINE_MODE" != "1" ]]; then
  die "--image-archive 仅在 --offline 下有效；在线 basic gate 不读取离线归档，请移除该参数或追加 --offline。" 2
fi

if [[ "$RUN_RELEASE_CHECK" == "1" ]]; then
  if [[ "$STRICT_RELEASE_CHECK" == "1" ]]; then
    RELEASE_POLICY="strict_release"
  else
    RELEASE_POLICY="relaxed_install"
  fi
fi

reject_root_runtime_user

trap 'basic_test_on_error $?' ERR
trap basic_test_cleanup EXIT

BASIC_CURRENT_STAGE='control_plane_defaults'
flow_preflight_run_and_load load_one_click_test_basic_control_plane_defaults "$OPENCLAW_PYTHON_TOOL" setup flow one-click-test preflight-basic

check_env_exists() {
  if [[ -f "$ENV_FILE" ]]; then
    record_pass "env_file_exists" "$ENV_FILE" "config"
  else
    record_fail "env_file_exists" "配置文件不存在：$ENV_FILE" "config"
    CONFIG_FAILURE=1
  fi
}

check_required_placeholders() {
  [[ -f "$ENV_FILE" ]] || { record_skip "env_required_placeholders" "env 文件不存在，跳过 __REQUIRED__ 检查" "config"; return; }
  local missing_output=''
  local missing_detail=''
  missing_output="$(missing_required_keys "$ENV_FILE")"

  if [[ -n "$missing_output" ]]; then
    missing_detail="$(printf '%s\n' "$missing_output" | sed 's/^/  - /')"
    record_fail "env_required_placeholders" "$(printf '配置文件中仍存在未完成项:\n%s' "$missing_detail")" "config"
    CONFIG_FAILURE=1
  else
    record_pass "env_required_placeholders" "" "config"
  fi
}

check_verify_required_env() {
  [[ -f "$ENV_FILE" ]] || { record_skip "verify_required_deploy_env" "env 文件不存在，跳过" "config"; return; }
  local out=""
  if run_and_capture out bash "$OPENCLAW_PYTHON_TOOL" setup env validate --env-file "$ENV_FILE"; then
    record_pass "verify_required_deploy_env" "" "config"
  else
    record_fail "verify_required_deploy_env" "$out" "config"
    CONFIG_FAILURE=1
  fi
}

check_deployment_image_contract() {
  if [[ "$CONFIG_FAILURE" == "1" ]]; then
    record_skip "deploy_image_coverage" "配置阶段未通过，跳过部署镜像覆盖合同检查" "config"
    return
  fi
  local out=""
  if run_and_capture out bash "$ROOT_DIR/scripts/images/check_deployment_image_contract.sh" --env-file "$ENV_FILE"; then
    record_pass "deploy_image_coverage" "" "config"
  else
    record_fail "deploy_image_coverage" "$out" "config"
    CONFIG_FAILURE=1
  fi
}

check_runtime_compose_contract() {
  if [[ "$CONFIG_FAILURE" == "1" ]]; then
    record_skip "runtime_compose_contract" "配置阶段未通过，跳过 compose 合同检查" "config"
    return
  fi
  local out=""
  if run_and_capture out bash "$ROOT_DIR/scripts/doctor/check_runtime_compose_contract.sh" --env-file "$ENV_FILE"; then
    record_pass "runtime_compose_contract" "" "config"
  else
    record_fail "runtime_compose_contract" "$out" "config"
    CONFIG_FAILURE=1
  fi
}

check_ingress_boundary_evidence_preflight() {
  if [[ "$CONFIG_FAILURE" == "1" ]]; then
    record_skip "ingress_boundary_evidence_preflight" "配置阶段未通过，跳过 ingress 边界语义预检" "config"
    return
  fi
  if [[ "$DOCKER_READY" != "1" ]]; then
    record_skip "ingress_boundary_evidence_preflight" "Docker 宿主机前提未通过，跳过 ingress 边界语义预检" "host"
    return
  fi
  local out=""
  if ingress_boundary_cached_evidence_ok "$ROOT_DIR" "$ENV_FILE" 0 "$(host_install_defaults_state_root_default)"; then
    record_pass "ingress_boundary_evidence_preflight" "复用已落盘且与当前 deploy env 一致的 ingress 边界证据；当前用户无法直接读取宿主机防火墙语义时，必须先由 apply_ingress_boundary_rules.sh 完成 root 侧物化与证据写出。" "config"
  elif run_and_capture out bash "$ROOT_DIR/scripts/doctor/check_ingress_boundary_evidence.sh" --env-file "$ENV_FILE" --no-write; then
    record_pass "ingress_boundary_evidence_preflight" "" "config"
  elif ingress_boundary_cached_evidence_ok "$ROOT_DIR" "$ENV_FILE" 0 "$(host_install_defaults_state_root_default)"; then
    record_pass "ingress_boundary_evidence_preflight" "复用已落盘且与当前 deploy env 一致的 ingress 边界证据；当前用户无法直接读取宿主机防火墙语义时，必须先由 apply_ingress_boundary_rules.sh 完成 root 侧物化与证据写出。" "config"
  else
    record_fail "ingress_boundary_evidence_preflight" "$out" "config"
    CONFIG_FAILURE=1
  fi
}

check_local_runtime_fs_contract() {
  if [[ "$CONFIG_FAILURE" == "1" ]]; then
    record_skip "local_runtime_fs_contract" "配置阶段未通过，跳过本地文件系统合同检查" "host"
    return
  fi
  local out="" args=(
    bash "$ROOT_DIR/scripts/doctor/check_local_runtime_fs_contract.sh"
    --env-file "$ENV_FILE"
    --require-current-runtime-user
    --reject-root-runtime-user
  )
  if run_and_capture out "${args[@]}"; then
    if printf '%s\n' "$out" | grep -q '^\[WARN\]'; then
      record_warn "local_runtime_fs_contract" "$out" "host"
    else
      record_pass "local_runtime_fs_contract" "" "host"
    fi
  else
    record_fail "local_runtime_fs_contract" "$out" "host"
  fi
}

check_deployment_image_readiness() {
  if [[ "$CONFIG_FAILURE" == "1" ]]; then
    record_skip "deployment_image_readiness" "配置阶段未通过，跳过镜像就绪性检查" "host"
    return
  fi
  if [[ "$DOCKER_READY" != "1" ]]; then
    record_skip "deployment_image_readiness" "Docker 宿主机前提未通过，跳过镜像就绪性检查" "host"
    return
  fi
  local out="" args=(bash "$ROOT_DIR/scripts/doctor/check_deployment_image_readiness.sh" --env-file "$ENV_FILE")
  if [[ "$OFFLINE_MODE" == "1" ]]; then
    args+=(--offline)
    [[ -n "$IMAGE_ARCHIVE_PATH" ]] && args+=(--image-archive "$IMAGE_ARCHIVE_PATH")
  fi
  if run_and_capture out "${args[@]}"; then
    if printf '%s\n' "$out" | grep -q '^\[WARN\]'; then
      record_warn "deployment_image_readiness" "$out" "host"
    else
      record_pass "deployment_image_readiness" "" "host"
    fi
  else
    record_fail "deployment_image_readiness" "$out" "host"
  fi
}

check_docker_host() {
  if [[ "$CONFIG_FAILURE" == "1" ]]; then
    record_skip "check_docker_host_readiness" "配置阶段未通过，跳过 Docker 宿主机检查" "host"
    return
  fi

  local out="" args=(bash "$ROOT_DIR/scripts/doctor/check_docker_host_readiness.sh" --env-file "$ENV_FILE")
  if [[ "$OFFLINE_MODE" == "1" ]]; then
    args+=(--offline)
  fi

  if run_and_capture_with_env out "${args[@]}"; then
    DOCKER_READY=1
    local host_mode=""
    local detail=""
    host_mode="$(printf '%s\n' "$out" | grep -E '^\[INFO\] HOST_MODE=' | tail -n 1 | sed 's/^\[INFO\] HOST_MODE=//')"
    case "$host_mode" in
      supported_centos7)
        detail="当前宿主机以 CentOS 7 宿主机支持策略通过预检"
        ;;
      recommended)
        detail="当前宿主机以推荐基线通过预检"
        ;;
    esac
    record_pass "check_docker_host_readiness" "$detail" "host"
  else
    DOCKER_READY=0
    record_fail "check_docker_host_readiness" "$out" "host"
  fi
}


check_runtime_bind_user_contract() {
  if [[ "$CONFIG_FAILURE" == "1" ]]; then
    record_skip "runtime_bind_user_contract" "配置阶段未通过，跳过 runtime bind mount UID/GID 合同检查" "host"
    return
  fi
  if [[ "$DOCKER_READY" != "1" ]]; then
    record_skip "runtime_bind_user_contract" "Docker 宿主机前提未通过，跳过 runtime bind mount UID/GID 合同检查" "host"
    return
  fi
  local out="" args=(bash "$ROOT_DIR/scripts/doctor/check_runtime_bind_user_contract.sh" --env-file "$ENV_FILE")
  if run_and_capture out "${args[@]}"; then
    if printf '%s\n' "$out" | grep -q '^\[WARN\]'; then
      record_warn "runtime_bind_user_contract" "$out" "host"
    else
      record_pass "runtime_bind_user_contract" "" "host"
    fi
  else
    record_fail "runtime_bind_user_contract" "$out" "host"
  fi
}

check_openclaw_release_alignment() {
  if [[ "$CONFIG_FAILURE" == "1" ]]; then
    record_skip "openclaw_release_alignment" "配置阶段未通过，跳过版本对齐检查" "release"
    return
  fi
  if [[ "$RUN_RELEASE_CHECK" != "1" ]]; then
    record_skip "openclaw_release_alignment" "显式 --skip-release-check，跳过 OpenClaw 版本对齐联网检查" "release"
    return
  fi
  if [[ "$OFFLINE_MODE" == "1" ]]; then
    record_skip "openclaw_release_alignment" "离线模式跳过 OpenClaw 版本对齐联网检查" "release"
    return
  fi
  local out="" rc=0
  # release 对齐检查的非 0 返回码是业务结果，必须先入账为 FAIL，
  # 不能让 ERR trap 在摘要写出前截断，导致终端看到“无 FAIL 但返回失败”。
  if run_and_capture out env IMAGE_ENV_DEPLOY_ENV_PATH="$ENV_FILE" bash "$CHECK_OPENCLAW_RELEASE_SCRIPT"; then
    rc=0
  else
    rc=$?
  fi
  if [[ $rc -eq 0 ]]; then
    record_pass "openclaw_release_alignment" "$out" "release"
    return
  fi
  case "$rc" in
    10|12)
      if [[ "$STRICT_RELEASE_CHECK" == "1" ]]; then
        record_fail "openclaw_release_alignment" "$out" "release"
      else
        record_warn "openclaw_release_alignment" "$(printf '%s\n[release_policy] relaxed_install：当前 pin digest 可验证，upstream latest 更新不阻断首装；发布门禁或升级验证请使用 --strict-release-check。' "$out")" "release"
      fi
      ;;
    11)
      record_fail "openclaw_release_alignment" "$out" "release"
      ;;
    13)
      record_fail "openclaw_release_alignment" "$out" "release"
      ;;
    14)
      record_fail "openclaw_release_alignment" "$out" "release"
      ;;
    *)
      record_fail "openclaw_release_alignment" "$out" "release"
      ;;
  esac
  return 0
}

BASIC_CURRENT_STAGE='config_checks'
check_env_exists
check_required_placeholders
check_verify_required_env
check_deployment_image_contract
check_runtime_compose_contract

BASIC_CURRENT_STAGE='host_checks'
check_local_runtime_fs_contract
check_docker_host
check_ingress_boundary_evidence_preflight
check_deployment_image_readiness
check_runtime_bind_user_contract

BASIC_CURRENT_STAGE='release_checks'
check_openclaw_release_alignment

FINAL_EXIT_CODE="$(basic_test_calculate_exit_code "$CONFIG_FAILURE")"
basic_test_write_result_lines_file

if [[ "$FINAL_EXIT_CODE" == "0" ]]; then
  BASIC_CURRENT_STAGE='basic_gate_proof'
  proof_output=''
  if run_and_capture proof_output basic_test_write_gate_proof; then
    record_pass "basic_gate_proof" "$proof_output" "config"
  else
    record_fail "basic_gate_proof" "$proof_output" "config"
    CONFIG_FAILURE=1
    FINAL_EXIT_CODE=2
  fi
  basic_test_write_result_lines_file
fi

BASIC_CURRENT_STAGE='final_emit'
if [[ "$QUIET" != "1" ]]; then
  basic_summary_output=''
  if ! run_and_capture basic_summary_output basic_test_emit_surface_summary text "$FINAL_EXIT_CODE" "$BASIC_CURRENT_STAGE"; then
    [[ -n "$basic_summary_output" ]] && echo "$basic_summary_output" >&2
  else
    printf '%s\n' "$basic_summary_output"
  fi
fi

if [[ "$JSON_STDOUT" == "1" ]]; then
  basic_summary_output=''
  if ! run_and_capture basic_summary_output basic_test_emit_surface_summary json "$FINAL_EXIT_CODE" "$BASIC_CURRENT_STAGE"; then
    [[ -n "$basic_summary_output" ]] && echo "$basic_summary_output" >&2
  else
    printf '%s\n' "$basic_summary_output"
  fi
fi

trap - ERR
exit "$FINAL_EXIT_CODE"
}

main "$@"
