#!/usr/bin/env bash
# 用途：为 Nginx HTTPS 入口生成自签名证书与私钥。
# 说明：OPENCLAW_TLS_CN 固定作为精确 DNS SAN；不支持 IP SAN。
set -euo pipefail

BASE_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH='' cd -- "$BASE_DIR/../.." && pwd)
CERT_DIR="$BASE_DIR/certs"
CN_INPUT="${1:-${OPENCLAW_TLS_CN:-}}"
DAYS=365

# shellcheck source=scripts/setup/lib/tls_hostname_contract.sh
source "$ROOT_DIR/scripts/setup/lib/tls_hostname_contract.sh"

mkdir -p "$CERT_DIR"
chmod 700 "$CERT_DIR"

openclaw_tls_hostname_require "$CN_INPUT" 'CN' || exit 1

TMP_CONF=$(mktemp)
cleanup() {
  rm -f "$TMP_CONF"
}
trap cleanup EXIT INT TERM

cat > "$TMP_CONF" <<EOF
[ req ]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
x509_extensions = v3_req

[ dn ]
CN = $CN_INPUT

[ v3_req ]
subjectAltName = @alt_names
extendedKeyUsage = serverAuth

[ alt_names ]
DNS.1 = $CN_INPUT
EOF

openssl req -x509 -nodes -days "$DAYS" -newkey rsa:2048 \
  -keyout "$CERT_DIR/openclaw.key" \
  -out "$CERT_DIR/openclaw.crt" \
  -config "$TMP_CONF" \
  -extensions v3_req

chmod 600 "$CERT_DIR/openclaw.key" "$CERT_DIR/openclaw.crt"

printf '%s\n' "已生成：$CERT_DIR/openclaw.crt 和 $CERT_DIR/openclaw.key"
printf '%s\n' "证书精确 dNSName SAN：$CN_INPUT"
