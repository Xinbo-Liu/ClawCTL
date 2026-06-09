#!/usr/bin/env bash
# 用途：显式准备 host 控制面执行 openclaw Python CLI 所需的唯一 OPENCLAW_CONTROL_PLANE_IMAGE，避免 run_python_container 再依赖 docker run 隐式拉取。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/images/ensure_control_plane_image.sh [选项]

说明：
  - 只负责显式准备 host 控制面执行 `scripts/runtime/run_openclaw_python_tool.sh` 所需的唯一 `OPENCLAW_CONTROL_PLANE_IMAGE`；
  - 在线场景：若本地缺失，会先尝试导入显式指定或自动发现的 `deployment_images_*.tar`；归档未命中时再执行 `docker pull $OPENCLAW_CONTROL_PLANE_IMAGE`；
  - 离线场景：若本地缺失，可通过 `--image-archive <path>` 指定归档；未指定时会自动尝试 `state/image_artifacts/` 下最新的 `deployment_images_*.tar`；
  - `--quiet-if-ready`：镜像已在本地时静默通过；
  - `--no-pull`：镜像缺失时禁止网络拉取，但仍允许从部署镜像归档导入。
USAGE
}

print_control_plane_image_failure_next_steps() {
  echo '[ensure_control_plane_image][HINT] 先执行 bash ./scripts/doctor/check_docker_host_readiness.sh，定位 Docker daemon、registry-mirrors 与当前 selected runtime source 是否闭合。' >&2
  echo '[ensure_control_plane_image][HINT] 中国国内网络首轮部署先执行 sudo bash ./scripts/setup/prepare_docker_host.sh --all --network-profile cn；仅补 daemon 时执行 sudo bash ./scripts/setup/prepare_docker_host.sh --configure-daemon。' >&2
  echo '[ensure_control_plane_image][HINT] 受限网络或离线目标机使用 export_deployment_images.sh 生成 deployment_images_*.tar，再执行 load_deployment_images.sh 或把归档放到 state/image_artifacts/ 后重试。' >&2
}

QUIET_IF_READY=0
NO_PULL=0
IMAGE_ARCHIVE_PATH=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --quiet-if-ready)
      QUIET_IF_READY=1
      shift
      ;;
    --no-pull)
      NO_PULL=1
      shift
      ;;
    --image-archive)
      [[ $# -ge 2 ]] || { echo '[ensure_control_plane_image][FAIL] --image-archive 缺少路径参数' >&2; exit 2; }
      IMAGE_ARCHIVE_PATH="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[ensure_control_plane_image][FAIL] 未知参数：$1" >&2
      exit 2
      ;;
  esac
done

source "$ROOT_DIR/scripts/lib/deployment_images.sh"

deployment_images_require_docker_ready || exit $?

if deployment_images_image_present "$OPENCLAW_CONTROL_PLANE_IMAGE"; then
  if [[ "$QUIET_IF_READY" != "1" ]]; then
    printf '[ensure_control_plane_image] 已就绪：%s\n' "$OPENCLAW_CONTROL_PLANE_IMAGE"
  fi
  exit 0
fi

resolved_archive=''
if resolved_archive="$(deployment_images_try_resolve_archive_path "$IMAGE_ARCHIVE_PATH")"; then
  :
else
  resolve_status=$?
  if [[ "$resolve_status" -ne 1 ]]; then
    exit "$resolve_status"
  fi
fi
if [[ -n "$resolved_archive" ]]; then
  printf '[ensure_control_plane_image] 当前本地缺少 OPENCLAW_CONTROL_PLANE_IMAGE，开始导入部署镜像归档：%s\n' "$resolved_archive" >&2
  deployment_images_load_archive_and_verify "$resolved_archive" || exit $?
  if deployment_images_image_present "$OPENCLAW_CONTROL_PLANE_IMAGE"; then
    if [[ "$QUIET_IF_READY" != "1" ]]; then
      printf '[ensure_control_plane_image] 已通过部署镜像归档就绪：%s\n' "$OPENCLAW_CONTROL_PLANE_IMAGE"
    fi
    exit 0
  fi
fi

if [[ "$NO_PULL" == "1" ]]; then
  echo "[ensure_control_plane_image][FAIL] 当前本地尚未准备 OPENCLAW_CONTROL_PLANE_IMAGE：$OPENCLAW_CONTROL_PLANE_IMAGE" >&2
  echo '[ensure_control_plane_image][FAIL] 当前模式不允许网络拉取；请先导入 deployment_images_*.tar，或把可读归档放到 state/image_artifacts/ 后重试。' >&2
  print_control_plane_image_failure_next_steps
  exit 4
fi

printf '[ensure_control_plane_image] 当前本地缺少 OPENCLAW_CONTROL_PLANE_IMAGE，开始显式拉取：%s\n' "$OPENCLAW_CONTROL_PLANE_IMAGE" >&2
if docker pull "$OPENCLAW_CONTROL_PLANE_IMAGE"; then
  printf '[ensure_control_plane_image] 拉取完成：%s\n' "$OPENCLAW_CONTROL_PLANE_IMAGE"
  exit 0
fi

echo "[ensure_control_plane_image][FAIL] 拉取 OPENCLAW_CONTROL_PLANE_IMAGE 失败：$OPENCLAW_CONTROL_PLANE_IMAGE" >&2
if [[ -n "$resolved_archive" ]]; then
  echo "[ensure_control_plane_image][FAIL] 已检测到可读部署镜像归档，但导入后仍未命中目标镜像：$resolved_archive" >&2
else
  echo '[ensure_control_plane_image][FAIL] 若当前目标机处于离线/受限网络场景，请先导入 deployment_images_*.tar，或把归档放到 state/image_artifacts/ 后重试。' >&2
fi
print_control_plane_image_failure_next_steps
exit 2
