#!/usr/bin/env bash
# 用途：按 OPENCLAW_TLS_MODE 准备 ingress HTTPS 证书资产。
# 说明：
# - self_signed：生成 365 天自签 DNS SAN 证书到 deploy/nginx/certs/
# - provided_files：严格校验未过期 PEM 证书、精确 dNSName SAN、未加密 PEM 私钥与非输出目录源路径后复制到 deploy/nginx/certs/
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
ENV_FILE="$ROOT_DIR/deploy/.env"
source "$ROOT_DIR/scripts/setup/lib/runtime_permissions.sh"

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/setup/gen_cert.sh

说明：
  - 按 OPENCLAW_TLS_MODE 准备 ingress HTTPS 证书资产；
  - self_signed：生成 365 天自签 DNS SAN 证书到 deploy/nginx/certs/；
  - provided_files：严格校验未过期 PEM 证书、精确 dNSName SAN、未加密 PEM 私钥与非输出目录源路径后复制到 deploy/nginx/certs/。
USAGE
}

if [[ $# -gt 0 ]]; then
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[gen_cert][FAIL] 未知参数：$1" >&2
      exit 2
      ;;
  esac
fi

fail() {
  echo "[gen_cert][FAIL] $*" >&2
  exit 2
}

read_env_value() {
  local key="$1"
  local line value
  [[ -f "$ENV_FILE" ]] || fail "缺少 env 文件：$ENV_FILE"
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -n "$line" ]] || continue
    [[ "$line" == \#* ]] && continue
    [[ "$line" == "$key="* ]] || continue
    value="${line#*=}"
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"
    printf '%s' "$value"
    return 0
  done < "$ENV_FILE"
  return 1
}

TLS_MODE="$(read_env_value OPENCLAW_TLS_MODE || true)"
TLS_CN="$(read_env_value OPENCLAW_TLS_CN || true)"
CERT_SOURCE="$(read_env_value OPENCLAW_TLS_CERT_SOURCE_PATH || true)"
KEY_SOURCE="$(read_env_value OPENCLAW_TLS_KEY_SOURCE_PATH || true)"

[[ -n "$TLS_MODE" ]] || fail 'OPENCLAW_TLS_MODE 未配置，无法准备证书资产。'
[[ -n "$TLS_CN" ]] || fail 'OPENCLAW_TLS_CN 未配置，无法准备证书资产。'

case "$TLS_MODE" in
  self_signed)
    OPENCLAW_TLS_CN="$TLS_CN" bash "$ROOT_DIR/deploy/nginx/gen-self-signed-cert.sh" "$TLS_CN"
    ;;
  provided_files)
    [[ -n "$CERT_SOURCE" ]] || fail 'OPENCLAW_TLS_CERT_SOURCE_PATH 未配置，provided_files 模式无法继续。'
    [[ -n "$KEY_SOURCE" ]] || fail 'OPENCLAW_TLS_KEY_SOURCE_PATH 未配置，provided_files 模式无法继续。'
    OPENCLAW_TLS_CN="$TLS_CN" bash "$ROOT_DIR/deploy/nginx/install-provided-cert.sh" "$CERT_SOURCE" "$KEY_SOURCE" "$TLS_CN"
    ;;
  *)
    fail "OPENCLAW_TLS_MODE 只允许 self_signed 或 provided_files，当前值：$TLS_MODE"
    ;;
esac

runtime_permissions_harden_certs "$ROOT_DIR"
runtime_permissions_prepare_ingress_cap_drop_mount_access "$ROOT_DIR"
