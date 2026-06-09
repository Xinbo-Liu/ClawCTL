#!/usr/bin/env bash
# 用途：清理“同一个 IMAGE ID 被多个同义 tag 指向”的冗余残留。
# 规则：
# - 外部镜像只保留 deploy/.env 中配置的主 tag；
# - 仅删除与主 tag 指向同一 IMAGE ID，且名称属于同一仓库语义的别名 tag；
# - 默认直接执行删除；若需预演，可设置 DRY_RUN=1。

set -euo pipefail
export TZ=Asia/Shanghai

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
# shellcheck source=scripts/lib/deployment_images.sh
source "$ROOT_DIR/scripts/lib/deployment_images.sh"

DRY_RUN="${DRY_RUN:-0}"
CLEANUP_LOG="${CLEANUP_LOG:-$ROOT_DIR/state/image_pull/cleanup_aliases.log}"

fail() {
  echo "[FAIL] $1" >&2
  exit "${2:-1}"
}

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

command -v docker >/dev/null 2>&1 || fail "未检测到 docker，请先安装 Docker。" 3
if ! docker info >/dev/null 2>&1; then
  docker_info_err="$(docker info 2>&1 || true)"
  if printf '%s' "$docker_info_err" | grep -qiE 'permission denied|/var/run/docker\.sock|got permission denied'; then
    echo "[FAIL] 当前用户无权访问 Docker daemon（通常是 /var/run/docker.sock 权限不足）；请先修复 docker 组 / sudo / daemon 权限。" >&2
  else
    echo "[FAIL] 当前无法连接 Docker daemon；请先确认 dockerd 已启动，且当前用户具备访问 Docker daemon 的权限。" >&2
  fi
  exit 3
fi

require_dir_manageable_or_creatable "$(dirname "$CLEANUP_LOG")" "镜像别名清理记录目录"
# state/image_pull 是镜像脚本运行时状态，只在真实清理操作前创建。
mkdir -p "$(dirname "$CLEANUP_LOG")"
require_file_manageable_or_creatable "$CLEANUP_LOG" "镜像别名清理记录文件"
touch "$CLEANUP_LOG"

target_images_text="$(deployment_images_list_images)" || fail '无法解析部署镜像合同集合' 2
mapfile -t TARGET_IMAGES <<< "$target_images_text"
declare -A PROTECTED_IMAGE_REFS=()

build_protected_image_refs() {
  local role='' env_key='' pin_ref='' label='' source_tag='' managed_tag='' local_ref='' role_rows_text=''
  role_rows_text="$(deployment_images_role_rows)" || return $?
  while IFS='|' read -r role env_key pin_ref label; do
    source_tag="$(deployment_images_source_tag_ref "$pin_ref")"
    managed_tag="$(deployment_images_managed_tag_for_role "$role" "$pin_ref")"
    PROTECTED_IMAGE_REFS["$source_tag"]=1
    PROTECTED_IMAGE_REFS["$managed_tag"]=1
    local_ref="$(deployment_images_resolve_verified_local_ref "$pin_ref" || true)"
    [[ -z "$local_ref" ]] || PROTECTED_IMAGE_REFS["$local_ref"]=1
  done <<< "$role_rows_text"
}

is_protected_image_ref() {
  local image_ref="$1"
  [[ -n "${PROTECTED_IMAGE_REFS["$image_ref"]+x}" ]]
}

image_exists_locally() {
  local image="$1"
  deployment_images_image_present "$image"
}

resolve_image_inspect_ref() {
  local image="$1"
  local local_ref=''
  if docker image inspect "$image" >/dev/null 2>&1; then
    printf '%s\n' "$image"
    return 0
  fi
  local_ref="$(deployment_images_resolve_verified_local_ref "$image" || true)"
  [[ -n "$local_ref" ]] || return 1
  printf '%s\n' "$local_ref"
}

inspect_image_id() {
  local image="$1"
  local inspect_ref=''
  inspect_ref="$(resolve_image_inspect_ref "$image")" || return $?
  docker image inspect "$inspect_ref" --format '{{.Id}}'
}

extract_repo_and_tag() {
  local image="$1"
  local base="$image"
  local repo=""
  local tag="latest"
  local last_segment=""

  if [[ "$base" == *@* ]]; then
    base="${base%@*}"
  fi

  last_segment="${base##*/}"
  if [[ "$last_segment" == *:* ]]; then
    repo="${base%:*}"
    tag="${base##*:}"
  else
    repo="$base"
  fi

  printf '%s
%s
' "$repo" "$tag"
}

build_suffix_patterns() {
  local image="$1"
  mapfile -t _parts < <(extract_repo_and_tag "$image")
  local repo="${_parts[0]}"
  local tag="${_parts[1]}"
  local repo_after_registry="$repo"

  if [[ "$repo" == */* ]]; then
    local first_token="${repo%%/*}"
    if [[ "$first_token" == *.* || "$first_token" == *:* || "$first_token" == "localhost" ]]; then
      repo_after_registry="${repo#*/}"
    fi
  fi

  local -a patterns=("${repo_after_registry}:${tag}")
  if [[ "$repo_after_registry" == library/* ]]; then
    patterns+=("${repo_after_registry#library/}:${tag}")
  fi

  printf '%s
' "${patterns[@]}"
}

same_semantic_suffix() {
  local candidate="$1"
  shift
  local suffix
  for suffix in "$@"; do
    if [[ "$candidate" == "$suffix" || "$candidate" == */"$suffix" ]]; then
      return 0
    fi
  done
  return 1
}

delete_alias_tag() {
  local image="$1"
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[DRY-RUN] 将删除别名：$image"
  else
    docker image rm "$image" >/dev/null
    echo "[OK] 已删除别名：$image"
  fi

  printf '%s | %s | %s
' "$(date +"%Y-%m-%dT%H:%M:%S%:z")" "delete-alias" "$image" >> "$CLEANUP_LOG"
}

all_local_refs=$(docker image ls --format '{{.Repository}}:{{.Tag}} {{.ID}}')
build_protected_image_refs

for target_image in "${TARGET_IMAGES[@]}"; do
  if ! image_exists_locally "$target_image"; then
    echo "[SKIP] 目标镜像不存在，跳过：$target_image"
    continue
  fi

  target_id="$(inspect_image_id "$target_image")"
  mapfile -t suffix_patterns < <(build_suffix_patterns "$target_image")

  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    local_ref="${line% *}"
    local_id="${line##* }"

    [[ "$local_ref" == "<none>:<none>" ]] && continue
    [[ "$local_ref" == "$target_image" ]] && continue
    is_protected_image_ref "$local_ref" && continue
    [[ "$local_id" == "$target_id" ]] || continue

    if same_semantic_suffix "$local_ref" "${suffix_patterns[@]}"; then
      delete_alias_tag "$local_ref"
    fi
  done <<< "$all_local_refs"
done

echo "[OK] 镜像别名清理完成。"
echo "[INFO] 清理记录：$CLEANUP_LOG"
