#!/usr/bin/env bash
# 用途：导入当前部署镜像合同归档，并按需触发同义别名清理。

set -euo pipefail
export TZ=Asia/Shanghai

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
source "$ROOT_DIR/scripts/lib/deployment_images.sh"

ARCHIVE_PATH="${1:-}"
RUN_CLEANUP_AFTER_LOAD="${RUN_CLEANUP_AFTER_LOAD:-0}"

usage() {
  cat <<'USAGE' >&2
用法：./scripts/images/load_deployment_images.sh [镜像tar路径]

说明：
  - 支持 OpenClaw deployment image bundle：内含 Docker save tar、deployment-images.contract.json 与 sha256 清单。
  - bundle 会按合同校验 source_strategy 声明的部署镜像角色，并在导入后写出 verified local refs；compose 运行态只接受 pin、managed role tag 与 image id 都匹配的本地 ref。
  - 未显式提供路径时，会自动尝试 state/image_artifacts/ 下最新的 deployment_images_*.tar。
  - 当前脚本不会自动提权；仍要求当前用户可访问 Docker daemon。
USAGE
}

fail() {
  echo "[FAIL] $1" >&2
  exit "${2:-1}"
}

if [[ "$ARCHIVE_PATH" == "-h" || "$ARCHIVE_PATH" == "--help" ]]; then
  usage
  exit 0
fi

deployment_images_require_docker_ready || exit $?

resolved_archive=''
if resolved_archive="$(deployment_images_try_resolve_archive_path "$ARCHIVE_PATH")"; then
  ARCHIVE_PATH="$resolved_archive"
else
  resolve_status=$?
  if [[ "$resolve_status" -ne 1 ]]; then
    exit "$resolve_status"
  fi
fi
[[ -n "$ARCHIVE_PATH" ]] || { usage; fail '未指定可读的部署镜像归档，且 state/image_artifacts/ 下也没有可自动选取的 deployment_images_*.tar。' 2; }

deployment_images_load_archive_and_verify "$ARCHIVE_PATH" || exit $?

images_text="$(deployment_images_list_images)" || fail '无法解析部署镜像合同集合' 2
mapfile -t IMAGES <<< "$images_text"
for image in "${IMAGES[@]}"; do
  echo "[OK] 镜像已就绪：$image"
done

if [[ "$RUN_CLEANUP_AFTER_LOAD" == "1" ]]; then
  echo "[INFO] 开始执行镜像别名清理。"
  bash "$ROOT_DIR/scripts/images/cleanup_image_aliases.sh"
fi
