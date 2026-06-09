#!/usr/bin/env bash
# shellcheck disable=SC2034
# 用途：统一承接 deploy/release/test 入口的阶段或分组顺序加载、索引构建与顺序执行，避免重复维护 mapfile / for-loop 模式。
set -euo pipefail

# 将命令输出逐行装载到目标数组中。
flow_sequence_load_lines() {
  local target_name="$1"
  shift
  # shellcheck disable=SC2034
  local line=''
  case "$target_name" in
    EFFECTIVE_STAGES)
      EFFECTIVE_STAGES=()
      ;;
    ordered_groups)
      ordered_groups=()
      ;;
    *)
      echo "[flow_sequence][FAIL] 未知顺序数组：$target_name" >&2
      return 2
      ;;
  esac
  while IFS= read -r line; do
    case "$target_name" in
      EFFECTIVE_STAGES)
        EFFECTIVE_STAGES+=("$line")
        ;;
      ordered_groups)
        ordered_groups+=("$line")
        ;;
    esac
  done < <("$@")
}

# 为顺序数组构建名称到下标的索引映射。
flow_sequence_build_index() {
  local index_name="$1"
  local items_name="$2"
  local -a items=()
  local i=''
  # shellcheck disable=SC2034
  local item_key=''
  case "$items_name" in
    EFFECTIVE_STAGES)
      items=("${EFFECTIVE_STAGES[@]}")
      ;;
    *)
      echo "[flow_sequence][FAIL] 未知顺序数组：$items_name" >&2
      return 2
      ;;
  esac
  case "$index_name" in
    EFFECTIVE_STAGE_INDEX)
      EFFECTIVE_STAGE_INDEX=()
      ;;
    *)
      echo "[flow_sequence][FAIL] 未知顺序索引：$index_name" >&2
      return 2
      ;;
  esac
  for i in "${!items[@]}"; do
    item_key="${items[$i]}"
    case "$index_name" in
      EFFECTIVE_STAGE_INDEX)
        printf -v "EFFECTIVE_STAGE_INDEX[$item_key]" '%s' "$i"
        ;;
    esac
  done
}

# 判断当前项是否位于 resume 起点之后。
flow_sequence_should_run_from() {
  local index_name="$1"
  # shellcheck disable=SC2034
  local current_name="$2"
  local start_name="${3:-}"
  local current_index='' start_index=''
  [[ -z "$start_name" ]] && return 0
  case "$index_name" in
    EFFECTIVE_STAGE_INDEX)
      current_index="${EFFECTIVE_STAGE_INDEX["$current_name"]-}"
      start_index="${EFFECTIVE_STAGE_INDEX["$start_name"]-}"
      ;;
    *)
      echo "[flow_sequence][FAIL] 未知顺序索引：$index_name" >&2
      return 2
      ;;
  esac
  [[ -n "$current_index" ]] || return 1
  [[ -n "$start_index" ]] || return 1
  (( current_index >= start_index ))
}

# 按数组顺序逐项调用给定回调函数。
flow_sequence_run_array() {
  local items_name="$1"
  local callback="$2"
  local -a items=()
  local item=''
  case "$items_name" in
    ordered_groups)
      items=("${ordered_groups[@]}")
      ;;
    *)
      echo "[flow_sequence][FAIL] 未知顺序数组：$items_name" >&2
      return 2
      ;;
  esac
  for item in "${items[@]}"; do
    "$callback" "$item"
  done
}
