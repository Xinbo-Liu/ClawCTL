#!/usr/bin/env bash
# 用途：以 shell+jq 静态检查文档入口合同；不依赖 Docker 或宿主机 Python。
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
  bash ./scripts/docs/check_documentation_entrypoints.sh
  bash ./scripts/docs/check_documentation_entrypoints.sh --stdout

说明：
  - 检查 entrypointContract 声明的必需文本与禁止文本；
  - 不生成或校验额外 manifest。
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
      echo "[check_documentation_entrypoints][FAIL] 未知参数：$1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

openclaw_static_doc_require_jq
openclaw_static_doc_validate_registry_pages
REGISTRY_PATH="$(openclaw_static_doc_registry_path)"

errors=()
entry_count=0
while IFS= read -r encoded_page; do
  [[ -n "$encoded_page" ]] || continue
  entry_count=$((entry_count + 1))
  page_json="$(openclaw_static_doc_decode_b64 "$encoded_page")"
  rel_path="$(printf '%s\n' "$page_json" | jq -r '.path | tostring')"
  file_path="$ROOT_DIR/$rel_path"
  if [[ ! -f "$file_path" ]]; then
    errors+=("$rel_path 不存在")
    [[ "$STDOUT_MODE" == '1' ]] && printf -- '- %s errors=1\n' "$rel_path"
    continue
  fi
  content="$(openclaw_static_doc_read_text "$file_path")"
  file_errors=0
  while IFS= read -r token; do
    token="$(openclaw_static_doc_trim "$token")"
    [[ -n "$token" ]] || continue
    if [[ "$content" != *"$token"* ]]; then
      errors+=("$rel_path 缺少必须文本：$token")
      file_errors=$((file_errors + 1))
    fi
  done < <(
    printf '%s\n' "$page_json" |
      jq -r '.entrypointContract.requiredRefs[]? | select(.kind == "literal" or .kind == "doc_page") | (.value // .path // "") | tostring'
  )
  while IFS= read -r token; do
    token="$(openclaw_static_doc_trim "$token")"
    [[ -n "$token" ]] || continue
    if [[ "$content" == *"$token"* ]]; then
      errors+=("$rel_path 出现禁止文本：$token")
      file_errors=$((file_errors + 1))
    fi
  done < <(
    printf '%s\n' "$page_json" |
      jq -r '.entrypointContract.forbiddenRefs[]? | select(.kind == "literal" or .kind == "doc_page") | (.value // .path // "") | tostring'
  )
  [[ "$STDOUT_MODE" == '1' ]] && printf -- '- %s errors=%s\n' "$rel_path" "$file_errors"
done < <(openclaw_static_doc_entrypoint_contracts_b64)

if [[ "$STDOUT_MODE" == '1' ]]; then
  printf '[check_documentation_entrypoints] registry=%s entries=%s\n' \
    "${REGISTRY_PATH#"$ROOT_DIR/"}" "$entry_count"
fi

if (( ${#errors[@]} > 0 )); then
  echo '[check_documentation_entrypoints] 入口边界校验失败：' >&2
  printf -- '- %s\n' "${errors[@]}" >&2
  exit 1
fi

echo '[check_documentation_entrypoints] 已通过'
