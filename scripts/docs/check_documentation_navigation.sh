#!/usr/bin/env bash
# 用途：以 shell+jq 静态检查导航页任务分流结构；不依赖 Docker 或宿主机 Python。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
source "$ROOT_DIR/scripts/docs/lib/static_doc_checks.sh"

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/docs/check_documentation_navigation.sh
  bash ./scripts/docs/check_documentation_navigation.sh --stdout

说明：
  校验导航页是否仍具备最小任务分流结构，避免目录页退化为无序长索引。
USAGE
}

STDOUT_MODE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --stdout) STDOUT_MODE=1 ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[check_documentation_navigation][FAIL] 未知参数：$1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

openclaw_static_doc_require_jq
openclaw_static_doc_validate_registry_pages

errors=()
page_count=0
while IFS= read -r encoded_page; do
  [[ -n "$encoded_page" ]] || continue
  page_count=$((page_count + 1))
  page_json="$(openclaw_static_doc_decode_b64 "$encoded_page")"
  rel_path="$(printf '%s\n' "$page_json" | jq -r '.path | tostring')"
  file_path="$ROOT_DIR/$rel_path"
  file_errors=0
  if [[ ! -f "$file_path" ]]; then
    errors+=("$rel_path 不存在")
    [[ "$STDOUT_MODE" == '1' ]] && printf -- '- %s errors=1\n' "$rel_path"
    continue
  fi
  content="$(openclaw_static_doc_read_text "$file_path")"
  while IFS= read -r token; do
    token="$(openclaw_static_doc_trim "$token")"
    [[ -n "$token" ]] || continue
    if [[ "$content" != *"$token"* ]]; then
      errors+=("$rel_path 缺少导航区块：$token")
      file_errors=$((file_errors + 1))
    fi
  done < <(printf '%s\n' "$page_json" | jq -r '.navigationContract.requiredTokens[]? | tostring')
  min_links="$(printf '%s\n' "$page_json" | jq -r '.navigationContract.minLinks // 0')"
  link_total="$(openclaw_static_doc_markdown_link_count "$file_path")"
  if [[ "$min_links" != '0' && "$link_total" -lt "$min_links" ]]; then
    errors+=("$rel_path 有效链接数量不足：需要至少 $min_links 个，当前 $link_total 个")
    file_errors=$((file_errors + 1))
  fi
  [[ "$STDOUT_MODE" == '1' ]] && printf -- '- %s errors=%s\n' "$rel_path" "$file_errors"
done < <(openclaw_static_doc_navigation_contracts_b64)

if [[ "$STDOUT_MODE" == '1' ]]; then
  printf '[check_documentation_navigation] count=%s\n' "$page_count"
fi

if (( ${#errors[@]} > 0 )); then
  echo '[check_documentation_navigation] 导航结构校验失败：' >&2
  printf -- '- %s\n' "${errors[@]}" >&2
  exit 1
fi

echo '[check_documentation_navigation] 已通过'
