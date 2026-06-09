#!/usr/bin/env bash
# 用途：以 shell+jq + merged docs registry 检查 pages 结构与登记页面存在性。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
# shellcheck source=../lib/control_plane_config_paths.sh
source "$ROOT_DIR/scripts/lib/control_plane_config_paths.sh"
REQUESTED_CONFIG_PATH=""
PROFILE_ID="agent_platform"
EXPLICIT_PROFILE="0"
RESOLVED_CONFIG_PATH=""
source "$ROOT_DIR/scripts/docs/lib/static_doc_checks.sh"

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/docs/check_docs_registry_sync.sh
  bash ./scripts/docs/check_docs_registry_sync.sh --stdout
  bash ./scripts/docs/check_docs_registry_sync.sh --config-path <control-plane-config-path>

说明：
  - 校验 merged docs registry 的 pages 结构、path 唯一性与登记页面存在性；
  - docs registry 由基座 registry + enabled extension docs fragment additive merge 组成；
  - 不生成第二份仓库真源，只在检查进程内 materialize merged 结果。
USAGE
}

STDOUT_MODE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --stdout)
      STDOUT_MODE=1
      ;;
    --config-path)
      [[ $# -ge 2 ]] || { echo '[docs_registry][FAIL] --config-path 缺少路径参数' >&2; exit 2; }
      REQUESTED_CONFIG_PATH="$2"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[docs_registry][FAIL] 未知参数：$1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [[ -z "$REQUESTED_CONFIG_PATH" ]]; then
  openclaw_control_plane_apply_default_selection_from_env_files \
    REQUESTED_CONFIG_PATH \
    PROFILE_ID \
    EXPLICIT_PROFILE \
    "$ROOT_DIR/deploy/.env|deploy/.env" \
    "$ROOT_DIR/deploy/site.env|deploy/site.env"
fi
RESOLVED_CONFIG_PATH="$(openclaw_control_plane_resolve_config_path "$PROFILE_ID" "$REQUESTED_CONFIG_PATH" "$EXPLICIT_PROFILE")"
export OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH="$RESOLVED_CONFIG_PATH"
export OPENCLAW_STATIC_DOC_CONFIG_PATH="$RESOLVED_CONFIG_PATH"

openclaw_static_doc_require_jq
openclaw_static_doc_validate_registry_pages
REGISTRY_PATH="$(openclaw_static_doc_registry_path)"

errors=()
page_count=0
while IFS= read -r encoded_page; do
  [[ -n "$encoded_page" ]] || continue
  page_count=$((page_count + 1))
  page_json="$(openclaw_static_doc_decode_b64 "$encoded_page")"
  rel_path="$(printf '%s\n' "$page_json" | jq -r '.path | tostring')"
  file_path="$ROOT_DIR/$rel_path"
  if [[ ! -f "$file_path" ]]; then
    errors+=("$rel_path 不存在")
    if [[ "$STDOUT_MODE" == '1' ]]; then
      printf -- '- %s status=missing\n' "$rel_path"
    fi
    continue
  fi
  if [[ "$STDOUT_MODE" == '1' ]]; then
    printf -- '- %s status=present role=%s entryLevel=%s\n' \
      "$rel_path" \
      "$(printf '%s\n' "$page_json" | jq -r '.role // "" | tostring')" \
      "$(printf '%s\n' "$page_json" | jq -r '.entryLevel // "" | tostring')"
  fi
done < <(openclaw_static_doc_registered_pages_b64)

if [[ "$STDOUT_MODE" == '1' ]]; then
  printf '[docs_registry] registry=%s config=%s pages=%s\n' \
    "${REGISTRY_PATH#"$ROOT_DIR/"}" \
    "${RESOLVED_CONFIG_PATH#"$ROOT_DIR/"}" \
    "$page_count"
fi

if (( ${#errors[@]} > 0 )); then
  echo '[docs_registry] 同步校验失败：' >&2
  printf -- '- %s\n' "${errors[@]}" >&2
  exit 1
fi

echo '[docs_registry] 已通过'
