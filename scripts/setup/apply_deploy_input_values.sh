#!/usr/bin/env bash
# 用途：按 active profile 路由 owner-only env 文件到 site/extension/target 三类部署输入真源。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
INPUT_ENV_FILE=""
forward_args=()
fail() {
  echo "[apply_deploy_input_values][FAIL] $*" >&2
  exit 2
}
usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/setup/apply_deploy_input_values.sh --profile <profile_id> --input <path> --init
  bash ./scripts/setup/apply_deploy_input_values.sh --profile <profile_id> --input <path> --validate-only

说明：
  - 读取 owner-only env 文件，按 active profile 的 schema、组合 sharedDeployEnvFields 与 dispatch target registry 计算 key 归属。
  - 也接受 `--input-env-file <path>`，语义与 `--input <path>` 相同。
  - site 键写入 deploy/site.env，扩展键写入 agent/extensions/<id>/deploy/extension.env，target 键写入 deploy/targets.d/<target_id>.env。
  - `--validate-only` 只输出脱敏路由、未知键/错位键和缺失 required/manual_required 提示，不写入任何部署输入真源。
  - 拒绝未知键、跨 profile target、共享 site env 键位置错误，以及包含换行/回车/制表符的值。
  - secret 日志只输出 <redacted>。
  - 执行前需已准备 host 控制面介质：bash ./scripts/setup/prepare_control_plane_medium.sh。
USAGE
}

if [[ "${1:-}" == '-h' || "${1:-}" == '--help' ]]; then
  usage
  exit 0
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input|--input-env-file)
      [[ $# -ge 2 ]] || fail "$1 缺少路径参数"
      INPUT_ENV_FILE="$2"
      shift 2
      ;;
    --input=*)
      INPUT_ENV_FILE="${1#--input=}"
      shift
      ;;
    --input-env-file=*)
      INPUT_ENV_FILE="${1#--input-env-file=}"
      shift
      ;;
    *)
      forward_args+=("$1")
      shift
      ;;
  esac
done

[[ -n "$INPUT_ENV_FILE" ]] || fail '必须提供 --input <path>'
[[ -f "$INPUT_ENV_FILE" ]] || fail "输入 env 文件不存在：$INPUT_ENV_FILE"

if command -v stat >/dev/null 2>&1 && [[ "$(uname -s 2>/dev/null || true)" == Linux* ]]; then
  mode="$(stat -c '%a' "$INPUT_ENV_FILE" 2>/dev/null || true)"
  if [[ "$mode" =~ ^[0-7]+$ ]] && (( (8#$mode & 8#077) != 0 )); then
    fail "输入 env 文件必须是 owner-only 权限，请执行 chmod 600 $INPUT_ENV_FILE"
  fi
fi

tmp_dir="$ROOT_DIR/state/deploy_input_values"
mkdir -p "$tmp_dir"
chmod 700 "$tmp_dir" 2>/dev/null || true
tmp_file="$(mktemp "$tmp_dir/deploy-input.XXXXXX.env")"
trap 'rm -f "$tmp_file"' EXIT
cp "$INPUT_ENV_FILE" "$tmp_file"
chmod 600 "$tmp_file" 2>/dev/null || true

export OPENCLAW_DEPLOY_INPUT_OWNER_ONLY_CHECKED=1
export OPENCLAW_PYTHON_TOOL_EXTRA_ENV_VARS="${OPENCLAW_PYTHON_TOOL_EXTRA_ENV_VARS:-} OPENCLAW_DEPLOY_INPUT_OWNER_ONLY_CHECKED"

set +e
bash "$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh" setup env input-values apply "${forward_args[@]+"${forward_args[@]}"}" --input-env-file "$tmp_file"
rc=$?
set -e
rm -f "$tmp_file"
trap - EXIT
exit "$rc"
