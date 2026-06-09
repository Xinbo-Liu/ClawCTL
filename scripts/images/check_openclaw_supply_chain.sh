#!/usr/bin/env bash
# 用途：统一收口 OpenClaw 供应链检查所需的版本与 digest 元数据，输出 JSON 供 release / digest 检查复用。
# 说明：
# - current-tag：只解析当前 pin 对应 tag 的官方 / 当前镜像源 digest；
# - latest-stable：解析 GitHub latest stable tag，以及该 tag 在官方 / 当前镜像源上的 digest；
# - full：同时输出 current-tag 与 latest-stable 两组结果；
# - 只负责提供结构化事实，不直接决定“落后版本”“candidate 源缺失”“digest 不等值”等策略结论。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
source "$ROOT_DIR/scripts/lib/image_env.sh"
source "$ROOT_DIR/scripts/lib/registry_manifest_probe.sh"
source "$ROOT_DIR/scripts/lib/openclaw_runtime_contract.sh"
image_env_load

SCOPE="full"
OPENCLAW_GIT_LS_REMOTE_TIMEOUT_SECONDS="${OPENCLAW_GIT_LS_REMOTE_TIMEOUT_SECONDS:-30}"
declare -A RESOLVE_DIGEST_CAPTURE_STATUS_CACHE=()
declare -A RESOLVE_DIGEST_CAPTURE_DIGEST_CACHE=()
RESOLVE_DIGEST_CAPTURE_STATUS=''
RESOLVE_DIGEST_CAPTURE_DIGEST=''

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/images/check_openclaw_supply_chain.sh [--scope current-tag|latest-stable|full]

说明：
  - 输出 OpenClaw 版本与供应链 digest 的结构化 JSON；
  - current-tag 只检查当前 pin 对应 tag；
  - latest-stable 只检查 GitHub latest stable 与当前镜像源 / 官方 GHCR 的 digest；
  - full 同时输出两组数据；
  - 如需离线复核，可结合 OPENCLAW_LATEST_TAG_OVERRIDE / OPENCLAW_LATEST_RELEASE_OVERRIDE /
    OPENCLAW_REMOTE_DIGEST_OVERRIDE / OPENCLAW_CURRENT_REMOTE_DIGEST_OVERRIDE /
    OPENCLAW_OFFICIAL_REMOTE_DIGEST_OVERRIDE / OPENCLAW_CURRENT_OFFICIAL_REMOTE_DIGEST_OVERRIDE /
    OPENCLAW_LATEST_REMOTE_DIGEST_OVERRIDE / OPENCLAW_LATEST_OFFICIAL_REMOTE_DIGEST_OVERRIDE。
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scope)
      [[ $# -ge 2 ]] || { echo '[check_openclaw_supply_chain] --scope 缺少参数' >&2; exit 2; }
      SCOPE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[check_openclaw_supply_chain] 未知参数：$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

[[ "$SCOPE" == 'current-tag' || "$SCOPE" == 'latest-stable' || "$SCOPE" == 'full' ]] || {
  echo "[check_openclaw_supply_chain] 不支持的 --scope：$SCOPE" >&2
  exit 2
}

supply_chain_has_shell_stack() {
  command -v curl >/dev/null 2>&1 && command -v jq >/dev/null 2>&1
}

require_supply_chain_cmds() {
  if supply_chain_has_shell_stack; then
    return 0
  fi
  registry_manifest_probe_python_executable >/dev/null 2>&1 || {
    echo '[check_openclaw_supply_chain] 缺少 curl/jq，且未检测到可用 Python。' >&2
    exit 20
  }
}

normalize_tag() {
  local raw="$1"
  raw="${raw#v}"
  printf '%s\n' "$raw"
}

infer_release_version() {
  local tag="$1"
  local value=''
  value="$(normalize_tag "$tag")"
  printf '%s\n' "${value%%-*}"
}

is_stable_release_tag() {
  local tag=''
  tag="$(normalize_tag "$1")"
  [[ "$tag" =~ ^[0-9]+(\.[0-9]+)*(-[0-9]+)?$ ]]
}

supply_chain_compare_release_parts() {
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

supply_chain_tag_is_newer() {
  local candidate=''
  local current=''
  local candidate_release='' current_release=''
  local candidate_correction=0 current_correction=0
  candidate="$(normalize_tag "$1")"
  current="$(normalize_tag "$2")"
  candidate_release="$(infer_release_version "$candidate")"
  current_release="$(infer_release_version "$current")"
  supply_chain_compare_release_parts "$candidate_release" "$current_release"
  case $? in
    1) return 0 ;;
    2) return 1 ;;
  esac
  if [[ "$candidate" == *-* ]]; then
    candidate_correction="${candidate#*-}"
  fi
  if [[ "$current" == *-* ]]; then
    current_correction="${current#*-}"
  fi
  (( 10#$candidate_correction > 10#$current_correction ))
}

release_info_field() {
  local payload="${1-}"
  local wanted="$2"
  local key='' value=''
  while IFS='=' read -r key value; do
    [[ "$key" == "$wanted" ]] || continue
    printf '%s\n' "$value"
    return 0
  done <<< "$payload"
  return 1
}

release_lookup_git_remote() {
  local template="${OPENCLAW_RUNTIME_CONTRACT_GITHUB_RELEASE_URL_TEMPLATE:-}"
  local repo_url=''
  if [[ -n "${OPENCLAW_GITHUB_TAGS_REMOTE_OVERRIDE:-}" ]]; then
    printf '%s\n' "$OPENCLAW_GITHUB_TAGS_REMOTE_OVERRIDE"
    return 0
  fi
  repo_url="${template%/releases/tag/v\{tag\}}"
  if [[ -z "$repo_url" || "$repo_url" == "$template" ]]; then
    repo_url='https://github.com/openclaw/openclaw'
  fi
  printf '%s.git\n' "${repo_url%.git}"
}

release_lookup_cache_file() {
  local cache_root=''
  if [[ -n "${OPENCLAW_SUPPLY_CHAIN_CACHE_DIR_OVERRIDE:-}" ]]; then
    cache_root="$OPENCLAW_SUPPLY_CHAIN_CACHE_DIR_OVERRIDE"
  elif [[ -n "${XDG_CACHE_HOME:-}" ]]; then
    cache_root="${XDG_CACHE_HOME%/}/openclaw"
  elif [[ -n "${HOME:-}" ]]; then
    cache_root="${HOME%/}/.cache/openclaw"
  fi
  [[ -n "$cache_root" ]] || return 1
  printf '%s/latest_release_info.env\n' "$cache_root"
}

release_lookup_cache_write() {
  local tag="$1"
  local release="$2"
  local source="$3"
  local detail="${4:-}"
  local cache_file='' cache_dir='' tmp_file=''
  is_stable_release_tag "$tag" || return 0
  cache_file="$(release_lookup_cache_file)" || return 0
  cache_dir="$(dirname "$cache_file")"
  mkdir -p "$cache_dir" 2>/dev/null || return 0
  tmp_file="$cache_file.tmp.$$"
  {
    printf 'tag=%s\n' "$tag"
    printf 'release=%s\n' "$release"
    printf 'source=%s\n' "$source"
    printf 'detail=%s\n' "$detail"
  } > "$tmp_file" 2>/dev/null || {
    rm -f "$tmp_file"
    return 0
  }
  mv -f "$tmp_file" "$cache_file" 2>/dev/null || rm -f "$tmp_file"
}

resolve_latest_release_info_cache() {
  local prior_detail="${1:-}"
  local cache_file='' key='' value='' tag='' release='' source='' detail=''
  cache_file="$(release_lookup_cache_file)" || return 11
  [[ -f "$cache_file" ]] || return 11
  while IFS='=' read -r key value; do
    case "$key" in
      tag) tag="$(normalize_tag "$value")" ;;
      release) release="$(normalize_tag "$value")" ;;
      source) source="$value" ;;
      detail) detail="$value" ;;
    esac
  done < "$cache_file"
  [[ -n "$tag" ]] || return 11
  is_stable_release_tag "$tag" || return 11
  [[ -n "$release" ]] || release="$(infer_release_version "$tag")"
  [[ -n "$prior_detail" ]] && detail="$prior_detail"
  printf 'status=ok\nsource=cache\ndetail=%s\ntag=%s\nrelease=%s\ncached_source=%s\n' "$detail" "$tag" "$release" "$source"
}

resolve_latest_release_info_github_api() {
  local github_url="$1"
  local temp_body='' http_status='' curl_status=0 payload='' tag='' release=''
  temp_body="$(mktemp)"
  set +e
  http_status="$(curl -sS -L --connect-timeout 10 --max-time 20 -H 'Accept: application/vnd.github+json' -H 'User-Agent: openclaw-minimax-supply-chain-checker' -o "$temp_body" -w '%{http_code}' "$github_url" 2>/dev/null)"
  curl_status=$?
  set -e
  payload="$(cat "$temp_body" 2>/dev/null || true)"
  rm -f "$temp_body"
  if [[ "$curl_status" -ne 0 ]]; then
    printf 'status=network-unavailable\nsource=github-api\n'
    return 11
  fi
  if [[ "$http_status" == '403' ]] && printf '%s' "$payload" | LC_ALL=C grep -qi 'rate limit exceeded'; then
    printf 'status=github-rate-limited\nsource=github-api\n'
    return 11
  fi
  if [[ "$http_status" != '200' ]]; then
    printf 'status=network-unavailable\nsource=github-api\nhttp_status=%s\n' "$http_status"
    return 11
  fi
  tag="$(printf '%s' "$payload" | jq -r '.tag_name // empty' 2>/dev/null | sed 's/^v//')"
  if [[ -z "$tag" ]]; then
    printf 'status=parse-failed\nsource=github-api\n'
    return 3
  fi
  release="$(infer_release_version "$tag")"
  [[ -n "$release" ]] || {
    printf 'status=parse-failed\nsource=github-api\n'
    return 3
  }
  printf 'status=ok\nsource=github-api\ntag=%s\nrelease=%s\n' "$tag" "$release"
}

resolve_latest_release_info_python() {
  local github_url="$1"
  local python_bin=''
  python_bin="$(registry_manifest_probe_python_executable)" || return 1
  "$python_bin" - "$github_url" <<'PY'
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

github_url = sys.argv[1]
MAX_GITHUB_RESPONSE_BYTES = 262144
request = urllib.request.Request(
    github_url,
    headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'openclaw-minimax-supply-chain-checker',
    },
)
try:
    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read(MAX_GITHUB_RESPONSE_BYTES + 1)
        if len(body) > MAX_GITHUB_RESPONSE_BYTES:
            print('status=response-too-large')
            print('source=github-api')
            raise SystemExit(11)
        payload = json.loads(body.decode('utf-8'))
except urllib.error.HTTPError as exc:
    body = ""
    try:
        body = exc.read(MAX_GITHUB_RESPONSE_BYTES + 1).decode('utf-8', errors='ignore')
    except Exception:
        body = ""
    if exc.code == 403 and 'rate limit exceeded' in body.lower():
        print('status=github-rate-limited')
        print('source=github-api')
        raise SystemExit(11)
    print('status=network-unavailable')
    print('source=github-api')
    print(f'http_status={exc.code}')
    raise SystemExit(11)
except urllib.error.URLError:
    print('status=network-unavailable')
    print('source=github-api')
    raise SystemExit(11)
tag = str(payload.get('tag_name') or '').lstrip('v').strip()
if not tag:
    print('status=parse-failed')
    print('source=github-api')
    raise SystemExit(3)
print('status=ok')
print('source=github-api')
print(f'tag={tag}')
print(f'release={tag.split("-", 1)[0]}')
PY
}

resolve_latest_release_info_git_tags() {
  local git_remote="$1"
  local output='' git_status=0 best_tag='' ref='' tag=''
  local -a git_cmd=(
    git
    -c http.lowSpeedLimit=1024
    -c http.lowSpeedTime=20
    ls-remote
    --tags
    --refs
    "$git_remote"
  )
  command -v git >/dev/null 2>&1 || {
    printf 'status=network-unavailable\nsource=git-tags\n'
    return 11
  }
  set +e
  if command -v timeout >/dev/null 2>&1 && [[ "$OPENCLAW_GIT_LS_REMOTE_TIMEOUT_SECONDS" =~ ^[0-9]+$ && "$OPENCLAW_GIT_LS_REMOTE_TIMEOUT_SECONDS" -gt 0 ]]; then
    output="$(timeout --foreground "$OPENCLAW_GIT_LS_REMOTE_TIMEOUT_SECONDS" "${git_cmd[@]}" 2>/dev/null)"
  else
    output="$("${git_cmd[@]}" 2>/dev/null)"
  fi
  git_status=$?
  set -e
  if [[ "$git_status" -ne 0 || -z "$output" ]]; then
    printf 'status=network-unavailable\nsource=git-tags\n'
    return 11
  fi
  while IFS=$'\t' read -r _sha ref; do
    tag="${ref#refs/tags/}"
    tag="$(normalize_tag "$tag")"
    is_stable_release_tag "$tag" || continue
    if [[ -z "$best_tag" ]] || supply_chain_tag_is_newer "$tag" "$best_tag"; then
      best_tag="$tag"
    fi
  done <<< "$output"
  [[ -n "$best_tag" ]] || {
    printf 'status=parse-failed\nsource=git-tags\n'
    return 3
  }
  printf 'status=ok\nsource=git-tags\ntag=%s\nrelease=%s\n' "$best_tag" "$(infer_release_version "$best_tag")"
}

release_candidate_manifest_available() {
  local tag="$1"
  local status='' digest=''
  resolve_digest_capture_assign "$RELEASE_CHECK_IMAGE_REPO" "$tag" "$LATEST_MIRROR_OVERRIDE"
  status="$RESOLVE_DIGEST_CAPTURE_STATUS"
  digest="$RESOLVE_DIGEST_CAPTURE_DIGEST"
  if [[ "$status" == '0' && -n "$digest" ]]; then
    return 0
  fi
  if [[ "$OFFICIAL_RELEASE_IMAGE_REPO" != "$RELEASE_CHECK_IMAGE_REPO" ]]; then
    resolve_digest_capture_assign "$OFFICIAL_RELEASE_IMAGE_REPO" "$tag" "$LATEST_OFFICIAL_OVERRIDE"
    status="$RESOLVE_DIGEST_CAPTURE_STATUS"
    digest="$RESOLVE_DIGEST_CAPTURE_DIGEST"
    if [[ "$status" == '0' && -n "$digest" ]]; then
      return 0
    fi
  fi
  return 1
}

resolve_latest_release_info_git_tags_with_release_manifest() {
  local git_remote="$1"
  local minimum_tag="${2:-}"
  local output='' git_status=0 best_tag='' ref='' tag=''
  local -a git_cmd=(
    git
    -c http.lowSpeedLimit=1024
    -c http.lowSpeedTime=20
    ls-remote
    --tags
    --refs
    "$git_remote"
  )
  command -v git >/dev/null 2>&1 || {
    printf 'status=network-unavailable\nsource=git-tags\n'
    return 11
  }
  set +e
  if command -v timeout >/dev/null 2>&1 && [[ "$OPENCLAW_GIT_LS_REMOTE_TIMEOUT_SECONDS" =~ ^[0-9]+$ && "$OPENCLAW_GIT_LS_REMOTE_TIMEOUT_SECONDS" -gt 0 ]]; then
    output="$(timeout --foreground "$OPENCLAW_GIT_LS_REMOTE_TIMEOUT_SECONDS" "${git_cmd[@]}" 2>/dev/null)"
  else
    output="$("${git_cmd[@]}" 2>/dev/null)"
  fi
  git_status=$?
  set -e
  if [[ "$git_status" -ne 0 || -z "$output" ]]; then
    printf 'status=network-unavailable\nsource=git-tags\n'
    return 11
  fi
  while IFS=$'\t' read -r _sha ref; do
    tag="${ref#refs/tags/}"
    tag="$(normalize_tag "$tag")"
    is_stable_release_tag "$tag" || continue
    if [[ -n "$minimum_tag" ]] && ! supply_chain_tag_is_newer "$tag" "$minimum_tag"; then
      continue
    fi
    if [[ -n "$best_tag" ]] && ! supply_chain_tag_is_newer "$tag" "$best_tag"; then
      continue
    fi
    release_candidate_manifest_available "$tag" || continue
    best_tag="$tag"
  done <<< "$output"
  [[ -n "$best_tag" ]] || {
    printf 'status=manifest-unavailable\nsource=git-tags\ndetail=newer-git-tags-without-release-manifest\n'
    return 11
  }
  printf 'status=ok\nsource=git-tags\ntag=%s\nrelease=%s\n' "$best_tag" "$(infer_release_version "$best_tag")"
}

resolve_digest_capture_assign() {
  local repo_ref="$1"
  local tag="$2"
  local override_digest="${3:-}"
  local cache_key="$repo_ref"$'\t'"$tag"$'\t'"$override_digest"
  local digest=''
  local status=0
  if [[ "${RESOLVE_DIGEST_CAPTURE_STATUS_CACHE[$cache_key]+set}" == 'set' ]]; then
    RESOLVE_DIGEST_CAPTURE_STATUS="${RESOLVE_DIGEST_CAPTURE_STATUS_CACHE[$cache_key]}"
    RESOLVE_DIGEST_CAPTURE_DIGEST="${RESOLVE_DIGEST_CAPTURE_DIGEST_CACHE[$cache_key]}"
    return 0
  fi
  set +e
  digest="$(registry_manifest_probe_resolve_digest "$repo_ref" "$tag" "$override_digest" 2>/dev/null)"
  status=$?
  set -e
  RESOLVE_DIGEST_CAPTURE_STATUS_CACHE["$cache_key"]="$status"
  RESOLVE_DIGEST_CAPTURE_DIGEST_CACHE["$cache_key"]="$digest"
  RESOLVE_DIGEST_CAPTURE_STATUS="$status"
  RESOLVE_DIGEST_CAPTURE_DIGEST="$digest"
}

resolve_digest_capture() {
  resolve_digest_capture_assign "$@"
  local status="$RESOLVE_DIGEST_CAPTURE_STATUS"
  local digest="$RESOLVE_DIGEST_CAPTURE_DIGEST"
  printf '%s\t%s\n' "$status" "$digest"
}

resolve_latest_release_info() {
  local override_tag='' override_release='' github_url='' git_remote='' info='' detail='' api_status=0 fallback_status=0 cache_status=0 fallback_info='' cache_info=''
  override_tag="$(normalize_tag "${OPENCLAW_LATEST_TAG_OVERRIDE:-}")"
  override_release="$(normalize_tag "${OPENCLAW_LATEST_RELEASE_OVERRIDE:-}")"
  if [[ -n "$override_tag" ]]; then
    printf 'status=ok\nsource=override-tag\ntag=%s\nrelease=%s\n' "$override_tag" "$(infer_release_version "$override_tag")"
    return 0
  fi
  if [[ -n "$override_release" ]]; then
    printf 'status=ok\nsource=override-release\ntag=%s\nrelease=%s\n' "$override_release" "$(infer_release_version "$override_release")"
    return 0
  fi
  github_url="${OPENCLAW_GITHUB_RELEASES_URL_OVERRIDE:-$OPENCLAW_RUNTIME_CONTRACT_GITHUB_LATEST_RELEASE_API}"
  if supply_chain_has_shell_stack; then
    set +e
    info="$(resolve_latest_release_info_github_api "$github_url")"
    api_status=$?
    set -e
  else
    set +e
    info="$(resolve_latest_release_info_python "$github_url")"
    api_status=$?
    set -e
  fi
  if [[ "$api_status" -eq 0 ]]; then
    local api_tag='' api_source='' git_tag=''
    api_tag="$(release_info_field "$info" tag || true)"
    api_source="$(release_info_field "$info" source || true)"
    git_remote="$(release_lookup_git_remote)"
    set +e
    fallback_info="$(resolve_latest_release_info_git_tags_with_release_manifest "$git_remote" "$api_tag")"
    fallback_status=$?
    set -e
    if [[ "$fallback_status" -eq 0 ]]; then
      git_tag="$(release_info_field "$fallback_info" tag || true)"
      if [[ -n "$api_tag" && -n "$git_tag" ]] && supply_chain_tag_is_newer "$git_tag" "$api_tag"; then
        info="$(printf '%s\n' "$fallback_info"; printf 'detail=%s\n' "corrected-from-${api_source:-github-api}:$api_tag")"
      fi
    elif [[ "$fallback_status" -eq 11 ]]; then
      detail="$(release_info_field "$fallback_info" detail || true)"
      if [[ -n "$detail" ]]; then
        info="$(printf '%s\n' "$info"; printf 'detail=%s\n' "$detail")"
      fi
    fi
    release_lookup_cache_write \
      "$(release_info_field "$info" tag)" \
      "$(release_info_field "$info" release)" \
      "$(release_info_field "$info" source)" \
      "$(release_info_field "$info" detail)"
    printf '%s\n' "$info"
    return 0
  fi

  detail="$(release_info_field "$info" status || true)"
  git_remote="$(release_lookup_git_remote)"
  set +e
  fallback_info="$(resolve_latest_release_info_git_tags_with_release_manifest "$git_remote")"
  fallback_status=$?
  set -e
  if [[ "$fallback_status" -eq 0 ]]; then
    if [[ -n "$detail" ]]; then
      fallback_info="$(printf '%s\n' "$fallback_info"; printf 'detail=%s\n' "$detail")"
    fi
    release_lookup_cache_write \
      "$(release_info_field "$fallback_info" tag)" \
      "$(release_info_field "$fallback_info" release)" \
      "$(release_info_field "$fallback_info" source)" \
      "$(release_info_field "$fallback_info" detail)"
    printf '%s\n' "$fallback_info"
    return 0
  fi

  set +e
  cache_info="$(resolve_latest_release_info_cache "$detail")"
  cache_status=$?
  set -e
  if [[ "$cache_status" -eq 0 ]]; then
    printf '%s\n' "$cache_info"
    return 0
  fi

  if [[ "$api_status" -eq 3 || "$fallback_status" -eq 3 ]]; then
    printf '%s\n' "${fallback_info:-$info}"
    return 3
  fi
  printf '%s\n' "$info"
  return 11
}

openclaw_runtime_contract_load "$ROOT_DIR"

CURRENT_REF="$OPENCLAW_OFFICIAL_GATEWAY_IMAGE"
CURRENT_RELEASE_VERSION="$(image_env_openclaw_release_version)"
CURRENT_IMAGE_TAG="$(image_env_openclaw_image_tag)"
CURRENT_IMAGE_DIGEST="$(image_env_openclaw_image_digest)"
mapfile -t CURRENT_IMAGE_PARTS < <(image_env_split_image_ref "$CURRENT_REF")
CURRENT_IMAGE_REPO="${CURRENT_IMAGE_PARTS[0]}"
RELEASE_CHECK_IMAGE_REPO="${OPENCLAW_RELEASE_CHECK_IMAGE_REPO_OVERRIDE:-$CURRENT_IMAGE_REPO}"
OFFICIAL_RELEASE_IMAGE_REPO="${OPENCLAW_OFFICIAL_RELEASE_IMAGE_REPO_OVERRIDE:-$OPENCLAW_RUNTIME_CONTRACT_OFFICIAL_RELEASE_IMAGE_REPO}"

[[ -n "$CURRENT_RELEASE_VERSION" && -n "$CURRENT_IMAGE_TAG" && -n "$CURRENT_IMAGE_REPO" ]] || {
  echo '[check_openclaw_supply_chain] 当前 pin 缺少 release version / image tag / image repo，无法输出结构化结果。' >&2
  exit 2
}

LATEST_TAG=''
LATEST_RELEASE_VERSION=''

CURRENT_MIRROR_OVERRIDE="${OPENCLAW_CURRENT_REMOTE_DIGEST_OVERRIDE:-${OPENCLAW_REMOTE_DIGEST_OVERRIDE:-}}"
CURRENT_OFFICIAL_OVERRIDE="${OPENCLAW_CURRENT_OFFICIAL_REMOTE_DIGEST_OVERRIDE:-${OPENCLAW_OFFICIAL_REMOTE_DIGEST_OVERRIDE:-}}"
LATEST_MIRROR_OVERRIDE="${OPENCLAW_LATEST_REMOTE_DIGEST_OVERRIDE:-}"
LATEST_OFFICIAL_OVERRIDE="${OPENCLAW_LATEST_OFFICIAL_REMOTE_DIGEST_OVERRIDE:-${OPENCLAW_OFFICIAL_REMOTE_DIGEST_OVERRIDE:-}}"

resolve_digest_capture_assign "$CURRENT_IMAGE_REPO" "$CURRENT_IMAGE_TAG" "$CURRENT_MIRROR_OVERRIDE"
CURRENT_MIRROR_STATUS="$RESOLVE_DIGEST_CAPTURE_STATUS"
CURRENT_MIRROR_DIGEST="$RESOLVE_DIGEST_CAPTURE_DIGEST"
resolve_digest_capture_assign "$OFFICIAL_RELEASE_IMAGE_REPO" "$CURRENT_IMAGE_TAG" "$CURRENT_OFFICIAL_OVERRIDE"
CURRENT_OFFICIAL_STATUS="$RESOLVE_DIGEST_CAPTURE_STATUS"
CURRENT_OFFICIAL_DIGEST="$RESOLVE_DIGEST_CAPTURE_DIGEST"

LATEST_LOOKUP_STATUS=''
LATEST_LOOKUP_SOURCE=''
LATEST_LOOKUP_DETAIL=''
LATEST_MIRROR_STATUS=''
LATEST_MIRROR_DIGEST=''
LATEST_OFFICIAL_STATUS=''
LATEST_OFFICIAL_DIGEST=''

emit_supply_chain_json() {
  if command -v jq >/dev/null 2>&1; then
    jq -n \
      --arg scope "$SCOPE" \
      --arg current_ref "$CURRENT_REF" \
      --arg current_repo "$CURRENT_IMAGE_REPO" \
      --arg current_tag "$CURRENT_IMAGE_TAG" \
      --arg current_release "$CURRENT_RELEASE_VERSION" \
      --arg current_digest "$CURRENT_IMAGE_DIGEST" \
      --arg current_mirror_status "$CURRENT_MIRROR_STATUS" \
      --arg current_mirror_digest "$CURRENT_MIRROR_DIGEST" \
      --arg current_official_repo "$OFFICIAL_RELEASE_IMAGE_REPO" \
      --arg current_official_status "$CURRENT_OFFICIAL_STATUS" \
      --arg current_official_digest "$CURRENT_OFFICIAL_DIGEST" \
      --arg latest_tag "$LATEST_TAG" \
      --arg latest_release "$LATEST_RELEASE_VERSION" \
      --arg latest_mirror_repo "$RELEASE_CHECK_IMAGE_REPO" \
      --arg latest_mirror_status "$LATEST_MIRROR_STATUS" \
      --arg latest_mirror_digest "$LATEST_MIRROR_DIGEST" \
      --arg latest_official_repo "$OFFICIAL_RELEASE_IMAGE_REPO" \
      --arg latest_official_status "$LATEST_OFFICIAL_STATUS" \
      --arg latest_official_digest "$LATEST_OFFICIAL_DIGEST" \
      --arg release_lookup_status "$LATEST_LOOKUP_STATUS" \
      --arg release_lookup_source "$LATEST_LOOKUP_SOURCE" \
      --arg release_lookup_detail "$LATEST_LOOKUP_DETAIL" \
      '
      def status_or_null($value):
        if ($value | length) == 0 then null else ($value | tonumber) end;
      def digest_or_null($value):
        if ($value | length) == 0 then null else $value end;
      def string_or_null($value):
        if ($value | length) == 0 then null else $value end;
      {
        schema_version: 1,
        generated_at: (now | todateiso8601 | sub("\\.000Z$"; "Z")),
        scope: $scope,
        current: {
          ref: $current_ref,
          repo: $current_repo,
          tag: $current_tag,
          release_version: $current_release,
          pinned_digest: $current_digest,
          mirror_repo: $current_repo,
          mirror_digest_status: status_or_null($current_mirror_status),
          mirror_digest: digest_or_null($current_mirror_digest),
          official_repo: $current_official_repo,
          official_digest_status: status_or_null($current_official_status),
          official_digest: digest_or_null($current_official_digest)
        },
        release_lookup: (if $scope == "current-tag" then null else {
          status: string_or_null($release_lookup_status),
          source: string_or_null($release_lookup_source),
          detail: string_or_null($release_lookup_detail)
        } end),
        latest: (if $scope == "current-tag" then null else {
          tag: $latest_tag,
          release_version: $latest_release,
          mirror_repo: $latest_mirror_repo,
          mirror_digest_status: status_or_null($latest_mirror_status),
          mirror_digest: digest_or_null($latest_mirror_digest),
          official_repo: $latest_official_repo,
          official_digest_status: status_or_null($latest_official_status),
          official_digest: digest_or_null($latest_official_digest)
        } end)
      }
      '
    return 0
  fi
  local python_bin=''
  python_bin="$(registry_manifest_probe_python_executable)" || {
    echo '[check_openclaw_supply_chain] 缺少 jq，且未检测到可用 Python，无法输出 JSON。' >&2
    exit 20
  }
  "$python_bin" - \
    "$SCOPE" "$CURRENT_REF" "$CURRENT_IMAGE_REPO" "$CURRENT_IMAGE_TAG" "$CURRENT_RELEASE_VERSION" "$CURRENT_IMAGE_DIGEST" \
    "$CURRENT_MIRROR_STATUS" "$CURRENT_MIRROR_DIGEST" "$OFFICIAL_RELEASE_IMAGE_REPO" "$CURRENT_OFFICIAL_STATUS" "$CURRENT_OFFICIAL_DIGEST" \
    "$LATEST_TAG" "$LATEST_RELEASE_VERSION" "$RELEASE_CHECK_IMAGE_REPO" "$LATEST_MIRROR_STATUS" "$LATEST_MIRROR_DIGEST" \
    "$OFFICIAL_RELEASE_IMAGE_REPO" "$LATEST_OFFICIAL_STATUS" "$LATEST_OFFICIAL_DIGEST" \
    "$LATEST_LOOKUP_STATUS" "$LATEST_LOOKUP_SOURCE" "$LATEST_LOOKUP_DETAIL" <<'PY'
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone


def status_or_none(value: str) -> int | None:
    value = value.strip()
    return int(value) if value else None


def value_or_none(value: str) -> str | None:
    value = value.strip()
    return value or None


payload = {
    "schema_version": 1,
    "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "scope": sys.argv[1],
    "current": {
        "ref": sys.argv[2],
        "repo": sys.argv[3],
        "tag": sys.argv[4],
        "release_version": sys.argv[5],
        "pinned_digest": sys.argv[6],
        "mirror_repo": sys.argv[3],
        "mirror_digest_status": status_or_none(sys.argv[7]),
        "mirror_digest": value_or_none(sys.argv[8]),
        "official_repo": sys.argv[9],
        "official_digest_status": status_or_none(sys.argv[10]),
        "official_digest": value_or_none(sys.argv[11]),
    },
    "release_lookup": None,
    "latest": None,
}
if sys.argv[1] != "current-tag":
    payload["release_lookup"] = {
        "status": value_or_none(sys.argv[20]),
        "source": value_or_none(sys.argv[21]),
        "detail": value_or_none(sys.argv[22]),
    }
    payload["latest"] = {
        "tag": sys.argv[12],
        "release_version": sys.argv[13],
        "mirror_repo": sys.argv[14],
        "mirror_digest_status": status_or_none(sys.argv[15]),
        "mirror_digest": value_or_none(sys.argv[16]),
        "official_repo": sys.argv[17],
        "official_digest_status": status_or_none(sys.argv[18]),
        "official_digest": value_or_none(sys.argv[19]),
    }
print(json.dumps(payload, ensure_ascii=False, indent=2))
PY
}

if [[ "$SCOPE" != 'current-tag' ]]; then
  CHECK_STATUS=0
  LATEST_INFO=''
  set +e
  LATEST_INFO="$(resolve_latest_release_info)"
  CHECK_STATUS=$?
  set -e
  LATEST_LOOKUP_STATUS="$(release_info_field "$LATEST_INFO" status || true)"
  LATEST_LOOKUP_SOURCE="$(release_info_field "$LATEST_INFO" source || true)"
  LATEST_LOOKUP_DETAIL="$(release_info_field "$LATEST_INFO" detail || true)"
  if [[ "$CHECK_STATUS" -eq 11 ]]; then
    emit_supply_chain_json
    if [[ "$LATEST_LOOKUP_STATUS" == 'github-rate-limited' ]]; then
      echo '[check_openclaw_supply_chain] GitHub releases/latest 命中 403 rate limit，且回退发现源未能产出 latest stable release。' >&2
    else
      echo '[check_openclaw_supply_chain] 无法联网查询上游 latest stable release。' >&2
    fi
    exit 11
  fi
  if [[ "$CHECK_STATUS" -ne 0 ]]; then
    emit_supply_chain_json
    echo "[check_openclaw_supply_chain] latest stable 解析失败（exit=$CHECK_STATUS）。" >&2
    exit 3
  fi
  LATEST_TAG="$(release_info_field "$LATEST_INFO" tag || true)"
  LATEST_RELEASE_VERSION="$(release_info_field "$LATEST_INFO" release || true)"
  [[ -n "$LATEST_TAG" && -n "$LATEST_RELEASE_VERSION" ]] || {
    emit_supply_chain_json
    echo '[check_openclaw_supply_chain] 未识别到 latest stable tag / release。' >&2
    exit 3
  }
  resolve_digest_capture_assign "$RELEASE_CHECK_IMAGE_REPO" "$LATEST_TAG" "$LATEST_MIRROR_OVERRIDE"
  LATEST_MIRROR_STATUS="$RESOLVE_DIGEST_CAPTURE_STATUS"
  LATEST_MIRROR_DIGEST="$RESOLVE_DIGEST_CAPTURE_DIGEST"
  if [[ "$OFFICIAL_RELEASE_IMAGE_REPO" == "$RELEASE_CHECK_IMAGE_REPO" && "$LATEST_OFFICIAL_OVERRIDE" == "$LATEST_MIRROR_OVERRIDE" ]]; then
    LATEST_OFFICIAL_STATUS="$LATEST_MIRROR_STATUS"
    LATEST_OFFICIAL_DIGEST="$LATEST_MIRROR_DIGEST"
  else
    resolve_digest_capture_assign "$OFFICIAL_RELEASE_IMAGE_REPO" "$LATEST_TAG" "$LATEST_OFFICIAL_OVERRIDE"
    LATEST_OFFICIAL_STATUS="$RESOLVE_DIGEST_CAPTURE_STATUS"
    LATEST_OFFICIAL_DIGEST="$RESOLVE_DIGEST_CAPTURE_DIGEST"
  fi
fi

require_supply_chain_cmds
emit_supply_chain_json
