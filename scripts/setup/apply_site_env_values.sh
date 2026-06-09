#!/usr/bin/env bash
# 用途：按 KEY=VALUE 批量回填 deploy/site.env；已存在键会原位替换，缺失键会在文件末尾追加。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
TARGET_FILE="$ROOT_DIR/deploy/site.env"
EXAMPLE_FILE="$ROOT_DIR/deploy/site.env.example"
INIT_FROM_EXAMPLE=0

print_help() {
  cat <<'HELP'
用法：
  bash ./scripts/setup/apply_site_env_values.sh --file deploy/site.env --set KEY=VALUE [--set KEY=VALUE ...]
  bash ./scripts/setup/apply_site_env_values.sh --file deploy/site.env --from-env KEY [--from-env KEY ...]
  bash ./scripts/setup/apply_site_env_values.sh --file deploy/site.env --init-from-example --set KEY=VALUE [--set KEY=VALUE ...]

说明：
  1. 已存在的 KEY 会原位替换；缺失的 KEY 会在文件末尾追加。
  2. 该脚本只负责批量回填 deploy/site.env；--file 与 --example-file 只允许指向仓库内的 deploy/site.env / deploy/site.env.example。
  3. deploy/.env 仍需通过 one_click_config.sh 生成。
  4. 建议先完成宿主机基础环境准备，再执行本脚本。

示例：
  export OPENCLAW_INGRESS_LISTEN_IP=<第 0 步选定的目标机私网或 loopback IP>
  export OPENCLAW_TLS_CN=openclaw.internal.example
  bash ./scripts/setup/apply_site_env_values.sh \
    --file deploy/site.env \
    --init-from-example \
    --set OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS=<目标机实际看到的来源网段 CIDR，逗号分隔> \
    --from-env OPENCLAW_INGRESS_LISTEN_IP \
    --from-env OPENCLAW_TLS_CN \
    --set OPENCLAW_TLS_MODE=self_signed \
    --set OPENCLAW_INGRESS_BOUNDARY_MODE=host_firewall

  # 如启用扩展额外要求业务密钥或 provider/API 入口，按对应扩展说明写入 agent/extensions/<extension-id>/deploy/extension.env。
HELP
}

fail() {
  echo "[apply_site_env_values][FAIL] $*" >&2
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

normalize_repo_controlled_path() {
  local raw_path="$1"
  raw_path="${raw_path#./}"
  if [[ "$raw_path" == "$ROOT_DIR/"* ]]; then
    raw_path="${raw_path#"$ROOT_DIR"/}"
  fi
  printf '%s\n' "$raw_path"
}

resolve_allowed_path() {
  local flag="$1"
  local raw_path="$2"
  local normalized=''
  normalized="$(normalize_repo_controlled_path "$raw_path")"
  case "$flag:$normalized" in
    --file:deploy/site.env)
      printf '%s\n' "$ROOT_DIR/deploy/site.env"
      ;;
    --example-file:deploy/site.env.example)
      printf '%s\n' "$ROOT_DIR/deploy/site.env.example"
      ;;
    --file:*)
      fail "--file 只允许指向 deploy/site.env：$raw_path"
      ;;
    --example-file:*)
      fail "--example-file 只允许指向 deploy/site.env.example：$raw_path"
      ;;
    *)
      fail "未登记的路径参数：$flag"
      ;;
  esac
}

pairs=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      print_help
      exit 0
      ;;
    --file)
      require_value "$1" "${2-}"
      TARGET_FILE="$(resolve_allowed_path --file "$2")"
      shift 2
      ;;
    --example-file)
      require_value "$1" "${2-}"
      EXAMPLE_FILE="$(resolve_allowed_path --example-file "$2")"
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
      [[ -n "${value}" ]] || fail "环境变量未设置：$key"
      pairs+=("$key=$value")
      shift 2
      ;;
    *)
      fail "未知参数：$1；使用 --help 查看用法"
      ;;
  esac
done

[[ ${#pairs[@]} -gt 0 ]] || fail "至少提供一个 --set 或 --from-env"

TARGET_FILE="$(resolve_allowed_path --file "$TARGET_FILE")"
EXAMPLE_FILE="$(resolve_allowed_path --example-file "$EXAMPLE_FILE")"

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

echo "[apply_site_env_values] 已写入 $(realpath "$TARGET_FILE" 2>/dev/null || printf '%s' "$TARGET_FILE")"
for pair in "${pairs[@]}"; do
  echo "[apply_site_env_values] 已更新 ${pair%%=*}"
done
