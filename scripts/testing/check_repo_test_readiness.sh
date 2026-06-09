#!/usr/bin/env bash
# 用途：只读预检 repo unittest / repo release gate 所需的 Docker 与控制面执行介质。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/testing/check_repo_test_readiness.sh

说明：
  - 只读检查仓库级 unittest 与 repo release gate 所需前提；
  - 当前脚本属于无 Docker 也可直接运行的仓库级 readiness 入口；
  - 与之对应，run_repo_unittest.sh / run_repo_release_gate.sh 以及 docs / doctor 静态 Python 检查属于 Docker 必需入口；
  - 不调用 repo Python，不修改本地状态，不自动执行 prepare_control_plane_medium.sh；
  - 通过后即可继续执行 scripts/testing/run_repo_unittest.sh 与 scripts/doctor/run_repo_release_gate.sh。
USAGE
}

note() {
  printf '[check_repo_test_readiness] %s\n' "$*"
}

fail() {
  printf '[check_repo_test_readiness][FAIL] %s\n' "$*" >&2
}

next_step() {
  printf '[check_repo_test_readiness][NEXT] %s\n' "$*" >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "未知参数：$1"
      usage >&2
      exit 2
      ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  fail '未检测到 docker CLI；仓库级正式测试入口固定要求 Docker 与控制面容器。'
  next_step '先安装并启动 Docker，并确保 docker info 可用。'
  next_step '修复后重新执行 bash ./scripts/testing/check_repo_test_readiness.sh。'
  exit 2
fi

if ! command -v jq >/dev/null 2>&1; then
  fail '未检测到 jq；repo release gate 会把 jq 复制到临时工具覆盖层供检查容器使用。'
  next_step '先安装 jq；CentOS 7 可执行 sudo bash ./scripts/setup/prepare_docker_host.sh --install-base-tools。'
  next_step '修复后重新执行 bash ./scripts/testing/check_repo_test_readiness.sh。'
  exit 2
fi

docker_daemon_output=''
docker_daemon_status=0
set +e
docker_daemon_output="$(docker info 2>&1)"
docker_daemon_status=$?
set -e
if [[ "$docker_daemon_status" != "0" ]]; then
  fail 'Docker daemon 当前不可用；仓库级正式测试入口固定要求 Docker 与控制面容器。'
  if printf '%s' "$docker_daemon_output" | grep -qiE 'permission denied|/var/run/docker\.sock|got permission denied'; then
    fail '当前用户无法访问 Docker daemon（通常是 /var/run/docker.sock 权限不足）；请先修复 docker 组 / sudo / daemon 权限。'
  else
    fail '当前无法连接 Docker daemon；请先确认 dockerd 已启动，且当前用户具备访问 Docker daemon 的权限。'
  fi
  [[ -z "$docker_daemon_output" ]] || printf '%s\n' "$docker_daemon_output" >&2
  next_step '先修复 Docker daemon 可访问性或当前用户权限。'
  next_step '修复后重新执行 bash ./scripts/testing/check_repo_test_readiness.sh。'
  exit 2
fi

# shellcheck source=../lib/deployment_images.sh
source "$ROOT_DIR/scripts/lib/deployment_images.sh"

if deployment_images_image_present "$OPENCLAW_CONTROL_PLANE_IMAGE"; then
  note "Docker CLI / daemon 已就绪。"
  note "jq 已就绪：$(command -v jq)"
  note "控制面执行介质已就绪：$OPENCLAW_CONTROL_PLANE_IMAGE"
  note '当前可继续执行：'
  printf '  - bash ./scripts/testing/run_repo_unittest.sh ...\n'
  printf '  - bash ./scripts/doctor/run_repo_release_gate.sh [--with-docker-sock] [--quiet] [--json]\n'
  exit 0
fi

resolved_archive=''
resolve_status=0
set +e
resolved_archive="$(deployment_images_try_resolve_archive_path "" 2>/dev/null)"
resolve_status=$?
set -e
if [[ "$resolve_status" -ne "0" && "$resolve_status" -ne "1" ]]; then
  fail '部署镜像归档探测失败。'
  next_step '先修复 state/image_artifacts 访问路径后，再重新执行本预检。'
  exit "$resolve_status"
fi

fail "当前本地尚未准备 OPENCLAW_CONTROL_PLANE_IMAGE：$OPENCLAW_CONTROL_PLANE_IMAGE"
if [[ -n "$resolved_archive" ]]; then
  printf -v escaped_archive '%q' "$resolved_archive"
  next_step "优先执行：bash ./scripts/setup/prepare_control_plane_medium.sh --offline --image-archive $escaped_archive"
else
  next_step '执行：bash ./scripts/setup/prepare_control_plane_medium.sh'
fi
next_step '完成后重新执行 bash ./scripts/testing/check_repo_test_readiness.sh。'
exit 4
