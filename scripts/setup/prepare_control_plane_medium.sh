#!/usr/bin/env bash
# 用途：显式准备 host 控制面执行 openclaw Python CLI 所需的唯一 OPENCLAW_CONTROL_PLANE_IMAGE；该步骤是进入 one_click_config 与任意 host 控制面命令前的统一前置条件。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
# shellcheck source=scripts/lib/flow_entry_surface_shell.sh
source "$ROOT_DIR/scripts/lib/flow_entry_surface_shell.sh"
# shellcheck source=scripts/setup/lib/setup_cli_common.sh
source "$ROOT_DIR/scripts/setup/lib/setup_cli_common.sh"
OFFLINE=0
IMAGE_ARCHIVE_PATH=""
HELP_ONLY=0
EXPLAIN_ONLY=0

medium_fail() {
  echo "[prepare_control_plane_medium][FAIL] $1" >&2
  exit 2
}

show_prepare_control_plane_medium_help() {
  local purpose=''
  local help_lines=''
  local references=''
  purpose="$(setup_cli_control_plane_medium_scalar_from_truth '.entrypoint.purpose' 'entrypoint.purpose')"
  help_lines="$(setup_cli_control_plane_medium_lines_from_truth '.help_surface.lines' 'help_surface.lines')"
  references="$(setup_cli_control_plane_medium_lines_from_truth '.entrypoint.references' 'entrypoint.references')"
  cat <<'USAGE'
用法：
  bash ./scripts/setup/prepare_control_plane_medium.sh [--offline] [--image-archive <path>]
USAGE
  if [[ -n "$purpose" ]]; then
    echo
    echo "$purpose"
  fi
  echo
  echo '默认行为：'
  echo "  1) 在线命令：$(setup_cli_control_plane_medium_scalar_from_truth '.online.command' 'online.command')"
  echo "  2) 离线命令：$(setup_cli_control_plane_medium_scalar_from_truth '.offline.command' 'offline.command')"
  local line=''
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    echo "  - $line"
  done <<< "$help_lines"
  if [[ -n "$references" ]]; then
    echo
    echo '入口参考：'
    while IFS= read -r line; do
      [[ -n "$line" ]] || continue
      echo "  - $line"
    done <<< "$references"
  fi
  setup_help_surface_guarantee_text
  setup_help_surface_reference_text
}

show_prepare_control_plane_medium_explain() {
  local purpose=''
  local boundaries=''
  local online_steps=''
  local offline_steps=''
  purpose="$(setup_cli_control_plane_medium_scalar_from_truth '.entrypoint.purpose' 'entrypoint.purpose')"
  boundaries="$(setup_cli_control_plane_medium_lines_from_truth '.entrypoint.boundaries' 'entrypoint.boundaries')"
  online_steps="$(setup_cli_control_plane_medium_lines_from_truth '.online.steps' 'online.steps')"
  offline_steps="$(setup_cli_control_plane_medium_lines_from_truth '.offline.steps' 'offline.steps')"
  echo 'prepare_control_plane_medium 职责边界'
  echo
  if [[ -n "$purpose" ]]; then
    echo "$purpose"
    echo
  fi
  if [[ -n "$boundaries" ]]; then
    echo '边界：'
    local line=''
    while IFS= read -r line; do
      [[ -n "$line" ]] || continue
      echo "  - $line"
    done <<< "$boundaries"
    echo
  fi
  echo '在线模式执行内容：'
  local line=''
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    echo "  - $line"
  done <<< "$online_steps"
  echo
  echo '离线模式执行内容：'
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    echo "  - $line"
  done <<< "$offline_steps"
  echo
  echo '补充说明：'
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    echo "  - $line"
  done < <(setup_cli_control_plane_medium_lines_from_truth '.help_surface.lines' 'help_surface.lines')
  setup_help_surface_guarantee_text
  setup_help_surface_reference_text
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --offline)
      OFFLINE=1
      shift
      ;;
    --image-archive)
      [[ $# -ge 2 ]] || medium_fail "--image-archive 缺少路径参数"
      IMAGE_ARCHIVE_PATH="$2"
      shift 2
      ;;
    --explain)
      EXPLAIN_ONLY=1
      shift
      ;;
    -h|--help)
      HELP_ONLY=1
      shift
      ;;
    *)
      flow_entry_handle_unknown_arg "prepare_control_plane_medium" "$1" show_prepare_control_plane_medium_help
      exit 2
      ;;
  esac
done

if flow_entry_maybe_render_static_surface "$HELP_ONLY" "$EXPLAIN_ONLY" show_prepare_control_plane_medium_help show_prepare_control_plane_medium_explain; then
  exit 0
fi

set --
if [[ "$OFFLINE" == "1" ]]; then
  set -- "$@" --no-pull
fi
if [[ -n "$IMAGE_ARCHIVE_PATH" ]]; then
  set -- "$@" --image-archive "$IMAGE_ARCHIVE_PATH"
fi
exec bash "$ROOT_DIR/scripts/images/ensure_control_plane_image.sh" "$@"
