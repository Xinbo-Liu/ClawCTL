#!/usr/bin/env bash
# 用途：导出当前配置下的部署镜像，便于离线部署前整体打包。

set -euo pipefail
export TZ=Asia/Shanghai

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
source "$ROOT_DIR/scripts/lib/deployment_images.sh"

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/images/export_deployment_images.sh --output <deployment_images.tar>

说明：
  - 导出 OpenClaw deployment image bundle，供离线部署加载；
  - bundle 内含 Docker save tar、deployment-images.contract.json 与 sha256 清单；
  - 要求当前用户可访问 Docker daemon。
USAGE
}

OUT_DIR="${OUT_DIR:-$ROOT_DIR/state/image_artifacts}"
TS="$(date +%Y%m%d_%H%M%S)"
OUT_FILE="$OUT_DIR/deployment_images_${TS}.tar"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --output)
      [[ $# -ge 2 ]] || { echo '[FAIL] --output 缺少路径参数' >&2; exit 2; }
      OUT_FILE="$2"
      shift 2
      ;;
    --output=*)
      OUT_FILE="${1#--output=}"
      shift
      ;;
    *)
      if [[ "$OUT_FILE" == "$OUT_DIR/deployment_images_${TS}.tar" ]]; then
        OUT_FILE="$1"
        shift
      else
        echo "[FAIL] 不支持的参数：$1" >&2
        usage >&2
        exit 2
      fi
      ;;
  esac
done

fail() {
  echo "[FAIL] $1" >&2
  exit "${2:-1}"
}

images_text="$(deployment_images_list_images)" || fail '无法解析部署镜像合同集合' 2
mapfile -t IMAGES <<< "$images_text"

require_dir_manageable_or_creatable() {
  local path="$1"
  local label="$2"
  if [[ -d "$path" ]]; then
    [[ -r "$path" && -w "$path" && -x "$path" ]] || fail "$label 缺少读取/写入/执行权限：$path；当前脚本不会自动提权或 chown，请先修正宿主机权限。" 4
    return 0
  fi
  local parent
  parent="$(dirname "$path")"
  [[ -d "$parent" ]] || fail "$label 的父目录不存在：$parent；当前脚本不会自动补建越级路径。" 4
  [[ -r "$parent" && -w "$parent" && -x "$parent" ]] || fail "$label 的父目录不可写：$parent；当前脚本不会自动提权或 chown，请先修正宿主机权限。" 4
}

require_file_manageable_or_creatable() {
  local path="$1"
  local label="$2"
  if [[ -e "$path" ]]; then
    [[ -f "$path" ]] || fail "$label 不是常规文件：$path" 4
    [[ -r "$path" && -w "$path" ]] || fail "$label 缺少读取/写入权限：$path；当前脚本不会自动提权或 chown，请先修正宿主机权限。" 4
    return 0
  fi
  local parent
  parent="$(dirname "$path")"
  [[ -d "$parent" ]] || fail "$label 的父目录不存在：$parent；当前脚本不会自动补建越级路径。" 4
  [[ -r "$parent" && -w "$parent" && -x "$parent" ]] || fail "$label 的父目录不可写：$parent；当前脚本不会自动提权或 chown，请先修正宿主机权限。" 4
}

require_dir_manageable_or_creatable "$OUT_DIR" "部署镜像归档输出目录"
mkdir -p "$OUT_DIR"
deployment_images_require_docker_ready || exit $?
deployment_images_require_jq || exit $?
deployment_images_require_tar || exit $?
deployment_images_require_sha256sum || exit $?

require_file_manageable_or_creatable "$OUT_FILE" "部署镜像归档文件"
require_file_manageable_or_creatable "$OUT_FILE.sha256" "部署镜像归档校验文件"

declare -A SAVE_REF_SEEN=()
SAVE_REFS=()
# 将 docker save ref 去重后加入导出集合，避免合同内重复保存同一 tag。
add_save_ref() {
  local ref="$1"
  [[ -n "$ref" ]] || return 0
  if [[ -z "${SAVE_REF_SEEN["$ref"]+x}" ]]; then
    SAVE_REF_SEEN["$ref"]=1
    SAVE_REFS+=("$ref")
  fi
}

for image in "${IMAGES[@]}"; do
  if ! deployment_images_image_present "$image"; then
    echo "[FAIL] 镜像未找到：$image" >&2
    echo "        请先执行：./scripts/images/pull_images.sh，或导入 deployment_images_*.tar。" >&2
    exit 1
  fi
done

tmp_parent="$OUT_DIR/tmp"
mkdir -p "$tmp_parent"
TMP_DIR="$(mktemp -d "$tmp_parent/export-bundle.XXXXXX")"
trap '[[ -n "${TMP_DIR:-}" ]] && rm -rf "$TMP_DIR"' EXIT

role_rows_text="$(deployment_images_role_rows)" || fail '无法解析部署镜像角色表' 2
while IFS='|' read -r role env_key image label; do
  source_tag="$(deployment_images_source_tag_ref "$image")"
  managed_tag="$(deployment_images_managed_tag_for_role "$role" "$image")"
  inspect_ref="$image"
  if ! docker image inspect "$inspect_ref" >/dev/null 2>&1; then
    inspect_ref="$(deployment_images_resolve_verified_local_ref "$image")"
  fi
  docker tag "$inspect_ref" "$source_tag"
  docker tag "$inspect_ref" "$managed_tag"
  add_save_ref "$source_tag"
  add_save_ref "$managed_tag"
done <<< "$role_rows_text"

docker save -o "$TMP_DIR/$DEPLOYMENT_IMAGE_BUNDLE_DOCKER_ARCHIVE" "${SAVE_REFS[@]}"
(cd "$TMP_DIR" && sha256sum "$DEPLOYMENT_IMAGE_BUNDLE_DOCKER_ARCHIVE") > "$TMP_DIR/$DEPLOYMENT_IMAGE_BUNDLE_SHA256"
deployment_images_write_contract_json "$TMP_DIR/$DEPLOYMENT_IMAGE_BUNDLE_CONTRACT" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
tar -C "$TMP_DIR" -cf "$OUT_FILE" \
  "$DEPLOYMENT_IMAGE_BUNDLE_CONTRACT" \
  "$DEPLOYMENT_IMAGE_BUNDLE_DOCKER_ARCHIVE" \
  "$DEPLOYMENT_IMAGE_BUNDLE_SHA256"
rm -rf "$TMP_DIR"
trap - EXIT
sha256sum "$OUT_FILE" > "$OUT_FILE.sha256"

echo "[OK] 已导出部署镜像：$OUT_FILE"
echo "[OK] 校验文件：$OUT_FILE.sha256"
printf '[INFO] 包含镜像角色：\n'
while IFS='|' read -r role env_key image label; do
  printf '  - %s (%s): %s\n' "$role" "$env_key" "$image"
done <<< "$role_rows_text"
