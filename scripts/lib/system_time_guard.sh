#!/usr/bin/env bash
# 用途：统一校验并修复宿主机系统时间，避免证书、镜像拉取与日志时间因服务器时钟漂移失败。

SYSTEM_TIME_GUARD_DEFAULT_TIMEZONE="${SYSTEM_TIME_GUARD_DEFAULT_TIMEZONE:-Asia/Shanghai}"
SYSTEM_TIME_GUARD_DEFAULT_MAX_DRIFT_SECONDS="${OPENCLAW_SYSTEM_TIME_MAX_DRIFT_SECONDS:-300}"
SYSTEM_TIME_GUARD_DEFAULT_MIN_EPOCH="${OPENCLAW_SYSTEM_TIME_MIN_EPOCH:-1767225600}"
SYSTEM_TIME_GUARD_DEFAULT_MAX_EPOCH="${OPENCLAW_SYSTEM_TIME_MAX_EPOCH:-1924992000}"
SYSTEM_TIME_GUARD_DEFAULT_MAX_STEP_SECONDS="${OPENCLAW_SYSTEM_TIME_MAX_STEP_SECONDS:-94608000}"
SYSTEM_TIME_GUARD_DEFAULT_MIN_REFERENCE_COUNT="${OPENCLAW_SYSTEM_TIME_MIN_REFERENCE_COUNT:-2}"
SYSTEM_TIME_GUARD_DEFAULT_MAX_REFERENCE_SKEW_SECONDS="${OPENCLAW_SYSTEM_TIME_MAX_REFERENCE_SKEW_SECONDS:-120}"
SYSTEM_TIME_GUARD_DEFAULT_REFERENCE_URLS=(
  "https://www.baidu.com/"
  "https://www.qq.com/"
  "https://www.cloudflare.com/"
  "https://github.com/"
)
SYSTEM_TIME_GUARD_OFFLINE="${SYSTEM_TIME_GUARD_OFFLINE:-0}"
SYSTEM_TIME_GUARD_MAX_DRIFT_SECONDS="${SYSTEM_TIME_GUARD_MAX_DRIFT_SECONDS:-$SYSTEM_TIME_GUARD_DEFAULT_MAX_DRIFT_SECONDS}"
SYSTEM_TIME_GUARD_MIN_EPOCH="${SYSTEM_TIME_GUARD_MIN_EPOCH:-$SYSTEM_TIME_GUARD_DEFAULT_MIN_EPOCH}"
SYSTEM_TIME_GUARD_MAX_EPOCH="${SYSTEM_TIME_GUARD_MAX_EPOCH:-$SYSTEM_TIME_GUARD_DEFAULT_MAX_EPOCH}"
SYSTEM_TIME_GUARD_MAX_STEP_SECONDS="${SYSTEM_TIME_GUARD_MAX_STEP_SECONDS:-$SYSTEM_TIME_GUARD_DEFAULT_MAX_STEP_SECONDS}"
SYSTEM_TIME_GUARD_MIN_REFERENCE_COUNT="${SYSTEM_TIME_GUARD_MIN_REFERENCE_COUNT:-$SYSTEM_TIME_GUARD_DEFAULT_MIN_REFERENCE_COUNT}"
SYSTEM_TIME_GUARD_MAX_REFERENCE_SKEW_SECONDS="${SYSTEM_TIME_GUARD_MAX_REFERENCE_SKEW_SECONDS:-$SYSTEM_TIME_GUARD_DEFAULT_MAX_REFERENCE_SKEW_SECONDS}"
SYSTEM_TIME_GUARD_TIMEZONE="${SYSTEM_TIME_GUARD_TIMEZONE:-$SYSTEM_TIME_GUARD_DEFAULT_TIMEZONE}"

system_time_guard_note() {
  echo "[system_time][INFO] $1"
}

system_time_guard_warn() {
  echo "[system_time][WARN] $1"
}

system_time_guard_fail() {
  echo "[system_time][FAIL] $1" >&2
  return "${2:-24}"
}

system_time_guard_is_uint() {
  [[ "${1:-}" =~ ^[0-9]+$ ]]
}

system_time_guard_abs_delta() {
  local left="$1"
  local right="$2"
  if (( left >= right )); then
    printf '%s\n' "$(( left - right ))"
  else
    printf '%s\n' "$(( right - left ))"
  fi
}

system_time_guard_current_epoch() {
  date -u +%s 2>/dev/null
}

system_time_guard_format_epoch() {
  local epoch="$1"
  date -u -d "@$epoch" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || printf '%s\n' "$epoch"
}

system_time_guard_reference_urls() {
  if [[ -n "${OPENCLAW_SYSTEM_TIME_REFERENCE_URLS:-}" ]]; then
    # shellcheck disable=SC2086
    printf '%s\n' $OPENCLAW_SYSTEM_TIME_REFERENCE_URLS
    return 0
  fi
  printf '%s\n' "${SYSTEM_TIME_GUARD_DEFAULT_REFERENCE_URLS[@]}"
}

system_time_guard_reference_epoch() {
  command -v curl >/dev/null 2>&1 || {
    system_time_guard_fail '缺少 curl；无法获取外部 HTTP Date 时间基准。请先执行 sudo bash ./scripts/setup/prepare_docker_host.sh --install-base-tools。' 24
    return $?
  }
  local url=''
  local headers=''
  local date_header=''
  local epoch=''
  local refs=()
  local line=''
  while IFS= read -r url; do
    [[ -n "$url" ]] || continue
    headers="$(curl -k -sS -I -L --connect-timeout 5 --max-time 12 "$url" 2>/dev/null || true)"
    date_header="$(
      printf '%s\n' "$headers" |
        awk 'tolower($0) ~ /^date:[[:space:]]*/ { value=$0; sub(/\r$/, "", value); sub(/^[^:]+:[[:space:]]*/, "", value) } END { print value }'
    )"
    [[ -n "$date_header" ]] || continue
    epoch="$(LC_ALL=C TZ=UTC date -u -d "$date_header" +%s 2>/dev/null || true)"
    if system_time_guard_is_uint "$epoch"; then
      refs+=("${epoch}|${url}|${date_header}")
    fi
  done < <(system_time_guard_reference_urls)
  if (( ${#refs[@]} < SYSTEM_TIME_GUARD_MIN_REFERENCE_COUNT )); then
    system_time_guard_fail "可用 HTTP Date 参考源不足：需要 ${SYSTEM_TIME_GUARD_MIN_REFERENCE_COUNT} 个，实际 ${#refs[@]} 个；请修复网络出口、调整 OPENCLAW_SYSTEM_TIME_REFERENCE_URLS，或在受控内网基准下显式降低 --min-reference-count。" 24
    return $?
  fi
  local epochs=()
  local min_epoch=''
  local max_epoch=''
  for line in "${refs[@]}"; do
    epoch="${line%%|*}"
    epochs+=("$epoch")
    if [[ -z "$min_epoch" || "$epoch" -lt "$min_epoch" ]]; then
      min_epoch="$epoch"
    fi
    if [[ -z "$max_epoch" || "$epoch" -gt "$max_epoch" ]]; then
      max_epoch="$epoch"
    fi
  done
  local skew_seconds="$(( max_epoch - min_epoch ))"
  if (( skew_seconds > SYSTEM_TIME_GUARD_MAX_REFERENCE_SKEW_SECONDS )); then
    system_time_guard_fail "HTTP Date 参考源不一致：skew=${skew_seconds}s，超过阈值 ${SYSTEM_TIME_GUARD_MAX_REFERENCE_SKEW_SECONDS}s；拒绝使用单点或异常时间基准校验 / 校时。" 24
    return $?
  fi
  local selected_epoch=''
  selected_epoch="$(printf '%s\n' "${epochs[@]}" | sort -n | sed -n "$(( (${#epochs[@]} + 1) / 2 ))p")"
  for line in "${refs[@]}"; do
    epoch="${line%%|*}"
    if [[ "$epoch" == "$selected_epoch" ]]; then
      local rest="${line#*|}"
      local selected_url="${rest%%|*}"
      local selected_header="${rest#*|}"
      printf '%s\n%s\n%s\n%s\n%s\n' "$selected_epoch" "$selected_url" "$selected_header" "${#refs[@]}" "$skew_seconds"
      return 0
    fi
  done
  system_time_guard_fail '无法选择 HTTP Date 中位数参考源；请检查参考源返回值。' 24
}

system_time_guard_parse_check_args() {
  SYSTEM_TIME_GUARD_OFFLINE=0
  SYSTEM_TIME_GUARD_MAX_DRIFT_SECONDS="$SYSTEM_TIME_GUARD_DEFAULT_MAX_DRIFT_SECONDS"
  SYSTEM_TIME_GUARD_MIN_EPOCH="$SYSTEM_TIME_GUARD_DEFAULT_MIN_EPOCH"
  SYSTEM_TIME_GUARD_MAX_EPOCH="$SYSTEM_TIME_GUARD_DEFAULT_MAX_EPOCH"
  SYSTEM_TIME_GUARD_MAX_STEP_SECONDS="$SYSTEM_TIME_GUARD_DEFAULT_MAX_STEP_SECONDS"
  SYSTEM_TIME_GUARD_MIN_REFERENCE_COUNT="$SYSTEM_TIME_GUARD_DEFAULT_MIN_REFERENCE_COUNT"
  SYSTEM_TIME_GUARD_MAX_REFERENCE_SKEW_SECONDS="$SYSTEM_TIME_GUARD_DEFAULT_MAX_REFERENCE_SKEW_SECONDS"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --offline)
        SYSTEM_TIME_GUARD_OFFLINE=1
        shift
        ;;
      --max-drift-seconds)
        [[ $# -ge 2 ]] || { system_time_guard_fail '--max-drift-seconds 缺少秒数参数' 2; return $?; }
        SYSTEM_TIME_GUARD_MAX_DRIFT_SECONDS="$2"
        shift 2
        ;;
      --min-epoch)
        [[ $# -ge 2 ]] || { system_time_guard_fail '--min-epoch 缺少 epoch 秒数参数' 2; return $?; }
        SYSTEM_TIME_GUARD_MIN_EPOCH="$2"
        shift 2
        ;;
      --max-epoch)
        [[ $# -ge 2 ]] || { system_time_guard_fail '--max-epoch 缺少 epoch 秒数参数' 2; return $?; }
        SYSTEM_TIME_GUARD_MAX_EPOCH="$2"
        shift 2
        ;;
      --max-step-seconds)
        [[ $# -ge 2 ]] || { system_time_guard_fail '--max-step-seconds 缺少秒数参数' 2; return $?; }
        SYSTEM_TIME_GUARD_MAX_STEP_SECONDS="$2"
        shift 2
        ;;
      --min-reference-count)
        [[ $# -ge 2 ]] || { system_time_guard_fail '--min-reference-count 缺少数量参数' 2; return $?; }
        SYSTEM_TIME_GUARD_MIN_REFERENCE_COUNT="$2"
        shift 2
        ;;
      --max-reference-skew-seconds)
        [[ $# -ge 2 ]] || { system_time_guard_fail '--max-reference-skew-seconds 缺少秒数参数' 2; return $?; }
        SYSTEM_TIME_GUARD_MAX_REFERENCE_SKEW_SECONDS="$2"
        shift 2
        ;;
      *)
        system_time_guard_fail "未知参数：$1" 2
        return $?
        ;;
    esac
  done
  system_time_guard_is_uint "$SYSTEM_TIME_GUARD_MAX_DRIFT_SECONDS" || { system_time_guard_fail "--max-drift-seconds 必须是非负整数：$SYSTEM_TIME_GUARD_MAX_DRIFT_SECONDS" 2; return $?; }
  system_time_guard_is_uint "$SYSTEM_TIME_GUARD_MIN_EPOCH" || { system_time_guard_fail "--min-epoch 必须是非负整数：$SYSTEM_TIME_GUARD_MIN_EPOCH" 2; return $?; }
  system_time_guard_is_uint "$SYSTEM_TIME_GUARD_MAX_EPOCH" || { system_time_guard_fail "--max-epoch 必须是非负整数：$SYSTEM_TIME_GUARD_MAX_EPOCH" 2; return $?; }
  system_time_guard_is_uint "$SYSTEM_TIME_GUARD_MAX_STEP_SECONDS" || { system_time_guard_fail "--max-step-seconds 必须是非负整数：$SYSTEM_TIME_GUARD_MAX_STEP_SECONDS" 2; return $?; }
  system_time_guard_is_uint "$SYSTEM_TIME_GUARD_MIN_REFERENCE_COUNT" || { system_time_guard_fail "--min-reference-count 必须是非负整数：$SYSTEM_TIME_GUARD_MIN_REFERENCE_COUNT" 2; return $?; }
  system_time_guard_is_uint "$SYSTEM_TIME_GUARD_MAX_REFERENCE_SKEW_SECONDS" || { system_time_guard_fail "--max-reference-skew-seconds 必须是非负整数：$SYSTEM_TIME_GUARD_MAX_REFERENCE_SKEW_SECONDS" 2; return $?; }
  (( SYSTEM_TIME_GUARD_MIN_EPOCH <= SYSTEM_TIME_GUARD_MAX_EPOCH )) || { system_time_guard_fail "--min-epoch 不得晚于 --max-epoch：${SYSTEM_TIME_GUARD_MIN_EPOCH} > ${SYSTEM_TIME_GUARD_MAX_EPOCH}" 2; return $?; }
  (( SYSTEM_TIME_GUARD_MIN_REFERENCE_COUNT >= 1 )) || { system_time_guard_fail "--min-reference-count 必须大于 0：$SYSTEM_TIME_GUARD_MIN_REFERENCE_COUNT" 2; return $?; }
}

system_time_guard_validate_local_epoch() {
  local now_epoch="$1"
  local min_epoch="$2"
  local max_epoch="$3"
  local label="${4:-本机时间}"
  system_time_guard_is_uint "$now_epoch" || {
    system_time_guard_fail "无法读取${label} UTC epoch；请先修复 date 命令或系统时钟。" 24
    return $?
  }
  if (( now_epoch < min_epoch )); then
    system_time_guard_fail "${label}早于最低可信时间 $(system_time_guard_format_epoch "$min_epoch")；当前为 $(system_time_guard_format_epoch "$now_epoch")。请先执行 sudo bash ./scripts/setup/update_system_time.sh。" 24
    return $?
  fi
  if (( now_epoch > max_epoch )); then
    system_time_guard_fail "${label}晚于最高可信时间 $(system_time_guard_format_epoch "$max_epoch")；当前为 $(system_time_guard_format_epoch "$now_epoch")。请先执行 sudo bash ./scripts/setup/update_system_time.sh，或在受控离线场景显式传入 --max-epoch。" 24
    return $?
  fi
}

system_time_guard_print_timedatectl_status() {
  command -v timedatectl >/dev/null 2>&1 || return 0
  local timezone=''
  local ntp=''
  local synchronized=''
  timezone="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
  ntp="$(timedatectl show -p NTP --value 2>/dev/null || true)"
  synchronized="$(timedatectl show -p NTPSynchronized --value 2>/dev/null || true)"
  [[ -n "$timezone$ntp$synchronized" ]] || return 0
  system_time_guard_note "timedatectl: timezone=${timezone:-<unknown>} ntp=${ntp:-<unknown>} synchronized=${synchronized:-<unknown>}"
}

system_time_guard_check() {
  system_time_guard_parse_check_args "$@" || return $?
  local now_epoch=''
  now_epoch="$(system_time_guard_current_epoch || true)"
  system_time_guard_validate_local_epoch "$now_epoch" "$SYSTEM_TIME_GUARD_MIN_EPOCH" "$SYSTEM_TIME_GUARD_MAX_EPOCH" || return $?
  system_time_guard_print_timedatectl_status

  if [[ "$SYSTEM_TIME_GUARD_OFFLINE" == "1" ]]; then
    system_time_guard_note "离线模式：已完成本机可信时间窗口校验，跳过外部 HTTP Date 基准比对。当前时间：$(system_time_guard_format_epoch "$now_epoch")"
    return 0
  fi

  local reference=''
  local reference_epoch=''
  local reference_source=''
  local reference_header=''
  local reference_count=''
  local reference_skew=''
  local drift_seconds=''
  reference="$(system_time_guard_reference_epoch)" || return $?
  reference_epoch="$(printf '%s\n' "$reference" | sed -n '1p')"
  reference_source="$(printf '%s\n' "$reference" | sed -n '2p')"
  reference_header="$(printf '%s\n' "$reference" | sed -n '3p')"
  reference_count="$(printf '%s\n' "$reference" | sed -n '4p')"
  reference_skew="$(printf '%s\n' "$reference" | sed -n '5p')"
  system_time_guard_validate_local_epoch "$reference_epoch" "$SYSTEM_TIME_GUARD_MIN_EPOCH" "$SYSTEM_TIME_GUARD_MAX_EPOCH" 'HTTP Date 参考时间' || return $?
  drift_seconds="$(system_time_guard_abs_delta "$now_epoch" "$reference_epoch")"
  if (( drift_seconds > SYSTEM_TIME_GUARD_MAX_DRIFT_SECONDS )); then
    system_time_guard_fail "本机时间与外部基准相差 ${drift_seconds}s，超过阈值 ${SYSTEM_TIME_GUARD_MAX_DRIFT_SECONDS}s；local=$(system_time_guard_format_epoch "$now_epoch") reference=$(system_time_guard_format_epoch "$reference_epoch") source=$reference_source date='$reference_header'。请先执行 sudo bash ./scripts/setup/update_system_time.sh。" 24
    return $?
  fi
  system_time_guard_note "系统时间校验通过：drift=${drift_seconds}s max=${SYSTEM_TIME_GUARD_MAX_DRIFT_SECONDS}s references=${reference_count:-unknown} reference_skew=${reference_skew:-unknown}s source=$reference_source"
}

system_time_guard_start_ntp() {
  if command -v timedatectl >/dev/null 2>&1; then
    timedatectl set-timezone "$SYSTEM_TIME_GUARD_TIMEZONE" || system_time_guard_warn "timedatectl set-timezone $SYSTEM_TIME_GUARD_TIMEZONE 未生效。"
    timedatectl set-ntp true >/dev/null 2>&1 || system_time_guard_warn 'timedatectl set-ntp true 未生效；继续尝试 chronyd。'
  else
    system_time_guard_warn '未检测到 timedatectl；跳过时区与 systemd-timesyncd NTP 设置。'
  fi

  if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files chronyd.service >/dev/null 2>&1; then
    systemctl enable --now chronyd >/dev/null 2>&1 || system_time_guard_warn 'chronyd 启动失败；继续尝试直接校正系统时间。'
  elif command -v service >/dev/null 2>&1 && { service chronyd status >/dev/null 2>&1 || [[ -x /etc/init.d/chronyd ]]; }; then
    service chronyd start >/dev/null 2>&1 || system_time_guard_warn 'chronyd service 启动失败；继续尝试直接校正系统时间。'
    command -v chkconfig >/dev/null 2>&1 && chkconfig chronyd on >/dev/null 2>&1 || true
  else
    system_time_guard_warn '未检测到 chronyd；仅执行外部时间基准校验与必要的直接校时。'
  fi

  if command -v chronyc >/dev/null 2>&1; then
    chronyc -a 'burst 4/4' >/dev/null 2>&1 || true
    chronyc -a makestep >/dev/null 2>&1 || system_time_guard_warn 'chronyc makestep 未完成；继续按 HTTP Date 基准校验。'
  fi
}

system_time_guard_step_to_reference_if_needed() {
  [[ "$SYSTEM_TIME_GUARD_OFFLINE" == "0" ]] || return 0
  command -v curl >/dev/null 2>&1 || {
    system_time_guard_fail '缺少 curl；无法获取外部 HTTP Date 时间基准并校时。请先执行 sudo bash ./scripts/setup/prepare_docker_host.sh --install-base-tools。' 24
    return $?
  }
  local reference=''
  local reference_epoch=''
  local reference_source=''
  local now_epoch=''
  local drift_seconds=''
  reference="$(system_time_guard_reference_epoch)" || return $?
  reference_epoch="$(printf '%s\n' "$reference" | sed -n '1p')"
  reference_source="$(printf '%s\n' "$reference" | sed -n '2p')"
  system_time_guard_validate_local_epoch "$reference_epoch" "$SYSTEM_TIME_GUARD_MIN_EPOCH" "$SYSTEM_TIME_GUARD_MAX_EPOCH" 'HTTP Date 参考时间' || return $?
  now_epoch="$(system_time_guard_current_epoch || true)"
  system_time_guard_is_uint "$now_epoch" || now_epoch=0
  drift_seconds="$(system_time_guard_abs_delta "$now_epoch" "$reference_epoch")"
  if (( drift_seconds <= SYSTEM_TIME_GUARD_MAX_DRIFT_SECONDS )); then
    system_time_guard_note "NTP/chrony 校时后 drift=${drift_seconds}s，未超过阈值。"
    return 0
  fi
  if (( drift_seconds > SYSTEM_TIME_GUARD_MAX_STEP_SECONDS )); then
    system_time_guard_fail "拒绝直接校正超过最大跳变阈值的系统时间：drift=${drift_seconds}s max_step=${SYSTEM_TIME_GUARD_MAX_STEP_SECONDS}s；local=$(system_time_guard_format_epoch "$now_epoch") reference=$(system_time_guard_format_epoch "$reference_epoch")。请先确认可信时间源，或在受控恢复场景显式传入 --max-step-seconds。" 24
    return $?
  fi
  system_time_guard_warn "NTP/chrony 后 drift=${drift_seconds}s，超过阈值；按 HTTP Date 基准直接校正到 $(system_time_guard_format_epoch "$reference_epoch")，source=$reference_source。"
  date -u -s "@$reference_epoch" >/dev/null || {
    system_time_guard_fail 'date -s 校正系统时间失败。' 24
    return $?
  }
  if command -v hwclock >/dev/null 2>&1; then
    hwclock --systohc --utc >/dev/null 2>&1 || system_time_guard_warn 'hwclock --systohc --utc 未完成；系统时间已校正，但硬件时钟可能未同步。'
  fi
}

system_time_guard_update() {
  local raw_args=("$@")
  local filtered_args=()
  local filtered_count=0
  local index=0
  SYSTEM_TIME_GUARD_TIMEZONE="$SYSTEM_TIME_GUARD_DEFAULT_TIMEZONE"
  while [[ "$index" -lt "${#raw_args[@]}" ]]; do
    case "${raw_args[$index]}" in
      --timezone)
        index=$((index + 1))
        [[ "$index" -lt "${#raw_args[@]}" ]] || { system_time_guard_fail '--timezone 缺少时区参数' 2; return $?; }
        SYSTEM_TIME_GUARD_TIMEZONE="${raw_args[$index]}"
        ;;
      *)
        filtered_args+=("${raw_args[$index]}")
        filtered_count=$((filtered_count + 1))
        ;;
    esac
    index=$((index + 1))
  done
  if (( filtered_count > 0 )); then
    system_time_guard_parse_check_args "${filtered_args[@]}" || return $?
  else
    system_time_guard_parse_check_args || return $?
  fi
  [[ "$(id -u)" == "0" ]] || {
    system_time_guard_fail '更新系统时间需要 root 权限；请使用 sudo bash ./scripts/setup/update_system_time.sh。' 30
    return $?
  }
  system_time_guard_start_ntp
  system_time_guard_step_to_reference_if_needed || return $?
  if (( filtered_count > 0 )); then
    system_time_guard_check "${filtered_args[@]}" || return $?
  else
    system_time_guard_check || return $?
  fi
}
