#!/usr/bin/env bash
# 用途：按共享真源清理本地工作区残留目标。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
# shellcheck source=../lib/local_workspace_policy.sh
source "$ROOT_DIR/scripts/lib/local_workspace_policy.sh"
APPLY=0

cleanup_usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/setup/cleanup_local_workspace.sh [--apply] [<target> ...]

说明：
  - 零参数时只处理 cleanupByDefault=true 的目标
  - 显式传入路径时，可按统一真源清理默认保留目标
  - 默认仅 dry-run；只有 --apply 才实际删除

边界：
  - 只允许删除仓库根目录内、且在统一真源中登记的路径
  - 粗粒度目标 `state` 不支持，请改用 `state/openclaw`、`state/image_artifacts`、`state/image_pull`、`state/remote_first_install`
USAGE
}

cleanup_fail() {
  echo "[cleanup_local_workspace][FAIL] $*" >&2
  exit "${2:-2}"
}

cleanup_note() {
  echo "[cleanup_local_workspace] $*"
}

cleanup_target_path() {
  local rel_path="$1"
  printf '%s/%s' "$ROOT_DIR" "$rel_path"
}

cleanup_assert_in_repo() {
  local target_path="$1"
  case "$target_path" in
    "$ROOT_DIR"/*) ;;
    *)
      cleanup_fail "拒绝删除仓库外路径：$target_path"
      ;;
  esac
}

declare -A allowed_targets=()
default_targets=()
selected_targets=()
declare -A selected_seen=()

while IFS=$'\t' read -r _target_id rel_path _target_class cleanup_by_default _gitignore_pattern; do
  rel_path="${rel_path%$'\r'}"
  cleanup_by_default="${cleanup_by_default%$'\r'}"
  allowed_targets["$rel_path"]=1
  if [[ "$cleanup_by_default" == 'yes' ]]; then
    default_targets+=("$rel_path")
  fi
done < <(openclaw_local_workspace_policy_targets)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      APPLY=1
      shift
      ;;
    -h|--help)
      cleanup_usage
      exit 0
      ;;
    state)
      cleanup_fail '粗粒度目标 state 不支持；请改用 state/openclaw、state/image_artifacts、state/image_pull 或 state/remote_first_install'
      ;;
    *)
      [[ -n "${allowed_targets[$1]:-}" ]] || cleanup_fail "仅允许统一真源中的目标路径：$1"
      if [[ -z "${selected_seen[$1]:-}" ]]; then
        selected_targets+=("$1")
        selected_seen["$1"]=1
      fi
      shift
      ;;
  esac
done

if (( ${#selected_targets[@]} == 0 )); then
  for rel_path in "${default_targets[@]}"; do
    if [[ -z "${selected_seen[$rel_path]:-}" ]]; then
      selected_targets+=("$rel_path")
      selected_seen["$rel_path"]=1
    fi
  done
  while IFS= read -r rel_path; do
    [[ -n "$rel_path" ]] || continue
    if [[ -z "${selected_seen[$rel_path]:-}" ]]; then
      selected_targets+=("$rel_path")
      selected_seen["$rel_path"]=1
    fi
  done < <(openclaw_local_workspace_policy_disposable_paths)
fi

cleanup_note "目标路径：${selected_targets[*]}"
if (( APPLY == 0 )); then
  cleanup_note '当前为 dry-run；传入 --apply 才会实际删除。'
fi

for rel_path in "${selected_targets[@]}"; do
  target_path="$(cleanup_target_path "$rel_path")"
  cleanup_assert_in_repo "$target_path"
  [[ "$target_path" != "$ROOT_DIR" ]] || cleanup_fail '拒绝删除仓库根目录。'

  if [[ ! -e "$target_path" ]]; then
    cleanup_note "跳过不存在路径：$rel_path"
    continue
  fi

  if (( APPLY == 0 )); then
    cleanup_note "dry-run 将删除：$rel_path -> $target_path"
    continue
  fi

  cleanup_note "删除：$rel_path -> $target_path"
  rm -rf -- "$target_path"
done
