#!/usr/bin/env bash
# 用途：读取 extension-aware 的 one_click_test_full 检查组真源，并驱动脚本检查与 follow-up 动作。
set -euo pipefail

FULL_TEST_GROUP_REGISTRY_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=repo_root_bootstrap.sh
source "$FULL_TEST_GROUP_REGISTRY_LIB_DIR/repo_root_bootstrap.sh"
openclaw_setup_lib_source_repo_root "$FULL_TEST_GROUP_REGISTRY_LIB_DIR" || return 2 2>/dev/null || exit 2
unset -f openclaw_setup_lib_source_repo_root
FULL_TEST_GROUP_REGISTRY_ROOT="$(openclaw_repo_root_from "$FULL_TEST_GROUP_REGISTRY_LIB_DIR")"
# shellcheck source=scripts/lib/repo_python_env.sh
source "$FULL_TEST_GROUP_REGISTRY_ROOT/scripts/lib/repo_python_env.sh"
# shellcheck source=scripts/lib/control_plane_config_paths.sh
source "$FULL_TEST_GROUP_REGISTRY_ROOT/scripts/lib/control_plane_config_paths.sh"
# shellcheck source=scripts/setup/lib/ingress_boundary_evidence_cache.sh
source "$FULL_TEST_GROUP_REGISTRY_ROOT/scripts/setup/lib/ingress_boundary_evidence_cache.sh"
unset FULL_TEST_GROUP_REGISTRY_LIB_DIR

FULL_TEST_GROUP_REGISTRY_JSON_CACHE=""

# 检测当前环境是否提供 jq。
full_test_group_registry_jq_available() {
  command -v jq >/dev/null 2>&1
}

# 渲染 extension-aware 的 full_test_group_registry 真源 JSON。
full_test_group_registry_render_json() {
  local config_path=""
  local python_runner="${PYTHON_RUNNER:-$FULL_TEST_GROUP_REGISTRY_ROOT/scripts/runtime/run_python_container.sh}"
  local item=''
  local -a repo_python_env_args=()
  config_path="$(openclaw_control_plane_resolve_config_path agent_platform)" || return 1
  while IFS= read -r -d '' item; do
    repo_python_env_args+=("$item")
  done < <(openclaw_repo_python_env_args "$FULL_TEST_GROUP_REGISTRY_ROOT")
  OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH="$config_path" \
    bash "$python_runner" \
      --workdir "$FULL_TEST_GROUP_REGISTRY_ROOT" \
      "${repo_python_env_args[@]}" \
      --env "OPENCLAW_REPO_ROOT=$FULL_TEST_GROUP_REGISTRY_ROOT" \
      --env "OPENCLAW_TOOLS_ROOT=$FULL_TEST_GROUP_REGISTRY_ROOT" \
      --env "OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH=$config_path" \
      -- -m openclaw.setup.surface.governance_cli full-test-group-registry --config-path "$config_path"
}

# 返回当前 one_click_test_full 进程内缓存的 full_test_group_registry 真源 JSON。
full_test_group_registry_cached_json() {
  local rendered_json=''
  if [[ -z "$FULL_TEST_GROUP_REGISTRY_JSON_CACHE" ]]; then
    rendered_json="$(full_test_group_registry_render_json)" || return $?
    FULL_TEST_GROUP_REGISTRY_JSON_CACHE="$rendered_json"
  fi
  printf '%s\n' "$FULL_TEST_GROUP_REGISTRY_JSON_CACHE"
}

# 确认 full test 分组真源可以被正常解析。
full_test_group_registry_require_truth() {
  if ! full_test_group_registry_jq_available; then
    echo '[full_test_group_registry] 缺少 jq' >&2
    return 2
  fi
  if ! full_test_group_registry_cached_json >/dev/null; then
    echo '[full_test_group_registry] 无法渲染 full_test_group_registry 真源' >&2
    return 2
  fi
}

# 执行一条由真源声明的 shell 检查。
full_test_group_registry_run_shell_check() {
  local check_id="$1"
  local group="$2"
  local failure_status="$3"
  local failure_action="$4"
  local use_env="$5"
  local command="$6"
  local out=''

  should_run_check "$check_id" "$group" || return 0
  full_test_mark_check_started "$check_id"

  if [[ "$check_id" == 'ingress_boundary_evidence' ]] && ingress_boundary_cached_evidence_ok "$ROOT_DIR" "$ENV_FILE" 1; then
    record_pass "$check_id" "复用已落盘且与当前 deploy env 一致的 root 侧 ingress 边界证据，并已对当前 Nginx allowlist 做本地校验；当前用户无法直接读取宿主机防火墙语义时，必须先由 apply_ingress_boundary_rules.sh 或 root 侧 check_ingress_boundary_evidence.sh 写出基础证据。" "$group"
    return 0
  fi

  if [[ "$use_env" == 'with_env' ]]; then
    if run_and_capture_with_env out env ROOT_DIR="$ROOT_DIR" ENV_FILE="$ENV_FILE" OPENCLAW_PYTHON_TOOL="$OPENCLAW_PYTHON_TOOL" bash -lc "$command" </dev/null; then
      record_pass "$check_id" "$out" "$group"
    else
      if [[ "$check_id" == 'ingress_boundary_evidence' ]] && ingress_boundary_cached_evidence_ok "$ROOT_DIR" "$ENV_FILE" 1; then
        record_pass "$check_id" "复用已落盘且与当前 deploy env 一致的 root 侧 ingress 边界证据，并已对当前 Nginx allowlist 做本地校验；当前用户无法直接读取宿主机防火墙语义时，必须先由 apply_ingress_boundary_rules.sh 或 root 侧 check_ingress_boundary_evidence.sh 写出基础证据。" "$group"
        return 0
      fi
      if [[ "$failure_status" == 'warn' ]]; then
        record_warn "$check_id" "$out" "$group"
      else
        record_fail "$check_id" "$out" "$group"
      fi
      [[ -n "$failure_action" ]] && append_action "$failure_action"
    fi
    return 0
  fi

  if run_and_capture out env ROOT_DIR="$ROOT_DIR" ENV_FILE="$ENV_FILE" OPENCLAW_PYTHON_TOOL="$OPENCLAW_PYTHON_TOOL" bash -lc "$command" </dev/null; then
    record_pass "$check_id" "$out" "$group"
  else
    if [[ "$failure_status" == 'warn' ]]; then
      record_warn "$check_id" "$out" "$group"
    else
      record_fail "$check_id" "$out" "$group"
    fi
    [[ -n "$failure_action" ]] && append_action "$failure_action"
  fi
}

# 按分组批量执行真源声明的脚本检查。
full_test_group_registry_run_script_checks_for_group() {
  local group="$1"
  full_test_group_registry_require_truth
  local spec='' check_id='' failure_status='' failure_action='' use_env='' command=''
  local -a script_specs=()
  mapfile -t script_specs < <(full_test_group_registry_cached_json | jq -c --arg group "$group" '.groups[$group].script_checks[]?')
  for spec in "${script_specs[@]:-}"; do
    [[ -n "$spec" ]] || continue
    check_id="$(jq -r '.id' <<<"$spec")"
    failure_status="$(jq -r '.failure_status // "fail"' <<<"$spec")"
    failure_action="$(jq -r '.failure_action // ""' <<<"$spec")"
    use_env="$(jq -r '.use_env // "plain"' <<<"$spec")"
    command="$(jq -r '.command // ""' <<<"$spec")"
    [[ -n "$check_id" && -n "$command" ]] || {
      echo "[full_test_group_registry] $group 存在缺少 id/command 的检查定义" >&2
      return 2
    }
    full_test_group_registry_run_shell_check "$check_id" "$group" "$failure_status" "$failure_action" "$use_env" "$command"
  done
}

# 按分组执行入口脚本存在性检查。
full_test_group_registry_run_entrypoint_presence_checks_for_group() {
  local group="$1"
  full_test_group_registry_require_truth
  local spec='' check_id='' failure_action=''
  local -a entrypoint_specs=()
  mapfile -t entrypoint_specs < <(full_test_group_registry_cached_json | jq -c --arg group "$group" '.groups[$group].entrypoint_presence_checks[]?')
  for spec in "${entrypoint_specs[@]:-}"; do
    [[ -n "$spec" ]] || continue
    check_id="$(jq -r '.id' <<<"$spec")"
    failure_action="$(jq -r '.failure_action // ""' <<<"$spec")"
    mapfile -t files < <(jq -r '.files[]?' <<<"$spec")
    full_test_run_entrypoint_presence_check "$check_id" "$group" "$failure_action" "${files[@]}"
  done
}

# 判断 extension-aware full test group registry 是否声明了指定分组。
full_test_group_registry_has_group() {
  local group="$1"
  full_test_group_registry_require_truth
  full_test_group_registry_cached_json | jq -e --arg group "$group" '.groups[$group] != null' >/dev/null
}

# 执行任意由 full_test_group_registry 真源声明的分组。
full_test_group_registry_run_declared_group() {
  local group="$1"
  full_test_group_registry_run_entrypoint_presence_checks_for_group "$group"
  full_test_group_registry_run_script_checks_for_group "$group"
}

# 追加 dispatch 恢复动作建议。
full_test_append_dispatch_recovery_actions() {
  full_test_group_registry_require_truth
  local action=''
  while IFS= read -r action; do
    [[ -n "$action" ]] || continue
    append_action "$action"
  done < <(full_test_group_registry_cached_json | jq -r '.dispatch_recovery_actions[]?')
}

# 执行 service 分组的脚本检查。
full_test_group_registry_run_service_script_checks() {
  full_test_group_registry_run_script_checks_for_group service
}

# 当关键 service 检查失败时追加 official CLI follow-up 动作。
full_test_group_registry_append_official_cli_followups_if_failed() {
  full_test_group_registry_require_truth
  local check_id='' action=''
  for check_id in ingress_boundary_evidence official_openclaw_cli; do
    [[ "$(check_status_by_id "$check_id")" == 'FAIL' ]] || continue
    while IFS= read -r action; do
      [[ -n "$action" ]] || continue
      append_action "$action"
    done < <(full_test_group_registry_cached_json | jq -r --arg group service --arg checkId "$check_id" '.groups[$group].followups_if_failed[$checkId][]?')
  done
}

# 执行 dispatch 分组的脚本检查。
full_test_group_registry_run_dispatch_script_checks() {
  full_test_group_registry_run_script_checks_for_group dispatch
}

# 执行 dispatch target 分组的脚本检查。
full_test_group_registry_run_dispatch_target_script_checks() {
  full_test_group_registry_run_script_checks_for_group dispatch_targets
}

# 执行 pipeline 分组的入口脚本与脚本类检查。
full_test_group_registry_run_pipeline_checks() {
  full_test_group_registry_run_entrypoint_presence_checks_for_group pipeline
  full_test_group_registry_run_script_checks_for_group pipeline
}
