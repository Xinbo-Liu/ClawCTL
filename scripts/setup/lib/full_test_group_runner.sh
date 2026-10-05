#!/usr/bin/env bash
# 用途：承接 one_click_test_full 的检查组执行映射，减少主脚本内联的 check id ↔ 命令装配与 HTTP/healthz 细节。

# shellcheck source=scripts/setup/lib/full_test_group_registry.sh
source "$ROOT_DIR/scripts/setup/lib/full_test_group_registry.sh"

FULL_TEST_DECLARED_CHECK_IDS_LOADED=0
FULL_TEST_DECLARED_CHECK_IDS=()

# 加载真源声明的检查 ID 列表，供后续过滤复用。
full_test_declared_check_ids_load() {
  [[ "${FULL_TEST_DECLARED_CHECK_IDS_LOADED:-0}" == '1' ]] && return 0
  FULL_TEST_DECLARED_CHECK_IDS=()
  local manifest_json=''
  if declare -F full_test_testing_manifest_json >/dev/null 2>&1 && manifest_json="$(full_test_testing_manifest_json 2>/dev/null)"; then
    mapfile -t FULL_TEST_DECLARED_CHECK_IDS < <(jq -r '.checks[]?.id // empty' <<<"$manifest_json")
  fi
  if ((${#FULL_TEST_DECLARED_CHECK_IDS[@]} == 0)) && ((${#FULL_TEST_SURFACE_CMD[@]} > 0)); then
    mapfile -t FULL_TEST_DECLARED_CHECK_IDS < <("${FULL_TEST_SURFACE_CMD[@]}" check-ids --format lines 2>/dev/null || true)
  fi
  FULL_TEST_DECLARED_CHECK_IDS_LOADED=1
}

# 判断给定检查 ID 是否在真源中声明。
full_test_check_declared() {
  local check_id="$1"
  local item=''
  full_test_declared_check_ids_load
  if ((${#FULL_TEST_DECLARED_CHECK_IDS[@]} == 0)); then
    return 0
  fi
  for item in "${FULL_TEST_DECLARED_CHECK_IDS[@]+"${FULL_TEST_DECLARED_CHECK_IDS[@]}"}"; do
    [[ "$item" == "$check_id" ]] && return 0
  done
  return 1
}

# 同时根据声明列表与运行过滤条件决定是否执行检查。
full_test_should_run_declared_check() {
  local check_id="$1"
  local group="$2"
  full_test_check_declared "$check_id" || return 1
  should_run_check "$check_id" "$group" || return 1
  full_test_mark_check_started "$check_id"
  return 0
}

# 统一执行脚本类检查，并按结果写入 pass/warn/fail。
full_test_run_script_check() {
  local check_id="$1"
  local group="$2"
  local failure_status="$3"
  local failure_action="$4"
  local use_env="$5"
  shift 5

  full_test_should_run_declared_check "$check_id" "$group" || return 0

  local out=""
  if [[ "$use_env" == "with_env" ]]; then
    if run_and_capture_with_env out "$@"; then
      record_pass "$check_id" "$out" "$group"
    else
      if [[ "$failure_status" == "warn" ]]; then
        record_warn "$check_id" "$out" "$group"
      else
        record_fail "$check_id" "$out" "$group"
      fi
      [[ -n "$failure_action" ]] && append_action "$failure_action"
    fi
    return 0
  fi

  if run_and_capture out "$@"; then
    record_pass "$check_id" "$out" "$group"
  else
    if [[ "$failure_status" == "warn" ]]; then
      record_warn "$check_id" "$out" "$group"
    else
      record_fail "$check_id" "$out" "$group"
    fi
    [[ -n "$failure_action" ]] && append_action "$failure_action"
  fi
}

# 校验关键入口脚本是否齐全。
full_test_run_entrypoint_presence_check() {
  local check_id="$1"
  local group="$2"
  local failure_action="$3"
  shift 3

  full_test_should_run_declared_check "$check_id" "$group" || return 0

  local missing=()
  local rel=""
  for rel in "$@"; do
    [[ -f "$ROOT_DIR/$rel" ]] || missing+=("$rel")
  done

  if ((${#missing[@]} == 0)); then
    record_pass "$check_id" '关键入口脚本齐全' "$group"
  else
    record_fail "$check_id" "$(printf '缺少入口脚本：%s' "${missing[*]}")" "$group"
    [[ -n "$failure_action" ]] && append_action "$failure_action"
  fi
}

# 判断 HTTP 状态码是否属于可接受的有效响应。
full_test_accept_http_code() {
  local code="$1"
  [[ "$code" =~ ^[1-5][0-9][0-9]$ && "$code" != "000" ]]
}

# 检查指定 runtime target 是否处于预期健康状态。
full_test_check_target_healthy() {
  local target="$1"
  local expected_status="${2:-running healthy}"
  local container_name=""
  container_name="$(runtime_container_name_for_target "$target")" || return 1
  local state
  state="$(runtime_container_status_line "$container_name" 2>/dev/null || true)"
  [[ "$state" == "$expected_status" ]]
}

# 拼接 Gateway HTTPS 检查 URL。
full_test_gateway_https_url() {
  local path="${1:-/}"
  printf 'https://%s%s' "$OPENCLAW_TLS_CN" "$path"
}

# curl --resolve 对 IPv6 地址要求使用方括号，IPv4 保持原样。
full_test_gateway_resolve_arg() {
  local bind_ip="${OPENCLAW_INGRESS_LISTEN_IP:-}"
  if [[ "$bind_ip" == *:* && "$bind_ip" != \[*\] ]]; then
    bind_ip="[$bind_ip]"
  fi
  printf '%s:443:%s' "$OPENCLAW_TLS_CN" "$bind_ip"
}

# 返回 Gateway HTTPS 校验所使用的 CA 文件路径。
full_test_gateway_ca_file() {
  printf '%s/deploy/nginx/certs/openclaw.crt' "$ROOT_DIR"
}

# 构造 Gateway HTTPS 验收统一 curl 参数。
full_test_gateway_build_curl_args() {
  FULL_TEST_GATEWAY_CURL_ARGS=(
    -sS
    -o /dev/null
    -w '%{http_code}'
    --connect-timeout 8
    --max-time 20
    --resolve "$(full_test_gateway_resolve_arg)"
  )
  case "${OPENCLAW_TLS_MODE:-}" in
    self_signed)
      local cert_file=''
      cert_file="$(full_test_gateway_ca_file)"
      [[ -f "$cert_file" ]] || {
        echo "[full_test] 缺少 self_signed trust anchor：$cert_file" >&2
        return 2
      }
      FULL_TEST_GATEWAY_CURL_ARGS+=( --cacert "$cert_file" )
      ;;
    provided_files)
      ;;
    *)
      echo "[full_test] OPENCLAW_TLS_MODE 只允许 self_signed 或 provided_files，当前=${OPENCLAW_TLS_MODE:-}" >&2
      return 2
      ;;
  esac
}

# 通过 curl 读取 Gateway 指定路径的 HTTP 状态码。
full_test_gateway_curl_code() {
  local path="${1:-/}"
  full_test_gateway_build_curl_args || return $?
  curl "${FULL_TEST_GATEWAY_CURL_ARGS[@]}" "$(full_test_gateway_https_url "$path")"
}

# 为 service 组失败场景追加建议的排障动作。
full_test_append_service_log_actions() {
  append_action "先执行：bash ./scripts/runtime/show_runtime_service_status.sh"
  append_action "查看 gateway 日志：bash ./scripts/runtime/show_runtime_container_logs.sh --target gateway"
  append_action "查看 private HTTPS ingress 日志：bash ./scripts/runtime/show_runtime_container_logs.sh --target ingress"
}

# 执行 Gateway 根路径 HTTPS 可达性检查。
full_test_run_gateway_https_root_check() {
  local group="$1"
  full_test_should_run_declared_check gateway_https_root "$group" || return 0

  if ! command -v curl >/dev/null 2>&1; then
    record_fail gateway_https_root '缺少 curl，无法检查 gateway HTTPS 入口' "$group"
    return 0
  fi

  local code rc gateway_url
  gateway_url="$(full_test_gateway_https_url '/')"
  set +e
  code="$(full_test_gateway_curl_code '/')"
  rc=$?
  set -e
  if [[ $rc -eq 0 ]] && full_test_accept_http_code "$code"; then
    record_pass gateway_https_root "${gateway_url} -> HTTP $code" "$group"
  else
    record_fail gateway_https_root "请求 ${gateway_url} 失败（bind=${OPENCLAW_INGRESS_LISTEN_IP}, code=${code:-N/A}, rc=$rc）" "$group"
    append_action '检查 gateway/nginx 服务状态、证书主机名与 ingress 绑定 IP'
    full_test_append_service_log_actions
  fi
}

# 执行双 healthz 入口的联动检查。
full_test_run_dual_healthz_check() {
  local check_id="$1"
  local group="$2"
  local health_url="$3"
  local ready_url="$4"
  local success_text="$5"
  local failure_prefix="$6"
  local failure_action="$7"

  full_test_should_run_declared_check "$check_id" "$group" || return 0

  if ! command -v curl >/dev/null 2>&1; then
    record_fail "$check_id" "缺少 curl，无法检查 ${check_id}" "$group"
    return 0
  fi

  local health_code ready_code rc rc_ready
  local -a curl_args=( -sS -o /dev/null -w '%{http_code}' --connect-timeout 8 --max-time 20 )
  if [[ "$health_url" == https://* ]] && [[ -n "${OPENCLAW_TLS_CN:-}" ]] && [[ -n "${OPENCLAW_INGRESS_LISTEN_IP:-}" ]]; then
    if ! full_test_gateway_build_curl_args; then
      record_fail "$check_id" "${failure_prefix}（无法构造 Gateway HTTPS curl 参数）" "$group"
      [[ -n "$failure_action" ]] && append_action "$failure_action"
      full_test_append_service_log_actions
      return 0
    fi
    curl_args=("${FULL_TEST_GATEWAY_CURL_ARGS[@]}")
  fi
  set +e
  health_code="$(curl "${curl_args[@]}" "$health_url")"
  rc=$?
  ready_code="$(curl "${curl_args[@]}" "$ready_url")"
  rc_ready=$?
  set -e

  if [[ $rc -eq 0 ]] && [[ $rc_ready -eq 0 ]] && [[ "$health_code" == "200" ]] && [[ "$ready_code" == "200" ]]; then
    record_pass "$check_id" "$success_text" "$group"
  else
    record_fail "$check_id" "${failure_prefix}（healthz=${health_code:-N/A}, rc=$rc; readyz=${ready_code:-N/A}, rc_ready=$rc_ready）" "$group"
    [[ -n "$failure_action" ]] && append_action "$failure_action"
    full_test_append_service_log_actions
  fi
}

# 执行单个 runtime target 的健康检查。
full_test_run_target_health_check() {
  local check_id="$1"
  local group="$2"
  local target="$3"
  local success_text="$4"
  local failure_text="$5"
  local failure_action="$6"

  full_test_should_run_declared_check "$check_id" "$group" || return 0

  if full_test_check_target_healthy "$target"; then
    record_pass "$check_id" "$success_text" "$group"
  else
    record_fail "$check_id" "$failure_text" "$group"
    [[ -n "$failure_action" ]] && append_action "$failure_action"
    if [[ "$target" == 'ingress' ]]; then
      full_test_append_service_log_actions
    fi
  fi
}

# 执行 service 分组下的脚本类检查。
full_test_run_service_script_checks() {
  full_test_group_registry_run_service_script_checks
  full_test_group_registry_append_official_cli_followups_if_failed
}

# 执行 service 分组的聚合检查。
full_test_run_service_group() {
  local group=service

  full_test_run_script_check compose_ps "$group" fail '先确认服务已成功启动，再执行 full 测试' with_env     bash "$ROOT_DIR/scripts/runtime/show_runtime_service_status.sh"

  full_test_run_gateway_https_root_check "$group"
  full_test_run_dual_healthz_check     gateway_healthz     "$group"     "$(full_test_gateway_https_url '/healthz')"     "$(full_test_gateway_https_url '/readyz')"     'healthz/readyz 均为 HTTP 200'     "请求 $(full_test_gateway_https_url '/healthz') 或 /readyz 失败（bind=${OPENCLAW_INGRESS_LISTEN_IP}）"     '检查官方 Gateway 自身健康状态，以及 ingress 对 /healthz /readyz 的透传配置'
  full_test_run_target_health_check     gateway_proxy_health     "$group"     ingress     'ingress=running healthy'     'ingress 未进入 running healthy；请检查统一状态/日志入口与 Nginx healthcheck'     '检查 ingress 的健康状态、Nginx 配置与 /healthz 透传'

  full_test_run_service_script_checks
}

# 按分组名称分发到对应的 full test 检查入口。
full_test_run_group_by_name() {
  local group_name="$1"
  case "$group_name" in
    service) full_test_run_service_group ;;
    *)
      if full_test_group_registry_has_group "$group_name"; then
        full_test_group_registry_run_declared_group "$group_name"
        return 0
      fi
      echo "[full_test_group_runner][FAIL] 未知检查组：$group_name" >&2
      return 2
      ;;
  esac
}
