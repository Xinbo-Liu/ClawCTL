#!/usr/bin/env bash
# 用途：为 one_click_test_full 提供 env / prereq helper，避免主脚本继续内联环境解析与前置校验。
set -euo pipefail

FULL_TEST_ENV_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=repo_root_bootstrap.sh
source "$FULL_TEST_ENV_LIB_DIR/repo_root_bootstrap.sh"
openclaw_setup_lib_source_repo_root "$FULL_TEST_ENV_LIB_DIR" || return 2 2>/dev/null || exit 2
unset -f openclaw_setup_lib_source_repo_root
FULL_TEST_ENV_ROOT="$(openclaw_repo_root_from "$FULL_TEST_ENV_LIB_DIR")"
# shellcheck source=scripts/setup/lib/runtime_permissions.sh
source "$FULL_TEST_ENV_ROOT/scripts/setup/lib/runtime_permissions.sh"
# shellcheck source=scripts/lib/repo_contracts.sh
source "$FULL_TEST_ENV_ROOT/scripts/lib/repo_contracts.sh"
# shellcheck source=scripts/lib/control_plane_config_paths.sh
source "$FULL_TEST_ENV_ROOT/scripts/lib/control_plane_config_paths.sh"
# shellcheck source=scripts/setup/lib/extension_env_gate.sh
source "$FULL_TEST_ENV_ROOT/scripts/setup/lib/extension_env_gate.sh"
unset FULL_TEST_ENV_LIB_DIR FULL_TEST_ENV_ROOT

FULL_TEST_TESTING_MANIFEST_JSON_CACHE="${FULL_TEST_TESTING_MANIFEST_JSON_CACHE:-}"

full_test_manifest_path() {
  repo_contract_path runtime.testing_manifest
}

full_test_resolve_path_from_dir() {
  local base_dir="$1"
  local value="$2"
  local rel_dir='' rel_base='' extension_root=''
  [[ -n "$value" ]] || return 1
  if [[ "$value" == /* ]]; then
    printf '%s\n' "$value"
    return 0
  fi
  if [[ "$value" == @repo/* ]]; then
    full_test_resolve_path_from_dir "$ROOT_DIR" "${value#@repo/}"
    return $?
  fi
  if [[ "$value" == @extension/* ]]; then
    extension_root="$(full_test_resolve_extension_root_from_dir "$base_dir")" || return 1
    full_test_resolve_path_from_dir "$extension_root" "${value#@extension/}"
    return $?
  fi
  rel_dir="$(dirname "$value")"
  rel_base="$(basename "$value")"
  (
    cd "$base_dir/$rel_dir" 2>/dev/null || exit 1
    printf '%s/%s\n' "$(pwd -P)" "$rel_base"
  )
}

full_test_resolve_extension_root_from_dir() {
  local current="$1"
  local extensions_root="$ROOT_DIR/agent/extensions"
  current="$(cd "$current" 2>/dev/null && pwd -P)" || return 1
  extensions_root="$(cd "$extensions_root" 2>/dev/null && pwd -P)" || return 1
  while [[ "$current" == "$ROOT_DIR"* && "$current" != '/' ]]; do
    if [[ "$(dirname "$current")" == "$extensions_root" ]]; then
      printf '%s\n' "$current"
      return 0
    fi
    current="$(dirname "$current")"
  done
  return 1
}

full_test_config_declares_testing_manifest_fragments() {
  local config_path="$1"
  local config_dir='' enabled_ids='' raw_dir='' manifests_dir='' manifest='' extension_id='' fragment_path=''
  command -v jq >/dev/null 2>&1 || return 0
  [[ -f "$config_path" ]] || return 0
  config_dir="$(cd "$(dirname "$config_path")" && pwd -P)" || return 0
  enabled_ids="$(jq -r '.extensions.enabledExtensionIds[]? // empty' "$config_path" 2>/dev/null || true)"
  [[ -n "$enabled_ids" ]] || return 1
  while IFS= read -r raw_dir; do
    [[ -n "$raw_dir" ]] || continue
    manifests_dir="$(full_test_resolve_path_from_dir "$config_dir" "$raw_dir" 2>/dev/null || true)"
    [[ -n "$manifests_dir" && -d "$manifests_dir" ]] || continue
    for manifest in "$manifests_dir"/*.json; do
      [[ -f "$manifest" ]] || continue
      extension_id="$(jq -r '.id // empty' "$manifest" 2>/dev/null || true)"
      [[ -n "$extension_id" ]] || continue
      if ! grep -Fxq "$extension_id" <<< "$enabled_ids"; then
        continue
      fi
      fragment_path="$(jq -r '.surfaceFragments.testingManifestPath // empty' "$manifest" 2>/dev/null || true)"
      [[ -z "$fragment_path" ]] || return 0
    done
  done < <(jq -r '.extensions.manifestsDirs[]? // empty' "$config_path" 2>/dev/null || true)
  return 1
}

full_test_testing_manifest_fast_path_allowed() {
  command -v jq >/dev/null 2>&1 || return 1
  full_test_testing_manifest_json >/dev/null
}

full_test_active_config_path_for_manifest() {
  local selected="${OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH:-}"
  local profile="${OPENCLAW_CONTROL_PLANE_PROFILE:-agent_platform}"
  local explicit_profile=0
  [[ -n "${OPENCLAW_CONTROL_PLANE_PROFILE:-}" ]] && explicit_profile=1
  openclaw_control_plane_apply_env_file_active_selection \
    "${ENV_FILE:-}" \
    selected \
    profile \
    explicit_profile \
    0 \
    "${ENV_FILE:-}" || return $?
  if [[ -z "$selected" && "$explicit_profile" != '1' ]]; then
    return 3
  fi
  OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH="$selected" \
    OPENCLAW_CONTROL_PLANE_PROFILE="$profile" \
    openclaw_control_plane_resolve_config_path "$profile" "$selected" "$explicit_profile"
}

full_test_testing_manifest_fragment_paths() {
  local config_path="$1"
  local config_dir='' enabled_ids='' raw_dir='' manifests_dir='' manifest='' extension_id='' fragment_path='' resolved_fragment=''
  [[ -f "$config_path" ]] || return 0
  config_dir="$(cd "$(dirname "$config_path")" && pwd -P)" || return 0
  enabled_ids="$(jq -r '.extensions.enabledExtensionIds[]? // empty' "$config_path" 2>/dev/null || true)"
  [[ -n "$enabled_ids" ]] || return 0
  while IFS= read -r raw_dir; do
    [[ -n "$raw_dir" ]] || continue
    manifests_dir="$(full_test_resolve_path_from_dir "$config_dir" "$raw_dir" 2>/dev/null || true)"
    [[ -n "$manifests_dir" && -d "$manifests_dir" ]] || continue
    for manifest in "$manifests_dir"/*.json; do
      [[ -f "$manifest" ]] || continue
      extension_id="$(jq -r '.id // empty' "$manifest" 2>/dev/null || true)"
      [[ -n "$extension_id" ]] || continue
      if ! grep -Fxq "$extension_id" <<< "$enabled_ids"; then
        continue
      fi
      fragment_path="$(jq -r '.surfaceFragments.testingManifestPath // empty' "$manifest" 2>/dev/null || true)"
      [[ -n "$fragment_path" ]] || continue
      resolved_fragment="$(full_test_resolve_path_from_dir "$(dirname "$manifest")" "$fragment_path" 2>/dev/null || true)"
      [[ -n "$resolved_fragment" && -f "$resolved_fragment" ]] || continue
      printf '%s\n' "$resolved_fragment"
    done
  done < <(jq -r '.extensions.manifestsDirs[]? // empty' "$config_path" 2>/dev/null || true)
}

full_test_testing_manifest_json() {
  local base_path='' config_path='' manifest_json=''
  local config_status=0
  local -a fragment_paths=()
  if [[ -n "$FULL_TEST_TESTING_MANIFEST_JSON_CACHE" ]]; then
    printf '%s\n' "$FULL_TEST_TESTING_MANIFEST_JSON_CACHE"
    return 0
  fi
  command -v jq >/dev/null 2>&1 || return 1
  base_path="$(full_test_manifest_path)"
  [[ -f "$base_path" ]] || return 1
  set +e
  config_path="$(full_test_active_config_path_for_manifest)"
  config_status=$?
  set -e
  if [[ "$config_status" -eq 0 ]]; then
    mapfile -t fragment_paths < <(full_test_testing_manifest_fragment_paths "$config_path")
  elif [[ "$config_status" -ne 3 ]]; then
    return "$config_status"
  fi
  manifest_json="$(jq -s '
    def append_unique($left; $right):
      reduce ((($left // []) + ($right // []))[]) as $item
        ([]; if index($item) then . else . + [$item] end);
    def append_rows($left; $right):
      reduce ((($left // []) + ($right // []))[]) as $item
        ([]; if (($item | type) != "object") then .
             elif any(.[]; .id == $item.id) then .
             else . + [$item] end);
    reduce .[] as $item ({};
      .schema_version = (.schema_version // $item.schema_version)
      | .title = (.title // $item.title)
      | .paths = ((.paths // {}) * ($item.paths // {}))
      | .valid_groups = append_unique(.valid_groups; $item.valid_groups)
      | .groups = append_rows(.groups; $item.groups)
      | .checks = append_rows(.checks; $item.checks)
      | .release_gate_checks = append_rows(.release_gate_checks; $item.release_gate_checks)
      | .entrypoints = append_rows(.entrypoints; $item.entrypoints)
      | .summary_topics = append_unique(.summary_topics; $item.summary_topics)
      | .execution_order = append_unique(.execution_order; $item.execution_order)
      | .acceptance_contract = (.acceptance_contract // $item.acceptance_contract)
      | .acceptance_reference = ((.acceptance_reference // {}) * ($item.acceptance_reference // {}))
      | .acceptance_reference.required_checks = append_unique(.acceptance_reference.required_checks; (($item.acceptance_reference // {}).required_checks // []))
      | .acceptance_reference.required_run_ledger_jobs = append_unique(.acceptance_reference.required_run_ledger_jobs; (($item.acceptance_reference // {}).required_run_ledger_jobs // []))
      | .acceptance_reference.entrypoints = append_rows(.acceptance_reference.entrypoints; (($item.acceptance_reference // {}).entrypoints // []))
      | .acceptance_reference.artifacts = append_rows(.acceptance_reference.artifacts; (($item.acceptance_reference // {}).artifacts // []))
      | .acceptance_reference.scenarios = append_rows(.acceptance_reference.scenarios; (($item.acceptance_reference // {}).scenarios // []))
    )
  ' "$base_path" "${fragment_paths[@]}")" || return 1
  [[ -n "$manifest_json" ]] || return 1
  FULL_TEST_TESTING_MANIFEST_JSON_CACHE="$manifest_json"
  printf '%s\n' "$manifest_json"
}

full_test_manifest_normalize_check_csv() {
  local raw="$1"
  local flag_name="$2"
  local manifest_json='' known_ids='' item='' value='' normalized=''
  local -A seen=()
  [[ -n "$raw" ]] || {
    printf '\n'
    return 0
  }
  manifest_json="$(full_test_testing_manifest_json)" || return 1
  known_ids="$(jq -r '.checks[]?.id // empty' <<<"$manifest_json")" || return 1
  local IFS=,
  for item in $raw; do
    value="$(setup_gate_trim_space "$item")"
    [[ -n "$value" ]] || continue
    if ! grep -Fxq "$value" <<<"$known_ids"; then
      die "$flag_name 包含未知检查项：$value" 2
    fi
    [[ -n "${seen[$value]:-}" ]] && continue
    seen["$value"]=1
    if [[ -n "$normalized" ]]; then
      normalized+=",$value"
    else
      normalized="$value"
    fi
  done
  printf '%s\n' "$normalized"
}

full_test_manifest_validate_group() {
  local group_name="$1"
  local manifest_json=''
  manifest_json="$(full_test_testing_manifest_json)" || return 1
  if jq -e --arg group "$group_name" '(.valid_groups // ["all"]) | index($group) != null' <<<"$manifest_json" >/dev/null; then
    printf '%s\n' "$group_name"
    return 0
  fi
  die "不支持的 group：$group_name" 2
}

full_test_validate_cli_filters() {
  GROUP="$(full_test_manifest_validate_group "$GROUP" 2>/dev/null || "${FULL_TEST_SURFACE_CMD[@]}" validate-group --group-name "$GROUP")"
  if [[ -n "$ONLY_RAW" ]]; then
    ONLY_RAW="$(full_test_manifest_normalize_check_csv "$ONLY_RAW" --only 2>/dev/null || "${FULL_TEST_SURFACE_CMD[@]}" normalize-check-csv --csv "$ONLY_RAW" --flag --only)"
  fi
  if [[ -n "$SKIP_RAW" ]]; then
    SKIP_RAW="$(full_test_manifest_normalize_check_csv "$SKIP_RAW" --skip 2>/dev/null || "${FULL_TEST_SURFACE_CMD[@]}" normalize-check-csv --csv "$SKIP_RAW" --flag --skip)"
  fi
}

full_test_load_env_values() {
  local env_file="${1:-$ENV_FILE}"
  local raw_line=""
  local line=""
  local key=""
  local value=""

  [[ -f "$env_file" ]] || die "环境文件不存在：$env_file"
  while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    line="$raw_line"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" || "${line:0:1}" == "#" ]] && continue
    if [[ "$line" == export[[:space:]]* ]]; then
      line="${line#export}"
      line="${line#"${line%%[![:space:]]*}"}"
    fi
    [[ "$line" == *"="* ]] || continue

    key="${line%%=*}"
    value="${line#*=}"
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"

    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || die "环境文件包含非法键：$key"
    if [[ ${#value} -ge 2 ]]; then
      if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; then
        value="${value:1:${#value}-2}"
      elif [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
        value="${value:1:${#value}-2}"
      fi
    fi

    printf -v "$key" '%s' "$value"
    export "${key?}"
  done < "$env_file"
}

full_test_require_prereqs() {
  [[ -f "$ENV_FILE" ]] || die "配置文件不存在：$ENV_FILE；请先执行 one_click_config.sh" 2
  runtime_permissions_assert_file_manageable_or_creatable "$DEPLOYMENT_ACCEPTANCE_STATE_PATH" "deployment acceptance 状态文件" || die "deployment acceptance 状态文件写出路径不可管理：$DEPLOYMENT_ACCEPTANCE_STATE_PATH" 4
  runtime_permissions_assert_dir_manageable_or_creatable "$FULL_TEST_LOG_DIR" "full test 日志目录" || die "full test 日志目录不可管理：$FULL_TEST_LOG_DIR" 4
  runtime_permissions_assert_file_manageable_or_creatable "$FULL_TEST_SUMMARY_JSON_PATH" "full test 机器摘要文件" || die "full test 机器摘要文件写出路径不可管理：$FULL_TEST_SUMMARY_JSON_PATH" 4
  runtime_permissions_assert_file_manageable_or_creatable "$FULL_TEST_SUMMARY_MD_PATH" "full test 人工摘要文件" || die "full test 人工摘要文件写出路径不可管理：$FULL_TEST_SUMMARY_MD_PATH" 4
  local latest_summary_json_path='' latest_summary_markdown_path=''
  latest_summary_json_path="$(full_test_runtime_path_default one_click_test_full_latest_summary_json)"
  latest_summary_markdown_path="$(full_test_runtime_path_default one_click_test_full_latest_summary_markdown)"
  runtime_permissions_assert_file_manageable_or_creatable "$latest_summary_json_path" "full test 最近一次机器摘要固定路径" || die "full test 最近一次机器摘要固定路径不可管理：$latest_summary_json_path" 4
  runtime_permissions_assert_file_manageable_or_creatable "$latest_summary_markdown_path" "full test 最近一次人工摘要固定路径" || die "full test 最近一次人工摘要固定路径不可管理：$latest_summary_markdown_path" 4
  mapfile -t missing < <(missing_required_keys "$ENV_FILE")

  if ((${#missing[@]} > 0)); then
    die "配置文件中仍存在 __REQUIRED__；请先完成 one_click_config.sh 输出的人工填写项" 2
  fi
  local out=""
  if ! run_and_capture out bash "$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh" setup env validate --env-file "$ENV_FILE"; then
    die "setup env validate 未通过；请先完成基础配置校验
$out" 2
  fi
  full_test_load_env_values "$ENV_FILE"
  local active_config_path=''
  active_config_path="$(full_test_active_config_path_for_manifest)" || die "无法解析当前 active control-plane profile 配置" 2
  extension_env_gate_verify_active_profile "$ROOT_DIR" "$active_config_path" "one_click_test_full" scheduler || die "当前 active profile 的扩展 runtime venv 未通过校验" 2
}
