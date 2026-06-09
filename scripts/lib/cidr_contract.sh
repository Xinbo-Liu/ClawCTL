#!/usr/bin/env bash
# 用途：统一 OpenClaw setup / doctor 入口的来源 CIDR 合同，避免脚本各自维护解析规则。

openclaw_cidr_contract_fail() {
  printf '[cidr_contract][FAIL] %s\n' "$*" >&2
  return 1
}

_openclaw_cidr_ipv4_private_or_loopback() {
  local ip="$1"
  local a=0 b=0 c=0 d=0
  [[ "$ip" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
  a="${BASH_REMATCH[1]}"; b="${BASH_REMATCH[2]}"; c="${BASH_REMATCH[3]}"; d="${BASH_REMATCH[4]}"
  ((10#$a <= 255 && 10#$b <= 255 && 10#$c <= 255 && 10#$d <= 255)) || return 1
  ((10#$a == 10)) && return 0
  ((10#$a == 127)) && return 0
  ((10#$a == 192 && 10#$b == 168)) && return 0
  ((10#$a == 172 && 10#$b >= 16 && 10#$b <= 31)) && return 0
  return 1
}

_openclaw_cidr_ipv6_group_count() {
  local value="$1"
  local group=''
  local groups=()
  [[ -n "$value" ]] || { printf '0'; return 0; }
  IFS=':' read -ra groups <<< "$value"
  for group in "${groups[@]}"; do
    [[ "$group" =~ ^[0-9a-f]{1,4}$ ]] || return 1
  done
  printf '%s' "${#groups[@]}"
}

_openclaw_cidr_ipv6_address_syntax_is_valid() {
  local address="${1,,}"
  local left=''
  local right=''
  local left_count=0
  local right_count=0
  local group_count=0
  [[ -n "$address" && "$address" == *:* ]] || return 1
  [[ "$address" != *.* ]] || return 1
  [[ "$address" =~ ^[0-9a-f:]+$ ]] || return 1
  [[ "$address" != *:::* ]] || return 1
  if [[ "$address" == *::* ]]; then
    [[ "$address" != *::*::* ]] || return 1
    left="${address%%::*}"
    right="${address#*::}"
    left_count="$(_openclaw_cidr_ipv6_group_count "$left")" || return 1
    right_count="$(_openclaw_cidr_ipv6_group_count "$right")" || return 1
    ((10#$left_count + 10#$right_count <= 7)) || return 1
    return 0
  fi
  group_count="$(_openclaw_cidr_ipv6_group_count "$address")" || return 1
  ((10#$group_count == 8))
}

_openclaw_cidr_ipv6_private_or_loopback() {
  local ip="${1,,}"
  [[ "$ip" == *:* ]] || return 1
  _openclaw_cidr_ipv6_address_syntax_is_valid "$ip" || return 1
  [[ "$ip" == "::1" ]] && return 0
  [[ "$ip" =~ ^f[c-d][0-9a-f:]*$ ]] && return 0
  return 1
}

openclaw_cidr_is_private_or_loopback() {
  local cidr="$1"
  local ip="${cidr%/*}"
  local prefix="${cidr#*/}"
  local a=0 b=0 c=0 d=0
  [[ "$cidr" == */* ]] || return 1
  [[ "$prefix" =~ ^[0-9]+$ ]] || return 1
  if [[ "$ip" == *:* ]]; then
    ((10#$prefix >= 0 && 10#$prefix <= 128)) || return 1
    ip="${ip,,}"
    _openclaw_cidr_ipv6_address_syntax_is_valid "$ip" || return 1
    [[ "$ip" == "::1" ]] && ((10#$prefix == 128)) && return 0
    [[ "$ip" =~ ^f[c-d][0-9a-f:]*$ ]] && ((10#$prefix >= 7)) && return 0
    return 1
  fi
  ((10#$prefix >= 0 && 10#$prefix <= 32)) || return 1
  [[ "$ip" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
  a="${BASH_REMATCH[1]}"; b="${BASH_REMATCH[2]}"; c="${BASH_REMATCH[3]}"; d="${BASH_REMATCH[4]}"
  ((10#$a <= 255 && 10#$b <= 255 && 10#$c <= 255 && 10#$d <= 255)) || return 1
  ((10#$a == 10 && 10#$prefix >= 8)) && return 0
  ((10#$a == 127 && 10#$prefix >= 8)) && return 0
  ((10#$a == 192 && 10#$b == 168 && 10#$prefix >= 16)) && return 0
  ((10#$a == 172 && 10#$b >= 16 && 10#$b <= 31 && 10#$prefix >= 12)) && return 0
  return 1
}

_openclaw_cidr_validate_item() {
  local item="$1"
  local label="$2"
  local address=''
  local prefix=''
  local lower_address=''
  local a=0 b=0 c=0 d=0
  [[ "$item" == */* ]] || openclaw_cidr_contract_fail "$label 每一项都必须是 CIDR：$item" || return 1
  [[ "$item" =~ ^[A-Fa-f0-9:.]+/[0-9]{1,3}$ ]] || openclaw_cidr_contract_fail "$label CIDR 格式无效：$item" || return 1
  address="${item%/*}"
  prefix="${item#*/}"
  if [[ "$address" == *:* ]]; then
    ((10#$prefix <= 128)) || openclaw_cidr_contract_fail "$label IPv6 前缀长度不能超过 128：$item" || return 1
    lower_address="${address,,}"
    _openclaw_cidr_ipv6_address_syntax_is_valid "$lower_address" || openclaw_cidr_contract_fail "$label IPv6 地址格式无效：$item" || return 1
    if [[ "$lower_address" == "::1" ]]; then
      ((10#$prefix == 128)) || openclaw_cidr_contract_fail "$label loopback IPv6 必须使用 /128：$item" || return 1
    elif [[ "$lower_address" =~ ^f[c-d][0-9a-f:]*$ ]]; then
      ((10#$prefix >= 7)) || openclaw_cidr_contract_fail "$label IPv6 私网前缀过宽：$item" || return 1
    else
      openclaw_cidr_contract_fail "$label 只允许私网或 loopback CIDR：$item" || return 1
    fi
  else
    ((10#$prefix <= 32)) || openclaw_cidr_contract_fail "$label IPv4 前缀长度不能超过 32：$item" || return 1
    [[ "$address" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || openclaw_cidr_contract_fail "$label IPv4 地址格式无效：$item" || return 1
    a="${BASH_REMATCH[1]}"; b="${BASH_REMATCH[2]}"; c="${BASH_REMATCH[3]}"; d="${BASH_REMATCH[4]}"
    ((10#$a <= 255 && 10#$b <= 255 && 10#$c <= 255 && 10#$d <= 255)) || openclaw_cidr_contract_fail "$label IPv4 地址越界：$item" || return 1
    if ((10#$a == 10)); then
      ((10#$prefix >= 8)) || openclaw_cidr_contract_fail "$label IPv4 私网前缀过宽：$item" || return 1
    elif ((10#$a == 127)); then
      ((10#$prefix >= 8)) || openclaw_cidr_contract_fail "$label IPv4 loopback 前缀过宽：$item" || return 1
    elif ((10#$a == 192 && 10#$b == 168)); then
      ((10#$prefix >= 16)) || openclaw_cidr_contract_fail "$label IPv4 私网前缀过宽：$item" || return 1
    elif ((10#$a == 172 && 10#$b >= 16 && 10#$b <= 31)); then
      ((10#$prefix >= 12)) || openclaw_cidr_contract_fail "$label IPv4 私网前缀过宽：$item" || return 1
    else
      openclaw_cidr_contract_fail "$label 只允许私网或 loopback CIDR：$item" || return 1
    fi
  fi
}

openclaw_cidr_validate_list() {
  local value="$1"
  local label="${2:---client-cidr}"
  local item=''
  [[ -z "$value" ]] && return 0
  [[ "$value" =~ ^[A-Fa-f0-9:./,]+$ ]] || openclaw_cidr_contract_fail "$label 只允许 CIDR 字符与逗号分隔，不允许空格或 shell 特殊字符。" || return 1
  [[ "$value" != *, && "$value" != ,* && "$value" != *,,* ]] || openclaw_cidr_contract_fail "$label 不能包含空项；多个来源段请使用形如 10.0.0.0/8,192.168.50.0/24 的逗号分隔列表。" || return 1
  IFS=',' read -ra __openclaw_cidr_items <<< "$value"
  for item in "${__openclaw_cidr_items[@]}"; do
    _openclaw_cidr_validate_item "$item" "$label" || return 1
  done
}

_openclaw_cidr_ipv4_to_int() {
  local ip="$1"
  local a='' b='' c='' d='' part=''
  local ai=0 bi=0 ci=0 di=0
  IFS=. read -r a b c d <<< "$ip"
  for part in "$a" "$b" "$c" "$d"; do
    [[ "$part" =~ ^[0-9]{1,3}$ ]] || return 1
    ((10#$part <= 255)) || return 1
  done
  ai=$((10#$a)); bi=$((10#$b)); ci=$((10#$c)); di=$((10#$d))
  printf '%u\n' $(((ai << 24) + (bi << 16) + (ci << 8) + di))
}

_openclaw_cidr_contains_ipv4() {
  local allowed="$1"
  local client="$2"
  local allowed_ip="${allowed%/*}" allowed_prefix="${allowed#*/}"
  local client_ip="${client%/*}" client_prefix="${client#*/}"
  local allowed_int=0 client_int=0 mask=0
  [[ "$allowed_ip" != *:* && "$client_ip" != *:* ]] || return 1
  [[ "$allowed_prefix" =~ ^[0-9]+$ && "$client_prefix" =~ ^[0-9]+$ ]] || return 1
  ((10#$allowed_prefix >= 0 && 10#$allowed_prefix <= 32 && 10#$client_prefix >= 0 && 10#$client_prefix <= 32)) || return 1
  ((10#$client_prefix >= 10#$allowed_prefix)) || return 1
  allowed_int="$(_openclaw_cidr_ipv4_to_int "$allowed_ip")" || return 1
  client_int="$(_openclaw_cidr_ipv4_to_int "$client_ip")" || return 1
  if ((10#$allowed_prefix == 0)); then
    return 0
  fi
  mask=$(((0xFFFFFFFF << (32 - 10#$allowed_prefix)) & 0xFFFFFFFF))
  (((allowed_int & mask) == (client_int & mask)))
}

_openclaw_cidr_ipv6_expand_to_hex() {
  local address="${1,,}"
  local left='' right='' group='' missing=0 value=0
  local groups=() left_groups=() right_groups=()
  _openclaw_cidr_ipv6_address_syntax_is_valid "$address" || return 1
  if [[ "$address" == *::* ]]; then
    left="${address%%::*}"
    right="${address#*::}"
    [[ -z "$left" ]] || IFS=':' read -ra left_groups <<< "$left"
    [[ -z "$right" ]] || IFS=':' read -ra right_groups <<< "$right"
    missing=$((8 - ${#left_groups[@]} - ${#right_groups[@]}))
    ((missing >= 1)) || return 1
    groups=("${left_groups[@]}")
    while ((missing > 0)); do
      groups+=('0')
      missing=$((missing - 1))
    done
    groups+=("${right_groups[@]}")
  else
    IFS=':' read -ra groups <<< "$address"
  fi
  ((${#groups[@]} == 8)) || return 1
  for group in "${groups[@]}"; do
    [[ "$group" =~ ^[0-9a-f]{1,4}$ ]] || return 1
    value=$((16#${group^^}))
    printf '%04x' "$value"
  done
}

_openclaw_cidr_hex_prefix_matches() {
  local allowed_hex="$1"
  local client_hex="$2"
  local prefix="$3"
  local full_nibbles=0 rem_bits=0 allowed_digit=0 client_digit=0 mask=0
  ((10#$prefix == 0)) && return 0
  full_nibbles=$((10#$prefix / 4))
  rem_bits=$((10#$prefix % 4))
  [[ "${allowed_hex:0:$full_nibbles}" == "${client_hex:0:$full_nibbles}" ]] || return 1
  ((rem_bits == 0)) && return 0
  allowed_digit=$((16#${allowed_hex:$full_nibbles:1}))
  client_digit=$((16#${client_hex:$full_nibbles:1}))
  mask=$((0xF & (0xF << (4 - rem_bits))))
  (((allowed_digit & mask) == (client_digit & mask)))
}

_openclaw_cidr_contains_ipv6() {
  local allowed="$1"
  local client="$2"
  local allowed_ip="${allowed%/*}" allowed_prefix="${allowed#*/}"
  local client_ip="${client%/*}" client_prefix="${client#*/}"
  local allowed_hex='' client_hex=''
  [[ "$allowed_ip" == *:* && "$client_ip" == *:* ]] || return 1
  [[ "$allowed_prefix" =~ ^[0-9]+$ && "$client_prefix" =~ ^[0-9]+$ ]] || return 1
  ((10#$allowed_prefix >= 0 && 10#$allowed_prefix <= 128 && 10#$client_prefix >= 0 && 10#$client_prefix <= 128)) || return 1
  ((10#$client_prefix >= 10#$allowed_prefix)) || return 1
  allowed_hex="$(_openclaw_cidr_ipv6_expand_to_hex "$allowed_ip")" || return 1
  client_hex="$(_openclaw_cidr_ipv6_expand_to_hex "$client_ip")" || return 1
  _openclaw_cidr_hex_prefix_matches "$allowed_hex" "$client_hex" "$allowed_prefix"
}

openclaw_cidr_contains() {
  local allowed="$1"
  local client="$2"
  if [[ "${allowed%/*}" == *:* || "${client%/*}" == *:* ]]; then
    _openclaw_cidr_contains_ipv6 "$allowed" "$client"
  else
    _openclaw_cidr_contains_ipv4 "$allowed" "$client"
  fi
}

openclaw_cidr_list_allows_client() {
  local list="$1"
  local expected="$2"
  local allowed=''
  while IFS= read -r allowed; do
    [[ -n "$allowed" ]] || continue
    openclaw_cidr_contains "$allowed" "$expected" && return 0
  done < <(printf '%s\n' "$list" | tr ',' '\n' | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')
  return 1
}

openclaw_cidr_list_count() {
  local list="$1"
  printf '%s\n' "$list" | tr ',' '\n' | sed -E 's/^[[:space:]]+|[[:space:]]+$//g' | awk 'NF { count += 1 } END { print count + 0 }'
}

openclaw_cidr_list_first() {
  local list="$1"
  printf '%s\n' "$list" | tr ',' '\n' | sed -E 's/^[[:space:]]+|[[:space:]]+$//g' | awk 'NF { print; exit }'
}

openclaw_cidr_first_non_private_or_loopback() {
  local list="$1"
  local cidr=''
  while IFS= read -r cidr; do
    [[ -n "$cidr" ]] || continue
    openclaw_cidr_is_private_or_loopback "$cidr" || { printf '%s\n' "$cidr"; return 0; }
  done < <(printf '%s\n' "$list" | tr ',' '\n' | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')
}

openclaw_cidr_first_not_allowed() {
  local allowed_list="$1"
  local client_list="$2"
  local cidr=''
  while IFS= read -r cidr; do
    [[ -n "$cidr" ]] || continue
    openclaw_cidr_list_allows_client "$allowed_list" "$cidr" || { printf '%s\n' "$cidr"; return 0; }
  done < <(printf '%s\n' "$client_list" | tr ',' '\n' | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')
}
