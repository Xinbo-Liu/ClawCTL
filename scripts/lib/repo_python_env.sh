#!/usr/bin/env bash
# 用途：repo bootstrap env 的 shell 代理；PYTHONPATH 前缀在本文件内完成，保证容器内加载仓库 Python bootstrap 路径。

if [[ -n "${OPENCLAW_REPO_PYTHON_ENV_SH_LOADED:-}" ]]; then
  return 0 2>/dev/null || exit 0
fi
OPENCLAW_REPO_PYTHON_ENV_SH_LOADED=1

OPENCLAW_REPO_PYTHON_ENV_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=repo_root.sh
source "$OPENCLAW_REPO_PYTHON_ENV_LIB_DIR/repo_root.sh"
OPENCLAW_REPO_PYTHON_ENV_ROOT_DIR="$(openclaw_repo_root_from "$OPENCLAW_REPO_PYTHON_ENV_LIB_DIR")"
unset OPENCLAW_REPO_PYTHON_ENV_LIB_DIR
OPENCLAW_REPO_PYTHON_ENV_TRUTH_REL="config/governance/support/repo_python_bootstrap.env"

openclaw_repo_python_env_truth_value() {
  local root_dir="${1:?root_dir is required}" key="${2:?key is required}"
  local truth_file="$root_dir/$OPENCLAW_REPO_PYTHON_ENV_TRUTH_REL"
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
  ' "$truth_file"
}

openclaw_repo_python_env_pythonpath() {
  local root_dir="${1:-$OPENCLAW_REPO_PYTHON_ENV_ROOT_DIR}"
  local rels rel item result=""
  local -a rel_items=()
  rels="$(openclaw_repo_python_env_truth_value "$root_dir" OPENCLAW_REPO_BOOTSTRAP_PYTHONPATH_RELS)"
  [[ -n "$rels" ]] || return 0
  IFS='|' read -r -a rel_items <<<"$rels"
  for rel in "${rel_items[@]}"; do
    item="$(printf '%s' "$rel" | tr -d '[:space:]')"
    [[ -n "$item" ]] || continue
    if [[ -n "$result" ]]; then
      result="$result:$root_dir/$item"
    else
      result="$root_dir/$item"
    fi
  done
  [[ -n "$result" ]] || return 0
  printf '%s\n' "$result"
}

openclaw_repo_python_env_defaults_lines() {
  local root_dir="${1:-$OPENCLAW_REPO_PYTHON_ENV_ROOT_DIR}"
  local env_name="" truth_key="" value=""
  local -a entries=(
    'PYTHONDONTWRITEBYTECODE:OPENCLAW_REPO_BOOTSTRAP_PYTHONDONTWRITEBYTECODE'
    'PYTHONIOENCODING:OPENCLAW_REPO_BOOTSTRAP_PYTHONIOENCODING'
    'PYTHONUTF8:OPENCLAW_REPO_BOOTSTRAP_PYTHONUTF8'
  )
  for entry in "${entries[@]}"; do
    env_name="${entry%%:*}"
    truth_key="${entry#*:}"
    value="$(openclaw_repo_python_env_truth_value "$root_dir" "$truth_key")"
    if [[ -z "$value" ]]; then
      echo "[repo_python_env][FAIL] $truth_key 不能为空：$root_dir/$OPENCLAW_REPO_PYTHON_ENV_TRUTH_REL" >&2
      return 2
    fi
    printf '%s=%s\n' "$env_name" "$value"
  done
}

openclaw_repo_python_env_surface() {
  local root_dir="${1:-$OPENCLAW_REPO_PYTHON_ENV_ROOT_DIR}"
  shift || true
  local proxy_python="${OPENCLAW_REPO_PYTHON_PROXY_PYTHON:-}"
  if [[ -n "$proxy_python" ]]; then
    (cd "$OPENCLAW_REPO_PYTHON_ENV_ROOT_DIR" && "$proxy_python" -B -m openclaw.lib.repo.bootstrap_surface --root-dir "$root_dir" "$@")
    return $?
  fi
  bash "$OPENCLAW_REPO_PYTHON_ENV_ROOT_DIR/scripts/runtime/run_python_container.sh" \
    --workdir "$OPENCLAW_REPO_PYTHON_ENV_ROOT_DIR" \
    --env "OPENCLAW_REPO_ROOT=$OPENCLAW_REPO_PYTHON_ENV_ROOT_DIR" \
    --env "OPENCLAW_TOOLS_ROOT=$OPENCLAW_REPO_PYTHON_ENV_ROOT_DIR" \
    -- -m openclaw.lib.repo.bootstrap_surface --root-dir "$root_dir" "$@"
}

openclaw_repo_python_env_lines() {
  local root_dir="${1:-$OPENCLAW_REPO_PYTHON_ENV_ROOT_DIR}"
  local pythonpath=""
  openclaw_repo_python_env_defaults_lines "$root_dir" || return $?
  pythonpath="$(openclaw_repo_python_env_pythonpath "$root_dir")"
  [[ -z "$pythonpath" ]] || printf 'PYTHONPATH=%s\n' "$pythonpath"
}

openclaw_repo_python_env_args() {
  local root_dir="${1:-$OPENCLAW_REPO_PYTHON_ENV_ROOT_DIR}"
  local line="" lines=""
  lines="$(openclaw_repo_python_env_lines "$root_dir")" || return $?
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    printf '%s\0%s\0' --env "$line"
  done <<< "$lines"
}
