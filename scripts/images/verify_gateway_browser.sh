#!/usr/bin/env bash
# 用途：验证官方 Gateway 镜像中的浏览器依赖可用。
set -euo pipefail
export TZ=Asia/Shanghai

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/images/verify_gateway_browser.sh

说明：
  - 验证官方 Gateway 镜像中的浏览器依赖可用；
  - 要求当前用户可访问 Docker daemon，且目标镜像已在本机存在。
USAGE
}

if [[ $# -gt 0 ]]; then
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[verify_gateway_browser][FAIL] 未知参数：$1" >&2
      exit 2
      ;;
  esac
fi

source "$ROOT_DIR/scripts/lib/image_env.sh"
image_env_load

GATEWAY_IMAGE="$(official_gateway_runtime_image)"
BROWSER_CHECK_SCRIPT="$ROOT_DIR/python/openclaw/images/browser_runtime_checks.cjs"
BROWSER_CHECK_MOUNT_PATH="/app/openclaw_browser_runtime_checks.cjs"

[[ -f "$BROWSER_CHECK_SCRIPT" ]] || { echo "[FAIL] 缺少浏览器运行时校验辅助：$BROWSER_CHECK_SCRIPT" >&2; exit 1; }
docker image inspect "$GATEWAY_IMAGE" >/dev/null 2>&1 || { echo "[FAIL] 镜像未找到：$GATEWAY_IMAGE" >&2; exit 1; }

docker run --rm \
  --workdir /app \
  -v "$BROWSER_CHECK_SCRIPT:$BROWSER_CHECK_MOUNT_PATH:ro,Z" \
  --entrypoint node \
  "$GATEWAY_IMAGE" \
  "$BROWSER_CHECK_MOUNT_PATH"

echo "[OK] 官方 Gateway 浏览器烟测通过：$GATEWAY_IMAGE"
