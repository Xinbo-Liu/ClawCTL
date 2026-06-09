#!/usr/bin/env bash
# 用途：静态校验本项目相对 OpenClaw 官方镜像的本地叠加层是否符合“最小 overlay”。
# 目标：只允许浏览器运行时、容器 Python 真源注入与运行面隔离相关改动，阻断把 OpenClaw 应用层 patch、额外依赖安装或源码复制重新带回仓库。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
# shellcheck source=../lib/repo_python_env.sh
source "$ROOT_DIR/scripts/lib/repo_python_env.sh"
# shellcheck source=../lib/image_env.sh
source "$ROOT_DIR/scripts/lib/image_env.sh"
image_env_load

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/images/check_openclaw_overlay_contract.sh

说明：
  - 读取 config/upstream/overlay_contract.json；
  - 校验 OpenClaw 浏览器基座镜像仓库是否仍来自允许的官方/镜像站来源；
  - 校验 compose 仍通过 source_strategy 声明的运行镜像变量引用运行镜像，不回退到 build: 或 mutable latest/main。
USAGE
}

if [[ ${1:-} == "-h" || ${1:-} == "--help" ]]; then
  usage
  exit 0
fi

REPO_PYTHON_ENV_ARGS=()
while IFS= read -r -d '' item; do
  REPO_PYTHON_ENV_ARGS+=("$item")
done < <(openclaw_repo_python_env_args "$ROOT_DIR")

RUNTIME_IMAGE_ENV_ARGS=()
runtime_image_vars_text="$(image_env_runtime_service_image_vars)" || {
  echo '[check_openclaw_overlay_contract][FAIL] 无法从 source_strategy 解析运行镜像变量集合' >&2
  exit 2
}
while IFS= read -r env_key; do
  [[ -n "$env_key" ]] || continue
  RUNTIME_IMAGE_ENV_ARGS+=(--env "$env_key=${!env_key}")
done <<< "$runtime_image_vars_text"

exec bash "$ROOT_DIR/scripts/runtime/run_python_container.sh" \
  --workdir "$ROOT_DIR" \
  "${REPO_PYTHON_ENV_ARGS[@]}" \
  "${RUNTIME_IMAGE_ENV_ARGS[@]}" \
  -- -m openclaw.cli images check-overlay-contract "$@"
