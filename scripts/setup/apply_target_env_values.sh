#!/usr/bin/env bash
# 用途：按 KEY=VALUE 批量回填 deploy/targets.d/<target_id>.env，避免远程多行编辑导致换行符或字面量 \n 漂移。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
TARGET_ID=""
TARGET_FILE=""
INIT_FROM_EXAMPLE=0
print_help() {
  cat <<'HELP'
用法：
  bash ./scripts/setup/apply_target_env_values.sh --target <target_id> --set KEY=VALUE [--set KEY=VALUE ...]
  bash ./scripts/setup/apply_target_env_values.sh --target <target_id> --from-env KEY [--from-env KEY ...]
  bash ./scripts/setup/apply_target_env_values.sh --target <target_id> --init-from-example --set KEY=VALUE [--set KEY=VALUE ...]

说明：
  1. 已存在的 KEY 会原位替换；缺失的 KEY 会在文件末尾追加。
  2. --target <target_id> 只会写入 deploy/targets.d/<target_id>.env。
  3. --init-from-example 从 active profile 的 dispatch target registry 生成目标 env 初始内容；deploy/targets.d/*.env.example 是派生参考，交付真源为 deploy/targets.d/<target_id>.env。
  4. 写出的文件固定为 LF 文本，并设置为 owner-only 可读写。
  5. deploy/.env 仍需通过 one_click_config.sh 生成。
  6. 执行前需已准备 host 控制面介质：bash ./scripts/setup/prepare_control_plane_medium.sh。

示例（先查询 active profile；以下占位符须替换为其 registry 声明的 target ID 与 env 键）：
  bash ./scripts/runtime/run_openclaw_python_tool.sh setup env query-dispatch-registry summary --env-file deploy/.env
  # endpoint_env_key、secret_env_key、enabled_env_key 从对应 deploy/targets.d/<target_id>.env.example 读取。
  # 未声明 dispatch target 的 profile 不能使用本命令。
  export <endpoint_env_key>='<endpoint>'
  export <secret_env_key>='<secret>'
  bash ./scripts/setup/apply_target_env_values.sh \
    --target '<target_id>' \
    --init-from-example \
    --from-env '<endpoint_env_key>' \
    --from-env '<secret_env_key>' \
    --set '<enabled_env_key>=true'
HELP
}
fail() {
  echo "[apply_target_env_values][FAIL] $*" >&2
  exit 2
}
require_value() {
  local flag="$1"
  local value="${2-}"
  [[ -n "$value" ]] || fail "$flag 缺少参数"
}
validate_key() {
  local key="$1"
  [[ "$key" =~ ^[A-Z0-9_]+$ ]] || fail "非法键名：$key；仅允许大写字母、数字与下划线"
}
validate_env_value() {
  local key="$1"
  local value="$2"
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* || "$value" == *$'\t'* ]]; then
    fail "$key 的值包含换行、回车或制表符，无法安全写入 target env。"
  fi
}
validate_target_id() {
  local value="$1"
  [[ "$value" =~ ^[A-Za-z0-9_.-]+$ ]] || fail "非法 target id：$value；仅允许字母、数字、下划线、点与短横线"
  [[ "$value" != "." && "$value" != ".." ]] || fail "非法 target id：$value"
}
resolve_target_paths() {
  [[ -n "$TARGET_ID" ]] || fail "必须提供 --target <target_id>"
  validate_target_id "$TARGET_ID"
  TARGET_FILE="$ROOT_DIR/deploy/targets.d/$TARGET_ID.env"
}
delegate_python_write_target_env() {
  local tmp_dir="$ROOT_DIR/state/deploy_input_values"
  mkdir -p "$tmp_dir"
  chmod 700 "$tmp_dir" 2>/dev/null || true
  local tmp_file=''
  tmp_file="$(mktemp "$tmp_dir/apply-target.XXXXXX.env")"
  chmod 600 "$tmp_file" 2>/dev/null || true
  trap 'rm -f "$tmp_file"' EXIT
  local pair=''
  for pair in "${pairs[@]}"; do
    printf '%s\n' "$pair" >> "$tmp_file"
  done

  export OPENCLAW_DEPLOY_INPUT_OWNER_ONLY_CHECKED=1
  export OPENCLAW_PYTHON_TOOL_EXTRA_ENV_VARS="${OPENCLAW_PYTHON_TOOL_EXTRA_ENV_VARS:-} OPENCLAW_DEPLOY_INPUT_OWNER_ONLY_CHECKED"

  python_args=(setup env input-values write-target-env --target "$TARGET_ID" --input-env-file "$tmp_file")
  [[ "$INIT_FROM_EXAMPLE" == '1' ]] && python_args+=(--init)
  set +e
  bash "$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh" "${python_args[@]}"
  local rc=$?
  set -e
  rm -f "$tmp_file"
  trap - EXIT
  exit "$rc"
}

pairs=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      print_help
      exit 0
      ;;
    --target)
      require_value "$1" "${2-}"
      TARGET_ID="$2"
      shift 2
      ;;
    --init-from-example)
      INIT_FROM_EXAMPLE=1
      shift
      ;;
    --set)
      require_value "$1" "${2-}"
      [[ "$2" == *=* ]] || fail "--set 需要 KEY=VALUE 形式"
      key="${2%%=*}"
      value="${2#*=}"
      validate_key "$key"
      validate_env_value "$key" "$value"
      pairs+=("$key=$value")
      shift 2
      ;;
    --from-env)
      require_value "$1" "${2-}"
      key="$2"
      validate_key "$key"
      value="${!key-}"
      [[ -n "$value" ]] || fail "环境变量未设置：$key"
      validate_env_value "$key" "$value"
      pairs+=("$key=$value")
      shift 2
      ;;
    *)
      fail "未知参数：$1；使用 --help 查看用法"
      ;;
  esac
done

[[ ${#pairs[@]} -gt 0 ]] || fail "至少提供一个 --set 或 --from-env"
resolve_target_paths
delegate_python_write_target_env
