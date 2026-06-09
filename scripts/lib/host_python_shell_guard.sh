#!/usr/bin/env bash
# 用途：通过统一容器化 Python 入口扫描 *.sh 文件中的宿主机 Python 直调。
set -euo pipefail

HOST_PYTHON_SHELL_GUARD_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=repo_root.sh
source "$HOST_PYTHON_SHELL_GUARD_LIB_DIR/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$HOST_PYTHON_SHELL_GUARD_LIB_DIR")"
unset HOST_PYTHON_SHELL_GUARD_LIB_DIR
CURRENT_DIR="$(pwd -P)"
# shellcheck source=repo_python_env.sh
source "$ROOT_DIR/scripts/lib/repo_python_env.sh"
MOUNTS=()
PASSTHRU=()

abs_path() {
  local raw="$1"
  if [[ "$raw" = /* ]]; then
    if [[ -e "$raw" ]]; then
      if [[ -d "$raw" ]]; then
        (cd "$raw" && pwd -P)
      else
        local base
        local name
        base="$(dirname "$raw")"
        name="$(basename "$raw")"
        printf '%s/%s\n' "$(cd "$base" && pwd -P)" "$name"
      fi
    else
      printf '%s\n' "$raw"
    fi
    return 0
  fi
  if [[ -e "$CURRENT_DIR/$raw" ]]; then
    if [[ -d "$CURRENT_DIR/$raw" ]]; then
      (cd "$CURRENT_DIR/$raw" && pwd -P)
    else
      local base
      local name
      base="$(dirname "$CURRENT_DIR/$raw")"
      name="$(basename "$raw")"
      printf '%s/%s\n' "$(cd "$base" && pwd -P)" "$name"
    fi
    return 0
  fi
  printf '%s/%s\n' "$CURRENT_DIR" "$raw"
}

maybe_add_mount() {
  local candidate="$1"
  [[ "$candidate" = /* ]] || return 0
  [[ -e "$candidate" ]] || return 0
  case "$candidate" in
    "$ROOT_DIR"|"$ROOT_DIR"/*) return 0 ;;
  esac
  local existing
  for existing in "${MOUNTS[@]:-}"; do
    [[ "$existing" != "$candidate" ]] || return 0
  done
  MOUNTS+=("$candidate")
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root)
      [[ $# -ge 2 ]] || { echo "[host_python_shell_guard][FAIL] --repo-root 缺少参数" >&2; exit 2; }
      local_repo_root="$(abs_path "$2")"
      maybe_add_mount "$local_repo_root"
      PASSTHRU+=("$1" "$local_repo_root")
      shift 2
      ;;
    -*)
      PASSTHRU+=("$1")
      shift
      ;;
    *)
      local_target="$(abs_path "$1")"
      maybe_add_mount "$local_target"
      PASSTHRU+=("$local_target")
      shift
      ;;
  esac
done

REPO_PYTHON_ENV_ARGS=()
while IFS= read -r -d '' item; do
  REPO_PYTHON_ENV_ARGS+=("$item")
done < <(openclaw_repo_python_env_args "$ROOT_DIR")

RUNNER=(bash "$ROOT_DIR/scripts/runtime/run_python_container.sh" --workdir "$ROOT_DIR" "${REPO_PYTHON_ENV_ARGS[@]}")
if ((${#MOUNTS[@]})); then
  mount_path=""
  for mount_path in "${MOUNTS[@]}"; do
    RUNNER+=(--mount "$mount_path")
  done
fi
RUNNER+=(-- -m openclaw.cli guards host-python-shell)
exec "${RUNNER[@]}" "${PASSTHRU[@]}"
