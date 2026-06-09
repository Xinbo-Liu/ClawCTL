#!/usr/bin/env bash
# 用途：只读盘点本地工作区残留目标与 .gitignore 覆盖情况。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
# shellcheck source=../lib/local_workspace_policy.sh
source "$ROOT_DIR/scripts/lib/local_workspace_policy.sh"

hygiene_usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/doctor/check_local_workspace_hygiene.sh

说明：
  - 只读盘点本地残留与运行态目录，不会删除文件
  - 输出目标路径是否存在、文件数、字节数、最近更新时间
  - 校验统一真源要求的目标路径与派生物模式都已被 .gitignore 覆盖

边界：
  - 路径存在本身不算失败
  - 只有 .gitignore 覆盖缺失才失败
USAGE
}

hygiene_fail() {
  echo "[local_workspace_hygiene][FAIL] $*" >&2
  exit "${2:-2}"
}

hygiene_note() {
  echo "[local_workspace_hygiene] $*"
}

hygiene_mtime_epoch() {
  local target="$1"
  if stat -c %Y "$target" >/dev/null 2>&1; then
    stat -c %Y "$target"
    return 0
  fi
  stat -f %m "$target"
}

hygiene_format_timestamp() {
  local epoch="${1:-}"
  [[ -n "$epoch" ]] || {
    printf '%s' '-'
    return 0
  }
  if date -u -d "@$epoch" '+%Y-%m-%dT%H:%M:%SZ' >/dev/null 2>&1; then
    date -u -d "@$epoch" '+%Y-%m-%dT%H:%M:%SZ'
    return 0
  fi
  if date -u -r "$epoch" '+%Y-%m-%dT%H:%M:%SZ' >/dev/null 2>&1; then
    date -u -r "$epoch" '+%Y-%m-%dT%H:%M:%SZ'
    return 0
  fi
  printf '%s' "$epoch"
}

hygiene_find_pruned() {
  local target_path="$1"
  shift
  find "$target_path" \
    \( \
      -path '*/.git' \
      -o -path '*/.venv' \
      -o -path '*/venv' \
      -o -path '*/extension_envs' \
      -o -path '*/wheelhouse/extensions' \
      -o -path '*/pip-cache' \
      -o -path '*/__pycache__' \
      -o -path '*/.pytest_cache' \
      -o -path '*/.mypy_cache' \
      -o -path '*/.ruff_cache' \
      -o -path '*/.nox' \
      -o -path '*/.tox' \
      -o -path '*/.cache' \
      -o -path '*/htmlcov' \
      -o -path '*/dist' \
      -o -path '*/build' \
    \) -prune -o "$@"
}

hygiene_file_count() {
  local target_path="$1"
  [[ -e "$target_path" ]] || {
    printf '0'
    return 0
  }
  if [[ -f "$target_path" || -L "$target_path" ]]; then
    printf '1'
    return 0
  fi
  hygiene_find_pruned "$target_path" -type f -print0 2>/dev/null \
    | awk 'BEGIN { RS = "\0"; count = 0 } { count += 1 } END { printf "%d", count }'
}

hygiene_total_bytes() {
  local target_path="$1"
  [[ -e "$target_path" ]] || {
    printf '0'
    return 0
  }
  if [[ -f "$target_path" ]]; then
    wc -c < "$target_path" | tr -d '[:space:]'
    return 0
  fi
  if [[ -L "$target_path" ]]; then
    printf '0'
    return 0
  fi
  hygiene_find_pruned "$target_path" -type f -print0 2>/dev/null \
    | while IFS= read -r -d '' file_path; do
        [[ -f "$file_path" ]] || continue
        wc -c < "$file_path" 2>/dev/null || true
      done \
    | awk '{ sum += $1 } END { printf "%.0f", sum + 0 }'
}

hygiene_latest_epoch() {
  local target_path="$1"
  local latest=''
  local item=''
  [[ -e "$target_path" ]] || return 0
  while IFS= read -r -d '' item; do
    [[ -n "$item" ]] || continue
    [[ -e "$item" ]] || continue
    local epoch=''
    epoch="$(hygiene_mtime_epoch "$item" 2>/dev/null || true)"
    [[ -n "$epoch" ]] || continue
    if [[ -z "$latest" || "$epoch" -gt "$latest" ]]; then
      latest="$epoch"
    fi
  done < <(hygiene_find_pruned "$target_path" -print0 2>/dev/null)
  if [[ -z "$latest" ]]; then
    latest="$(hygiene_mtime_epoch "$target_path" 2>/dev/null || true)"
  fi
  printf '%s' "$latest"
}

if [[ $# -gt 0 ]]; then
  case "$1" in
    -h|--help)
      hygiene_usage
      exit 0
      ;;
    *)
      hygiene_fail "不接受额外参数：$1"
      ;;
  esac
fi

declare -A gitignore_lines=()
declare -A target_patterns=()
missing_ignore=()

while IFS= read -r line; do
  line="${line%$'\r'}"
  [[ -n "$line" ]] || continue
  gitignore_lines["$line"]=1
done < "$ROOT_DIR/.gitignore"

printf '%s\n' '| 路径 | 分类 | 默认清理 | 存在 | .gitignore 覆盖 | 文件数 | 总字节数 | 最近更新时间 (UTC) |'
printf '%s\n' '| --- | --- | --- | --- | --- | ---: | ---: | --- |'
while IFS=$'\t' read -r _target_id rel_path target_class cleanup_by_default gitignore_pattern; do
  rel_path="${rel_path%$'\r'}"
  target_class="${target_class%$'\r'}"
  cleanup_by_default="${cleanup_by_default%$'\r'}"
  gitignore_pattern="${gitignore_pattern%$'\r'}"
  target_patterns["$gitignore_pattern"]=1
  target_path="$ROOT_DIR/$rel_path"
  exists='no'
  ignored='no'
  file_count='0'
  total_bytes='0'
  latest='-'

  if [[ -e "$target_path" ]]; then
    exists='yes'
    file_count="$(hygiene_file_count "$target_path")"
    total_bytes="$(hygiene_total_bytes "$target_path")"
    latest="$(hygiene_format_timestamp "$(hygiene_latest_epoch "$target_path")")"
  fi

  if [[ -n "${gitignore_lines[$gitignore_pattern]:-}" ]]; then
    ignored='yes'
  else
    missing_ignore+=("$gitignore_pattern")
  fi

  printf '| `%s` | `%s` | %s | %s | %s | %s | %s | %s |\n' \
    "$rel_path" "$target_class" "$cleanup_by_default" "$exists" "$ignored" "$file_count" "$total_bytes" "$latest"
done < <(openclaw_local_workspace_policy_targets)

printf '\n%s\n' '| 派生物模式 | .gitignore 覆盖 |'
printf '%s\n' '| --- | --- |'
while IFS= read -r pattern; do
  [[ -n "$pattern" ]] || continue
  pattern="${pattern%$'\r'}"
  [[ -z "${target_patterns[$pattern]:-}" ]] || continue
  ignored='no'
  if [[ -n "${gitignore_lines[$pattern]:-}" ]]; then
    ignored='yes'
  else
    missing_ignore+=("$pattern")
  fi
  printf '| `%s` | %s |\n' "$pattern" "$ignored"
done < <(openclaw_local_workspace_policy_gitignore_patterns)

if (( ${#missing_ignore[@]} > 0 )); then
  hygiene_fail "以下模式未被 .gitignore 覆盖：${missing_ignore[*]}" 3
fi

hygiene_note '扫描完成；存在路径仅作为本机卫生信息展示，不视为失败。'
