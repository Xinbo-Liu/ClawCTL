#!/usr/bin/env bash
# 用途：检查当前 OpenClaw 官方 Gateway 镜像 pin 是否落后于上游公开 stable Release / correction tag。
# 约束：
# - 只做检查与提示，不自动修改运行态 deploy/.env 或仓库默认 pin；
# - 通过统一供应链事实脚本输出 full JSON，再由本脚本判断 release/correction 漂移与 selected runtime source digest 一致性；
# - 若当前环境无法联网，会给出明确诊断，并允许用 override 变量做离线比对；
# - 官方 GHCR digest 只作为 release 参考输出；当前默认 pin 以 selected runtime source digest 为准。
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
  bash ./scripts/images/check_openclaw_release.sh [--json]

说明：
  - 检查当前 OpenClaw 官方 Gateway 镜像 pin 是否落后于上游公开 stable base release 或 correction tag；
  - 同时探测当前 selected runtime source 是否已提供 latest stable tag 对应 manifest / digest；
  - 复核当前 pin 是否与当前 selected runtime source 的 manifest digest 一致；
  - 官方 GHCR latest digest 只作为 release 参考输出；当前默认 pin 以 selected runtime source digest 为准；
  - 只做检查与提示，不自动修改运行态 deploy/.env 或仓库默认 pin；
  - 本脚本不判断安全公告是否已经闭环；版本已对齐不等于治理已完成；
  - 如无法联网，可设置 OPENCLAW_LATEST_TAG_OVERRIDE / OPENCLAW_LATEST_RELEASE_OVERRIDE / OPENCLAW_LATEST_REMOTE_DIGEST_OVERRIDE / OPENCLAW_LATEST_OFFICIAL_REMOTE_DIGEST_OVERRIDE 做离线比对。

选项：
  --json    输出机器可读摘要

退出码：
  0  当前版本未落后，且当前 pin 与 selected runtime source digest 一致
  10 落后于更高 base release
  11 无法联网查询上游 Release 或当前 selected runtime source digest
  12 落后于同一 base release 下更高 correction tag
  13 selected runtime source 未提供 latest tag 对应 digest
  14 当前 pin 与当前 selected runtime source digest 不一致
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
      echo "[check_openclaw_release][FAIL] 未知参数：$1" >&2
      exit 2
      ;;
  esac
done

source "$ROOT_DIR/scripts/lib/image_env.sh"
source "$ROOT_DIR/scripts/lib/registry_manifest_probe.sh"
source "$ROOT_DIR/scripts/lib/openclaw_runtime_contract.sh"
image_env_load
openclaw_runtime_contract_load "$ROOT_DIR"
SUPPLY_CHAIN_SCRIPT="$ROOT_DIR/scripts/images/check_openclaw_supply_chain.sh"

CURRENT_REF="$OPENCLAW_OFFICIAL_GATEWAY_IMAGE"
CURRENT_RELEASE_VERSION="$(image_env_openclaw_release_version)"
CURRENT_IMAGE_TAG="$(image_env_openclaw_image_tag)"
CURRENT_IMAGE_DIGEST="$(image_env_openclaw_image_digest)"
mapfile -t CURRENT_IMAGE_PARTS < <(image_env_split_image_ref "$CURRENT_REF")
CURRENT_IMAGE_REPO="${CURRENT_IMAGE_PARTS[0]}"
RELEASE_CHECK_IMAGE_REPO="${OPENCLAW_RELEASE_CHECK_IMAGE_REPO_OVERRIDE:-$CURRENT_IMAGE_REPO}"
OFFICIAL_RELEASE_IMAGE_REPO="${OPENCLAW_OFFICIAL_RELEASE_IMAGE_REPO_OVERRIDE:-$OPENCLAW_RUNTIME_CONTRACT_OFFICIAL_RELEASE_IMAGE_REPO}"

TMP_JSON="$(mktemp)"
cleanup() {
  rm -f "$TMP_JSON"
}
trap cleanup EXIT INT TERM

json_field_from_file() {
  local file_path="$1"
  local dotted_path="$2"
  if command -v jq >/dev/null 2>&1; then
    jq -r ".$dotted_path // empty" "$file_path"
    return 0
  fi
  local python_bin=''
  python_bin="$(registry_manifest_probe_python_executable)" || {
    echo '[check_openclaw_release] 缺少 jq，且未检测到可用 Python，无法解析 JSON。' >&2
    exit 20
  }
  "$python_bin" - "$file_path" "$dotted_path" <<'PY'
from __future__ import annotations

import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
value = payload
for segment in [item for item in sys.argv[2].split('.') if item]:
    if not isinstance(value, dict):
        value = ''
        break
    value = value.get(segment)
if value is None:
    print('')
elif isinstance(value, bool):
    print('true' if value else 'false')
elif isinstance(value, (dict, list)):
    print(json.dumps(value, ensure_ascii=False))
else:
    print(str(value))
PY
}

emit_json() {
  local ok="$1"
  local result="$2"
  local exit_code="$3"
  local message="$4"
  local supply_chain_json='null'
  if [[ -s "$TMP_JSON" ]]; then
    supply_chain_json="$(cat "$TMP_JSON")"
  fi
  if command -v jq >/dev/null 2>&1; then
    jq -n \
      --arg ok "$ok" \
      --arg result "$result" \
      --arg exit_code "$exit_code" \
      --arg message "$message" \
      --arg current_ref "$CURRENT_REF" \
      --arg current_repo "$CURRENT_IMAGE_REPO" \
      --arg current_tag "$CURRENT_IMAGE_TAG" \
      --arg current_release "$CURRENT_RELEASE_VERSION" \
      --arg current_digest "$CURRENT_IMAGE_DIGEST" \
      --arg release_check_repo "$RELEASE_CHECK_IMAGE_REPO" \
      --arg official_release_repo "$OFFICIAL_RELEASE_IMAGE_REPO" \
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
        },
        release_check_image_repo: $release_check_repo,
        official_release_image_repo: $official_release_repo
      } + (if $supply_chain == null then {} else {supply_chain: $supply_chain} end)'
    return 0
  fi
  local python_bin=''
  python_bin="$(registry_manifest_probe_python_executable)" || {
    echo '[check_openclaw_release] 缺少 jq，且未检测到可用 Python，无法输出 JSON。' >&2
    exit 20
  }
  "$python_bin" - \
    "$ok" "$result" "$exit_code" "$message" "$CURRENT_REF" "$CURRENT_IMAGE_REPO" "$CURRENT_IMAGE_TAG" "$CURRENT_RELEASE_VERSION" "$CURRENT_IMAGE_DIGEST" \
    "$RELEASE_CHECK_IMAGE_REPO" "$OFFICIAL_RELEASE_IMAGE_REPO" "${TMP_JSON:-}" <<'PY'
from __future__ import annotations

import json
import pathlib
import sys

payload = {
    "ok": sys.argv[1] == "true",
    "result": sys.argv[2],
    "exit_code": int(sys.argv[3]),
    "message": sys.argv[4],
    "current": {
        "ref": sys.argv[5],
        "repo": sys.argv[6],
        "tag": sys.argv[7],
        "release_version": sys.argv[8],
        "pinned_digest": sys.argv[9],
    },
    "release_check_image_repo": sys.argv[10],
    "official_release_image_repo": sys.argv[11],
}
tmp_json_path = pathlib.Path(sys.argv[12]) if sys.argv[12] else None
if tmp_json_path and tmp_json_path.exists() and tmp_json_path.stat().st_size > 0:
    payload["supply_chain"] = json.loads(tmp_json_path.read_text(encoding="utf-8"))
print(json.dumps(payload, ensure_ascii=False, indent=2))
PY
}

normalize_tag() {
  local raw="$1"
  raw="${raw#v}"
  printf '%s\n' "$raw"
}

normalize_release() {
  local value=''
  value="$(normalize_tag "$1")"
  printf '%s\n' "${value%%-*}"
}

validate_numeric_version() {
  local value="$1"
  [[ "$value" =~ ^[0-9]+(\.[0-9]+)*$ ]]
}

parse_correction() {
  local value=''
  local suffix=''
  value="$(normalize_tag "$1")"
  if [[ "$value" != *-* ]]; then
    printf '0\n'
    return 0
  fi
  suffix="${value#*-}"
  [[ "$suffix" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "$suffix"
}

compare_release_parts() {
  local left="$1"
  local right="$2"
  local left_parts=() right_parts=() max_len=0 idx=0 left_num=0 right_num=0
  IFS='.' read -r -a left_parts <<< "$left"
  IFS='.' read -r -a right_parts <<< "$right"
  if (( ${#left_parts[@]} > ${#right_parts[@]} )); then
    max_len=${#left_parts[@]}
  else
    max_len=${#right_parts[@]}
  fi
  for (( idx=0; idx<max_len; idx++ )); do
    left_num="${left_parts[idx]:-0}"
    right_num="${right_parts[idx]:-0}"
    (( 10#$left_num > 10#$right_num )) && return 1
    (( 10#$left_num < 10#$right_num )) && return 2
  done
  return 0
}

compare_release_status() {
  local current_tag="$1"
  local current_release="$2"
  local latest_tag="$3"
  local latest_release="$4"
  local current_release_from_tag='' latest_release_from_tag='' current_correction=0 latest_correction=0
  current_tag="$(normalize_tag "$current_tag")"
  current_release="$(normalize_release "$current_release")"
  latest_tag="$(normalize_tag "$latest_tag")"
  latest_release="$(normalize_release "$latest_release")"
  current_release_from_tag="$(normalize_release "$current_tag")"
  latest_release_from_tag="$(normalize_release "$latest_tag")"
  [[ "$current_release_from_tag" == "$current_release" ]] || return 3
  [[ "$latest_release_from_tag" == "$latest_release" ]] || return 3
  validate_numeric_version "$current_release" || return 3
  validate_numeric_version "$latest_release" || return 3
  current_correction="$(parse_correction "$current_tag")" || return 3
  latest_correction="$(parse_correction "$latest_tag")" || return 3

  compare_release_parts "$current_release" "$latest_release"
  case $? in
    2) printf 'behind-release\n'; return 0 ;;
    1) printf 'up-to-date\n'; return 0 ;;
  esac
  if (( 10#$current_correction < 10#$latest_correction )); then
    printf 'behind-correction\n'
    return 0
  fi
  printf 'up-to-date\n'
  return 0
}

[[ -n "$CURRENT_RELEASE_VERSION" && -n "$CURRENT_IMAGE_TAG" ]] || {
  msg='当前 pin 缺少 release version 或 image tag，无法执行比对。'
  if [[ "$JSON_STDOUT" == '1' ]]; then
    emit_json false invalid-pin 2 "$msg"
  else
    echo "[check_openclaw_release] $msg" >&2
  fi
  exit 2
}

set +e
bash "$SUPPLY_CHAIN_SCRIPT" --scope full > "$TMP_JSON"
CHECK_STATUS=$?
set -e

if [[ "$JSON_STDOUT" != '1' ]]; then
  printf '当前镜像引用: %s\n' "$CURRENT_REF"
  printf '当前固定 release 版本: %s\n' "$CURRENT_RELEASE_VERSION"
  printf '当前 Docker tag: %s\n' "$CURRENT_IMAGE_TAG"
  if [[ -n "$CURRENT_IMAGE_DIGEST" ]]; then
    printf '当前 digest pin: %s\n' "$CURRENT_IMAGE_DIGEST"
  fi
fi

if [[ "$CHECK_STATUS" -eq 11 ]]; then
  RELEASE_LOOKUP_STATUS="$(json_field_from_file "$TMP_JSON" 'release_lookup.status')"
  RELEASE_LOOKUP_SOURCE="$(json_field_from_file "$TMP_JSON" 'release_lookup.source')"
  RELEASE_LOOKUP_DETAIL="$(json_field_from_file "$TMP_JSON" 'release_lookup.detail')"
  if [[ "$RELEASE_LOOKUP_STATUS" == 'github-rate-limited' ]]; then
    msg='GitHub releases/latest 命中 403 rate limit，且回退发现源未能完成 latest stable 对齐；保持当前固定版本，不修改 pin。'
    result='github-rate-limited'
  else
    msg='无法联网查询上游 Release 或当前 selected runtime source digest；保持当前固定版本，不修改 pin。'
    result='network-unavailable'
  fi
  if [[ "$JSON_STDOUT" == '1' ]]; then
    emit_json false "$result" 11 "$msg"
  else
    echo "[WARN] $msg" >&2
    if [[ -n "$RELEASE_LOOKUP_SOURCE" || -n "$RELEASE_LOOKUP_DETAIL" ]]; then
      echo "       release_lookup.source=${RELEASE_LOOKUP_SOURCE:-unknown} detail=${RELEASE_LOOKUP_DETAIL:-}" >&2
    fi
    echo '       如需离线复核，可显式设置 OPENCLAW_LATEST_TAG_OVERRIDE=<tag> / OPENCLAW_REMOTE_DIGEST_OVERRIDE=<sha256:...> 后重跑。' >&2
  fi
  exit 11
fi
if [[ "$CHECK_STATUS" -ne 0 ]]; then
  msg="统一供应链事实解析失败（exit=$CHECK_STATUS）；请先检查统一探针与版本元数据。"
  if [[ "$JSON_STDOUT" == '1' ]]; then
    emit_json false supply-chain-parse-failed 3 "$msg"
  else
    echo "[check_openclaw_release] $msg" >&2
  fi
  exit 3
fi

CURRENT_MIRROR_STATUS="$(json_field_from_file "$TMP_JSON" 'current.mirror_digest_status')"
CURRENT_MIRROR_DIGEST="$(json_field_from_file "$TMP_JSON" 'current.mirror_digest')"
CURRENT_OFFICIAL_STATUS="$(json_field_from_file "$TMP_JSON" 'current.official_digest_status')"
CURRENT_OFFICIAL_DIGEST="$(json_field_from_file "$TMP_JSON" 'current.official_digest')"
LATEST_TAG="$(json_field_from_file "$TMP_JSON" 'latest.tag')"
LATEST_RELEASE_VERSION="$(json_field_from_file "$TMP_JSON" 'latest.release_version')"
LATEST_MIRROR_STATUS="$(json_field_from_file "$TMP_JSON" 'latest.mirror_digest_status')"
LATEST_REMOTE_DIGEST="$(json_field_from_file "$TMP_JSON" 'latest.mirror_digest')"
LATEST_OFFICIAL_STATUS="$(json_field_from_file "$TMP_JSON" 'latest.official_digest_status')"
OFFICIAL_REMOTE_DIGEST="$(json_field_from_file "$TMP_JSON" 'latest.official_digest')"
RELEASE_LOOKUP_STATUS="$(json_field_from_file "$TMP_JSON" 'release_lookup.status')"
RELEASE_LOOKUP_SOURCE="$(json_field_from_file "$TMP_JSON" 'release_lookup.source')"
RELEASE_LOOKUP_DETAIL="$(json_field_from_file "$TMP_JSON" 'release_lookup.detail')"

if [[ "$JSON_STDOUT" != '1' ]]; then
  printf '当前 selected runtime source: %s\n' "$CURRENT_IMAGE_REPO"
  printf '当前 selected runtime source digest: %s\n' "$CURRENT_MIRROR_DIGEST"
  if [[ "$CURRENT_OFFICIAL_STATUS" == "0" && -n "$CURRENT_OFFICIAL_DIGEST" ]]; then
    printf '当前官方 GHCR 参考 digest: %s\n' "$CURRENT_OFFICIAL_DIGEST"
  fi
  printf '上游公开 stable latest tag: %s\n' "$LATEST_TAG"
  printf '上游公开 stable release 版本: %s\n' "$LATEST_RELEASE_VERSION"
  printf '上游 release 发现源: %s\n' "$OPENCLAW_RUNTIME_CONTRACT_GITHUB_LATEST_RELEASE_API"
  if [[ -n "$RELEASE_LOOKUP_SOURCE" ]]; then
    printf '上游 release 对齐来源: %s\n' "$RELEASE_LOOKUP_SOURCE"
  fi
  if [[ -n "$RELEASE_LOOKUP_DETAIL" ]]; then
    printf '上游 release 对齐诊断: %s\n' "$RELEASE_LOOKUP_DETAIL"
  fi
  printf '用于 latest candidate 验证的 selected runtime source: %s\n' "$RELEASE_CHECK_IMAGE_REPO"
  printf '官方发布镜像源: %s\n' "$OFFICIAL_RELEASE_IMAGE_REPO"
fi

if [[ "$CURRENT_MIRROR_STATUS" != "0" ]]; then
  msg='无法取得当前 selected runtime source digest；请先修复当前镜像源连通性或用 OPENCLAW_REMOTE_DIGEST_OVERRIDE 离线复核。'
  if [[ "$JSON_STDOUT" == '1' ]]; then
    emit_json false selected-runtime-digest-unavailable 11 "$msg"
  else
    echo "[WARN] $msg" >&2
  fi
  exit 11
fi
if ! registry_manifest_probe_require_equal "$CURRENT_MIRROR_DIGEST" "$CURRENT_IMAGE_DIGEST" 'selected_runtime_digest' 'pinned_digest' >/dev/null 2>&1; then
  msg='当前 pin 与当前 selected runtime source digest 不一致。先修正默认 pin，再继续判断 release / correction 漂移。'
  if [[ "$JSON_STDOUT" == '1' ]]; then
    emit_json false digest-mismatch 14 "$msg"
  else
    echo '[WARN] 当前 pin 与当前 selected runtime source digest 不一致。' >&2
    echo '       先修正默认 pin，再继续判断 release / correction 漂移。' >&2
  fi
  exit 14
fi

if [[ -z "$LATEST_TAG" || -z "$LATEST_RELEASE_VERSION" ]]; then
  msg='未识别到上游公开 stable 版本；请手动检查 GitHub Releases。'
  if [[ "$JSON_STDOUT" == '1' ]]; then
    emit_json true latest-release-missing 0 "$msg"
  else
    echo "[WARN] $msg"
  fi
  exit 0
fi

if [[ "$LATEST_MIRROR_STATUS" == "11" ]]; then
  msg='当前 selected runtime source 尚未提供上游 latest tag 的 manifest / digest，无法形成可验证 candidate 引用。'
  if [[ "$JSON_STDOUT" == '1' ]]; then
    emit_json false latest-tag-unavailable-on-selected-source 13 "$msg"
  else
    echo "[WARN] $msg" >&2
    echo '       请等待镜像源同步、检查 registry 访问控制，或设置 OPENCLAW_RELEASE_CHECK_IMAGE_REPO_OVERRIDE / OPENCLAW_LATEST_REMOTE_DIGEST_OVERRIDE 后重跑。' >&2
  fi
  exit 13
fi
if [[ "$LATEST_MIRROR_STATUS" != "0" ]]; then
  msg="selected runtime source latest tag 探测失败（status=$LATEST_MIRROR_STATUS）；请先检查统一供应链探针与版本元数据。"
  if [[ "$JSON_STDOUT" == '1' ]]; then
    emit_json false latest-tag-probe-failed 3 "$msg"
  else
    echo "[check_openclaw_release] $msg" >&2
  fi
  exit 3
fi

if [[ "$JSON_STDOUT" != '1' ]]; then
  LATEST_REMOTE_REF="$RELEASE_CHECK_IMAGE_REPO:$LATEST_TAG@$LATEST_REMOTE_DIGEST"
  printf 'selected runtime source latest tag 对应 digest: %s\n' "$LATEST_REMOTE_DIGEST"
  printf 'selected runtime source candidate 引用: %s\n' "$LATEST_REMOTE_REF"
  if [[ "$LATEST_OFFICIAL_STATUS" == "0" && -n "$OFFICIAL_REMOTE_DIGEST" ]]; then
    printf '官方 GHCR latest tag 参考 digest: %s\n' "$OFFICIAL_REMOTE_DIGEST"
  fi
fi

COMPARE_STATUS="$(compare_release_status "$CURRENT_IMAGE_TAG" "$CURRENT_RELEASE_VERSION" "$LATEST_TAG" "$LATEST_RELEASE_VERSION" || true)"

case "$COMPARE_STATUS" in
  behind-release)
    msg="当前版本落后于更高 base release：$CURRENT_IMAGE_TAG -> $LATEST_TAG"
    if [[ "$JSON_STDOUT" == '1' ]]; then
      emit_json false behind-release 10 "$msg"
    else
      echo "[WARN] $msg"
    fi
    exit 10
    ;;
  behind-correction)
    msg="当前版本落后于同一 base release 下更高 correction tag：$CURRENT_IMAGE_TAG -> $LATEST_TAG"
    if [[ "$JSON_STDOUT" == '1' ]]; then
      emit_json false behind-correction 12 "$msg"
    else
      echo "[WARN] $msg"
    fi
    exit 12
    ;;
  up-to-date)
    msg='当前版本未落后，且当前 pin 与 selected runtime source digest 一致。'
    if [[ "$JSON_STDOUT" == '1' ]]; then
      emit_json true ok 0 "$msg"
    else
      echo "[OK] $msg"
    fi
    exit 0
    ;;
  *)
    msg='版本比较失败；请先检查当前 tag / release version 格式。'
    if [[ "$JSON_STDOUT" == '1' ]]; then
      emit_json false compare-failed 3 "$msg"
    else
      echo "[check_openclaw_release] $msg" >&2
    fi
    exit 3
    ;;
esac
