#!/usr/bin/env bash
# 用途：从访问端视角闭合 DNS/hosts、证书信任、来源 CIDR 与 HTTPS 验证命令；不替代目标机 deployment acceptance。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
# shellcheck source=../lib/cidr_contract.sh
source "$ROOT_DIR/scripts/lib/cidr_contract.sh"

ENV_FILE="$ROOT_DIR/deploy/.env"
CLIENT_CIDR=""
TLS_CN_OVERRIDE=""
FORMAT="text"

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/setup/check_client_access_acceptance.sh --env-file deploy/.env --client-cidr <cidr[,cidr]> --tls-cn <host>

说明：
  - deployment_acceptance 表示目标机本机验收；现有 one_click_test_full 覆盖。
  - client_access_acceptance 表示访问端 DNS/hosts、证书信任、来源 CIDR、浏览器/HTTP 验证闭合。
  - client-cidr 只接受逗号分隔的私网或 loopback CIDR；公网来源应通过外部 ACL、VPN 或 NAT 私网来源先完成边界确认。
  - 仅把目标机自身 /32 写入 allowlist 只代表目标机自验通过，不代表外部浏览器已放行。
USAGE
}

fail() {
  echo "[check_client_access_acceptance][FAIL] $*" >&2
  exit 2
}

warn() {
  echo "[WARN] $*"
}

note() {
  echo "[INFO] $*"
}

read_env_key() {
  local key="$1"
  [[ -f "$ENV_FILE" ]] || return 0
  awk -F= -v expected="$key" '
    $0 ~ /^[[:space:]]*#/ { next }
    $0 !~ /=/ { next }
    {
      name = $1
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
      if (name == expected) {
        value = substr($0, index($0, "=") + 1)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
        gsub(/^'\''|'\''$/, "", value)
        gsub(/^"|"$/, "", value)
        print value
        exit
      }
    }
  ' "$ENV_FILE"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      [[ $# -ge 2 ]] || fail '--env-file 缺少路径参数'
      ENV_FILE="$2"
      shift 2
      ;;
    --client-cidr)
      [[ $# -ge 2 ]] || fail '--client-cidr 缺少 CIDR 参数'
      CLIENT_CIDR="$2"
      shift 2
      ;;
    --tls-cn)
      [[ $# -ge 2 ]] || fail '--tls-cn 缺少 host 参数'
      TLS_CN_OVERRIDE="$2"
      shift 2
      ;;
    --format)
      [[ $# -ge 2 ]] || fail '--format 缺少参数'
      FORMAT="$2"
      [[ "$FORMAT" == 'text' || "$FORMAT" == 'json' ]] || fail '--format 仅支持 text|json'
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "未知参数：$1"
      ;;
  esac
done

[[ -f "$ENV_FILE" ]] || fail "env 文件不存在：$ENV_FILE"
[[ -n "$CLIENT_CIDR" ]] || fail '必须提供 --client-cidr <cidr[,cidr]>'
openclaw_cidr_validate_list "$CLIENT_CIDR" '--client-cidr' || exit 2
UNSAFE_CLIENT_CIDR="$(openclaw_cidr_first_non_private_or_loopback "$CLIENT_CIDR")"
if [[ -n "$UNSAFE_CLIENT_CIDR" ]]; then
  fail "client-cidr 不是私网或 loopback：$UNSAFE_CLIENT_CIDR。目标机实际看到的来源不符合当前合同；请先确认外部 ACL、VPN 或 NAT 后的私网来源 CIDR，再重新执行。"
fi

TLS_CN="${TLS_CN_OVERRIDE:-$(read_env_key OPENCLAW_TLS_CN)}"
LISTEN_IP="$(read_env_key OPENCLAW_INGRESS_LISTEN_IP)"
CERT_DIR="$(read_env_key OPENCLAW_TLS_CERT_DIR)"
CERT_FILE="$(read_env_key OPENCLAW_TLS_CERT_FILE)"
TLS_MODE="$(read_env_key OPENCLAW_TLS_MODE)"
ALLOWED_CIDRS="$(read_env_key OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS)"
HOST_STATE_ROOT="$(read_env_key HOST_STATE_ROOT)"
[[ -n "$TLS_CN" ]] || TLS_CN='<OPENCLAW_TLS_CN>'
[[ -n "$LISTEN_IP" ]] || LISTEN_IP='<OPENCLAW_INGRESS_LISTEN_IP>'
[[ -n "$CERT_DIR" ]] || CERT_DIR='deploy/nginx/certs'
[[ -n "$CERT_FILE" ]] || CERT_FILE='openclaw.crt'
[[ -n "$TLS_MODE" ]] || TLS_MODE='self_signed'
[[ -n "$HOST_STATE_ROOT" ]] || HOST_STATE_ROOT='state/openclaw'
RESOLVE_IP="$LISTEN_IP"
if [[ "$RESOLVE_IP" == *:* && "$RESOLVE_IP" != \[*\] ]]; then
  RESOLVE_IP="[$RESOLVE_IP]"
fi

STATUS='blocked'
DETAIL=''
ONLY_ALLOWED_CIDR="$(openclaw_cidr_list_first "$ALLOWED_CIDRS")"
UNSAFE_ALLOWED_CIDR="$(openclaw_cidr_first_non_private_or_loopback "$ALLOWED_CIDRS")"
DENIED_CLIENT_CIDR="$(openclaw_cidr_first_not_allowed "$ALLOWED_CIDRS" "$CLIENT_CIDR")"
if [[ -n "$UNSAFE_ALLOWED_CIDR" ]]; then
  fail "OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS 包含公网、非私网或过宽 CIDR：$UNSAFE_ALLOWED_CIDR。目标机实际看到的来源不符合当前合同；请先确认外部 ACL、VPN 或 NAT 后的私网来源 CIDR，再重新物化边界规则。"
fi
if [[ -z "$DENIED_CLIENT_CIDR" ]]; then
  STATUS='ready'
  DETAIL='client CIDR 列表已全部被当前 Nginx allowlist 覆盖；仍需在访问端执行 curl/浏览器验证。'
elif [[ "$(openclaw_cidr_list_count "$ALLOWED_CIDRS")" == '1' && ( "$ONLY_ALLOWED_CIDR" == */32 || "$ONLY_ALLOWED_CIDR" == */128 ) ]]; then
  STATUS='not_closed'
  DETAIL='当前 allowlist 只有目标机本机单地址 CIDR；deployment acceptance 可通过，但外部访问尚未闭合。'
else
  STATUS='blocked'
  DETAIL="client CIDR 未进入当前 allowlist：$DENIED_CLIENT_CIDR；请重新物化 ingress 边界规则或确认 VPN/NAT 后的来源 CIDR。"
fi

if [[ "$FORMAT" == 'json' ]]; then
  command -v jq >/dev/null 2>&1 || fail '--format json 需要 jq'
  jq -n \
    --arg envFile "$ENV_FILE" \
    --arg tlsCn "$TLS_CN" \
    --arg listenIp "$LISTEN_IP" \
    --arg certPath "$CERT_DIR/$CERT_FILE" \
    --arg tlsMode "$TLS_MODE" \
    --arg clientCidr "$CLIENT_CIDR" \
    --arg allowedCidrs "$ALLOWED_CIDRS" \
    --arg deploymentAcceptance "target_local_acceptance" \
    --arg clientAccessAcceptance "$STATUS" \
    --arg detail "$DETAIL" \
    '{
      envFile: $envFile,
      tlsCn: $tlsCn,
      listenIp: $listenIp,
      certPath: $certPath,
      tlsMode: $tlsMode,
      clientCidr: $clientCidr,
      clientCidrs: ($clientCidr | split(",") | map(gsub("^\\s+|\\s+$"; "")) | map(select(length > 0))),
      allowedCidrs: ($allowedCidrs | split(",") | map(gsub("^\\s+|\\s+$"; "")) | map(select(length > 0))),
      deployment_acceptance: $deploymentAcceptance,
      client_access_acceptance: $clientAccessAcceptance,
      detail: $detail
    }'
else
  note "deployment_acceptance=target_local_acceptance（目标机本机验收由 one_click_test_full 覆盖）"
  note "client_access_acceptance=$STATUS"
  note "$DETAIL"
  note "OPENCLAW_TLS_CN=$TLS_CN"
  note "OPENCLAW_INGRESS_LISTEN_IP=$LISTEN_IP"
  note "OPENCLAW_TLS_MODE=$TLS_MODE"
  note "证书路径=$CERT_DIR/$CERT_FILE"
  note "HOST_STATE_ROOT=$HOST_STATE_ROOT"
  echo
  echo "访问端 DNS/hosts 检查："
  echo "  getent hosts $TLS_CN || nslookup $TLS_CN"
  echo "  # 临时 hosts 示例：$LISTEN_IP $TLS_CN"
  echo
  echo "访问端证书检查："
  echo "  openssl s_client -connect $TLS_CN:443 -servername $TLS_CN -showcerts </dev/null"
  echo
  echo "访问端 HTTP 验证："
  if [[ "$TLS_MODE" == "self_signed" ]]; then
    echo "  curl --cacert $CERT_DIR/$CERT_FILE --resolve $TLS_CN:443:$RESOLVE_IP https://$TLS_CN/"
    echo "  curl --cacert $CERT_DIR/$CERT_FILE --resolve $TLS_CN:443:$RESOLVE_IP https://$TLS_CN/healthz"
  else
    echo "  curl --resolve $TLS_CN:443:$RESOLVE_IP https://$TLS_CN/"
    echo "  curl --resolve $TLS_CN:443:$RESOLVE_IP https://$TLS_CN/healthz"
  fi
fi

[[ "$STATUS" == 'ready' ]] || exit 1
