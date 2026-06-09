#!/usr/bin/env bash
# 用途：统一通过 OPENCLAW_CONTROL_PLANE_IMAGE 容器执行 Python 代码，避免脚本直接依赖宿主机 Python。
# 用法：
#   bash ./scripts/runtime/run_python_container.sh -- path/to/script.py arg1 arg2
#   bash ./scripts/runtime/run_python_container.sh -- - <<'PY'
#   print('hello')
#   PY
#   bash ./scripts/runtime/run_python_container.sh --stdin -- path/to/script_that_reads_stdin.py
#   bash ./scripts/runtime/run_python_container.sh --mount /tmp/work --network host -- path/to/script.py
#   bash ./scripts/runtime/run_python_container.sh --mount-to state/openclaw/control_plane /home/openclaw/.openclaw -- -c 'print("ok")'
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir

WORKDIR="${PWD}"
NETWORK_MODE=""
USER_SPEC="$(id -u):$(id -g)"
STDIN_MODE="auto"
EXTRA_MOUNT_SOURCES=()
EXTRA_MOUNT_TARGETS=()
ENV_VARS=(
  "PYTHONDONTWRITEBYTECODE=1"
  "PYTHONNOUSERSITE=1"
  "PYTHONUNBUFFERED=1"
  "TZ=${TZ:-Asia/Shanghai}"
)

# 将输入路径解析为绝对路径，并兼容仓库内相对路径。
abs_path() {
  local target="$1"
  local base=""
  local name=""
  if [[ "$target" = /* ]]; then
    if [[ -d "$target" ]]; then
      (cd "$target" && pwd)
    else
      base="$(dirname "$target")"
      name="$(basename "$target")"
      printf '%s/%s\n' "$(cd "$base" && pwd)" "$name"
    fi
    return 0
  fi
  if [[ -d "$target" ]]; then
    (cd "$target" && pwd)
    return 0
  fi
  base="$(dirname "$target")"
  name="$(basename "$target")"
  printf '%s/%s\n' "$(cd "$ROOT_DIR/$base" && pwd)" "$name"
}

# 判断给定路径是否仍位于当前仓库根目录之下。
in_repo() {
  local candidate="$1"
  case "$candidate" in
    "$ROOT_DIR"|"$ROOT_DIR"/*) return 0 ;;
    *) return 1 ;;
  esac
}

# 按宿主机 SELinux 状态生成 docker 挂载参数。
volume_spec() {
  local target="$1"
  if command -v getenforce >/dev/null 2>&1; then
    local mode
    mode="$(getenforce 2>/dev/null || true)"
    if [[ "$mode" == "Enforcing" || "$mode" == "Permissive" ]]; then
      printf '%s:%s:Z\n' "$target" "$target"
      return 0
    fi
  fi
  printf '%s:%s\n' "$target" "$target"
}

# 校验并登记额外挂载目录，避免重复挂载。
add_mount() {
  local raw="$1"
  local target="${2:-}"
  local mounted
  mounted="$(abs_path "$raw")"
  [[ -e "$mounted" ]] || {
    echo "[python_container] 挂载路径不存在：$raw" >&2
    exit 2
  }
  if [[ -z "$target" ]]; then
    target="$mounted"
  fi
  [[ "$target" = /* ]] || {
    echo "[python_container] 挂载目标必须是容器内绝对路径：$target" >&2
    exit 2
  }
  if ((${#EXTRA_MOUNT_SOURCES[@]})); then
    local index
    for index in "${!EXTRA_MOUNT_SOURCES[@]}"; do
      [[ "${EXTRA_MOUNT_SOURCES[$index]}|${EXTRA_MOUNT_TARGETS[$index]}" != "$mounted|$target" ]] || return 0
    done
  fi
  EXTRA_MOUNT_SOURCES+=("$mounted")
  EXTRA_MOUNT_TARGETS+=("$target")
}

# 默认不把调用者标准输入暴露给容器，避免 runner 作为管道中间命令时吞掉后续命令。
# 仅 Python 从 stdin 读取源码（首个参数为 '-'）时自动挂载；数据流 stdin 需调用方显式传 --stdin。
should_attach_stdin() {
  case "$STDIN_MODE" in
    auto)
      [[ "${1:-}" == "-" ]]
      ;;
    attach)
      return 0
      ;;
    none)
      return 1
      ;;
    *)
      echo "[python_container] 内部错误：未知 stdin 模式：$STDIN_MODE" >&2
      exit 2
      ;;
  esac
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stdin|--attach-stdin)
      STDIN_MODE="attach"
      shift
      ;;
    --no-stdin)
      STDIN_MODE="none"
      shift
      ;;
    --workdir)
      [[ $# -ge 2 ]] || { echo "[python_container] --workdir 缺少参数" >&2; exit 2; }
      WORKDIR="$(abs_path "$2")"
      shift 2
      ;;
    --mount)
      [[ $# -ge 2 ]] || { echo "[python_container] --mount 缺少参数" >&2; exit 2; }
      add_mount "$2"
      shift 2
      ;;
    --mount-to)
      [[ $# -ge 3 ]] || { echo "[python_container] --mount-to 缺少 source target 参数" >&2; exit 2; }
      add_mount "$2" "$3"
      shift 3
      ;;
    --network)
      [[ $# -ge 2 ]] || { echo "[python_container] --network 缺少参数" >&2; exit 2; }
      NETWORK_MODE="$2"
      shift 2
      ;;
    --env)
      [[ $# -ge 2 ]] || { echo "[python_container] --env 缺少参数" >&2; exit 2; }
      ENV_VARS+=("$2")
      shift 2
      ;;
    --)
      shift
      break
      ;;
    *)
      echo "[python_container] 未知参数：$1" >&2
      exit 2
      ;;
  esac
done

[[ $# -gt 0 ]] || { echo "[python_container] 缺少 python 参数" >&2; exit 2; }
[[ -d "$WORKDIR" ]] || { echo "[python_container] workdir 不存在：$WORKDIR" >&2; exit 2; }

if [[ "${OPENCLAW_STATIC_PYTHON_IN_CONTAINER:-0}" == "1" || "${OPENCLAW_PYTHON_CONTAINER_IN_CONTAINER:-0}" == "1" ]]; then
  if [[ -n "$NETWORK_MODE" ]]; then
    echo "[python_container] 已在控制面容器内，无法切换 Docker network：$NETWORK_MODE" >&2
    exit 2
  fi
  for local_env in "${ENV_VARS[@]}"; do
    export "$local_env"
  done
  if ((${#EXTRA_MOUNT_SOURCES[@]})); then
    for local_mount in "${EXTRA_MOUNT_TARGETS[@]}"; do
      [[ -e "$local_mount" ]] || {
        echo "[python_container] 已在控制面容器内，无法新增不存在的挂载路径：$local_mount" >&2
        exit 2
      }
    done
  fi
  cd "$WORKDIR"
  if should_attach_stdin "$@"; then
    exec "${PYTHON_BIN:-python3}" -B "$@"
  fi
  exec "${PYTHON_BIN:-python3}" -B "$@" </dev/null
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "[python_container] 未检测到 docker；Python 工具必须通过控制面容器执行，请先安装并启动 Docker。" >&2
  exit 2
fi

if ! docker info >/dev/null 2>&1; then
  docker_info_err="$(docker info 2>&1 || true)"
  if printf '%s' "$docker_info_err" | grep -qiE 'permission denied|/var/run/docker\.sock|got permission denied'; then
    echo "[python_container] 已检测到 docker，但当前用户无法访问 Docker daemon（通常是 /var/run/docker.sock 权限不足）。请先修复 docker 组 / sudo / daemon 权限，再重试。" >&2
  else
    echo "[python_container] 已检测到 docker，但当前无法连接 Docker daemon。请先确认 dockerd 已启动，且当前用户具备访问 Docker daemon 的权限。" >&2
  fi
  [[ -n "$docker_info_err" ]] && printf '%s\n' "$docker_info_err" >&2
  exit 2
fi

source "$ROOT_DIR/scripts/lib/image_env.sh"
image_env_load

CONTROL_PLANE_RUN_IMAGE="$OPENCLAW_CONTROL_PLANE_IMAGE"
if ! docker image inspect "$CONTROL_PLANE_RUN_IMAGE" >/dev/null 2>&1; then
  # shellcheck source=scripts/lib/deployment_images.sh
  source "$ROOT_DIR/scripts/lib/deployment_images.sh"
  CONTROL_PLANE_RUN_IMAGE="$(deployment_images_resolve_verified_local_ref "$OPENCLAW_CONTROL_PLANE_IMAGE" || true)"
fi

if [[ -z "$CONTROL_PLANE_RUN_IMAGE" ]] || ! docker image inspect "$CONTROL_PLANE_RUN_IMAGE" >/dev/null 2>&1; then
  echo "[python_container] 当前本地尚未准备 OPENCLAW_CONTROL_PLANE_IMAGE：$OPENCLAW_CONTROL_PLANE_IMAGE" >&2
  echo "[python_container] 当前入口不支持 docker run 隐式拉取；请先执行 bash ./scripts/setup/prepare_control_plane_medium.sh，离线场景使用 --offline --image-archive <local-path>。" >&2
  echo "[python_container] 统一参考：docs/getting-started/environment-setup.md" >&2
  exit 2
fi

DOCKER_ARGS=(run --rm --user "$USER_SPEC")
if should_attach_stdin "$@"; then
  DOCKER_ARGS+=(-i)
fi
if ((${#EXTRA_MOUNT_SOURCES[@]})); then
  for local_mount in "${EXTRA_MOUNT_SOURCES[@]}"; do
    if [[ "$local_mount" == "/var/run/docker.sock" && -S "$local_mount" ]]; then
      socket_gid="$(stat -c '%g' "$local_mount" 2>/dev/null || true)"
      [[ -n "$socket_gid" ]] && DOCKER_ARGS+=(--group-add "$socket_gid")
    fi
  done
fi
if [[ -n "$NETWORK_MODE" ]]; then
  DOCKER_ARGS+=(--network "$NETWORK_MODE")
fi
local_env=""
for local_env in "${ENV_VARS[@]}"; do
  DOCKER_ARGS+=(-e "$local_env")
done
DOCKER_ARGS+=(-e 'OPENCLAW_PYTHON_CONTAINER_IN_CONTAINER=1')
DOCKER_ARGS+=(-v "$(volume_spec "$ROOT_DIR")")
if ! in_repo "$WORKDIR"; then
  DOCKER_ARGS+=(-v "$(volume_spec "$WORKDIR")")
fi
if ((${#EXTRA_MOUNT_SOURCES[@]})); then
  local_mount=""
  local_mount_target=""
  for index in "${!EXTRA_MOUNT_SOURCES[@]}"; do
    local_mount="${EXTRA_MOUNT_SOURCES[$index]}"
    local_mount_target="${EXTRA_MOUNT_TARGETS[$index]}"
    if [[ "$local_mount" == "$local_mount_target" ]] && { in_repo "$local_mount" || [[ "$local_mount" == "$WORKDIR" ]]; }; then
      continue
    fi
    if [[ "$local_mount" == "$local_mount_target" ]]; then
      DOCKER_ARGS+=(-v "$(volume_spec "$local_mount")")
    else
      if command -v getenforce >/dev/null 2>&1; then
        mode="$(getenforce 2>/dev/null || true)"
        if [[ "$mode" == "Enforcing" || "$mode" == "Permissive" ]]; then
          DOCKER_ARGS+=(-v "$local_mount:$local_mount_target:Z")
        else
          DOCKER_ARGS+=(-v "$local_mount:$local_mount_target")
        fi
      else
        DOCKER_ARGS+=(-v "$local_mount:$local_mount_target")
      fi
    fi
  done
fi
DOCKER_ARGS+=(-w "$WORKDIR" "$CONTROL_PLANE_RUN_IMAGE" python3 -B)
exec docker "${DOCKER_ARGS[@]}" "$@"
