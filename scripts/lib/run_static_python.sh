#!/usr/bin/env bash
# 用途：统一执行只读静态 Python 检查；执行面固定走控制面容器，帮助面可离线查看。
set -euo pipefail

RUN_STATIC_PYTHON_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=repo_root.sh
source "$RUN_STATIC_PYTHON_LIB_DIR/repo_root.sh"
ROOT_DIR="${ROOT_DIR:-$(openclaw_repo_root_from "$RUN_STATIC_PYTHON_LIB_DIR")}"
unset RUN_STATIC_PYTHON_LIB_DIR
# shellcheck source=repo_python_env.sh
source "$ROOT_DIR/scripts/lib/repo_python_env.sh"

WORKDIR="$ROOT_DIR"
EXTRA_ENVS=()
EXTRA_MOUNTS=()
READINESS_LABEL="${OPENCLAW_STATIC_PYTHON_READINESS_LABEL:-当前静态检查入口}"

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/lib/run_static_python.sh [--workdir <dir>] [--mount <path>] [--env KEY=VALUE] -- <python args...>

说明：
  - 只用于仓库内只读静态 Python 检查；
  - `--help` 可离线查看；真正执行属于 Docker 必需的静态 Python 检查；
  - 统一复用固定控制面容器；宿主机 Python 不属于支持路径；
  - 建议先执行 bash ./scripts/testing/check_repo_test_readiness.sh，再进入具体 docs / doctor / unittest / release gate 入口；
  - 若只缺少控制面镜像，执行 bash ./scripts/setup/prepare_control_plane_medium.sh，离线场景追加 --offline --image-archive <local-path>。
USAGE
}

readiness_next_step() {
  echo "[run_static_python][NEXT] $*" >&2
}

ensure_static_python_readiness() {
  local docker_info_output=''

  if ! command -v docker >/dev/null 2>&1; then
    echo "[run_static_python][FAIL] $READINESS_LABEL 属于 Docker 必需的静态 Python 检查；未检测到 docker CLI。" >&2
    readiness_next_step '帮助面仍可直接执行 --help；真正执行前请先安装并启动 Docker。'
    readiness_next_step '仓库级静态治理建议先执行 bash ./scripts/testing/check_repo_test_readiness.sh。'
    exit 2
  fi

  if ! docker info >/dev/null 2>&1; then
    docker_info_output="$(docker info 2>&1 || true)"
    echo "[run_static_python][FAIL] $READINESS_LABEL 当前无法连接 Docker daemon；静态 Python 检查固定要求 Docker daemon 与控制面容器。" >&2
    [[ -z "$docker_info_output" ]] || printf '%s\n' "$docker_info_output" >&2
    readiness_next_step '先修复 Docker daemon 可访问性或当前用户权限。'
    readiness_next_step '修复后重新执行 bash ./scripts/testing/check_repo_test_readiness.sh。'
    exit 2
  fi

  # shellcheck source=image_env.sh
  source "$ROOT_DIR/scripts/lib/image_env.sh"
  image_env_load

  if ! docker image inspect "$OPENCLAW_CONTROL_PLANE_IMAGE" >/dev/null 2>&1; then
    # shellcheck source=deployment_images.sh
    source "$ROOT_DIR/scripts/lib/deployment_images.sh"
    if ! deployment_images_resolve_verified_local_ref "$OPENCLAW_CONTROL_PLANE_IMAGE" >/dev/null 2>&1; then
      echo "[run_static_python][FAIL] $READINESS_LABEL 需要 OPENCLAW_CONTROL_PLANE_IMAGE，但当前本地尚未准备：$OPENCLAW_CONTROL_PLANE_IMAGE" >&2
      readiness_next_step '先执行 bash ./scripts/setup/prepare_control_plane_medium.sh。'
      readiness_next_step '离线场景使用 bash ./scripts/setup/prepare_control_plane_medium.sh --offline --image-archive <local-path>。'
      readiness_next_step '完成后重新执行 bash ./scripts/testing/check_repo_test_readiness.sh。'
      exit 2
    fi
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workdir)
      [[ $# -ge 2 ]] || { echo '[run_static_python][FAIL] --workdir 缺少路径参数' >&2; exit 2; }
      WORKDIR="$2"
      shift 2
      ;;
    --env)
      [[ $# -ge 2 ]] || { echo '[run_static_python][FAIL] --env 缺少 KEY=VALUE 参数' >&2; exit 2; }
      EXTRA_ENVS+=("$2")
      shift 2
      ;;
    --mount)
      [[ $# -ge 2 ]] || { echo '[run_static_python][FAIL] --mount 缺少路径参数' >&2; exit 2; }
      EXTRA_MOUNTS+=("$2")
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    *)
      echo "[run_static_python][FAIL] 未知参数：$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

[[ $# -gt 0 ]] || { echo '[run_static_python][FAIL] 缺少 Python 参数' >&2; usage >&2; exit 2; }
[[ -d "$WORKDIR" ]] || { echo "[run_static_python][FAIL] workdir 不存在：$WORKDIR" >&2; exit 2; }

if [[ "${OPENCLAW_STATIC_PYTHON_IN_CONTAINER:-0}" == "1" ]]; then
  while IFS= read -r repo_env_line; do
    [[ -n "$repo_env_line" ]] || continue
    export "$repo_env_line"
  done < <(openclaw_repo_python_env_lines "$ROOT_DIR")
  if ((${#EXTRA_ENVS[@]} > 0)); then
    for assignment in "${EXTRA_ENVS[@]}"; do
      [[ "$assignment" == *=* ]] || {
        echo "[run_static_python][FAIL] --env 必须为 KEY=VALUE：$assignment" >&2
        exit 2
      }
      export "$assignment"
    done
  fi
  if ((${#EXTRA_MOUNTS[@]} > 0)); then
    local_mount=""
    for local_mount in "${EXTRA_MOUNTS[@]}"; do
      [[ -e "$local_mount" ]] || {
        echo "[run_static_python][FAIL] 已在控制面容器内，无法新增不存在的挂载路径：$local_mount" >&2
        exit 2
      }
    done
  fi
  cd "$WORKDIR"
  exec "${PYTHON_BIN:-python3}" -B "$@"
fi

ensure_static_python_readiness

CONTAINER_ARGS=(
  --workdir "$WORKDIR"
  --env "OPENCLAW_REPO_ROOT=$ROOT_DIR"
  --env "OPENCLAW_TOOLS_ROOT=$ROOT_DIR"
  --env 'OPENCLAW_STATIC_PYTHON_IN_CONTAINER=1'
  --env 'PYTHONIOENCODING=UTF-8'
  --env 'PYTHONUTF8=1'
)
while IFS= read -r repo_env_line; do
  [[ -n "$repo_env_line" ]] || continue
  CONTAINER_ARGS+=(--env "$repo_env_line")
done < <(openclaw_repo_python_env_lines "$ROOT_DIR")
if ((${#EXTRA_ENVS[@]} > 0)); then
  for assignment in "${EXTRA_ENVS[@]}"; do
    [[ "$assignment" == *=* ]] || {
      echo "[run_static_python][FAIL] --env 必须为 KEY=VALUE：$assignment" >&2
      exit 2
    }
    CONTAINER_ARGS+=(--env "$assignment")
  done
fi
if ((${#EXTRA_MOUNTS[@]} > 0)); then
  for mount_path in "${EXTRA_MOUNTS[@]}"; do
    CONTAINER_ARGS+=(--mount "$mount_path")
  done
fi

exec bash "$ROOT_DIR/scripts/runtime/run_python_container.sh" "${CONTAINER_ARGS[@]}" -- "$@"
