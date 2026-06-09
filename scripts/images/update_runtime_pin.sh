#!/usr/bin/env bash
# 用途：快速更新部署镜像 pin（control plane Python / runtime Python / Nginx）。
# 模式：
# - candidate：只更新本地 deploy/.env 中允许候选覆盖的运行时镜像；
# - promote：更新仓库默认 runtime pin 真源；one_click_config.sh 读取新的默认值生成部署配置。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
# shellcheck source=../lib/repo_contracts.sh
source "$ROOT_DIR/scripts/lib/repo_contracts.sh"
source "$ROOT_DIR/scripts/lib/image_env.sh"
source "$ROOT_DIR/scripts/lib/pin_env_shell.sh"

MODE=""
CONTROL_PLANE_REF=""
RUNTIME_PYTHON_REF=""
NGINX_REF=""
WRITE_LOCAL_ENV="false"
OPENCLAW_PYTHON_SURFACE_CMD=(bash "$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh" images governance-surface next-steps)
repo_contract_assign_relpath RUNTIME_PIN_REL_PATH image_pins.runtime

usage() {
  cat <<'USAGE' | sed "s|__RUNTIME_PIN__|$RUNTIME_PIN_REL_PATH|g"
用法：
  bash ./scripts/images/update_runtime_pin.sh --mode candidate [--runtime-python-ref <image:tag@sha256:...>] [--nginx-ref <image:tag@sha256:...>]
  bash ./scripts/images/update_runtime_pin.sh --mode promote [--control-plane-ref <image:tag@sha256:...>] [--runtime-python-ref <image:tag@sha256:...>] [--nginx-ref <image:tag@sha256:...>]

说明：
- candidate：只改 deploy/.env 中允许运行面候选覆盖的键，不改仓库默认 pin；
- promote：直接改 __RUNTIME_PIN__；one_click_config.sh 读取新的默认值生成部署配置；
- candidate 至少提供 --runtime-python-ref 或 --nginx-ref 之一；
- promote 至少提供 --control-plane-ref / --runtime-python-ref / --nginx-ref 之一；
- 部署镜像当前要求固定到完整 tag@digest，不接受 latest、无 digest 或 digest-only 引用。
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --control-plane-ref)
      [[ $# -ge 2 ]] || { echo "[update_runtime_pin] --control-plane-ref 缺少参数" >&2; exit 2; }
      CONTROL_PLANE_REF="$2"
      shift 2
      ;;
    --runtime-python-ref)
      [[ $# -ge 2 ]] || { echo "[update_runtime_pin] --runtime-python-ref 缺少参数" >&2; exit 2; }
      RUNTIME_PYTHON_REF="$2"
      shift 2
      ;;
    --nginx-ref)
      [[ $# -ge 2 ]] || { echo "[update_runtime_pin] --nginx-ref 缺少参数" >&2; exit 2; }
      NGINX_REF="$2"
      shift 2
      ;;
    --mode)
      [[ $# -ge 2 ]] || { echo "[update_runtime_pin] --mode 缺少参数" >&2; exit 2; }
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
      echo "[update_runtime_pin] 未知参数：$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

validate_ref() {
  local label="$1"
  local ref="$2"
  local repo="" tag="" digest=""
  [[ -n "$ref" ]] || return 0
  mapfile -t __parts < <(image_env_split_image_ref "$ref")
  repo="${__parts[0]}"
  tag="${__parts[1]}"
  digest="${__parts[2]}"
  [[ -n "$repo" ]] || { echo "[update_runtime_pin] $label 缺少 repository：$ref" >&2; exit 2; }
  [[ -n "$tag" && "$tag" != "latest" ]] || { echo "[update_runtime_pin] $label 必须包含明确 tag，且不得为 latest：$ref" >&2; exit 2; }
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "[update_runtime_pin] $label 必须是完整 image:tag@sha256:... 引用：$ref" >&2; exit 2; }
}

split_ref_field() {
  local ref="$1"
  local field="$2"
  mapfile -t __parts < <(image_env_split_image_ref "$ref")
  case "$field" in
    repo) printf '%s' "${__parts[0]}" ;;
    tag) printf '%s' "${__parts[1]}" ;;
    digest) printf '%s' "${__parts[2]}" ;;
    *) echo "[update_runtime_pin] 未知 split_ref_field 字段：$field" >&2; exit 2 ;;
  esac
}

[[ "$MODE" == "candidate" || "$MODE" == "promote" ]] || { echo "[update_runtime_pin] --mode 仅支持 candidate / promote" >&2; exit 2; }
validate_ref OPENCLAW_CONTROL_PLANE_IMAGE "$CONTROL_PLANE_REF"
validate_ref OPENCLAW_RUNTIME_PYTHON_IMAGE "$RUNTIME_PYTHON_REF"
validate_ref NGINX_IMAGE "$NGINX_REF"

repo_contract_assign_path PIN_FILE image_pins.runtime
DEPLOY_ENV="$ROOT_DIR/deploy/.env"

CURRENT_CONTROL_PLANE="$(image_env_read_key_from_file "$PIN_FILE" OPENCLAW_CONTROL_PLANE_IMAGE)"
CURRENT_RUNTIME_PYTHON="$(image_env_read_key_from_file "$PIN_FILE" OPENCLAW_RUNTIME_PYTHON_IMAGE)"
CURRENT_NGINX="$(image_env_read_key_from_file "$PIN_FILE" NGINX_IMAGE)"
[[ -n "$CONTROL_PLANE_REF" ]] || CONTROL_PLANE_REF="$CURRENT_CONTROL_PLANE"
[[ -n "$RUNTIME_PYTHON_REF" ]] || RUNTIME_PYTHON_REF="$CURRENT_RUNTIME_PYTHON"
[[ -n "$NGINX_REF" ]] || NGINX_REF="$CURRENT_NGINX"

if [[ "$MODE" == "candidate" ]]; then
  [[ -n "$RUNTIME_PYTHON_REF" || -n "$NGINX_REF" ]] || { echo "[update_runtime_pin] candidate 至少提供 --runtime-python-ref 或 --nginx-ref 之一" >&2; exit 2; }
  pin_env_ensure_local_env "$ROOT_DIR" "$DEPLOY_ENV"
  pin_env_upsert_key "$DEPLOY_ENV" OPENCLAW_RUNTIME_PYTHON_IMAGE "$RUNTIME_PYTHON_REF"
  pin_env_upsert_key "$DEPLOY_ENV" NGINX_IMAGE "$NGINX_REF"
  echo "[update_runtime_pin] 已更新运行态 deploy/.env 候选 runtime pin"
  echo '下一步动作（统一由 image update surface 派生）：'
  while IFS= read -r step; do
    [[ -n "$step" ]] || continue
    echo "  - $step"
  done < <("${OPENCLAW_PYTHON_SURFACE_CMD[@]}" --surface runtime_pin_candidate)
  exit 0
fi

[[ -n "$CONTROL_PLANE_REF" || -n "$RUNTIME_PYTHON_REF" || -n "$NGINX_REF" ]] || { echo "[update_runtime_pin] promote 至少提供 --control-plane-ref / --runtime-python-ref / --nginx-ref 之一" >&2; exit 2; }
pin_env_upsert_key "$PIN_FILE" OPENCLAW_CONTROL_PLANE_IMAGE "$CONTROL_PLANE_REF"
pin_env_upsert_key "$PIN_FILE" OPENCLAW_CONTROL_PLANE_IMAGE_TAG "$(split_ref_field "$CONTROL_PLANE_REF" tag)"
pin_env_upsert_key "$PIN_FILE" OPENCLAW_CONTROL_PLANE_IMAGE_DIGEST "$(split_ref_field "$CONTROL_PLANE_REF" digest)"
pin_env_upsert_key "$PIN_FILE" OPENCLAW_RUNTIME_PYTHON_IMAGE "$RUNTIME_PYTHON_REF"
pin_env_upsert_key "$PIN_FILE" OPENCLAW_RUNTIME_PYTHON_IMAGE_TAG "$(split_ref_field "$RUNTIME_PYTHON_REF" tag)"
pin_env_upsert_key "$PIN_FILE" OPENCLAW_RUNTIME_PYTHON_IMAGE_DIGEST "$(split_ref_field "$RUNTIME_PYTHON_REF" digest)"
pin_env_upsert_key "$PIN_FILE" NGINX_IMAGE "$NGINX_REF"
pin_env_upsert_key "$PIN_FILE" NGINX_IMAGE_TAG "$(split_ref_field "$NGINX_REF" tag)"
pin_env_upsert_key "$PIN_FILE" NGINX_IMAGE_DIGEST "$(split_ref_field "$NGINX_REF" digest)"

if [[ "$WRITE_LOCAL_ENV" == "true" && -f "$DEPLOY_ENV" ]]; then
  pin_env_upsert_key "$DEPLOY_ENV" OPENCLAW_RUNTIME_PYTHON_IMAGE "$RUNTIME_PYTHON_REF"
  pin_env_upsert_key "$DEPLOY_ENV" NGINX_IMAGE "$NGINX_REF"
fi

echo "[update_runtime_pin] 已更新 $RUNTIME_PIN_REL_PATH；仓库默认配置已更新。"
echo '下一步动作（统一由 image update surface 派生）：'
while IFS= read -r step; do
  [[ -n "$step" ]] || continue
  echo "  - $step"
done < <("${OPENCLAW_PYTHON_SURFACE_CMD[@]}" --surface runtime_pin_promote)
