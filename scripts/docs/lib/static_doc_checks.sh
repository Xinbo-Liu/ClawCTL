#!/usr/bin/env bash
# 用途：为 docs registry、入口和导航检查提供共享 shell+jq 辅助函数。

if [[ -n "${OPENCLAW_STATIC_DOC_CHECKS_SH_LOADED:-}" ]]; then
  return 0 2>/dev/null || exit 0
fi
OPENCLAW_STATIC_DOC_CHECKS_SH_LOADED=1

OPENCLAW_STATIC_DOC_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=repo_root_bootstrap.sh
source "$OPENCLAW_STATIC_DOC_LIB_DIR/repo_root_bootstrap.sh"
openclaw_docs_lib_source_repo_root "$OPENCLAW_STATIC_DOC_LIB_DIR" || return 2 2>/dev/null || exit 2
unset -f openclaw_docs_lib_source_repo_root
OPENCLAW_STATIC_DOC_ROOT_DIR="$(openclaw_repo_root_from "$OPENCLAW_STATIC_DOC_LIB_DIR")"
# shellcheck source=scripts/lib/control_plane_config_paths.sh
source "$OPENCLAW_STATIC_DOC_ROOT_DIR/scripts/lib/control_plane_config_paths.sh"
# shellcheck source=scripts/lib/repo_contracts.sh
source "$OPENCLAW_STATIC_DOC_ROOT_DIR/scripts/lib/repo_contracts.sh"
repo_contract_assign_path OPENCLAW_STATIC_DOC_BASE_REGISTRY_PATH governance.docs_registry
OPENCLAW_STATIC_DOC_PYTHON_RUNNER="$OPENCLAW_STATIC_DOC_ROOT_DIR/scripts/lib/run_static_python.sh"
OPENCLAW_STATIC_DOC_CONFIG_PATH="${OPENCLAW_STATIC_DOC_CONFIG_PATH:-$(openclaw_control_plane_resolve_config_path agent_platform)}"
OPENCLAW_STATIC_DOC_RENDERED_REGISTRY_PATH=""

openclaw_static_doc_trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

openclaw_static_doc_normalize_text() {
  tr -d '\r'
}

openclaw_static_doc_read_text() {
  local path="$1"
  [[ -f "$path" ]] || return 1
  openclaw_static_doc_normalize_text < "$path"
}

openclaw_static_doc_require_file() {
  local path="$1"
  [[ -f "$path" ]] || {
    echo "[static-doc-check][FAIL] 缺少文件：${path#"$OPENCLAW_STATIC_DOC_ROOT_DIR/"}" >&2
    return 1
  }
}

openclaw_static_doc_require_jq() {
  command -v jq >/dev/null 2>&1 || {
    echo '[static-doc-check][FAIL] 未检测到 jq；文档静态检查要求 jq 可用。' >&2
    return 1
  }
}

openclaw_static_doc_require_python_runner() {
  openclaw_static_doc_require_file "$OPENCLAW_STATIC_DOC_PYTHON_RUNNER"
}

openclaw_static_doc_materialize_registry() {
  if [[ -n "$OPENCLAW_STATIC_DOC_RENDERED_REGISTRY_PATH" && -f "$OPENCLAW_STATIC_DOC_RENDERED_REGISTRY_PATH" ]]; then
    printf '%s\n' "$OPENCLAW_STATIC_DOC_RENDERED_REGISTRY_PATH"
    return 0
  fi
  openclaw_static_doc_require_jq || return 1
  openclaw_static_doc_require_python_runner || return 1
  openclaw_static_doc_require_file "$OPENCLAW_STATIC_DOC_BASE_REGISTRY_PATH" || return 1

  local tmpfile=''
  tmpfile="$(mktemp)"
  if ! bash "$OPENCLAW_STATIC_DOC_PYTHON_RUNNER" \
    --workdir "$OPENCLAW_STATIC_DOC_ROOT_DIR" \
    --env "OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH=$OPENCLAW_STATIC_DOC_CONFIG_PATH" \
    -- -m openclaw.docs.support.docs_registry \
      --dump-json \
      --config-path "$OPENCLAW_STATIC_DOC_CONFIG_PATH" > "$tmpfile"; then
    rm -f "$tmpfile"
    return 1
  fi
  OPENCLAW_STATIC_DOC_RENDERED_REGISTRY_PATH="$tmpfile"
  printf '%s\n' "$OPENCLAW_STATIC_DOC_RENDERED_REGISTRY_PATH"
}

openclaw_static_doc_registry_path() {
  openclaw_static_doc_materialize_registry
}

openclaw_static_doc_validate_registry_pages() {
  openclaw_static_doc_require_jq || return 1
  local registry_path=''
  registry_path="$(openclaw_static_doc_registry_path)" || return 1
  openclaw_static_doc_require_file "$registry_path" || return 1

  local pages_type=''
  pages_type="$(jq -r '.pages | type' "$registry_path" 2>/dev/null || true)"
  [[ "$pages_type" == 'array' ]] || {
    echo '[static-doc-check][FAIL] pages 顶层必须为数组' >&2
    return 1
  }

  local page_count=0
  page_count="$(jq '.pages | length' "$registry_path")"
  local index=0
  local item_type=''
  local path=''
  declare -A seen_paths=()
  while (( index < page_count )); do
    item_type="$(jq -r --argjson index "$index" '.pages[$index] | type' "$registry_path")"
    [[ "$item_type" == 'object' ]] || {
      echo "[static-doc-check][FAIL] pages[$index] 必须为对象" >&2
      return 1
    }
    path="$(jq -r --argjson index "$index" '.pages[$index].path // ""' "$registry_path")"
    path="$(openclaw_static_doc_trim "$path")"
    [[ -n "$path" ]] || {
      echo "[static-doc-check][FAIL] pages[$index].path 不能为空" >&2
      return 1
    }
    if [[ -n "${seen_paths[$path]:-}" ]]; then
      echo "[static-doc-check][FAIL] pages.path 不能重复：$path" >&2
      return 1
    fi
    seen_paths["$path"]=1
    index=$((index + 1))
  done
}

openclaw_static_doc_registered_pages_b64() {
  local registry_path=''
  registry_path="$(openclaw_static_doc_registry_path)" || return 1
  jq -r '.pages[] | @base64' "$registry_path"
}

openclaw_static_doc_decode_b64() {
  local encoded="$1"
  printf '%s' "$encoded" | openclaw_static_doc_normalize_text | base64 --decode
}

openclaw_static_doc_entrypoint_contracts_b64() {
  local registry_path=''
  registry_path="$(openclaw_static_doc_registry_path)" || return 1
  jq -r '.pages[] | select((.entrypointContract | type) == "object") | @base64' "$registry_path"
}

openclaw_static_doc_navigation_contracts_b64() {
  local registry_path=''
  registry_path="$(openclaw_static_doc_registry_path)" || return 1
  jq -r '.pages[] | select((.navigationContract | type) == "object") | @base64' "$registry_path"
}

openclaw_static_doc_markdown_link_count() {
  local target_path="$1"
  [[ -f "$target_path" ]] || {
    printf '0\n'
    return 0
  }
  grep -oE '\[[^]]+\]\([^)]+\)' "$target_path" 2>/dev/null | wc -l | tr -d ' '
}
