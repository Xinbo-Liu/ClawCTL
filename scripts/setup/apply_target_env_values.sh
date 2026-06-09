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
EXAMPLE_FILE=""
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
  3. --init-from-example 会从 one_click_config 按 active profile 生成的 deploy/targets.d/<target_id>.env.example 初始化目标文件。
  4. 写出的文件固定为 LF 文本，并设置为 owner-only 可读写。
  5. deploy/.env 仍需通过 one_click_config.sh 生成。

示例：
  export DISPATCH_PRIMARY_WEBHOOK_URL=<webhook-url>
  export DISPATCH_PRIMARY_BOT_SECRET=<bot-secret>
  bash ./scripts/setup/apply_target_env_values.sh \
    --target dispatch_primary \
    --init-from-example \
    --from-env DISPATCH_PRIMARY_WEBHOOK_URL \
    --from-env DISPATCH_PRIMARY_BOT_SECRET \
    --set DISPATCH_PRIMARY_ENABLE=true
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

validate_target_id() {
  local value="$1"
  [[ "$value" =~ ^[A-Za-z0-9_.-]+$ ]] || fail "非法 target id：$value；仅允许字母、数字、下划线、点与短横线"
  [[ "$value" != "." && "$value" != ".." ]] || fail "非法 target id：$value"
}

resolve_target_paths() {
  [[ -n "$TARGET_ID" ]] || fail "必须提供 --target <target_id>"
  validate_target_id "$TARGET_ID"
  TARGET_FILE="$ROOT_DIR/deploy/targets.d/$TARGET_ID.env"
  EXAMPLE_FILE="$ROOT_DIR/deploy/targets.d/$TARGET_ID.env.example"
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
      pairs+=("$key=$value")
      shift 2
      ;;
    --from-env)
      require_value "$1" "${2-}"
      key="$2"
      validate_key "$key"
      value="${!key-}"
      [[ -n "$value" ]] || fail "环境变量未设置：$key"
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

if [[ "$INIT_FROM_EXAMPLE" == "1" && ! -f "$TARGET_FILE" ]]; then
  [[ -f "$EXAMPLE_FILE" ]] || fail "初始化模板不存在：$EXAMPLE_FILE"
  mkdir -p "$(dirname "$TARGET_FILE")"
  cp "$EXAMPLE_FILE" "$TARGET_FILE"
fi

[[ -f "$TARGET_FILE" ]] || fail "目标文件不存在：$TARGET_FILE；请先创建，或追加 --init-from-example"

updates_file="$(mktemp)"
output_file="$(mktemp)"
trap 'rm -f "$updates_file" "$output_file"' EXIT

for pair in "${pairs[@]}"; do
  key="${pair%%=*}"
  value="${pair#*=}"
  printf '%s\t%s\n' "$key" "$value" >> "$updates_file"
done

awk -F '\t' '
NR == FNR {
  key = $1
  value_start = index($0, $2)
  value = value_start > 0 ? substr($0, value_start) : ""
  if (!(key in seen_input)) {
    order[++count] = key
    seen_input[key] = 1
  }
  values[key] = value
  next
}
{
  line = $0
  if (line ~ /^[[:space:]]*[A-Z0-9_]+=.*$/) {
    key = line
    sub(/^[[:space:]]*/, "", key)
    sub(/=.*/, "", key)
    if (key in values) {
      print key "=" values[key]
      written[key] = 1
      next
    }
  }
  print line
}
END {
  for (idx = 1; idx <= count; idx++) {
    key = order[idx]
    if (!(key in written)) {
      print key "=" values[key]
    }
  }
}
' "$updates_file" "$TARGET_FILE" > "$output_file"

mv "$output_file" "$TARGET_FILE"
chmod 600 "$TARGET_FILE" 2>/dev/null || true

echo "[apply_target_env_values] 已写入 $(realpath "$TARGET_FILE" 2>/dev/null || printf '%s' "$TARGET_FILE")"
for pair in "${pairs[@]}"; do
  echo "[apply_target_env_values] 已更新 ${pair%%=*}"
done
