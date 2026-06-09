#!/usr/bin/env bash
# 用途：按 *.deploy_env_schema.json 检查扩展 extension.env 缺项，并输出分组修复命令。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir

PROFILE_ID=""
EXTENSION_ID=""
FORMAT="text"

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/setup/check_extension_env_values.sh --profile <profile_id> [--format text|json]
  bash ./scripts/setup/check_extension_env_values.sh --extension <id> [--format text|json]

说明：
  - 从 agent/extensions/<id>/config/control_plane/extensions.d/<id>.deploy_env_schema.json 读取 required/manual_required/secret 字段。
  - 检查 agent/extensions/<id>/deploy/extension.env 是否存在、字段是否为空或仍为 __REQUIRED__。
  - 输出只包含字段名、分组、是否 secret 与修复命令；不输出 secret 值。
USAGE
}

fail() {
  echo "[check_extension_env_values][FAIL] $*" >&2
  exit 2
}

validate_id() {
  local value="$1"
  [[ "$value" =~ ^[A-Za-z0-9_.-]+$ ]] || fail "非法 id：$value"
  [[ "$value" != "." && "$value" != ".." ]] || fail "非法 id：$value"
}

require_jq() {
  command -v jq >/dev/null 2>&1 || fail '缺少 jq；无法解析 extension deploy env schema'
}

extension_root_from_index() {
  local extension_id="$1"
  require_jq
  jq -r --arg id "$extension_id" '.extensions[]? | select(.id == $id) | .rootDir // empty' "$ROOT_DIR/agent/extensions/index.json" | tr -d '\r'
}

profile_config_path() {
  local profile_id="$1"
  awk -F '\t' -v id="$profile_id" '
    $0 ~ /^[[:space:]]*#/ { next }
    NF >= 2 && $1 == id { print $2; exit }
  ' "$ROOT_DIR/config/control_plane/profile_registry.tsv"
}

extensions_from_profile() {
  local profile_id="$1"
  local config_rel='' config_path='' known_ids=''
  require_jq
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

read_env_key() {
  local file_path="$1"
  local key="$2"
  [[ -f "$file_path" ]] || return 0
  awk -F= -v expected="$key" '
    $0 ~ /^[[:space:]]*#/ { next }
    $0 !~ /=/ { next }
    {
      name = $1
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
      if (name == expected) {
        value = substr($0, index($0, "=") + 1)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
        gsub(/^'\''|'\''$/, "", value)
        gsub(/^"|"$/, "", value)
        print value
        exit
      }
    }
  ' "$file_path"
}

is_missing_value() {
  local value="$1"
  [[ -z "$value" || "$value" == "__REQUIRED__" ]]
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      [[ $# -ge 2 ]] || fail '--profile 缺少参数'
      PROFILE_ID="$2"
      shift 2
      ;;
    --extension)
      [[ $# -ge 2 ]] || fail '--extension 缺少参数'
      EXTENSION_ID="$2"
      shift 2
      ;;
    --format)
      [[ $# -ge 2 ]] || fail '--format 缺少参数'
      FORMAT="$2"
      [[ "$FORMAT" == 'text' || "$FORMAT" == 'json' ]] || fail '--format 仅支持 text|json'
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "未知参数：$1"
      ;;
  esac
done

[[ -n "$PROFILE_ID" || -n "$EXTENSION_ID" ]] || fail '必须提供 --profile 或 --extension'
[[ -z "$PROFILE_ID" || -z "$EXTENSION_ID" ]] || fail '--profile 与 --extension 不能同时使用'
require_jq

extensions=()
if [[ -n "$EXTENSION_ID" ]]; then
  validate_id "$EXTENSION_ID"
  extensions+=("$EXTENSION_ID")
else
  validate_id "$PROFILE_ID"
  mapfile -t extensions < <(extensions_from_profile "$PROFILE_ID")
fi
if ((${#extensions[@]} == 0)); then
  if [[ "$FORMAT" == 'json' ]]; then
    jq -n --arg profile "$PROFILE_ID" '{profile: $profile, extensions: [], missing: [], status: "ready"}'
  else
    echo "[check_extension_env_values] profile=$PROFILE_ID 未启用 managed explicit extension。"
  fi
  exit 0
fi

rows_file="$(mktemp)"
trap 'rm -f "$rows_file"' EXIT
missing_count=0

for extension_id in "${extensions[@]}"; do
  root_rel="$(extension_root_from_index "$extension_id")"
  [[ -n "$root_rel" ]] || fail "未知 extension：$extension_id"
  extension_root="$ROOT_DIR/$root_rel"
  schema_path="$extension_root/config/control_plane/extensions.d/$extension_id.deploy_env_schema.json"
  env_path="$extension_root/deploy/extension.env"
  [[ -f "$schema_path" ]] || fail "extension deploy env schema 不存在：$schema_path"
  groups_json="$(jq -c '[.groups[]? | {id, title}]' "$schema_path")"
  while IFS=$'\t' read -r key group required manual_required secret; do
    [[ -n "$key" ]] || continue
    value="$(read_env_key "$env_path" "$key")"
    present=1
    missing=0
    if is_missing_value "$value"; then
      present=0
      if [[ "$required" == 'true' || "$manual_required" == 'true' ]]; then
        missing=1
        missing_count=$((missing_count + 1))
      fi
    fi
    group_title="$(jq -r --arg id "$group" '.[] | select(.id == $id) | .title // $id' <<< "$groups_json")"
    [[ -n "$group_title" ]] || group_title="$group"
    if [[ "$secret" == 'true' ]]; then
      fix_command="export $key=<secret>; bash ./scripts/setup/apply_extension_env_values.sh --extension $extension_id --init-from-example --set-secret-from-env $key"
    else
      fix_command="bash ./scripts/setup/apply_extension_env_values.sh --extension $extension_id --init-from-example --set $key=<value>"
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$extension_id" "$env_path" "$key" "$group" "$group_title" "$required" "$manual_required" "$secret" "$present" "$missing" "$fix_command" >> "$rows_file"
  done < <(
    jq -r '
      .fields[]? |
      select((.required == true) or (.manual_required == true) or (.secret == true)) |
      [
        (.key // ""),
        (.group // "default"),
        ((.required == true) | tostring),
        ((.manual_required == true) | tostring),
        ((.secret == true) | tostring)
      ] | @tsv
    ' "$schema_path" | tr -d '\r'
  )
done

if [[ "$FORMAT" == 'json' ]]; then
  jq -Rn --arg profile "$PROFILE_ID" --arg status "$([[ "$missing_count" -eq 0 ]] && printf ready || printf missing)" '
    [inputs | split("\t") | {
      extension: .[0],
      envPath: .[1],
      key: .[2],
      group: .[3],
      groupTitle: .[4],
      required: (.[5] == "true"),
      manualRequired: (.[6] == "true"),
      secret: (.[7] == "true"),
      present: (.[8] == "1"),
      missing: (.[9] == "1"),
      fixCommand: .[10]
    }] as $rows |
    {
      profile: $profile,
      status: $status,
      fields: $rows,
      missing: ($rows | map(select(.missing == true)))
    }
  ' < "$rows_file"
else
  if [[ "$missing_count" -eq 0 ]]; then
    echo "[check_extension_env_values] 扩展 env 必填项已闭合。"
  else
    echo "[check_extension_env_values][FAIL] 扩展 env 存在缺项：$missing_count"
  fi
  awk -F '\t' '
    $10 == "1" {
      group_key = $1 "|" $4 "|" $5
      if (!(group_key in printed_group)) {
        print ""
        print "## " $1 " / " $5 " (" $4 ")"
        printed_group[group_key] = 1
      }
      secret_label = ($8 == "true") ? " secret" : ""
      manual_label = ($7 == "true") ? " manual_required" : ""
      required_label = ($6 == "true") ? " required" : ""
      print "- " $3 required_label manual_label secret_label
      print "  修复：" $11
    }
  ' "$rows_file"
fi

[[ "$missing_count" -eq 0 ]] || exit 2
