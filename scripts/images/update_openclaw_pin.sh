#!/usr/bin/env bash
# 用途：快速更新 OpenClaw 官方 Gateway镜像 pin。
# 模式：
# - candidate：只更新本地 deploy/.env，适合先试拉取 / 构建 / 烟测；
# - promote：更新仓库默认 Gateway pin 真源；one_click_config.sh 读取新的默认值生成部署配置。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
# shellcheck source=../lib/repo_contracts.sh
source "$ROOT_DIR/scripts/lib/repo_contracts.sh"
source "$ROOT_DIR/scripts/lib/image_env.sh"
source "$ROOT_DIR/scripts/lib/openclaw_runtime_contract.sh"
# shellcheck source=scripts/lib/pin_env_shell.sh
source "$ROOT_DIR/scripts/lib/pin_env_shell.sh"

MODE=""
IMAGE_REF=""
CANDIDATE_REPO=""
WRITE_LOCAL_ENV="false"
OPENCLAW_PYTHON_SURFACE_CMD=(bash "$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh" images governance-surface next-steps)
repo_contract_assign_relpath OPENCLAW_PIN_REL_PATH image_pins.openclaw

usage() {
  cat <<'USAGE' | sed "s|__OPENCLAW_PIN__|$OPENCLAW_PIN_REL_PATH|g"
用法：
  bash ./scripts/images/update_openclaw_pin.sh --ref <image@digest> --mode candidate
  bash ./scripts/images/update_openclaw_pin.sh --candidate-repo <repo> --mode candidate
  bash ./scripts/images/update_openclaw_pin.sh --ref <image@digest> --mode promote

说明：
- candidate：只改 deploy/.env，不改仓库默认 pin；
- --candidate-repo：按当前 OPENCLAW_OFFICIAL_GATEWAY_IMAGE 的 tag@digest 派生候选仓库引用，适合切到 ghcr.nju.edu.cn 等已登记候选源；
- promote：直接改 __OPENCLAW_PIN__；one_click_config.sh 读取新的默认值生成部署配置；
- --write-local-env：在 promote 时，如果 deploy/.env 存在，也一并更新本地覆盖层；
- 默认要求完整 image@digest，避免把仓库再次带回 mutable tag。
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ref)
      [[ $# -ge 2 ]] || { echo "[update_openclaw_pin] --ref 缺少参数" >&2; exit 2; }
      IMAGE_REF="$2"
      shift 2
      ;;
    --candidate-repo)
      [[ $# -ge 2 ]] || { echo "[update_openclaw_pin] --candidate-repo 缺少参数" >&2; exit 2; }
      CANDIDATE_REPO="$2"
      shift 2
      ;;
    --mode)
      [[ $# -ge 2 ]] || { echo "[update_openclaw_pin] --mode 缺少参数" >&2; exit 2; }
      MODE="$2"
      shift 2
      ;;
    --write-local-env)
      WRITE_LOCAL_ENV="true"
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "[update_openclaw_pin] 未知参数：$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

[[ "$MODE" == "candidate" || "$MODE" == "promote" ]] || { echo "[update_openclaw_pin] --mode 仅支持 candidate / promote" >&2; exit 2; }
if [[ -n "$CANDIDATE_REPO" && -n "$IMAGE_REF" ]]; then
  echo "[update_openclaw_pin] --candidate-repo 与 --ref 不能同时使用" >&2
  exit 2
fi
if [[ -n "$CANDIDATE_REPO" && "$MODE" != "candidate" ]]; then
  echo "[update_openclaw_pin] --candidate-repo 仅支持 --mode candidate" >&2
  exit 2
fi

openclaw_runtime_contract_load "$ROOT_DIR"

if [[ -n "$CANDIDATE_REPO" ]]; then
  image_env_load
  IMAGE_REF="$CANDIDATE_REPO:$OPENCLAW_OFFICIAL_GATEWAY_TAG@$OPENCLAW_OFFICIAL_GATEWAY_DIGEST"
fi

[[ -n "$IMAGE_REF" ]] || { echo "[update_openclaw_pin] 必须提供 --ref <image@digest>，或使用 --candidate-repo <repo> --mode candidate" >&2; exit 2; }

mapfile -t IMAGE_PARTS < <(image_env_split_image_ref "$IMAGE_REF")
IMAGE_REPO="${IMAGE_PARTS[0]}"
IMAGE_TAG="${IMAGE_PARTS[1]}"
IMAGE_DIGEST="${IMAGE_PARTS[2]}"

[[ -n "$IMAGE_REPO" && -n "$IMAGE_TAG" && -n "$IMAGE_DIGEST" ]] || {
  echo "[update_openclaw_pin] --ref 必须是完整的 image:tag@sha256:... 形式" >&2
  exit 2
}

if [[ "$MODE" == "candidate" ]]; then
  openclaw_runtime_contract_require_candidate_repo "$IMAGE_REPO"
else
  openclaw_runtime_contract_require_promote_repo "$IMAGE_REPO"
fi

repo_contract_assign_path PIN_FILE image_pins.openclaw
DEPLOY_ENV="$ROOT_DIR/deploy/.env"

if [[ "$MODE" == "candidate" ]]; then
  pin_env_ensure_local_env "$ROOT_DIR" "$DEPLOY_ENV"
  pin_env_upsert_key "$DEPLOY_ENV" OPENCLAW_OFFICIAL_GATEWAY_IMAGE "$IMAGE_REF"
  echo "[update_openclaw_pin] 已更新运行态 deploy/.env 候选 pin"
  echo '下一步动作（统一由 image update surface 派生）：'
  while IFS= read -r step; do
    [[ -n "$step" ]] || continue
    echo "  - $step"
  done < <("${OPENCLAW_PYTHON_SURFACE_CMD[@]}" --surface openclaw_pin_candidate)
  exit 0
fi

pin_env_upsert_key "$PIN_FILE" OPENCLAW_OFFICIAL_GATEWAY_IMAGE "$IMAGE_REF"

if [[ "$WRITE_LOCAL_ENV" == "true" && -f "$DEPLOY_ENV" ]]; then
  pin_env_upsert_key "$DEPLOY_ENV" OPENCLAW_OFFICIAL_GATEWAY_IMAGE "$IMAGE_REF"
fi

echo "[update_openclaw_pin] 已更新 $OPENCLAW_PIN_REL_PATH；仓库默认配置已更新。"
echo '下一步动作（统一由 image update surface 派生）：'
while IFS= read -r step; do
  [[ -n "$step" ]] || continue
  echo "  - $step"
done < <("${OPENCLAW_PYTHON_SURFACE_CMD[@]}" --surface openclaw_pin_promote)
