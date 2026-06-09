#!/usr/bin/env bash
# 用途：统一 shell 入口的 OPENCLAW_TLS_CN 主机名合同。

if [[ -n "${OPENCLAW_TLS_HOSTNAME_CONTRACT_SH_LOADED:-}" ]]; then
  return 0 2>/dev/null || exit 0
fi
OPENCLAW_TLS_HOSTNAME_CONTRACT_SH_LOADED=1

OPENCLAW_TLS_HOSTNAME_ERROR='必须是 ASCII DNS 主机名，拒绝 IP、IPv4 dotted-quad 形态、通配符、下划线、空白、尾随点、空 label、超长 label 和非 DNS label 字符'

openclaw_tls_hostname_error() {
  local value="${1-}"
  if [[ -z "$value" ]]; then
    printf '%s\n' '不能为空'
    return 0
  fi
  if [[ "$value" == *[[:space:]]* ]]; then
    printf '%s\n' '不能包含空白字符'
    return 0
  fi
  if [[ "$value" == .* || "$value" == *. ]]; then
    printf '%s\n' '不能以点开头或结尾'
    return 0
  fi
  if [[ "$value" == *..* ]]; then
    printf '%s\n' 'DNS label 不能为空'
    return 0
  fi
  if [[ "$value" == *"_"* || "$value" == *"*"* ]]; then
    printf '%s\n' "$OPENCLAW_TLS_HOSTNAME_ERROR"
    return 0
  fi
  if [[ ${#value} -gt 253 ]]; then
    printf '%s\n' '长度不能超过 253 个字符'
    return 0
  fi
  if [[ "$value" =~ ^[0-9]+(\.[0-9]+){3}$ ]]; then
    printf '%s\n' '不能是 IPv4 dotted-quad 形态'
    return 0
  fi
  if [[ ! "$value" =~ ^[A-Za-z0-9.-]+$ ]]; then
    printf '%s\n' "$OPENCLAW_TLS_HOSTNAME_ERROR"
    return 0
  fi

  local label=''
  local -a labels=()
  IFS='.' read -r -a labels <<< "$value"
  for label in "${labels[@]}"; do
    if [[ -z "$label" ]]; then
      printf '%s\n' 'DNS label 不能为空'
      return 0
    fi
    if [[ ${#label} -gt 63 ]]; then
      printf '%s\n' '单个 DNS label 长度不能超过 63 个字符'
      return 0
    fi
    if [[ ! "$label" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$ ]]; then
      printf '%s\n' "$OPENCLAW_TLS_HOSTNAME_ERROR"
      return 0
    fi
  done
  printf '\n'
}

openclaw_tls_hostname_is_valid() {
  [[ -z "$(openclaw_tls_hostname_error "${1-}")" ]]
}

openclaw_tls_hostname_require() {
  local value="${1-}"
  local label="${2:-OPENCLAW_TLS_CN}"
  local error=''
  error="$(openclaw_tls_hostname_error "$value")"
  if [[ -n "$error" ]]; then
    printf '%s: %s：%s\n' "$label" "$error" "$value" >&2
    return 1
  fi
}
