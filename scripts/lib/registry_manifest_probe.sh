#!/usr/bin/env bash
# 用途：解析 OCI/Distribution manifest，对指定 repo:tag 读取远端 digest。
# 约束：
# - 只负责“镜像源是否能返回指定 tag 的 manifest 及 digest”；
# - 失败统一返回 11，便于上层脚本区分为“镜像源不可用 / tag 未同步 / digest 无法解析”；
# - 如需离线复核，可传入 override digest 直接返回；
# - 支持同一 tag 在官方仓库与镜像站之间做 digest 等值校验，闭合供应链证明链。
set -euo pipefail

# 统一输出 registry manifest 探测失败信息，并以指定状态码退出。
registry_manifest_probe_fail() {
  echo "[registry_manifest_probe][FAIL] $1" >&2
  exit "${2:-2}"
}

REGISTRY_MANIFEST_PROBE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=repo_root.sh
source "$REGISTRY_MANIFEST_PROBE_LIB_DIR/repo_root.sh"
REGISTRY_MANIFEST_PROBE_ROOT_DIR="${REGISTRY_MANIFEST_PROBE_ROOT_DIR:-$(openclaw_repo_root_from "$REGISTRY_MANIFEST_PROBE_LIB_DIR")}"
unset REGISTRY_MANIFEST_PROBE_LIB_DIR
REGISTRY_MANIFEST_PROBE_ACCEPT_HEADER='application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.index.v1+json'
REGISTRY_MANIFEST_PROBE_USER_AGENT='openclaw-minimax-registry-probe'
REGISTRY_MANIFEST_PROBE_MAX_BYTES="${REGISTRY_MANIFEST_PROBE_MAX_BYTES:-4194304}"
REGISTRY_MANIFEST_PROBE_TOKEN_MAX_BYTES="${REGISTRY_MANIFEST_PROBE_TOKEN_MAX_BYTES:-262144}"

# 确认当前环境是否具备 shell manifest 探测栈。
registry_manifest_probe_has_shell_stack() {
  command -v curl >/dev/null 2>&1 && command -v jq >/dev/null 2>&1
}

# 确认当前环境具备 registry manifest 探测所需的 shell 工具链。
registry_manifest_probe_require_cmds() {
  if registry_manifest_probe_has_shell_stack; then
    return 0
  fi
  registry_manifest_probe_fail '缺少 curl/jq，无法探测远端 registry manifest。' 20
}

# 移除 HTTP 响应头中可能残留的回车符。
registry_manifest_probe_trim_cr() {
  printf '%s' "$1" | tr -d '\r'
}

# 在缺失 Docker-Content-Digest 头时回退计算文件 sha256。
registry_manifest_probe_sha256_file() {
  local file_path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file_path" | awk '{print $1}'
    return 0
  fi
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$file_path" | awk '{print $1}'
    return 0
  fi
  if command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 "$file_path" | awk '{print $NF}'
    return 0
  fi
  registry_manifest_probe_fail '缺少 sha256sum / shasum / openssl，无法在缺失 Docker-Content-Digest 头时回退计算 digest。' 20
}

# 从响应头文件中提取指定 header 的值。
registry_manifest_probe_extract_header_value() {
  local headers_file="$1"
  local header_name="$2"
  awk -v target="$header_name" 'BEGIN{IGNORECASE=1}
    index(tolower($0), tolower(target) ":") == 1 {
      sub(/^[^:]+:[[:space:]]*/, "", $0)
      sub(/[[:space:]]+$/, "", $0)
      print $0
      exit
    }
  ' "$headers_file" | tr -d '\r'
}

# 解析 WWW-Authenticate 头中的指定参数。
registry_manifest_probe_parse_www_authenticate_param() {
  local header_value="$1"
  local key="$2"
  printf '%s\n' "$header_value" | sed -nE "s/.*${key}=\"([^\"]*)\".*/\1/p" | head -n 1
}

# 请求远端 manifest，并输出 HTTP 状态码。
registry_manifest_probe_open_manifest() {
  local manifest_url="$1"
  local headers_file="$2"
  local body_file="$3"
  local auth_header="${4:-}"
  local -a curl_args=(
    -sS
    -L
    --connect-timeout 10
    --max-time 30
    --max-filesize "$REGISTRY_MANIFEST_PROBE_MAX_BYTES"
    -D "$headers_file"
    -o "$body_file"
    -w '%{http_code}'
    -H "Accept: $REGISTRY_MANIFEST_PROBE_ACCEPT_HEADER"
    -H "User-Agent: $REGISTRY_MANIFEST_PROBE_USER_AGENT"
  )
  if [[ -n "$auth_header" ]]; then
    curl_args+=(-H "$auth_header")
  fi
  curl_args+=("$manifest_url")
  local http_code='' body_size=''
  http_code="$(curl "${curl_args[@]}" 2>/dev/null)" || return 1
  if [[ -f "$body_file" ]]; then
    body_size="$(wc -c < "$body_file" | tr -d '[:space:]')"
    if [[ "$body_size" =~ ^[0-9]+$ && "$body_size" -gt "$REGISTRY_MANIFEST_PROBE_MAX_BYTES" ]]; then
      return 1
    fi
  fi
  printf '%s' "$http_code"
}

# 按 WWW-Authenticate 信息申请 bearer token。
registry_manifest_probe_fetch_bearer_token() {
  local auth_header_value="$1"
  local realm='' service='' scope='' token_url='' token_json=''
  realm="$(registry_manifest_probe_parse_www_authenticate_param "$auth_header_value" 'realm')"
  service="$(registry_manifest_probe_parse_www_authenticate_param "$auth_header_value" 'service')"
  scope="$(registry_manifest_probe_parse_www_authenticate_param "$auth_header_value" 'scope')"
  [[ -n "$realm" ]] || return 1
  token_url="$realm"
  if [[ -n "$service" || -n "$scope" ]]; then
    local encoded_service='' encoded_scope='' query=''
    if [[ -n "$service" ]]; then
      encoded_service="$(jq -nr --arg value "$service" '$value|@uri')"
      query="service=$encoded_service"
    fi
    if [[ -n "$scope" ]]; then
      encoded_scope="$(jq -nr --arg value "$scope" '$value|@uri')"
      if [[ -n "$query" ]]; then
        query+="&"
      fi
      query+="scope=$encoded_scope"
    fi
    token_url+="?$query"
  fi
  token_json="$(curl -sS -L --connect-timeout 10 --max-time 30 --max-filesize "$REGISTRY_MANIFEST_PROBE_TOKEN_MAX_BYTES" -H "User-Agent: $REGISTRY_MANIFEST_PROBE_USER_AGENT" "$token_url" 2>/dev/null || true)"
  [[ -n "$token_json" ]] || return 1
  [[ "${#token_json}" -le "$REGISTRY_MANIFEST_PROBE_TOKEN_MAX_BYTES" ]] || return 1
  printf '%s' "$token_json" | jq -r '.token // .access_token // empty'
}

# 解析指定 repo:tag 在远端 registry 上的 digest。
registry_manifest_probe_resolve_digest() {
  local repo_ref="$1"
  local tag="$2"
  local override_digest="${3:-}"
  local registry='' repo_path='' manifest_url=''
  local headers_file='' body_file='' http_code='' digest='' www_authenticate='' token=''

  if [[ -n "$override_digest" ]]; then
    printf '%s\n' "$override_digest"
    return 0
  fi

  registry_manifest_probe_require_cmds

  if [[ "$repo_ref" != */* ]]; then
    return 2
  fi
  registry="${repo_ref%%/*}"
  repo_path="${repo_ref#*/}"
  manifest_url="https://${registry}/v2/${repo_path}/manifests/${tag}"
  headers_file="$(mktemp)"
  body_file="$(mktemp)"
  cleanup_registry_manifest_probe() {
    rm -f "$headers_file" "$body_file"
  }
  trap cleanup_registry_manifest_probe RETURN

  http_code="$(registry_manifest_probe_open_manifest "$manifest_url" "$headers_file" "$body_file" || true)"
  if [[ "$http_code" == '401' ]]; then
    www_authenticate="$(registry_manifest_probe_extract_header_value "$headers_file" 'WWW-Authenticate')"
    [[ "${www_authenticate,,}" == bearer* ]] || return 11
    token="$(registry_manifest_probe_fetch_bearer_token "$www_authenticate" || true)"
    [[ -n "$token" ]] || return 11
    : > "$headers_file"
    : > "$body_file"
    http_code="$(registry_manifest_probe_open_manifest "$manifest_url" "$headers_file" "$body_file" "Authorization: Bearer $token" || true)"
  fi

  if [[ "$http_code" != '200' ]]; then
    return 11
  fi
  digest="$(registry_manifest_probe_extract_header_value "$headers_file" 'Docker-Content-Digest')"
  if [[ -z "$digest" ]]; then
    digest="sha256:$(registry_manifest_probe_sha256_file "$body_file")"
  fi
  [[ -n "$digest" ]] || return 11
  printf '%s\n' "$digest"
}

# 比较两个 digest 是否一致，并输出明确错误。
registry_manifest_probe_require_equal() {
  local left_digest="$1"
  local right_digest="$2"
  local left_label="$3"
  local right_label="$4"
  [[ -n "$left_digest" && -n "$right_digest" ]] || {
    echo "[registry_manifest_probe] digest 为空，无法比较：$left_label / $right_label" >&2
    return 2
  }
  if [[ "$left_digest" != "$right_digest" ]]; then
    echo "[registry_manifest_probe] digest 不一致：$left_label=$left_digest ; $right_label=$right_digest" >&2
    return 10
  fi
  return 0
}
