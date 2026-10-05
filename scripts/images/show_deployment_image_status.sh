#!/usr/bin/env bash
# 用途：统一查看当前部署镜像合同、角色边界与本地可用性。
set -euo pipefail
__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
source "$ROOT_DIR/scripts/lib/deployment_images.sh"
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'USAGE'
用法：
  bash ./scripts/images/show_deployment_image_status.sh

说明：
  输出 source_strategy 声明的部署镜像角色表，同时展示 pin ref、source tag、
  受管 role tag、本地 Docker 状态、Gateway 来源选择记录与 verified local refs
  的 image id 证明状态。若 Docker 保存了同一 digest 的多个 RepoDigest，本脚本会
  标注为 same-image-digest alias。source / managed tag 显示 tag-not-retained
  表示清理策略未保留该可变标签；部署可用性以 LOCAL_REF 列与下方 contract refs 为准。
USAGE
  exit 0
fi

local_ref_status() {
  local env_key="$1"
  local pin_ref="$2"
  local refs_file='' recorded_pin='' local_ref='' recorded_image_id='' actual_image_id=''
  if docker image inspect "$pin_ref" >/dev/null 2>&1; then
    printf 'exact-pin'
    return 0
  fi
  refs_file="$(deployment_images_local_refs_env_path)"
  [[ -f "$refs_file" ]] || { printf 'missing'; return 0; }
  recorded_pin="$(image_env_read_key_from_file "$refs_file" "${env_key}_PIN_REF")"
  local_ref="$(image_env_read_key_from_file "$refs_file" "${env_key}_LOCAL_REF")"
  [[ "$recorded_pin" == "$pin_ref" && -n "$local_ref" ]] || { printf 'missing'; return 0; }
  recorded_image_id="$(image_env_read_key_from_file "$refs_file" "${env_key}_IMAGE_ID")"
  [[ -n "$recorded_image_id" ]] || { printf 'local-ref-missing-image-id:%s' "$local_ref"; return 0; }
  actual_image_id="$(docker image inspect "$local_ref" --format '{{.Id}}' 2>/dev/null || true)"
  [[ -n "$actual_image_id" ]] || { printf 'local-ref-missing:%s' "$local_ref"; return 0; }
  [[ "$actual_image_id" == "$recorded_image_id" ]] || { printf 'local-ref-image-id-mismatch:%s' "$local_ref"; return 0; }
  printf 'verified-local:%s' "$local_ref"
}

local_ref_is_ready_status() {
  case "$1" in
    exact-pin|verified-local:*) return 0 ;;
    *) return 1 ;;
  esac
}

local_ref_status_label() {
  local status="$1"
  case "$status" in
    exact-pin) printf 'exact-pin' ;;
    verified-local:*) printf 'verified-local' ;;
    local-ref-missing-image-id:*) printf 'missing-image-id' ;;
    local-ref-image-id-mismatch:*) printf 'image-id-mismatch' ;;
    local-ref-missing:*) printf 'local-ref-missing' ;;
    *) printf '%s' "$status" ;;
  esac
}

local_ref_status_detail() {
  local status="$1"
  case "$status" in
    verified-local:*|local-ref-missing-image-id:*|local-ref-image-id-mismatch:*|local-ref-missing:*)
      printf '%s' "${status#*:}"
      return 0
      ;;
  esac
  printf 'missing'
}

plain_tag_status() {
  local ref="$1"
  if docker image inspect "$ref" >/dev/null 2>&1; then
    printf 'tag-present'
  else
    printf 'tag-not-retained'
  fi
}

print_gateway_source_selection() {
  local canonical_ref='' selected_ref='' canonical_digest='' selected_digest=''
  local canonical_repo='' selected_repo='' reason='' env_rewritten='' candidate_ref=''
  canonical_ref="$(image_env_read_key_from_file "$IMAGE_ENV_PIN_FILE" OPENCLAW_OFFICIAL_GATEWAY_IMAGE)"
  selected_ref="$OPENCLAW_OFFICIAL_GATEWAY_IMAGE"
  canonical_digest="$(deployment_images_ref_digest "$canonical_ref")"
  selected_digest="$(deployment_images_ref_digest "$selected_ref")"
  canonical_repo="$(deployment_images_ref_repo "$canonical_ref")"
  selected_repo="$(deployment_images_ref_repo "$selected_ref")"
  reason="$(deployment_images_gateway_source_selection_value reason)"
  env_rewritten="$(deployment_images_gateway_source_selection_value envRewritten)"
  candidate_ref="$(deployment_images_gateway_source_selection_value candidate)"

  echo '== gateway source =='
  printf 'canonical_pin=%s\n' "${canonical_ref:-<missing>}"
  printf 'selected_ref=%s\n' "${selected_ref:-<missing>}"
  printf 'canonical_repo=%s\n' "${canonical_repo:-<missing>}"
  printf 'selected_repo=%s\n' "${selected_repo:-<missing>}"
  if [[ -n "$canonical_ref" && "$selected_ref" == "$canonical_ref" ]]; then
    printf 'selected_source=canonical\n'
  elif [[ -n "$canonical_digest" && "$canonical_digest" == "$selected_digest" ]]; then
    printf 'selected_source=alternate-registry-same-digest\n'
    printf '  note=当前运行源与 canonical pin digest 相同；Docker 可能把 canonical/candidate RepoDigest 显示成多行，但仍是同一 image ID。\n'
  else
    printf 'selected_source=custom-or-digest-drift\n'
  fi
  if [[ -n "$reason" ]]; then
    printf 'selection_record_reason=%s\n' "$reason"
    printf 'selection_record_env_rewritten=%s\n' "${env_rewritten:-false}"
    [[ -z "$candidate_ref" ]] || printf 'selection_record_candidate=%s\n' "$candidate_ref"
  else
    printf 'selection_record=missing\n'
  fi
  echo
}

print_repo_digest_alias_hint() {
  local inspect_ref="$1"
  local pin_ref="$2"
  local expected_digest='' aliases_text='' alias_count=''
  expected_digest="$(deployment_images_ref_digest "$pin_ref")"
  aliases_text="$(deployment_images_repo_digest_alias_lines "$inspect_ref" "$expected_digest" || true)"
  alias_count="$(printf '%s\n' "$aliases_text" | awk 'NF { count += 1 } END { print count + 0 }')"
  if [[ "$alias_count" -gt 1 ]]; then
    printf '  repo_digest_aliases=same-image-digest (%s refs)\n' "$alias_count"
    printf '%s\n' "$aliases_text" | sed 's/^/    - /'
  fi
}

print_gateway_source_selection

echo '== deployment image role table =='
printf 'tag_status_note=SOURCE_TAG/MANAGED_TAG 为 tag-not-retained 时表示可变标签已清理；LOCAL_REF 为 exact-pin 或 verified-local 才表示本地合同镜像可用。\n'
printf '%-22s %-34s %-16s %-16s %-18s %s\n' 'ROLE' 'ENV_KEY' 'SOURCE_TAG' 'MANAGED_TAG' 'LOCAL_REF' 'PIN_REF'
role_rows_text="$(deployment_images_role_rows)" || exit $?
while IFS='|' read -r role env_key pin_ref label; do
  source_tag="$(deployment_images_source_tag_ref "$pin_ref")"
  managed_tag="$(deployment_images_managed_tag_for_role "$role" "$pin_ref")"
  local_detail='missing'
  inspect_ref="$pin_ref"
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    source_status="$(plain_tag_status "$source_tag")"
    managed_status="$(plain_tag_status "$managed_tag")"
    local_status="$(local_ref_status "$env_key" "$pin_ref")"
  else
    source_status='unchecked'
    managed_status='unchecked'
    local_status='docker-unavailable'
  fi
  printf '%-22s %-34s %-16s %-16s %-18s %s\n' "$role" "$env_key" "$source_status" "$managed_status" "$(local_ref_status_label "$local_status")" "$pin_ref"
  printf '  source_tag=%s\n' "$source_tag"
  printf '  managed_tag=%s\n' "$managed_tag"
  local_detail="$(local_ref_status_detail "$local_status")"
  [[ "$local_detail" == 'missing' ]] || printf '  local_ref=%s\n' "$local_detail"
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 && local_ref_is_ready_status "$local_status"; then
    [[ "$local_detail" == 'missing' ]] || inspect_ref="$local_detail"
    print_repo_digest_alias_hint "$inspect_ref" "$pin_ref"
  fi
done <<< "$role_rows_text"
echo

echo '== compose runtime image set =='
runtime_images_text="$(image_env_runtime_service_images)" || exit $?
while IFS= read -r image; do
  printf '%s\n' "$image"
done <<< "$runtime_images_text"
echo

echo '== local image presence (contract refs) =='
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  while IFS='|' read -r role env_key image label; do
    local_status="$(local_ref_status "$env_key" "$image")"
    local_label="$(local_ref_status_label "$local_status")"
    if local_ref_is_ready_status "$local_status"; then
      printf '[OK] %s (%s, %s)\n' "$image" "$env_key" "$local_label"
    else
      printf '[MISS] %s (%s, %s)\n' "$image" "$env_key" "$local_label"
    fi
  done <<< "$role_rows_text"
else
  echo '[INFO] 当前未连接 Docker daemon；跳过本地镜像可用性检查。'
fi
