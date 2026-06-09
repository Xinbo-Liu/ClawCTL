#!/usr/bin/env bash
# 用途：严格校验 provided_files 模式下的外部 PEM 证书、未加密 PEM 私钥与源路径，并复制到 Nginx 统一证书目录。
set -euo pipefail

BASE_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
ROOT_DIR="$(CDPATH='' cd -- "$BASE_DIR/../.." && pwd)"
CERT_DIR="$BASE_DIR/certs"
SRC_CERT="${1:-}"
SRC_KEY="${2:-}"
TLS_CN="${3:-${OPENCLAW_TLS_CN:-}}"

# shellcheck source=scripts/setup/lib/tls_hostname_contract.sh
source "$ROOT_DIR/scripts/setup/lib/tls_hostname_contract.sh"

fail() {
  echo "[install_provided_cert][FAIL] $*" >&2
  exit 2
}

[[ -n "$SRC_CERT" ]] || fail '缺少证书源路径'
[[ -n "$SRC_KEY" ]] || fail '缺少私钥源路径'
[[ -n "$TLS_CN" ]] || fail '缺少 OPENCLAW_TLS_CN'
[[ -f "$SRC_CERT" ]] || fail "证书文件不存在：$SRC_CERT"
[[ -f "$SRC_KEY" ]] || fail "私钥文件不存在：$SRC_KEY"
command -v openssl >/dev/null 2>&1 || fail '缺少 openssl，无法校验证书'

openclaw_tls_hostname_require "$TLS_CN" 'OPENCLAW_TLS_CN' || fail 'OPENCLAW_TLS_CN 不符合 TLS 主机名合同'

canonical_path() {
  local path="$1"
  local dir='' base=''
  dir="$(CDPATH='' cd -- "$(dirname -- "$path")" && pwd -P)" || return 1
  base="$(basename -- "$path")"
  printf '%s/%s' "$dir" "$base"
}

physical_path() {
  local path="$1"
  if command -v realpath >/dev/null 2>&1; then
    realpath -- "$path"
    return $?
  fi
  if command -v readlink >/dev/null 2>&1 && readlink -f -- "$path" >/dev/null 2>&1; then
    readlink -f -- "$path"
    return $?
  fi
  return 1
}

reject_output_source_path() {
  local path="$1"
  local cert_dir_real='' source_real='' target_real=''
  mkdir -p "$CERT_DIR"
  cert_dir_real="$(CDPATH='' cd -- "$CERT_DIR" && pwd -P)"
  source_real="$(canonical_path "$path")" || fail "无法解析源路径：$path"
  target_real="$(physical_path "$path")" || fail "无法解析源路径真实目标：$path"
  case "$source_real" in
    "$cert_dir_real"/*) fail "provided_files 源文件不得位于输出证书目录：$source_real" ;;
  esac
  case "$target_real" in
    "$cert_dir_real"/*) fail "provided_files 源文件不得指向输出证书目录：$target_real" ;;
  esac
}

validate_exact_dns_san() {
  local cert="$1"
  local host="$2"
  local san_text=''
  san_text="$(openssl x509 -in "$cert" -noout -ext subjectAltName 2>/dev/null)" || fail '证书缺少 subjectAltName 扩展或无法读取 SAN'
  printf '%s\n' "$san_text" \
    | tr ',' '\n' \
    | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' \
    | grep -Fx "DNS:$host" >/dev/null \
    || fail "证书必须包含精确 dNSName SAN：$host；不接受 CN-only 或 wildcard 替代"
}

validate_pair_match() {
  local cert="$1"
  local key="$2"
  local cert_pub key_pub
  cert_pub="$(openssl x509 -in "$cert" -pubkey -noout | openssl pkey -pubin -outform PEM 2>/dev/null | sha256sum | awk '{print $1}')"
  key_pub="$(openssl pkey -in "$key" -pubout -outform PEM -passin pass: 2>/dev/null | sha256sum | awk '{print $1}')"
  [[ -n "$cert_pub" && -n "$key_pub" ]] || fail '无法提取证书/私钥公钥，确认私钥格式受 openssl 支持'
  [[ "$cert_pub" == "$key_pub" ]] || fail '证书与私钥不匹配'
}

reject_output_source_path "$SRC_CERT"
reject_output_source_path "$SRC_KEY"
openssl x509 -in "$SRC_CERT" -noout >/dev/null 2>&1 || fail '证书文件不是有效的 PEM X.509 证书'
openssl x509 -in "$SRC_CERT" -noout -checkend 0 >/dev/null 2>&1 || fail '证书已经过期'
if grep -qiE 'ENCRYPTED|Proc-Type: 4,ENCRYPTED' "$SRC_KEY"; then
  fail '私钥必须是未加密 PEM 私钥'
fi
openssl pkey -in "$SRC_KEY" -noout -passin pass: >/dev/null 2>&1 || fail '私钥文件不是有效的未加密 PEM 私钥'
validate_exact_dns_san "$SRC_CERT" "$TLS_CN"
validate_pair_match "$SRC_CERT" "$SRC_KEY"

mkdir -p "$CERT_DIR"
chmod 700 "$CERT_DIR"
cp "$SRC_CERT" "$CERT_DIR/openclaw.crt"
cp "$SRC_KEY" "$CERT_DIR/openclaw.key"
chmod 600 "$CERT_DIR/openclaw.crt" "$CERT_DIR/openclaw.key"

echo "已安装 provided_files 证书：$CERT_DIR/openclaw.crt"
echo "已安装 provided_files 私钥：$CERT_DIR/openclaw.key"
echo "证书精确 dNSName SAN：$TLS_CN"
