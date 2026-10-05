#!/usr/bin/env bash
# 用途：按 schema 驱动的 extension id/profile 批量回填 agent/extensions/<id>/deploy/extension.env。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir

EXTENSION_ID=""
PROFILE_ID=""
EXTENSION_ROOT=""
EXTENSION_FILE=""
EXAMPLE_FILE=""
INIT_FROM_EXAMPLE=0
pairs=()
secret_keys=()
usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/setup/apply_extension_env_values.sh --extension <id> --init-from-example --set KEY=VALUE
  bash ./scripts/setup/apply_extension_env_values.sh --profile <profile_id> --from-env KEY --set-secret-from-env SECRET_KEY

说明：
  - --extension <id> 与 --profile <profile_id> 二选一；单扩展 profile 会解析唯一扩展。
    多扩展 profile 会按 key owner 写入对应 extension.env；共享 site env 键会被拒绝并给出 site.env 修复入口。
  - --init-from-example 从 agent/extensions/<id>/deploy/extension.env.example 初始化缺失文件。
  - --from-env KEY 读取当前 shell 环境变量；--set-secret-from-env KEY 读取 secret 并在输出中脱敏。
  - 写出的 extension.env 固定 LF，并 chmod 600。
  - deploy/.env 仍需通过 one_click_config.sh 重新生成。
USAGE
}
fail() {
  echo "[apply_extension_env_values][FAIL] $*" >&2
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
    fail "$key 的值包含换行、回车或制表符，无法安全写入 extension.env；请改用单行 owner-only 文件路径或重新编码后再导入。"
  fi
}
validate_id() {
  local value="$1"
  [[ "$value" =~ ^[A-Za-z0-9_.-]+$ ]] || fail "非法 id：$value"
  [[ "$value" != "." && "$value" != ".." ]] || fail "非法 id：$value"
}
extension_root_from_index() {
  local extension_id="$1"
  command -v jq >/dev/null 2>&1 || fail '缺少 jq；无法解析 agent/extensions/index.json'
  jq -r --arg id "$extension_id" '.extensions[]? | select(.id == $id) | .rootDir // empty' "$ROOT_DIR/agent/extensions/index.json" | tr -d '\r'
}
profile_config_path() {
  local profile_id="$1"
  awk -F '\t' -v id="$profile_id" '
    $0 ~ /^[[:space:]]*#/ { next }
    NF >= 2 && $1 == id { print $2; exit }
  ' "$ROOT_DIR/config/control_plane/profile_registry.tsv"
}
profile_managed_extensions() {
  local profile_id="$1"
  local config_rel='' config_path='' known_ids=''
  command -v jq >/dev/null 2>&1 || fail '缺少 jq；无法解析 profile enabled extensions'
  config_rel="$(profile_config_path "$profile_id")"
  [[ -n "$config_rel" ]] || fail "未知 profile：$profile_id"
  config_path="$ROOT_DIR/$config_rel"
  [[ -f "$config_path" ]] || fail "profile 配置不存在：$config_path"
  known_ids="$(jq -r '.extensions[]?.id // empty' "$ROOT_DIR/agent/extensions/index.json" | tr -d '\r')"
  jq -r '.extensions.enabledExtensionIds[]? // empty' "$config_path" | tr -d '\r' | while IFS= read -r id; do
    [[ -n "$id" ]] || continue
    printf '%s\n' "$known_ids" | grep -Fxq "$id" && printf '%s\n' "$id"
  done | awk '!seen[$0]++'
}
extension_from_profile() {
  local profile_id="$1"
  local enabled=''
  enabled="$(profile_managed_extensions "$profile_id")"
  local count=0 last=''
  while IFS= read -r id; do
    [[ -n "$id" ]] || continue
    count=$((count + 1))
    last="$id"
  done <<< "$enabled"
  [[ "$count" -gt 0 ]] || fail "profile 未启用 managed explicit extension：$profile_id"
  [[ "$count" -eq 1 ]] || fail "profile 启用多个 managed explicit extension；使用 --profile 与待写入 key 时会按 key owner 写入对应扩展，或使用 --extension 精确修复单个扩展：$(printf '%s' "$enabled" | paste -sd, -)"
  printf '%s\n' "$last"
}
delegate_multi_extension_profile_if_needed() {
  [[ -n "$PROFILE_ID" && -z "$EXTENSION_ID" ]] || return 0
  validate_id "$PROFILE_ID"
  local enabled='' count=0 id=''
  enabled="$(profile_managed_extensions "$PROFILE_ID")"
  while IFS= read -r id; do
    [[ -n "$id" ]] || continue
    count=$((count + 1))
  done <<< "$enabled"
  [[ "$count" -gt 1 ]] || return 0

  if ((${#pairs[@]} == 0)); then
    [[ "$INIT_FROM_EXAMPLE" == '1' ]] || fail '多扩展 profile 必须提供待写入 key，或使用 --init-from-example 初始化缺失 extension.env。'
    while IFS= read -r id; do
      [[ -n "$id" ]] || continue
      local root_rel='' extension_file='' example_file='' output_file=''
      root_rel="$(extension_root_from_index "$id")"
      [[ -n "$root_rel" ]] || fail "未知 extension：$id"
      extension_file="$ROOT_DIR/$root_rel/deploy/extension.env"
      example_file="$ROOT_DIR/$root_rel/deploy/extension.env.example"
      if [[ ! -f "$extension_file" ]]; then
        [[ -f "$example_file" ]] || fail "初始化模板不存在：$example_file"
        mkdir -p "$(dirname "$extension_file")"
        cp "$example_file" "$extension_file"
      fi
      output_file="$(mktemp)"
      awk '{ sub(/\r$/, ""); print }' "$extension_file" > "$output_file"
      mv "$output_file" "$extension_file"
      chmod 600 "$extension_file" 2>/dev/null || true
      echo "[apply_extension_env_values] extension=$id"
      echo "[apply_extension_env_values] 已写入 $(realpath "$extension_file" 2>/dev/null || printf '%s' "$extension_file")"
    done <<< "$enabled"
    exit 0
  fi

  local tmp_dir="$ROOT_DIR/state/deploy_input_values"
  mkdir -p "$tmp_dir"
  chmod 700 "$tmp_dir" 2>/dev/null || true
  local tmp_file=''
  tmp_file="$(mktemp "$tmp_dir/apply-extension.XXXXXX.env")"
  chmod 600 "$tmp_file" 2>/dev/null || true
  trap 'rm -f "$tmp_file"' EXIT
  local pair=''
  for pair in "${pairs[@]+"${pairs[@]}"}"; do
    printf '%s\n' "$pair" >> "$tmp_file"
  done

  export OPENCLAW_DEPLOY_INPUT_OWNER_ONLY_CHECKED=1
  export OPENCLAW_PYTHON_TOOL_EXTRA_ENV_VARS="${OPENCLAW_PYTHON_TOOL_EXTRA_ENV_VARS:-} OPENCLAW_DEPLOY_INPUT_OWNER_ONLY_CHECKED"

  python_args=(setup env input-values apply --profile "$PROFILE_ID" --scope extension --input-env-file "$tmp_file")
  [[ "$INIT_FROM_EXAMPLE" == '1' ]] && python_args+=(--init)
  set +e
  bash "$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh" "${python_args[@]}"
  local rc=$?
  set -e
  rm -f "$tmp_file"
  trap - EXIT
  exit "$rc"
}
resolve_extension_paths() {
  if [[ -n "$EXTENSION_ID" && -n "$PROFILE_ID" ]]; then
    fail '--extension 与 --profile 不能同时使用'
  fi
  if [[ -z "$EXTENSION_ID" ]]; then
    [[ -n "$PROFILE_ID" ]] || fail '必须提供 --extension <id> 或 --profile <profile_id>'
    validate_id "$PROFILE_ID"
    EXTENSION_ID="$(extension_from_profile "$PROFILE_ID")"
  fi
  validate_id "$EXTENSION_ID"
  local root_rel=''
  root_rel="$(extension_root_from_index "$EXTENSION_ID")"
  [[ -n "$root_rel" ]] || fail "未知 extension：$EXTENSION_ID"
  EXTENSION_ROOT="$ROOT_DIR/$root_rel"
  EXTENSION_FILE="$EXTENSION_ROOT/deploy/extension.env"
  EXAMPLE_FILE="$EXTENSION_ROOT/deploy/extension.env.example"
}
is_secret_key() {
  local key="$1"
  local item=''
  for item in "${secret_keys[@]+"${secret_keys[@]}"}"; do
    [[ "$item" == "$key" ]] && return 0
  done
  return 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --extension)
      require_value "$1" "${2-}"
      EXTENSION_ID="$2"
      shift 2
      ;;
    --profile)
      require_value "$1" "${2-}"
      PROFILE_ID="$2"
      shift 2
      ;;
    --init-from-example)
      INIT_FROM_EXAMPLE=1
      shift
      ;;
    --set)
      require_value "$1" "${2-}"
      [[ "$2" == *=* ]] || fail '--set 需要 KEY=VALUE 形式'
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
    --set-secret-from-env)
      require_value "$1" "${2-}"
      key="$2"
      validate_key "$key"
      value="${!key-}"
      [[ -n "$value" ]] || fail "环境变量未设置：$key"
      validate_env_value "$key" "$value"
      pairs+=("$key=$value")
      secret_keys+=("$key")
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "未知参数：$1；使用 --help 查看用法"
      ;;
  esac
done

delegate_multi_extension_profile_if_needed
resolve_extension_paths

if [[ "$INIT_FROM_EXAMPLE" == '1' && ! -f "$EXTENSION_FILE" ]]; then
  [[ -f "$EXAMPLE_FILE" ]] || fail "初始化模板不存在：$EXAMPLE_FILE"
  mkdir -p "$(dirname "$EXTENSION_FILE")"
  cp "$EXAMPLE_FILE" "$EXTENSION_FILE"
fi

[[ -f "$EXTENSION_FILE" ]] || fail "目标文件不存在：$EXTENSION_FILE；请先创建，或追加 --init-from-example"

if ((${#pairs[@]} > 0)); then
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
    separator = index($0, "\t")
    value = separator > 0 ? substr($0, separator + 1) : ""
    if (!(key in seen_input)) {
      order[++count] = key
      seen_input[key] = 1
    }
    values[key] = value
    next
  }
  {
    sub(/\r$/, "")
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
  ' "$updates_file" "$EXTENSION_FILE" > "$output_file"

  mv "$output_file" "$EXTENSION_FILE"
  rm -f "$updates_file"
  trap - EXIT
else
  output_file="$(mktemp)"
  awk '{ sub(/\r$/, ""); print }' "$EXTENSION_FILE" > "$output_file"
  mv "$output_file" "$EXTENSION_FILE"
fi

chmod 600 "$EXTENSION_FILE" 2>/dev/null || true

echo "[apply_extension_env_values] extension=$EXTENSION_ID"
echo "[apply_extension_env_values] 已写入 $(realpath "$EXTENSION_FILE" 2>/dev/null || printf '%s' "$EXTENSION_FILE")"
for pair in "${pairs[@]+"${pairs[@]}"}"; do
  key="${pair%%=*}"
  if is_secret_key "$key"; then
    echo "[apply_extension_env_values] 已更新 $key=<redacted>"
  else
    echo "[apply_extension_env_values] 已更新 $key"
  fi
done
