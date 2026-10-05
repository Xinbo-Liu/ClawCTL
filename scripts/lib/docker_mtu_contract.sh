#!/usr/bin/env bash
# 用途：统一探测宿主默认路由 MTU、Docker daemon MTU 与 bridge network MTU。
set -euo pipefail
openclaw_docker_mtu_default_interface() {
  command -v ip >/dev/null 2>&1 || return 1
  ip route get 1.1.1.1 2>/dev/null | awk '
    {
      for (idx = 1; idx <= NF; idx++) {
        if ($idx == "dev" && idx + 1 <= NF) {
          print $(idx + 1)
          exit
        }
      }
    }
  '
}
openclaw_docker_mtu_host_default_route_mtu() {
  local iface='' mtu=''
  command -v ip >/dev/null 2>&1 || return 1
  mtu="$(ip route get 1.1.1.1 2>/dev/null | awk '
    {
      for (idx = 1; idx <= NF; idx++) {
        if ($idx == "mtu" && idx + 1 <= NF) {
          print $(idx + 1)
          exit
        }
      }
    }
  ' || true)"
  if [[ "$mtu" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$mtu"
    return 0
  fi
  iface="$(openclaw_docker_mtu_default_interface || true)"
  [[ -n "$iface" && -r "/sys/class/net/$iface/mtu" ]] || return 1
  mtu="$(cat "/sys/class/net/$iface/mtu" 2>/dev/null || true)"
  [[ "$mtu" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "$mtu"
}
openclaw_docker_mtu_validate() {
  local mtu="$1"
  [[ "$mtu" =~ ^[0-9]+$ ]] || return 1
  (( mtu >= 576 && mtu <= 9000 ))
}
openclaw_docker_mtu_requested_value() {
  local requested="${1:-auto}"
  local host_mtu=''
  requested="${requested,,}"
  if [[ -z "$requested" || "$requested" == "auto" ]]; then
    host_mtu="$(openclaw_docker_mtu_host_default_route_mtu || true)"
    if [[ "$host_mtu" =~ ^[0-9]+$ && "$host_mtu" -lt 1500 ]]; then
      printf '%s\n' "$host_mtu"
    fi
    return 0
  fi
  openclaw_docker_mtu_validate "$requested" || return 2
  printf '%s\n' "$requested"
}
openclaw_docker_mtu_daemon_json_value() {
  local daemon_json="${1:-/etc/docker/daemon.json}"
  [[ -f "$daemon_json" && -r "$daemon_json" ]] || return 1
  if command -v jq >/dev/null 2>&1; then
    jq -r '.mtu // empty' "$daemon_json" 2>/dev/null || true
    return 0
  fi
  awk '
    /"mtu"[[:space:]]*:/ {
      line = $0
      sub(/^.*"mtu"[[:space:]]*:[[:space:]]*/, "", line)
      sub(/[,}].*$/, "", line)
      gsub(/"/, "", line)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", line)
      print line
      exit
    }
  ' "$daemon_json"
}
openclaw_docker_mtu_bridge_network_rows() {
  local daemon_mtu="${1:-}"
  local network='' driver='' option_mtu='' effective_mtu='' source='' default_bridge=''
  command -v docker >/dev/null 2>&1 || return 1
  docker network ls --format '{{.Name}}' 2>/dev/null | while IFS= read -r network; do
    [[ -n "$network" ]] || continue
    driver="$(docker network inspect "$network" --format '{{.Driver}}' 2>/dev/null || true)"
    [[ "$driver" == "bridge" ]] || continue
    option_mtu="$(docker network inspect "$network" --format '{{index .Options "com.docker.network.driver.mtu"}}' 2>/dev/null || true)"
    default_bridge="$(docker network inspect "$network" --format '{{index .Options "com.docker.network.bridge.default_bridge"}}' 2>/dev/null || true)"
    if [[ "$option_mtu" =~ ^[0-9]+$ ]]; then
      effective_mtu="$option_mtu"
      source='network'
    elif [[ "$default_bridge" == 'true' && "$daemon_mtu" =~ ^[0-9]+$ ]]; then
      effective_mtu="$daemon_mtu"
      source='daemon'
    else
      effective_mtu='1500'
      source='docker-default'
    fi
    printf '%s\t%s\t%s\n' "$network" "$effective_mtu" "$source"
  done
}
openclaw_docker_mtu_export_for_compose_render() {
  local requested="${OPENCLAW_DOCKER_NETWORK_MTU:-auto}"
  local effective_mtu=''
  if ! effective_mtu="$(openclaw_docker_mtu_requested_value "$requested")"; then
    return $?
  fi
  [[ -n "$effective_mtu" ]] || return 0
  export OPENCLAW_DOCKER_NETWORK_MTU="$effective_mtu"
  case " ${OPENCLAW_PYTHON_TOOL_EXTRA_ENV_VARS:-} " in
    *" OPENCLAW_DOCKER_NETWORK_MTU "*) ;;
    *) export OPENCLAW_PYTHON_TOOL_EXTRA_ENV_VARS="${OPENCLAW_PYTHON_TOOL_EXTRA_ENV_VARS:-} OPENCLAW_DOCKER_NETWORK_MTU" ;;
  esac
}
