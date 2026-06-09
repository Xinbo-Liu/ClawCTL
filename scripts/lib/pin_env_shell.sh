#!/usr/bin/env bash
# 用途：为镜像 pin 更新脚本提供共享的 env 回写与本地 deploy/.env 生成逻辑，避免 update_openclaw_pin / update_runtime_pin 各自维护一套 awk 与生成流程。
set -euo pipefail

pin_env_upsert_key() {
  local file_path="$1"
  local key="$2"
  local value="$3"
  local tmp_file
  tmp_file="$(mktemp)"
  if [[ -f "$file_path" ]]; then
    awk -v key="$key" -v value="$value" '
      BEGIN { done = 0 }
      $0 ~ ("^" key "=") {
        print key "=" value
        done = 1
        next
      }
      { print }
      END {
        if (!done) print key "=" value
      }
    ' "$file_path" >"$tmp_file"
  else
    printf '%s=%s\n' "$key" "$value" >"$tmp_file"
  fi
  cp "$tmp_file" "$file_path"
  rm -f "$tmp_file"
}

pin_env_ensure_local_env() {
  local root_dir="$1"
  local deploy_env="$2"
  [[ -f "$deploy_env" ]] && return 0
  bash "$root_dir/scripts/runtime/run_openclaw_python_tool.sh" setup env render --output "$deploy_env" >/dev/null
}
