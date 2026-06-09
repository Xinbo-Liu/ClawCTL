#!/usr/bin/env bash
# 用途：把 deploy/.env 中的 ingress 来源限制真源物化到宿主机规则；当前固定优先写入 DOCKER-USER。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
source "$ROOT_DIR/scripts/setup/lib/runtime_permissions.sh"
source "$ROOT_DIR/scripts/setup/lib/deploy_env_shell.sh"
# shellcheck source=../lib/repo_contracts.sh
source "$ROOT_DIR/scripts/lib/repo_contracts.sh"
PYTHON_RUNNER="$ROOT_DIR/scripts/runtime/run_python_container.sh"
repo_contract_assign_path POLICY_PATH governance.ingress_boundary_evidence
ENV_FILE="$ROOT_DIR/deploy/.env"
DRY_RUN=0
NO_VERIFY=0

usage() {
  cat <<'USAGE'
用法：
  sudo bash ./scripts/setup/apply_ingress_boundary_rules.sh [--env-file <path>] [--dry-run] [--no-verify]

说明：
  - 当前唯一职责：把 `OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS` 物化为宿主机 DOCKER-USER ingress 规则；
  - 只处理 `OPENCLAW_INGRESS_BOUNDARY_MODE=host_firewall`；若为 `external_acl`，不会改宿主机规则，但会检查证据路径前提；
  - `host_firewall` 模式固定按当前 deploy env 收口：允许授权来源访问 `OPENCLAW_INGRESS_LISTEN_IP:80/443`，并对其余来源默认拒绝；
  - 规则带 `OPENCLAW_INGRESS_BOUNDARY` 注释，重复执行幂等；
  - `--dry-run` 只校验输入并打印计划动作，不修改规则；
  - 默认 apply 完成后会调用 `check_ingress_boundary_evidence.sh` 做语义复核并写出 root 侧证据；若只想物化规则不立即复核，可加 `--no-verify`。

边界：
  - `one_click_config.sh`、`setup env validate`、`one_click_test_basic.sh` 与 `one_click_deploy.sh` 由各自入口执行；
  - 不负责上游 ACL / 安全组治理；`external_acl` 模式仍需提供结构化证据文件；
  - 当前只按受支持拓扑收口 private ingress 80/443；不负责额外业务端口或非默认 Docker 网络面。
USAGE
}

fail() {
  local message="$1"
  local code="${2:-2}"
  echo "[apply_ingress_boundary_rules][FAIL] $message" >&2
  exit "$code"
}

note() {
  echo "[apply_ingress_boundary_rules] $*"
}

run_ingress_boundary_verify() {
  local -a args=(--env-file "$ENV_FILE")
  if [[ -f "$(runtime_permissions_host_gateway_file "$ROOT_DIR" nginx.gateway.conf)" ]]; then
    args+=(--require-nginx-policy)
  fi
  bash "$ROOT_DIR/scripts/doctor/check_ingress_boundary_evidence.sh" "${args[@]}"
}

require_root() {
  [[ "$(id -u)" == '0' ]] || fail '当前脚本需要 root 权限；请使用 sudo bash ./scripts/setup/apply_ingress_boundary_rules.sh ...' 30
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      [[ $# -ge 2 ]] || fail '--env-file 缺少路径参数'
      ENV_FILE="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --no-verify)
      NO_VERIFY=1
      shift
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
[[ -f "$POLICY_PATH" ]] || fail "缺少 ingress 边界策略真源：$POLICY_PATH"
command -v jq >/dev/null 2>&1 || fail '缺少 jq'
[[ "$DRY_RUN" == '1' ]] || require_root

deploy_env_shell_load_keys "$ENV_FILE" \
  OPENCLAW_INGRESS_BOUNDARY_MODE \
  OPENCLAW_INGRESS_BOUNDARY_EVIDENCE_PATH \
  OPENCLAW_INGRESS_LISTEN_IP \
  OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS

BOUNDARY_MODE="${OPENCLAW_INGRESS_BOUNDARY_MODE:-host_firewall}"
BOUNDARY_EVIDENCE_PATH_RAW="${OPENCLAW_INGRESS_BOUNDARY_EVIDENCE_PATH:-}"
LISTEN_IP="${OPENCLAW_INGRESS_LISTEN_IP:-}"
if [[ -z "$LISTEN_IP" ]]; then
  fail '缺少 OPENCLAW_INGRESS_LISTEN_IP'
fi
if [[ -z "${OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS:-}" ]]; then
  fail '缺少 OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS'
fi
if [[ "$BOUNDARY_MODE" != 'host_firewall' && "$BOUNDARY_MODE" != 'external_acl' ]]; then
  fail "OPENCLAW_INGRESS_BOUNDARY_MODE 只允许 host_firewall 或 external_acl；当前=$BOUNDARY_MODE"
fi

TMP_JSON="$(mktemp)"
cleanup() {
  rm -f "$TMP_JSON"
}
trap cleanup EXIT

bash "$PYTHON_RUNNER" \
  --workdir "$ROOT_DIR" \
  -- \
  -m openclaw.doctor.platform.ingress_boundary_evidence_backend \
  normalize-source-cidrs \
  "$OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS" > "$TMP_JSON"

mapfile -t REQUIRED_PORTS < <(jq -r '.required_host_ports[]' "$POLICY_PATH")
(( ${#REQUIRED_PORTS[@]} > 0 )) || fail 'ingress_boundary_evidence.json 缺少 required_host_ports'

mapfile -t IPV4_CIDRS < <(jq -r '.ipv4[]?' "$TMP_JSON")
mapfile -t IPV6_CIDRS < <(jq -r '.ipv6[]?' "$TMP_JSON")
LISTEN_FAMILY='ipv4'
if [[ "$LISTEN_IP" == *:* ]]; then
  LISTEN_FAMILY='ipv6'
fi
NEEDS_IPV4=0
NEEDS_IPV6=0
if (( ${#IPV4_CIDRS[@]} > 0 )) || [[ "$LISTEN_FAMILY" == 'ipv4' ]]; then
  NEEDS_IPV4=1
fi
if (( ${#IPV6_CIDRS[@]} > 0 )) || [[ "$LISTEN_FAMILY" == 'ipv6' ]]; then
  NEEDS_IPV6=1
fi

if [[ "$BOUNDARY_MODE" == 'external_acl' ]]; then
  [[ -n "$BOUNDARY_EVIDENCE_PATH_RAW" ]] || fail 'external_acl 模式下缺少 OPENCLAW_INGRESS_BOUNDARY_EVIDENCE_PATH'
  BOUNDARY_EVIDENCE_PATH="$(runtime_permissions_abs_path "$ROOT_DIR" "$BOUNDARY_EVIDENCE_PATH_RAW")"
  [[ -f "$BOUNDARY_EVIDENCE_PATH" && -s "$BOUNDARY_EVIDENCE_PATH" ]] || fail "external_acl 证据文件不存在或为空：$BOUNDARY_EVIDENCE_PATH"
  note "boundary_mode=external_acl path=$BOUNDARY_EVIDENCE_PATH"
  if [[ "$DRY_RUN" == '1' ]]; then
    note 'dry-run=true external_acl 模式不改宿主机规则。'
    exit 0
  fi
  if [[ "$NO_VERIFY" == '0' ]]; then
    run_ingress_boundary_verify
  fi
  note 'external_acl 模式无需物化宿主机规则。'
  exit 0
fi

if [[ "$NEEDS_IPV4" == '1' ]]; then
  command -v iptables >/dev/null 2>&1 || fail '存在 IPv4 bind/CIDR，但缺少 iptables'
fi
if [[ "$NEEDS_IPV6" == '1' ]]; then
  command -v ip6tables >/dev/null 2>&1 || fail '存在 IPv6 bind/CIDR，但缺少 ip6tables'
fi

rule_spec_accept() {
  local cidr="$1"
  local port="$2"
  local original_dest_ip="${3:-}"
  if [[ -n "$original_dest_ip" ]]; then
    printf '%s\n' "-p tcp -m conntrack --ctorigdst $original_dest_ip --ctorigdstport $port -s $cidr -m comment --comment OPENCLAW_INGRESS_BOUNDARY -j ACCEPT"
  else
    printf '%s\n' "-p tcp -m conntrack --ctorigdstport $port -s $cidr -m comment --comment OPENCLAW_INGRESS_BOUNDARY -j ACCEPT"
  fi
}

rule_spec_established() {
  local port="$1"
  local original_dest_ip="${2:-}"
  if [[ -n "$original_dest_ip" ]]; then
    printf '%s\n' "-p tcp -m conntrack --ctstate RELATED,ESTABLISHED --ctorigdst $original_dest_ip --ctorigdstport $port -m comment --comment OPENCLAW_INGRESS_BOUNDARY -j ACCEPT"
  else
    printf '%s\n' "-p tcp -m conntrack --ctstate RELATED,ESTABLISHED --ctorigdstport $port -m comment --comment OPENCLAW_INGRESS_BOUNDARY -j ACCEPT"
  fi
}

rule_spec_drop() {
  local port="$1"
  local original_dest_ip="${2:-}"
  if [[ -n "$original_dest_ip" ]]; then
    printf '%s\n' "-p tcp -m conntrack --ctorigdst $original_dest_ip --ctorigdstport $port -m comment --comment OPENCLAW_INGRESS_BOUNDARY -j DROP"
  else
    printf '%s\n' "-p tcp -m conntrack --ctorigdstport $port -m comment --comment OPENCLAW_INGRESS_BOUNDARY -j DROP"
  fi
}

ensure_docker_user_chain() {
  local cmd="$1"
  if ! "$cmd" -S DOCKER-USER >/dev/null 2>&1; then
    "$cmd" -N DOCKER-USER >/dev/null 2>&1 || true
    "$cmd" -C FORWARD -j DOCKER-USER >/dev/null 2>&1 || "$cmd" -I FORWARD 1 -j DOCKER-USER >/dev/null 2>&1 || true
  fi
  "$cmd" -C DOCKER-USER -j RETURN >/dev/null 2>&1 || "$cmd" -A DOCKER-USER -j RETURN >/dev/null 2>&1 || true
}

prune_managed_rules() {
  local cmd="$1"
  local -a lines=()
  local idx=0 line=''
  mapfile -t lines < <("$cmd" -S DOCKER-USER 2>/dev/null | awk '/OPENCLAW_INGRESS_BOUNDARY/ {print}')
  if (( ${#lines[@]} == 0 )); then
    return 0
  fi
  for (( idx=${#lines[@]}-1; idx>=0; idx-- )); do
    line="${lines[idx]}"
    [[ -n "$line" ]] || continue
    read -r -a parts <<< "$line"
    parts[0]='-D'
    "$cmd" "${parts[@]}" >/dev/null
  done
}

insert_rule() {
  local cmd="$1"
  local spec="$2"
  read -r -a parts <<< "$spec"
  "$cmd" -I DOCKER-USER 1 "${parts[@]}" >/dev/null
}

apply_family_rules() {
  local cmd="$1"
  local dest_ip="$2"
  shift 2
  local cidrs=("$@")
  local spec=''
  local -a specs=()
  local i=0 cidr='' port=''
  ensure_docker_user_chain "$cmd"
  prune_managed_rules "$cmd"
  for port in "${REQUIRED_PORTS[@]}"; do
    specs+=("$(rule_spec_established "$port" "$dest_ip")")
  done
  for cidr in "${cidrs[@]+"${cidrs[@]}"}"; do
    for port in "${REQUIRED_PORTS[@]}"; do
      specs+=("$(rule_spec_accept "$cidr" "$port" "$dest_ip")")
    done
  done
  for port in "${REQUIRED_PORTS[@]}"; do
    specs+=("$(rule_spec_drop "$port" "$dest_ip")")
  done
  for (( i=${#specs[@]}-1; i>=0; i-- )); do
    spec="${specs[i]}"
    insert_rule "$cmd" "$spec"
  done
}

note "boundary_mode=host_firewall listen_ip=$LISTEN_IP ports=$(IFS=,; echo "${REQUIRED_PORTS[*]}")"
note "listen_family=$LISTEN_FAMILY authorized_ipv4=${#IPV4_CIDRS[@]} authorized_ipv6=${#IPV6_CIDRS[@]}"
for cidr in "${IPV4_CIDRS[@]+"${IPV4_CIDRS[@]}"}"; do
  note "plan ipv4 allow $cidr -> $LISTEN_IP:80,443"
done
for cidr in "${IPV6_CIDRS[@]+"${IPV6_CIDRS[@]}"}"; do
  note "plan ipv6 allow $cidr -> [docker-published 80/443]"
done

if [[ "$DRY_RUN" == '1' ]]; then
  note 'dry-run=true 仅输出计划，不修改宿主机规则。'
  exit 0
fi

IPV4_DEST=''
IPV6_DEST=''
if [[ "$LISTEN_FAMILY" == 'ipv4' ]]; then
  IPV4_DEST="$LISTEN_IP"
else
  IPV6_DEST="$LISTEN_IP"
fi
if [[ "$NEEDS_IPV4" == '1' ]]; then
  apply_family_rules iptables "$IPV4_DEST" "${IPV4_CIDRS[@]+"${IPV4_CIDRS[@]}"}"
fi
if [[ "$NEEDS_IPV6" == '1' ]]; then
  apply_family_rules ip6tables "$IPV6_DEST" "${IPV6_CIDRS[@]+"${IPV6_CIDRS[@]}"}"
fi
note '已刷新 DOCKER-USER 中由 OPENCLAW_INGRESS_BOUNDARY 管理的规则。'

if [[ "$NO_VERIFY" == '0' ]]; then
  run_ingress_boundary_verify
fi

note 'ingress 边界物化完成。'
