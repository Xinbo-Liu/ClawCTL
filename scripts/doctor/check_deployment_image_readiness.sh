#!/usr/bin/env bash
# 用途：检查部署镜像在本机是否已就绪，并在离线模式下校验部署镜像归档前提。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir

OFFLINE_MODE=0
IMAGE_ARCHIVE_PATH=""
ENV_FILE="$ROOT_DIR/deploy/.env"
WARNINGS=0

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/doctor/check_deployment_image_readiness.sh [选项]

说明：
  - 在线模式：缺少本地部署镜像只给出 WARN，因为后续仍可通过 pull/load 补齐。
  - 离线模式：若本地缺少部署镜像，会优先使用显式指定归档；未指定时自动尝试 state/image_artifacts/ 下最新的 deployment_images_*.tar。
  - 当前脚本不会自动 docker pull / docker load，只做就绪性判断。

选项：
  --offline               按离线模式校验
  --image-archive <path>  指定 deployment_images_*.tar
  --env-file <path>       覆盖镜像输入读取的 deploy env 文件（默认：deploy/.env）
  -h, --help              显示帮助
USAGE
}

note() { printf '[INFO] %s\n' "$*"; }
warn() { WARNINGS=1; printf '[WARN] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 2; }

warn_missing_image_next_steps() {
  warn "在线 basic gate 中的镜像缺失是非阻塞项；one_click_deploy 会在镜像准备阶段按当前 env 执行 pull/load。"
  warn "若拉取失败，先执行 bash ./scripts/doctor/check_docker_host_readiness.sh，确认 Docker daemon、registry-mirrors 与 selected runtime source；中国国内网络首轮部署先执行 sudo bash ./scripts/setup/prepare_docker_host.sh --all --network-profile cn。"
  warn "需要手工补齐时执行 bash ./scripts/images/pull_images.sh；受限网络或离线目标机使用 export_deployment_images.sh 生成 deployment_images_*.tar 后执行 load_deployment_images.sh，或把归档放到 state/image_artifacts/。"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --offline)
      OFFLINE_MODE=1
      shift
      ;;
    --image-archive)
      [[ $# -ge 2 ]] || { echo '[FAIL] --image-archive 缺少路径参数' >&2; exit 2; }
      IMAGE_ARCHIVE_PATH="$2"
      shift 2
      ;;
    --env-file)
      [[ $# -ge 2 ]] || { echo '[FAIL] --env-file 缺少路径参数' >&2; exit 2; }
      ENV_FILE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[FAIL] 未知参数：$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

IMAGE_ENV_DEPLOY_ENV_PATH="$ENV_FILE"
export IMAGE_ENV_DEPLOY_ENV_PATH
source "$ROOT_DIR/scripts/lib/deployment_images.sh"

deployment_images_require_docker_ready || exit $?

check_deployment_images() {
  local required=()
  local missing=()
  local resolved_archive=''
  local ref=''
  local required_text='' missing_text=''

  required_text="$(deployment_images_list_images)" || fail '无法解析部署镜像合同集合'
  mapfile -t required <<< "$required_text"
  missing_text="$(deployment_images_collect_missing_images "${required[@]}")"
  if [[ -n "$missing_text" ]]; then
    mapfile -t missing <<< "$missing_text"
  fi

  for ref in "${required[@]}"; do
    if deployment_images_image_present "$ref"; then
      note "本地已存在部署镜像：$ref"
    fi
  done

  if (( ${#missing[@]} == 0 )); then
    note '部署镜像已齐备。'
    return 0
  fi

  if resolved_archive="$(deployment_images_try_resolve_archive_path "$IMAGE_ARCHIVE_PATH")"; then
    :
  else
    local resolve_status=$?
    if [[ "$resolve_status" -ne 1 ]]; then
      exit "$resolve_status"
    fi
  fi

  if [[ "$OFFLINE_MODE" == "1" ]]; then
    if [[ -z "$resolved_archive" ]]; then
      fail '离线模式下当前本地仍缺少部署镜像，且未找到可读的 deployment_images_*.tar；请显式传 --image-archive，或先把归档放到 state/image_artifacts/。'
    fi
    local archive_missing=''
    if archive_missing="$(deployment_images_archive_verify_required_refs "$resolved_archive" "${required[@]}" || true)"; [[ -n "$archive_missing" ]]; then
      fail "离线模式下当前部署镜像归档与当前 pin 不一致，缺少镜像：$archive_missing（archive=$resolved_archive）。"
    fi
    note "离线模式下允许通过归档补齐缺失部署镜像：${missing[*]}"
    note "当前使用的部署镜像归档：$resolved_archive"
    return 0
  fi

  if [[ -n "$resolved_archive" ]]; then
    local archive_missing=''
    if archive_missing="$(deployment_images_archive_verify_required_refs "$resolved_archive" "${required[@]}" || true)"; [[ -n "$archive_missing" ]]; then
      warn "已检测到部署镜像归档，但其内容与当前 pin 不一致，缺少镜像：$archive_missing（archive=$resolved_archive）；当前在线路径仍可继续通过 pull_images.sh 拉取。"
      warn_missing_image_next_steps
    else
      warn "当前本地仍缺少部署镜像：${missing[*]}；已检测到与当前 pin 对齐的部署镜像归档：$resolved_archive。后续可直接执行 load_deployment_images.sh 或 ensure_control_plane_image.sh。"
      return 0
    fi
  fi

  warn "当前本地仍缺少部署镜像：${missing[*]}；后续按网络条件选择在线 pull 或离线 load 路线。"
  warn_missing_image_next_steps
}

check_deployment_images
if [[ "$WARNINGS" == "1" ]]; then
  note '部署镜像就绪性检查通过，但仍有缺失项。'
else
  note '部署镜像就绪性检查通过。'
fi
