#!/usr/bin/env bash
# 用途：为多条主链提供统一的阶段执行日志收口，避免 deploy/release 各自维护 pipe+tee 与失败变量更新逻辑。
set -euo pipefail

# 按变量名回写字符串值，便于外层以引用方式传递状态。
flow_set_var() {
  local name="$1"
  local value="$2"
  printf -v "$name" '%s' "$value"
}

# 同时写入终端与日志文件。
flow_log_line() {
  local log_path="$1"
  shift
  printf '%s\n' "$*" | flow_redact_sensitive_stream | tee -a "$log_path"
}

flow_redact_sensitive_stream() {
  if command -v perl >/dev/null 2>&1; then
    perl -0pe '
      s/(-----BEGIN [A-Z ]*PRIVATE KEY-----).*?(-----END [A-Z ]*PRIVATE KEY-----)/$1\n<redacted>\n$2/gs;
      s/((?:[A-Za-z_][A-Za-z0-9_]*USERS_JSON)\s*[:=]\s*)[^\r\n]*/$1<redacted>/gi;
      s/((?:["\x27]?(?:user_id|union_id|channel_id)["\x27]?\s*:\s*["\x27]))[^"\x27\r\n]+/$1<redacted>/gi;
      s/((?:(?:USER_ID|UNION_ID|CHANNEL_ID|CHAT_ID)\s*[:=]\s*["\x27]?))[A-Za-z0-9_.~:-]+/$1<redacted>/gi;
      s/((?:["\x27]?[A-Za-z_][A-Za-z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|PASS|API[_-]?KEY|ACCESS[_-]?KEY|WEBHOOK|HOOK|SIGN(?:ATURE)?|CREDENTIAL|AUTH|COOKIE|SESSION|PRIVATE|KEY|URL)[A-Za-z0-9_-]*["\x27]?\s*:\s*["\x27]))[^"\x27\r\n]+/$1<redacted>/gi;
      s/((?:[-[:space:]]*(?:export[[:space:]]+)?[A-Za-z_][A-Za-z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|PASS|API[_-]?KEY|ACCESS[_-]?KEY|WEBHOOK|HOOK|SIGN(?:ATURE)?|CREDENTIAL|AUTH|COOKIE|SESSION|PRIVATE|KEY|URL)[A-Za-z0-9_-]*[[:space:]]*[:=][[:space:]]*["\x27]?))[^[:space:]"\x27\r\n]+/$1<redacted>/gi;
      s/((?:(?:Authorization|Proxy-Authorization)[[:space:]]*:[[:space:]]*(?:Bearer|Basic)[[:space:]]+))[A-Za-z0-9._~+\/=-]+/$1<redacted>/gi;
      s/((?:--(?:token|api-key|access-key|secret|password|webhook-url|url|sign(?:ature)?-key|authorization|auth|cookie|session)(?:=|[[:space:]]+)["\x27]?))[^[:space:]"\x27\r\n]+/$1<redacted>/gi;
      s#https://[^[:space:]"\x27]+/(?:hook|hooks|webhook)/[A-Za-z0-9_-]+#https://<webhook-host>/<redacted>#gi;
    '
    return $?
  fi
  sed -E \
    -e 's/(["'\'']?[A-Za-z_][A-Za-z0-9_]*(TOKEN|SECRET|PASSWORD|PASS|API_KEY|ACCESS_KEY|WEBHOOK|URL)[A-Za-z0-9_]*["'\'']?[[:space:]]*:[[:space:]]*")[^"]+/\1<redacted>/g' \
    -e 's/([-[:space:]]*[A-Za-z_][A-Za-z0-9_]*(TOKEN|SECRET|PASSWORD|PASS|API_KEY|ACCESS_KEY|WEBHOOK|URL)[A-Za-z0-9_]*[=:][[:space:]]*")[^"]+/\1<redacted>/g' \
    -e 's/([-[:space:]]*[A-Za-z_][A-Za-z0-9_]*(TOKEN|SECRET|PASSWORD|PASS|API_KEY|ACCESS_KEY|WEBHOOK|URL)[A-Za-z0-9_]*[=:][[:space:]]*)[^[:space:]"]+/\1<redacted>/g' \
    -e 's/(OPENCLAW_(GATEWAY|INTERNAL_API)_TOKEN[=:][[:space:]]*")[^"]+/\1<redacted>/g' \
    -e 's/(OPENCLAW_(GATEWAY|INTERNAL_API)_TOKEN[=:][[:space:]]*)[^[:space:]"]+/\1<redacted>/g' \
    -e 's/([-[:space:]]*[A-Za-z_][A-Za-z0-9_]*USERS_JSON[=:][[:space:]]*).*/\1<redacted>/g' \
    -e 's/(["'\'']?(user_id|union_id|channel_id)["'\'']?[[:space:]]*:[[:space:]]*["'\''])[^"'\'']+/\1<redacted>/gI' \
    -e 's/((USER_ID|UNION_ID|CHANNEL_ID)[=:][[:space:]]*["'\'']?)[A-Za-z0-9_.~-]+/\1<redacted>/gI' \
    -e 's/(--token ")[^"]+/\1<redacted>/g' \
    -e 's/(--token )[A-Za-z0-9_.~-]+/\1<redacted>/g' \
    -e 's/("token"[[:space:]]*:[[:space:]]*")[^"]+/\1<redacted>/g'
}

# 统一执行单个阶段，并维护执行中阶段、失败阶段与退出码。
flow_run_logged_step() {
  local log_path="$1"
  local current_stage_var="$2"
  local failed_stage_var="$3"
  local failed_code_var="$4"
  local stage_name="$5"
  shift 5

  flow_set_var "$current_stage_var" "$stage_name"
  flow_log_line "$log_path" "[STEP] $stage_name"

  set +e
  "$@" 2>&1 | flow_redact_sensitive_stream | tee -a "$log_path"
  local exit_code=${PIPESTATUS[0]}
  set -e

  if [[ "$exit_code" -eq 0 ]]; then
    flow_log_line "$log_path" "[OK] $stage_name"
    return 0
  fi

  flow_set_var "$failed_stage_var" "$stage_name"
  flow_set_var "$failed_code_var" "$exit_code"
  flow_log_line "$log_path" "[FAIL] $stage_name (exit=$exit_code)"
  return "$exit_code"
}
