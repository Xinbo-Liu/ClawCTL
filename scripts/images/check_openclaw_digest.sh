#!/usr/bin/env bash
# 用途：检查当前 OpenClaw Docker tag 对应的 selected runtime source digest 是否仍与本地 pin 一致。
# 说明：
# - 默认从 OPENCLAW_OFFICIAL_GATEWAY_IMAGE 读取 repo/tag/digest；
# - 通过统一供应链事实脚本输出 current-tag JSON，再由本脚本判断 selected runtime source / 当前 pin 是否一致；
# - 官方 GHCR digest 只作为 release 参考输出；当前默认 pin 以 selected runtime source digest 为准；
# - 若当前环境无法联网，可用 OPENCLAW_REMOTE_DIGEST_OVERRIDE / OPENCLAW_OFFICIAL_REMOTE_DIGEST_OVERRIDE 做离线比对。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
JSON_STDOUT=0

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/images/check_openclaw_digest.sh [--json]

说明：
  - 独立检查当前 OpenClaw Docker tag 对应的 selected runtime source digest 是否仍与本地 pin 一致；
  - 默认从 OPENCLAW_OFFICIAL_GATEWAY_IMAGE 读取 repo/tag/digest；
  - 官方 GHCR digest 只作为参考输出；当前默认 pin 以 selected runtime source digest 为准；
  - 若当前环境无法联网，可用 OPENCLAW_REMOTE_DIGEST_OVERRIDE / OPENCLAW_OFFICIAL_REMOTE_DIGEST_OVERRIDE 做离线比对。

选项：
  --json    输出机器可读摘要
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --json)
      JSON_STDOUT=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[check_openclaw_digest][FAIL] 未知参数：$1" >&2
      exit 2
      ;;
  esac
done

source "$ROOT_DIR/scripts/lib/image_env.sh"
source "$ROOT_DIR/scripts/lib/registry_manifest_probe.sh"
image_env_load
SUPPLY_CHAIN_SCRIPT="$ROOT_DIR/scripts/images/check_openclaw_supply_chain.sh"

CURRENT_REF="$OPENCLAW_OFFICIAL_GATEWAY_IMAGE"
CURRENT_TAG="$(image_env_openclaw_image_tag)"
CURRENT_DIGEST="$(image_env_openclaw_image_digest)"
CURRENT_RELEASE_VERSION="$(image_env_openclaw_release_version)"
mapfile -t IMAGE_PARTS < <(image_env_split_image_ref "$CURRENT_REF")
CURRENT_REPO="${IMAGE_PARTS[0]}"

json_field_from_file() {
  local file_path="$1"
  local dotted_path="$2"
  command -v jq >/dev/null 2>&1 || {
    echo '[check_openclaw_digest] 缺少 jq，无法解析 JSON。' >&2
    exit 20
  }
  jq -r ".$dotted_path // empty" "$file_path"
}

emit_json() {
  local ok="$1"
  local result="$2"
  local exit_code="$3"
  local message="$4"
  local supply_chain_json=''
  if [[ -n "${TMP_JSON:-}" && -s "${TMP_JSON:-}" ]]; then
    supply_chain_json="$(cat "$TMP_JSON")"
  else
    supply_chain_json='null'
  fi
  command -v jq >/dev/null 2>&1 || {
    echo '[check_openclaw_digest] 缺少 jq，无法输出 JSON。' >&2
    exit 20
  }
  jq -n \
    --arg ok "$ok" \
    --arg result "$result" \
    --arg exit_code "$exit_code" \
    --arg message "$message" \
    --arg current_ref "$CURRENT_REF" \
    --arg current_repo "$CURRENT_REPO" \
    --arg current_tag "$CURRENT_TAG" \
    --arg current_release "$CURRENT_RELEASE_VERSION" \
    --arg current_digest "$CURRENT_DIGEST" \
    --argjson supply_chain "$supply_chain_json" \
    '{
      ok: ($ok == "true"),
      result: $result,
      exit_code: ($exit_code | tonumber),
      message: $message,
      current: {
        ref: $current_ref,
        repo: $current_repo,
        tag: $current_tag,
        release_version: $current_release,
        pinned_digest: $current_digest
      }
    } + (if $supply_chain == null then {} else {supply_chain: $supply_chain} end)'
}

[[ -n "$CURRENT_REPO" && -n "$CURRENT_TAG" && -n "$CURRENT_DIGEST" ]] || {
  msg='当前 OPENCLAW_OFFICIAL_GATEWAY_IMAGE 不是完整的 image:tag@digest 形式'
  if [[ "$JSON_STDOUT" == '1' ]]; then
    emit_json false invalid-pin 2 "$msg"
  else
    echo "[check_openclaw_digest] $msg" >&2
  fi
  exit 2
}

TMP_JSON="$(mktemp)"
cleanup() {
  rm -f "$TMP_JSON"
}
trap cleanup EXIT INT TERM

set +e
bash "$SUPPLY_CHAIN_SCRIPT" --scope current-tag > "$TMP_JSON"
CHECK_STATUS=$?
set -e

if [[ "$JSON_STDOUT" != '1' ]]; then
  printf '当前镜像引用: %s\n' "$CURRENT_REF"
  printf '当前 Docker tag: %s\n' "$CURRENT_TAG"
  printf '当前 digest pin: %s\n' "$CURRENT_DIGEST"
fi

if [[ "$CHECK_STATUS" -eq 11 ]]; then
  msg='无法联网查询 selected runtime source digest；可用 OPENCLAW_REMOTE_DIGEST_OVERRIDE 与 OPENCLAW_OFFICIAL_REMOTE_DIGEST_OVERRIDE 做离线比对。'
  if [[ "$JSON_STDOUT" == '1' ]]; then
    emit_json false network-unavailable 11 "$msg"
  else
    echo "[WARN] $msg" >&2
  fi
  exit 11
fi
if [[ "$CHECK_STATUS" -ne 0 ]]; then
  msg='供应链事实解析失败；请先检查统一探针输出。'
  if [[ "$JSON_STDOUT" == '1' ]]; then
    emit_json false supply-chain-parse-failed 3 "$msg"
  else
    echo "[check_openclaw_digest] $msg" >&2
  fi
  exit 3
fi

CURRENT_MIRROR_STATUS="$(json_field_from_file "$TMP_JSON" 'current.mirror_digest_status')"
CURRENT_MIRROR_DIGEST="$(json_field_from_file "$TMP_JSON" 'current.mirror_digest')"
CURRENT_OFFICIAL_STATUS="$(json_field_from_file "$TMP_JSON" 'current.official_digest_status')"
CURRENT_OFFICIAL_DIGEST="$(json_field_from_file "$TMP_JSON" 'current.official_digest')"

if [[ "$JSON_STDOUT" != '1' ]]; then
  printf 'selected runtime source digest: %s\n' "$CURRENT_MIRROR_DIGEST"
  printf '官方 GHCR 参考 digest: %s\n' "$CURRENT_OFFICIAL_DIGEST"
fi

if [[ "$CURRENT_MIRROR_STATUS" != "0" ]]; then
  msg='无法取得当前 selected runtime source digest；可通过 OPENCLAW_REMOTE_DIGEST_OVERRIDE 离线复核。'
  if [[ "$JSON_STDOUT" == '1' ]]; then
    emit_json false selected-runtime-digest-unavailable 11 "$msg"
  else
    echo "[WARN] $msg" >&2
  fi
  exit 11
fi

if ! registry_manifest_probe_require_equal "$CURRENT_MIRROR_DIGEST" "$CURRENT_DIGEST" 'selected_runtime_digest' 'pinned_digest' >/dev/null 2>&1; then
  msg='当前 pin 与 selected runtime source digest 不一致；请按当前 selected runtime source 的实际 manifest digest 更新默认 pin。'
  if [[ "$JSON_STDOUT" == '1' ]]; then
    emit_json false digest-mismatch 10 "$msg"
  else
    echo "[WARN] $msg"
  fi
  exit 10
fi

if [[ "$CURRENT_OFFICIAL_STATUS" == "0" && -n "$CURRENT_OFFICIAL_DIGEST" ]]; then
  if registry_manifest_probe_require_equal "$CURRENT_OFFICIAL_DIGEST" "$CURRENT_MIRROR_DIGEST" 'official_digest' 'selected_runtime_digest' >/dev/null 2>&1; then
    msg='当前 pin 与 selected runtime source digest 一致；官方 GHCR 参考 digest 同步一致。'
  else
    msg='当前 pin 与 selected runtime source digest 一致；官方 GHCR 参考 digest 仅作 release 对照，不影响当前默认 pin。'
  fi
else
  msg='当前 pin 与 selected runtime source digest 一致；官方 GHCR 参考 digest 当前不可用。'
fi

if [[ "$JSON_STDOUT" == '1' ]]; then
  emit_json true ok 0 "$msg"
else
  echo "[OK] $msg"
fi
