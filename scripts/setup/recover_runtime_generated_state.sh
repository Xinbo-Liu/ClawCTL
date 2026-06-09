#!/usr/bin/env bash
# 用途：从已部署容器事实恢复缺失的运行态配置与派生物。
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/repo_root.sh
source "${SCRIPT_DIR}/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "${SCRIPT_DIR}")"

# shellcheck source=scripts/lib/repo_contracts.sh
source "${SCRIPT_DIR}/../lib/repo_contracts.sh"
# shellcheck source=scripts/setup/lib/runtime_permissions.sh
source "${SCRIPT_DIR}/lib/runtime_permissions.sh"
# shellcheck source=scripts/setup/lib/tls_hostname_contract.sh
source "${SCRIPT_DIR}/lib/tls_hostname_contract.sh"
# shellcheck source=scripts/setup/lib/host_install_defaults.sh
source "${SCRIPT_DIR}/lib/host_install_defaults.sh"
# shellcheck source=scripts/lib/control_plane_config_paths.sh
source "${SCRIPT_DIR}/../lib/control_plane_config_paths.sh"

ENV_FILE="${ROOT_DIR}/deploy/.env"
SITE_ENV_FILE="${ROOT_DIR}/deploy/site.env"
RESTART_CONTAINERS=0
FORCE_RECOVER_ENV=0

usage() {
  cat <<'USAGE'
用法：bash ./scripts/setup/recover_runtime_generated_state.sh [--force-recover-env] [--restart]

恢复缺失的 deploy/.env 与运行态派生物，适用于已部署环境在代码同步后缺少 ignored 运行文件的场景。

选项：
  --force-recover-env    即使 env 文件已存在，也从当前容器事实重建
  --restart              渲染完成后重启/启动 OpenClaw 运行容器
  -h, --help             显示本帮助
USAGE
}

log() {
  printf '[recover-runtime] %s\n' "$*"
}

fail() {
  printf '[recover-runtime] ERROR: %s\n' "$*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --force-recover-env)
      FORCE_RECOVER_ENV=1
      shift
      ;;
    --restart)
      RESTART_CONTAINERS=1
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

require_command() {
  local name="$1"
  command -v "${name}" >/dev/null 2>&1 || fail "缺少命令：${name}"
}

container_exists() {
  docker inspect "$1" >/dev/null 2>&1
}

docker_env_value() {
  local container="$1"
  local key="$2"
  container_exists "${container}" || return 0
  docker inspect "${container}" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null |
    awk -F= -v env_key="${key}" '$1 == env_key {print substr($0, index($0, "=") + 1); exit}'
}

first_container_env_value() {
  local key="$1"
  shift
  local container value
  for container in "$@"; do
    value="$(docker_env_value "${container}" "${key}")"
    if [[ -n "${value}" ]]; then
      printf '%s\n' "${value}"
      return 0
    fi
  done
}

docker_image_ref() {
  local container="$1"
  container_exists "${container}" || return 0
  docker inspect "${container}" --format '{{.Config.Image}}' 2>/dev/null || true
}

first_container_image_ref() {
  local container value
  for container in "$@"; do
    value="$(docker_image_ref "${container}")"
    if [[ -n "${value}" ]]; then
      printf '%s\n' "${value}"
      return 0
    fi
  done
}

docker_port_host_ip() {
  local container="$1"
  local port="$2"
  container_exists "${container}" || return 0
  docker inspect "${container}" --format "{{with index .HostConfig.PortBindings \"${port}\"}}{{(index . 0).HostIp}}{{end}}" 2>/dev/null || true
}

state_mount_source() {
  local container="$1"
  container_exists "${container}" || return 0
  docker inspect "${container}" --format '{{range .Mounts}}{{if eq .Destination "/home/node/.openclaw"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || true
}

default_host_ip() {
  hostname -I 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i !~ /^127\./ && $i !~ /^169\.254\./) {print $i; exit}}'
}

single_host_cidr() {
  local ip="$1"
  [[ -n "${ip}" ]] || return 0
  if [[ "${ip}" == *:* ]]; then
    printf '%s/128\n' "${ip}"
  else
    printf '%s/32\n' "${ip}"
  fi
}

ssh_peer_ips() {
  command -v ss >/dev/null 2>&1 || return 0
  ss -Htn state established '( sport = :22 )' 2>/dev/null |
    awk '{print $NF}' |
    sed -E 's/^\[//; s/\](:[0-9]+)?$//; s/:([0-9]+)$//' |
    awk 'NF && $0 !~ /^127\./ {print}'
}

unique_lines() {
  awk 'NF && !seen[$0]++'
}

model_env_lines_from_containers() {
  local container
  for container in openclaw-control-plane-scheduler openclaw-internal-api; do
    container_exists "${container}" || continue
    docker inspect "${container}" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null |
      awk -F= '
        $0 !~ /=/ { next }
        {
          name = $1
          if (name !~ /^[A-Z][A-Z0-9_]*$/) { next }
          if (name ~ /^OPENCLAW_/) { next }
          if (name ~ /(^|_)MODEL_REF$/ || name ~ /(^|_)BASE_URL$/ || name ~ /(^|_)API_KEY$/ || name ~ /(^|_)ACCESS_KEY$/ || name ~ /(^|_)SECRET_KEY$/ || name ~ /(^|_)TOKEN$/ || name == "LOCAL_MODEL_COMMAND") {
            print
          }
        }
      '
  done | awk -F= '!seen[$1]++'
}

file_mode_600() {
  local path="$1"
  chmod 600 "${path}"
}

read_pin_key_or_empty() {
  local path="$1"
  local key="$2"
  [[ -f "${path}" ]] || return 0
  awk -F= -v expected="${key}" '
    $0 ~ /^[[:space:]]*#/ { next }
    $0 !~ /=/ { next }
    {
      name = $1
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
      if (name == expected) {
        value = substr($0, index($0, "=") + 1)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
        print value
        exit
      }
    }
  ' "${path}"
}

assert_required_env() {
  local missing=()
  local key
  for key in "$@"; do
    if [[ -z "${RECOVERED_ENV[${key}]:-}" ]]; then
      missing+=("${key}")
    fi
  done
  if ((${#missing[@]})); then
    printf '[recover-runtime] ERROR: 无法从当前容器事实恢复以下必填项：\n' >&2
    printf '  - %s\n' "${missing[@]}" >&2
    printf '[recover-runtime] 请先确认已存在一套可 inspect 的 OpenClaw 容器，或改用 one_click_config.sh 生成全新配置。\n' >&2
    exit 1
  fi
}

recover_env_file() {
  require_command docker
  require_command awk

  declare -gA RECOVERED_ENV=()
  local -a model_env_keys=()

  local uid_gid runtime_uid runtime_gid
  uid_gid="$(runtime_permissions_resolve_runtime_uid_gid "${ROOT_DIR}")"
  runtime_uid="${uid_gid%%:*}"
  runtime_gid="${uid_gid##*:}"

  local mount_source host_state_root
  mount_source="$(state_mount_source openclaw-official-gateway)"
  if [[ -n "${mount_source}" && "${mount_source}" == "${ROOT_DIR}"/* ]]; then
    host_state_root="${mount_source#"${ROOT_DIR}/"}"
  else
    host_state_root="$(host_install_defaults_state_root_default)"
  fi

  local listen_ip
  listen_ip="$(docker_port_host_ip openclaw-private-ingress '443/tcp')"
  if [[ -z "${listen_ip}" || "${listen_ip}" == "0.0.0.0" || "${listen_ip}" == "::" ]]; then
    listen_ip="$(docker_port_host_ip openclaw-private-ingress '80/tcp')"
  fi
  if [[ -z "${listen_ip}" || "${listen_ip}" == "0.0.0.0" || "${listen_ip}" == "::" ]]; then
    listen_ip="$(default_host_ip)"
  fi

  local allowed_cidrs
  allowed_cidrs="$(
    {
      single_host_cidr "${listen_ip}"
      ssh_peer_ips | while IFS= read -r peer_ip; do single_host_cidr "${peer_ip}"; done
    } | unique_lines | paste -sd, -
  )"

  RECOVERED_ENV[OPENCLAW_GATEWAY_TOKEN]="$(first_container_env_value OPENCLAW_GATEWAY_TOKEN openclaw-official-gateway openclaw-private-ingress)"
  RECOVERED_ENV[OPENCLAW_INTERNAL_API_TOKEN]="$(first_container_env_value OPENCLAW_INTERNAL_API_TOKEN openclaw-internal-api openclaw-control-plane-scheduler)"
  RECOVERED_ENV[OPENCLAW_INGRESS_HSTS_MAX_AGE]="$(first_container_env_value OPENCLAW_INGRESS_HSTS_MAX_AGE openclaw-private-ingress openclaw-official-gateway)"
  RECOVERED_ENV[OPENCLAW_TLS_MODE]="$(first_container_env_value OPENCLAW_TLS_MODE openclaw-private-ingress)"
  RECOVERED_ENV[OPENCLAW_TLS_CN]="$(first_container_env_value OPENCLAW_TLS_CN openclaw-private-ingress openclaw-official-gateway)"
  RECOVERED_ENV[OPENCLAW_INGRESS_LISTEN_IP]="${listen_ip}"
  RECOVERED_ENV[OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS]="${allowed_cidrs}"
  RECOVERED_ENV[OPENCLAW_INGRESS_BOUNDARY_MODE]="$(first_container_env_value OPENCLAW_INGRESS_BOUNDARY_MODE openclaw-private-ingress)"
  RECOVERED_ENV[OPENCLAW_OFFICIAL_GATEWAY_IMAGE]="$(first_container_image_ref openclaw-official-gateway)"
  RECOVERED_ENV[OPENCLAW_RUNTIME_PYTHON_IMAGE]="$(first_container_image_ref openclaw-internal-api openclaw-control-plane-scheduler)"
  RECOVERED_ENV[NGINX_IMAGE]="$(first_container_image_ref openclaw-private-ingress)"
  RECOVERED_ENV[CONTAINER_TZ]="$(first_container_env_value CONTAINER_TZ openclaw-official-gateway openclaw-internal-api openclaw-control-plane-scheduler openclaw-private-ingress)"
  RECOVERED_ENV[HOST_STATE_ROOT]="${host_state_root}"
  RECOVERED_ENV[OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH]="$(first_container_env_value OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH openclaw-internal-api openclaw-control-plane-scheduler)"
  RECOVERED_ENV[OPENCLAW_CONTROL_PLANE_PROFILE]="$(first_container_env_value OPENCLAW_CONTROL_PLANE_PROFILE openclaw-internal-api openclaw-control-plane-scheduler)"
  RECOVERED_ENV[OPENCLAW_RUNTIME_UID]="${runtime_uid}"
  RECOVERED_ENV[OPENCLAW_RUNTIME_GID]="${runtime_gid}"

  [[ -n "${RECOVERED_ENV[OPENCLAW_INGRESS_HSTS_MAX_AGE]}" ]] || RECOVERED_ENV[OPENCLAW_INGRESS_HSTS_MAX_AGE]="300"
  [[ -n "${RECOVERED_ENV[OPENCLAW_INGRESS_BOUNDARY_MODE]}" ]] || RECOVERED_ENV[OPENCLAW_INGRESS_BOUNDARY_MODE]="host_firewall"
  local openclaw_pin_env='' runtime_pin_env=''
  repo_contract_assign_path openclaw_pin_env image_pins.openclaw
  repo_contract_assign_path runtime_pin_env image_pins.runtime
  [[ -n "${RECOVERED_ENV[OPENCLAW_OFFICIAL_GATEWAY_IMAGE]}" ]] || RECOVERED_ENV[OPENCLAW_OFFICIAL_GATEWAY_IMAGE]="$(read_pin_key_or_empty "${openclaw_pin_env}" OPENCLAW_OFFICIAL_GATEWAY_IMAGE)"
  [[ -n "${RECOVERED_ENV[OPENCLAW_RUNTIME_PYTHON_IMAGE]}" ]] || RECOVERED_ENV[OPENCLAW_RUNTIME_PYTHON_IMAGE]="$(read_pin_key_or_empty "${runtime_pin_env}" OPENCLAW_RUNTIME_PYTHON_IMAGE)"
  [[ -n "${RECOVERED_ENV[NGINX_IMAGE]}" ]] || RECOVERED_ENV[NGINX_IMAGE]="$(read_pin_key_or_empty "${runtime_pin_env}" NGINX_IMAGE)"
  [[ -n "${RECOVERED_ENV[CONTAINER_TZ]}" ]] || RECOVERED_ENV[CONTAINER_TZ]="Asia/Shanghai"
  [[ -n "${RECOVERED_ENV[OPENCLAW_CONTROL_PLANE_PROFILE]}" ]] || RECOVERED_ENV[OPENCLAW_CONTROL_PLANE_PROFILE]="agent_platform"
  [[ -n "${RECOVERED_ENV[OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH]}" ]] || RECOVERED_ENV[OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH]="$(openclaw_control_plane_container_config_path "${RECOVERED_ENV[OPENCLAW_CONTROL_PLANE_PROFILE]}")"
  local recovered_profile_from_path=''
  recovered_profile_from_path="$(openclaw_control_plane_profile_id_for_path "${RECOVERED_ENV[OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH]}")" \
    || fail "无法从 OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH 反查 control-plane profile：${RECOVERED_ENV[OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH]}"
  if [[ "$recovered_profile_from_path" != "${RECOVERED_ENV[OPENCLAW_CONTROL_PLANE_PROFILE]}" ]]; then
    fail "容器中的 OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH 与 OPENCLAW_CONTROL_PLANE_PROFILE 不一致：path -> ${recovered_profile_from_path}, profile=${RECOVERED_ENV[OPENCLAW_CONTROL_PLANE_PROFILE]}"
  fi

  case "${RECOVERED_ENV[OPENCLAW_TLS_MODE]}" in
    self_signed)
      ;;
    provided_files)
      RECOVERED_ENV[OPENCLAW_TLS_CERT_SOURCE_PATH]="$(read_pin_key_or_empty "${SITE_ENV_FILE}" OPENCLAW_TLS_CERT_SOURCE_PATH)"
      RECOVERED_ENV[OPENCLAW_TLS_KEY_SOURCE_PATH]="$(read_pin_key_or_empty "${SITE_ENV_FILE}" OPENCLAW_TLS_KEY_SOURCE_PATH)"
      assert_required_env OPENCLAW_TLS_CERT_SOURCE_PATH OPENCLAW_TLS_KEY_SOURCE_PATH
      ;;
    '')
      fail "无法从当前容器事实恢复 OPENCLAW_TLS_MODE；请先补齐 deploy/site.env 并重新执行 one_click_config.sh"
      ;;
    *)
      fail "容器中的 OPENCLAW_TLS_MODE 非法：${RECOVERED_ENV[OPENCLAW_TLS_MODE]}"
      ;;
  esac
  openclaw_tls_hostname_require "${RECOVERED_ENV[OPENCLAW_TLS_CN]}" 'OPENCLAW_TLS_CN' \
    || fail "无法恢复合法的 OPENCLAW_TLS_CN；请重新执行 init_private_ingress.sh 并生成 deploy/.env"

  local model_line model_key
  while IFS= read -r model_line; do
    [[ -n "${model_line}" && "${model_line}" == *=* ]] || continue
    model_key="${model_line%%=*}"
    [[ -n "${model_key}" ]] || continue
    if [[ -z "${RECOVERED_ENV[${model_key}]+_}" ]]; then
      model_env_keys+=("${model_key}")
    fi
    RECOVERED_ENV[${model_key}]="${model_line#*=}"
  done < <(model_env_lines_from_containers)

  assert_required_env \
    OPENCLAW_GATEWAY_TOKEN \
    OPENCLAW_INTERNAL_API_TOKEN \
    OPENCLAW_INGRESS_HSTS_MAX_AGE \
    OPENCLAW_TLS_MODE \
    OPENCLAW_TLS_CN \
    OPENCLAW_INGRESS_LISTEN_IP \
    OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS \
    OPENCLAW_INGRESS_BOUNDARY_MODE \
    OPENCLAW_OFFICIAL_GATEWAY_IMAGE \
    OPENCLAW_RUNTIME_PYTHON_IMAGE \
    NGINX_IMAGE \
    CONTAINER_TZ \
    HOST_STATE_ROOT \
    OPENCLAW_CONTROL_PLANE_PROFILE \
    OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH \
    OPENCLAW_RUNTIME_UID \
    OPENCLAW_RUNTIME_GID

  mkdir -p -- "$(dirname -- "${ENV_FILE}")"
  umask 077
  {
    printf '# OpenClaw deployment environment recovered from running container facts.\n'
    printf '# Secrets are copied from existing containers; do not commit this file.\n'
    local key
    local -a recovered_env_keys=(
      OPENCLAW_GATEWAY_TOKEN \
      OPENCLAW_INTERNAL_API_TOKEN \
      OPENCLAW_INGRESS_HSTS_MAX_AGE \
      OPENCLAW_TLS_MODE \
      OPENCLAW_TLS_CN \
      OPENCLAW_INGRESS_LISTEN_IP \
      OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS \
      OPENCLAW_INGRESS_BOUNDARY_MODE \
      OPENCLAW_OFFICIAL_GATEWAY_IMAGE \
      OPENCLAW_RUNTIME_PYTHON_IMAGE \
      NGINX_IMAGE \
      CONTAINER_TZ \
      HOST_STATE_ROOT \
      OPENCLAW_CONTROL_PLANE_PROFILE \
      OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH \
      OPENCLAW_RUNTIME_UID \
      OPENCLAW_RUNTIME_GID
    )
    if [[ "${RECOVERED_ENV[OPENCLAW_TLS_MODE]}" == 'provided_files' ]]; then
      recovered_env_keys+=(OPENCLAW_TLS_CERT_SOURCE_PATH OPENCLAW_TLS_KEY_SOURCE_PATH)
    fi
    for key in "${recovered_env_keys[@]}"
    do
      printf '%s=%s\n' "${key}" "${RECOVERED_ENV[${key}]}"
    done
    if ((${#model_env_keys[@]} > 0)); then
      for key in "${model_env_keys[@]}"; do
        printf '%s=%s\n' "${key}" "${RECOVERED_ENV[${key}]}"
      done
    fi
  } >"${ENV_FILE}"
  file_mode_600 "${ENV_FILE}"
  log "从容器事实恢复 ${ENV_FILE} 完成（敏感值未输出）"
}

render_runtime_generated_state() {
  local gateway_conf="${ROOT_DIR}/state/openclaw/gateway/nginx.gateway.conf"
  if [[ -d "${gateway_conf}" ]]; then
    if rmdir "${gateway_conf}" 2>/dev/null; then
      log "已清理空目录占位：state/openclaw/gateway/nginx.gateway.conf"
    else
      fail "state/openclaw/gateway/nginx.gateway.conf 是非空目录，无法安全覆盖"
    fi
  fi

  bash "${ROOT_DIR}/scripts/runtime/run_openclaw_python_tool.sh" setup env validate --env-file "${ENV_FILE}"
  bash "${ROOT_DIR}/scripts/runtime/run_openclaw_python_tool.sh" runtime paths render-generated --repo-root "${ROOT_DIR}"
  bash "${SCRIPT_DIR}/gen_cert.sh"
  bash "${ROOT_DIR}/scripts/runtime/run_openclaw_python_tool.sh" setup ingress render-nginx --env-file "${ENV_FILE}"
  bash "${SCRIPT_DIR}/fix_permissions.sh"
  log "运行态派生物渲染与权限收口完成"
}

restart_containers() {
  require_command docker

  local container
  for container in openclaw-official-gateway openclaw-internal-api openclaw-control-plane-scheduler; do
    if container_exists "${container}"; then
      docker restart "${container}" >/dev/null
      log "已重启 ${container}"
    fi
  done

  if container_exists openclaw-private-ingress; then
    local running
    running="$(docker inspect openclaw-private-ingress --format '{{.State.Running}}' 2>/dev/null || true)"
    if [[ "${running}" == "true" ]]; then
      docker restart openclaw-private-ingress >/dev/null
      log "已重启 openclaw-private-ingress"
    else
      docker start openclaw-private-ingress >/dev/null
      log "已启动 openclaw-private-ingress"
    fi
  fi
}

main() {
  if [[ -f "${ENV_FILE}" && "${FORCE_RECOVER_ENV}" -eq 0 ]]; then
    log "检测到 ${ENV_FILE}，跳过 env 恢复"
  else
    recover_env_file
  fi

  render_runtime_generated_state

  if [[ "${RESTART_CONTAINERS}" -eq 1 ]]; then
    restart_containers
  fi
}

main "$@"
