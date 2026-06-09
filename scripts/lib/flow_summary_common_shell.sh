#!/usr/bin/env bash
# 用途：为 deploy / release / full test 的摘要 helper 提供统一桥接函数，减少重复的 summary_write / summary_emit 调用。
set -euo pipefail

flow_summary_resolve_args() {
  local array_name="$1"
  FLOW_SUMMARY_RESOLVED_ARGS=()
  case "$array_name" in
    summary_args)
      FLOW_SUMMARY_RESOLVED_ARGS=("${summary_args[@]}")
      ;;
    *)
      echo "[flow_summary_common][FAIL] 未知摘要参数数组：$array_name" >&2
      return 2
      ;;
  esac
}

# 从数组参数写入统一的 summary helper 输出。
flow_summary_write_from_array() {
  local helper_prefix_name="$1"
  local command="$2"
  local array_name="$3"
  flow_summary_resolve_args "$array_name" || return $?
  summary_write "$helper_prefix_name" "$command" "${FLOW_SUMMARY_RESOLVED_ARGS[@]}"
}

# 从数组参数触发统一的 summary 输出。
flow_summary_emit_from_array() {
  local log_func="$1"
  local helper_prefix_name="$2"
  local command="$3"
  local array_name="$4"
  flow_summary_resolve_args "$array_name" || return $?
  summary_emit "$log_func" "$helper_prefix_name" "$command" "${FLOW_SUMMARY_RESOLVED_ARGS[@]}"
}

# 输出摘要 Markdown/JSON 路径提示。
flow_summary_note_paths() {
  local log_func="$1"
  local summary_md_path="$2"
  local summary_json_path="$3"
  summary_note_paths "$log_func" "$summary_md_path" "$summary_json_path"
}

# 带前缀输出摘要文件路径提示。
flow_summary_emit_prefixed_note_paths() {
  local prefix="$1"
  local summary_md_path="$2"
  local summary_json_path="$3"
  echo "${prefix} 摘要文件：$summary_md_path"
  echo "${prefix} 机器摘要：$summary_json_path"
}

# 从 summary JSON 中按路径读取字段，并支持默认值回退。
flow_summary_json_get() {
  local json_path="$1"
  local path_expr="$2"
  local default_value="${3:-}"
  if command -v jq >/dev/null 2>&1 && [[ -f "$json_path" ]]; then
    local jq_filter='.' part=''
    IFS='.' read -r -a __flow_summary_parts <<< "$path_expr"
    for part in "${__flow_summary_parts[@]}"; do
      jq_filter+="[\"$part\"]"
    done
    local value=''
    value="$(jq -r "$jq_filter // empty" "$json_path" 2>/dev/null || true)"
    if [[ -n "$value" ]]; then
      printf '%s' "$value"
      return 0
    fi
  fi
  printf '%s' "$default_value"
}

flow_summary_relative_or_self() {
  local root_dir="$1"
  local file_path="${2:-}"
  [[ -n "$file_path" ]] || {
    printf ''
    return 0
  }
  case "$file_path" in
    "$root_dir") printf '.' ;;
    "$root_dir"/*) printf '%s' "${file_path#"$root_dir"/}" ;;
    *) printf '%s' "$file_path" ;;
  esac
}

flow_summary_bool_literal() {
  if [[ "${1:-}" == '1' ]]; then
    printf 'true'
  else
    printf 'false'
  fi
}

flow_summary_json_array_from_items() {
  local escape_func="$1"
  shift
  local first=1 item=''
  printf '['
  for item in "$@"; do
    [[ $first -eq 1 ]] || printf ','
    first=0
    printf '"%s"' "$("$escape_func" "$item")"
  done
  printf ']'
}

flow_summary_json_array_from_lines() {
  local escape_func="$1"
  local lines_file="$2"
  local first=1
  local line=''
  printf '['
  if [[ -f "$lines_file" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ $first -eq 1 ]] || printf ','
      first=0
      printf '"%s"' "$("$escape_func" "$line")"
    done < "$lines_file"
  fi
  printf ']'
}

flow_summary_generator_json_object() {
  local escape_func="$1"
  local mode="$2"
  local reason="${3:-}"
  if [[ -n "$reason" ]]; then
    printf '{"mode":"%s","reason":"%s"}' "$("$escape_func" "$mode")" "$("$escape_func" "$reason")"
    return 0
  fi
  printf '{"mode":"%s"}' "$("$escape_func" "$mode")"
}

flow_summary_copy_latest_artifacts() {
  local summary_json_path="$1"
  local latest_json_path="$2"
  local summary_markdown_path="$3"
  local latest_markdown_path="$4"
  cp "$summary_json_path" "$latest_json_path"
  cp "$summary_markdown_path" "$latest_markdown_path"
}
