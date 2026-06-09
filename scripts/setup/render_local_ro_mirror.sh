#!/usr/bin/env bash
# 用途：根据 manifest 增量生成或校验 Gateway 可挂载的最小 /local_ro 镜像目录。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
# shellcheck source=lib/runtime_permissions.sh
source "$ROOT_DIR/scripts/setup/lib/runtime_permissions.sh"
# shellcheck source=../lib/control_plane_config_paths.sh
source "$ROOT_DIR/scripts/lib/control_plane_config_paths.sh"

MANIFEST_PATH=""
OUTPUT_DIR=""
CHECK_ONLY=0
LABEL="render_local_ro_mirror"
CONFIG_PATH=""
CONFIG_PATH_EXPLICIT=0
OPENCLAW_PYTHON_TOOL="$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh"

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/setup/render_local_ro_mirror.sh --manifest <manifest> --output-dir <gateway-state-dir/child> [--label <name>] [--check] [--config-path <path>]
USAGE
}

fail() {
  echo "[$LABEL][FAIL] $1" >&2
  exit "${2:-2}"
}

ensure_output_dir_in_gateway_state() {
  local output_dir="$1"
  local gateway_root
  gateway_root="$(realpath -m "$(runtime_permissions_host_gateway_state_dir "$ROOT_DIR")")"
  case "$output_dir" in
    "$gateway_root"/*) ;;
    *) fail "输出目录必须位于 gateway state root 的子目录：$gateway_root；当前=$output_dir" 3 ;;
  esac
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --manifest)
      [[ $# -ge 2 ]] || fail '--manifest 缺少路径参数' 2
      MANIFEST_PATH="$2"; shift 2 ;;
    --output-dir)
      [[ $# -ge 2 ]] || fail '--output-dir 缺少路径参数' 2
      OUTPUT_DIR="$2"; shift 2 ;;
    --label)
      [[ $# -ge 2 ]] || fail '--label 缺少名称参数' 2
      LABEL="$2"; shift 2 ;;
    --config-path)
      [[ $# -ge 2 ]] || fail '--config-path 缺少路径参数' 2
      CONFIG_PATH="$2"; CONFIG_PATH_EXPLICIT=1; shift 2 ;;
    --check) CHECK_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "未知参数：$1" 2 ;;
  esac
done

[[ -n "$MANIFEST_PATH" ]] || fail '必须通过 --manifest 指定 manifest' 2
[[ -n "$OUTPUT_DIR" ]] || fail '必须通过 --output-dir 指定输出目录' 2
[[ "$MANIFEST_PATH" = /* ]] || MANIFEST_PATH="$ROOT_DIR/$MANIFEST_PATH"
[[ "$OUTPUT_DIR" = /* ]] || OUTPUT_DIR="$ROOT_DIR/$OUTPUT_DIR"
[[ -f "$MANIFEST_PATH" ]] || fail "缺少 manifest：$MANIFEST_PATH" 2
OUTPUT_DIR="$(realpath -m "$OUTPUT_DIR")"
ensure_output_dir_in_gateway_state "$OUTPUT_DIR"

if [[ "$CONFIG_PATH_EXPLICIT" == "1" ]]; then
  CONFIG_PATH="$(openclaw_control_plane_resolve_config_path agent_platform "$CONFIG_PATH")"
else
  CONFIG_PATH="$(openclaw_control_plane_resolve_config_path agent_platform)"
fi

args=(
  setup env render-local-ro-mirror
  --manifest "$MANIFEST_PATH"
  --output-dir "$OUTPUT_DIR"
  --label "$LABEL"
  --config-path "$CONFIG_PATH"
)
[[ "$CHECK_ONLY" == "1" ]] && args+=(--check)
bash "$OPENCLAW_PYTHON_TOOL" "${args[@]}"
