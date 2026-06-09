#!/usr/bin/env bash
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
source "$ROOT_DIR/scripts/runtime/runtime_compose_lib.sh"
source "$ROOT_DIR/scripts/runtime/runtime_container_lib.sh"
source "$ROOT_DIR/scripts/setup/lib/runtime_permissions.sh"
source "$ROOT_DIR/scripts/setup/lib/deploy_env_shell.sh"
source "$ROOT_DIR/scripts/lib/repo_contracts.sh"
source "$ROOT_DIR/scripts/lib/repo_python_env.sh"
PYTHON_RUNNER="$ROOT_DIR/scripts/runtime/run_python_container.sh"
repo_contract_assign_path POLICY_PATH governance.ingress_boundary_evidence

ENV_FILE="$ROOT_DIR/deploy/.env"
COMPOSE_FILE=""
COMPOSE_FILE_EXPLICIT=0
OUT_JSON="$(runtime_permissions_host_control_plane_file "$ROOT_DIR" setup/ingress_boundary_evidence.json)"
WRITE_OUTPUT=1
REQUIRE_NGINX_POLICY=0
REPO_PYTHON_ENV_ARGS=()

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/doctor/check_ingress_boundary_evidence.sh [选项]

说明：
  - 检查 private ingress 是否为唯一宿主机端口暴露面；
  - 检查 private ingress 是否只绑定 OPENCLAW_INGRESS_LISTEN_IP:80/443；
  - 检查除 ingress 外的 runtime targets 是否没有宿主机端口映射；
  - `host_firewall` 模式会按 OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS 语义核对宿主机防火墙规则；
  - `external_acl` 模式会核对结构化 JSON 证据文件中的 source_cidrs / allowed_ports / 目标信息；
  - 可选要求渲染后的 nginx.gateway.conf 与当前来源 allowlist 完全一致；
  - 若 runtime 容器尚未启动，本脚本会跳过运行态端口绑定实证，只检查 compose 暴露合同与 boundary 语义；
  - 默认把结果写到 <current-host-state-root>/control_plane/setup/ingress_boundary_evidence.json。

选项：
  --env-file <path>        覆盖默认 env 文件（默认：deploy/.env）
  --compose-file <path>    覆盖默认 compose 文件（默认：当前运行画像 effective compose；缺失时回退 deploy/docker-compose.yml）
  --out-json <path>        覆盖默认输出 JSON 路径
  --require-nginx-policy   要求 nginx.gateway.conf 已按当前来源 CIDR allowlist 渲染
  --no-write               只检查，不写出 JSON
  -h, --help               显示帮助
USAGE
}

fail() {
  echo "[check_ingress_boundary_evidence][FAIL] $*" >&2
  exit 2
}

note() {
  echo "[check_ingress_boundary_evidence] $*"
}

align_written_output_owner() {
  if [[ "$WRITE_OUTPUT" != "1" || "$(id -u)" != "0" ]]; then
    return 0
  fi
  local out_abs=""
  local out_dir=""
  local uid=""
  local gid=""
  local state_root=""
  out_abs="$(runtime_permissions_abs_path "$ROOT_DIR" "$OUT_JSON")"
  out_dir="$(dirname "$out_abs")"
  state_root="$(runtime_permissions_host_state_root "$ROOT_DIR")"
  case "$out_abs" in
    "$state_root"|"$state_root"/*)
      uid="$(runtime_permissions_deploy_env_value "$ROOT_DIR" OPENCLAW_RUNTIME_UID)"
      gid="$(runtime_permissions_deploy_env_value "$ROOT_DIR" OPENCLAW_RUNTIME_GID)"
      [[ "$uid" =~ ^[0-9]+$ && "$gid" =~ ^[0-9]+$ ]] || return 0
      chown "$uid:$gid" "$out_dir" "$out_abs" 2>/dev/null || true
      chmod 700 "$out_dir" 2>/dev/null || true
      chmod 600 "$out_abs" 2>/dev/null || true
      ;;
  esac
}

load_repo_python_env_args() {
  REPO_PYTHON_ENV_ARGS=()
  while IFS= read -r -d '' item; do
    REPO_PYTHON_ENV_ARGS+=("$item")
  done < <(openclaw_repo_python_env_args "$ROOT_DIR")
}

run_ingress_backend() {
  bash "$PYTHON_RUNNER" --workdir "$ROOT_DIR" "${REPO_PYTHON_ENV_ARGS[@]+"${REPO_PYTHON_ENV_ARGS[@]}"}" "$@"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      [[ $# -ge 2 ]] || fail "--env-file 缺少路径参数"
      ENV_FILE="$2"
      shift 2
      ;;
    --compose-file)
      [[ $# -ge 2 ]] || fail "--compose-file 缺少路径参数"
      COMPOSE_FILE="$2"
      COMPOSE_FILE_EXPLICIT=1
      shift 2
      ;;
    --out-json)
      [[ $# -ge 2 ]] || fail "--out-json 缺少路径参数"
      OUT_JSON="$2"
      shift 2
      ;;
    --require-nginx-policy)
      REQUIRE_NGINX_POLICY=1
      shift
      ;;
    --no-write)
      WRITE_OUTPUT=0
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
if [[ "$COMPOSE_FILE_EXPLICIT" != "1" ]]; then
  COMPOSE_FILE="$(runtime_compose_default_file "$ROOT_DIR" "$ENV_FILE")"
fi
[[ -f "$COMPOSE_FILE" ]] || fail "compose 文件不存在：$COMPOSE_FILE"
[[ -f "$POLICY_PATH" ]] || fail "缺少 ingress 边界证据真源：$POLICY_PATH"
command -v jq >/dev/null 2>&1 || fail "缺少 jq"
runtime_compose_require_cli >/dev/null
runtime_container_require_docker >/dev/null
load_repo_python_env_args

deploy_env_shell_load_keys "$ENV_FILE" \
  OPENCLAW_INGRESS_BOUNDARY_MODE \
  OPENCLAW_INGRESS_BOUNDARY_EVIDENCE_PATH \
  OPENCLAW_INGRESS_LISTEN_IP \
  OPENCLAW_TLS_CN \
  OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS

BOUNDARY_MODE="${OPENCLAW_INGRESS_BOUNDARY_MODE:-host_firewall}"
BOUNDARY_EVIDENCE_PATH_RAW="${OPENCLAW_INGRESS_BOUNDARY_EVIDENCE_PATH:-}"
if [[ "$BOUNDARY_MODE" != "host_firewall" && "$BOUNDARY_MODE" != "external_acl" ]]; then
  fail "OPENCLAW_INGRESS_BOUNDARY_MODE 只允许 host_firewall 或 external_acl；当前=$BOUNDARY_MODE"
fi
if [[ -z "${OPENCLAW_INGRESS_LISTEN_IP:-}" ]]; then
  fail "缺少 OPENCLAW_INGRESS_LISTEN_IP"
fi
if [[ -z "${OPENCLAW_TLS_CN:-}" ]]; then
  fail "缺少 OPENCLAW_TLS_CN"
fi
if [[ -z "${OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS:-}" ]]; then
  fail "缺少 OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS"
fi

rendered_compose="$(mktemp)"
compose_env_file=""
compose_tmp_dir=""
compose_json="$(mktemp)"
runtime_json="$(mktemp)"
allowed_sources_json="$(mktemp)"
boundary_json="$(mktemp)"
summary_json="$(mktemp)"
nginx_policy_json="$(mktemp)"
nginx_policy_err="$(mktemp)"
command_snapshot_json="$(mktemp)"
cleanup() {
  rm -f "$rendered_compose" "$compose_json" "$runtime_json" "$allowed_sources_json" "$boundary_json" "$summary_json" "$nginx_policy_json" "$nginx_policy_err" "$command_snapshot_json"
  runtime_compose_cleanup_transient_env_files "$ROOT_DIR" "$compose_tmp_dir"
}
trap cleanup EXIT

capture_command_snapshot_entry() {
  local key="$1"
  shift
  local stdout_path stderr_path update_path rc
  stdout_path="$(mktemp)"
  stderr_path="$(mktemp)"
  update_path="$(mktemp)"
  set +e
  "$@" > "$stdout_path" 2> "$stderr_path"
  rc=$?
  set -e
  jq \
    --arg key "$key" \
    --argjson rc "$rc" \
    --rawfile stdout "$stdout_path" \
    --rawfile stderr "$stderr_path" \
    '. + {($key): {"rc": $rc, "stdout": $stdout, "stderr": $stderr}}' \
    "$command_snapshot_json" > "$update_path"
  mv "$update_path" "$command_snapshot_json"
  rm -f "$stdout_path" "$stderr_path"
}

capture_host_command_snapshot() {
  printf '{}\n' > "$command_snapshot_json"
  capture_command_snapshot_entry 'iptables-save' iptables-save
  capture_command_snapshot_entry 'ip6tables-save' ip6tables-save
  capture_command_snapshot_entry 'nft list ruleset' nft list ruleset
  capture_command_snapshot_entry 'firewall-cmd --state' firewall-cmd --state
  capture_command_snapshot_entry 'firewall-cmd --get-default-zone' firewall-cmd --get-default-zone
  capture_command_snapshot_entry 'firewall-cmd --get-active-zones' firewall-cmd --get-active-zones
  capture_command_snapshot_entry 'firewall-cmd --get-zones' firewall-cmd --get-zones
  capture_command_snapshot_entry 'firewall-cmd --list-all-zones' firewall-cmd --list-all-zones

  local zones_raw zone
  zones_raw="$(jq -r '."firewall-cmd --get-zones".stdout // ""' "$command_snapshot_json")"
  for zone in $zones_raw; do
    capture_command_snapshot_entry "firewall-cmd --zone $zone --list-all" firewall-cmd --zone "$zone" --list-all
    capture_command_snapshot_entry "firewall-cmd --zone $zone --list-rich-rules" firewall-cmd --zone "$zone" --list-rich-rules
  done
}

run_ingress_backend \
  -- \
  -m openclaw.doctor.platform.ingress_boundary_evidence_backend \
  normalize-source-cidrs \
  "$OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS" > "$allowed_sources_json"

IFS=$'\t' read -r compose_env_file compose_tmp_dir < <(
  runtime_compose_prepare_transient_env_files "$ROOT_DIR" "$ENV_FILE" ingress-boundary-compose
)
runtime_compose_command "$compose_env_file" "$COMPOSE_FILE" config --format json > "$rendered_compose"

run_ingress_backend \
  --mount "$rendered_compose" \
  -- \
  -m openclaw.doctor.platform.ingress_boundary_evidence_backend \
  compose-contract \
  "$rendered_compose" \
  "$OPENCLAW_INGRESS_LISTEN_IP" \
  "$POLICY_PATH" > "$compose_json"

mapfile -t container_targets < <(runtime_known_targets)
(( ${#container_targets[@]} > 0 )) || fail "runtime service registry 未返回任何 target"
inspect_exists() {
  local container_name="$1"
  docker inspect "$container_name" >/dev/null 2>&1
}

{
  echo '{'
  first=1
  for target in "${container_targets[@]}"; do
    container_name="$(runtime_container_name_for_target "$target")" || fail "无法解析 target：$target"
    if inspect_exists "$container_name"; then
      inspect_payload="$(docker inspect "$container_name")"
      if [[ $first -eq 0 ]]; then
        echo ','
      fi
      first=0
      printf '  "%s": ' "$target"
      printf '%s' "$inspect_payload"
    fi
  done
  echo ''
  echo '}'
} > "$runtime_json"

RESOLVED_BOUNDARY_PATH=""
BOUNDARY_MOUNT_ARGS=(--mount "$allowed_sources_json")
if [[ -n "$BOUNDARY_EVIDENCE_PATH_RAW" ]]; then
  RESOLVED_BOUNDARY_PATH="$(runtime_permissions_abs_path "$ROOT_DIR" "$BOUNDARY_EVIDENCE_PATH_RAW")"
  if [[ -e "$RESOLVED_BOUNDARY_PATH" ]]; then
    BOUNDARY_MOUNT_ARGS+=(--mount "$RESOLVED_BOUNDARY_PATH")
  else
    boundary_parent="$(dirname "$RESOLVED_BOUNDARY_PATH")"
    [[ -d "$boundary_parent" ]] && BOUNDARY_MOUNT_ARGS+=(--mount "$boundary_parent")
  fi
fi

capture_host_command_snapshot
run_ingress_backend \
  "${BOUNDARY_MOUNT_ARGS[@]}" \
  --mount "$command_snapshot_json" \
  -- \
  -m openclaw.doctor.platform.ingress_boundary_evidence_backend \
  boundary-evidence \
  --command-snapshot "$command_snapshot_json" \
  "$BOUNDARY_MODE" \
  "$OPENCLAW_INGRESS_LISTEN_IP" \
  "$OPENCLAW_TLS_CN" \
  "$allowed_sources_json" \
  "$POLICY_PATH" \
  "$RESOLVED_BOUNDARY_PATH" > "$boundary_json"

run_ingress_backend \
  --mount "$compose_json" \
  --mount "$runtime_json" \
  --mount "$boundary_json" \
  -- \
  -m openclaw.doctor.platform.ingress_boundary_evidence_backend \
  summary \
  "$compose_json" \
  "$runtime_json" \
  "$boundary_json" \
  "$OPENCLAW_INGRESS_LISTEN_IP" \
  "$POLICY_PATH" > "$summary_json"

if [[ "$REQUIRE_NGINX_POLICY" == "1" ]]; then
  set +e
  run_ingress_backend \
    --mount "$ENV_FILE" \
    -- \
    -m openclaw.setup.network.gateway_ingress \
    check-nginx \
    --env-file "$ENV_FILE" > "$nginx_policy_json" 2> "$nginx_policy_err"
  nginx_policy_rc=$?
  set -e
  summary_with_nginx="$(mktemp)"
  if [[ "$nginx_policy_rc" -eq 0 ]]; then
    jq --slurpfile nginx "$nginx_policy_json" \
      '.nginx_policy = (($nginx[0] // {}) + {"required": true, "checked": true, "ok": true, "issues": []})' \
      "$summary_json" > "$summary_with_nginx"
  else
    nginx_issue="Nginx 来源 allowlist 未按当前 deploy env 渲染：$(tr '\n' ';' < "$nginx_policy_err")"
    jq --arg issue "$nginx_issue" \
      '.nginx_policy = {"required": true, "checked": true, "ok": false, "issues": [$issue]} | .accepted = false | .issues += [$issue]' \
      "$summary_json" > "$summary_with_nginx"
  fi
  mv "$summary_with_nginx" "$summary_json"
else
  summary_with_nginx="$(mktemp)"
  jq '.nginx_policy = {"required": false, "checked": false, "ok": null, "issues": []}' "$summary_json" > "$summary_with_nginx"
  mv "$summary_with_nginx" "$summary_json"
fi

accepted="$(jq -r '.accepted' "$summary_json")"
if [[ "$WRITE_OUTPUT" == "1" ]]; then
  mkdir -p "$(dirname "$OUT_JSON")"
  cp "$summary_json" "$OUT_JSON"
  chmod 600 "$OUT_JSON" || true
  align_written_output_owner
fi

compose_ok="$(jq -r '.compose_contract.compose_contract_ok' "$summary_json")"
runtime_ok="$(jq -r '.runtime_contract.runtime_contract_ok' "$summary_json")"
boundary_method="$(jq -r '.boundary_evidence.method // "unknown"' "$summary_json")"
boundary_ok="$(jq -r '.boundary_evidence.accepted' "$summary_json")"
nginx_policy_ok="$(jq -r '.nginx_policy.ok // "not_required"' "$summary_json")"
if [[ "$accepted" == "true" ]]; then
  note "accepted=true compose=$compose_ok runtime=$runtime_ok boundary_method=$boundary_method boundary_ok=$boundary_ok nginx_policy=$nginx_policy_ok"
  if [[ "$WRITE_OUTPUT" == "1" ]]; then
    note "已写出：$OUT_JSON"
  fi
  exit 0
fi

jq -r '.issues[]? | "[check_ingress_boundary_evidence][DETAIL] " + .' "$summary_json" >&2 || true
fail "accepted=false compose=$compose_ok runtime=$runtime_ok boundary_method=$boundary_method boundary_ok=$boundary_ok nginx_policy=$nginx_policy_ok"
