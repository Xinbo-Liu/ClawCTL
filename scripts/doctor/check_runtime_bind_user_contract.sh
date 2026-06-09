#!/usr/bin/env bash
# 用途：在镜像已经本地可用时，检查各 runtime 服务的容器运行用户与宿主机可写 bind mount 的 UID/GID 合同，提前暴露 bind mount 写入失败风险。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
source "$ROOT_DIR/scripts/lib/image_env.sh"
source "$ROOT_DIR/scripts/runtime/runtime_compose_lib.sh"
source "$ROOT_DIR/scripts/setup/lib/runtime_permissions.sh"

ENV_FILE="$ROOT_DIR/deploy/.env"
COMPOSE_FILE=""
WARNINGS=0
FAILURES=0

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/doctor/check_runtime_bind_user_contract.sh [选项]

说明：
  - 仅检查 docker-compose.yml 中当前定义的 runtime 服务，对可写 bind mount 的宿主机所有权与 mode bit 做 UID/GID 合同判断。
  - runtime 服务用户真源固定来自 deploy/.env 的 OPENCLAW_RUNTIME_UID / OPENCLAW_RUNTIME_GID；本检查会按 compose 中声明的 user 解析有效 UID/GID。
  - 当前脚本不会自动 sudo、提权、chown，也不会自动 docker pull / docker build；若镜像尚未在本机就绪，只会给出 WARN，提示在镜像到位后重跑本检查。
  - 本检查优先发现“容器不是 root、宿主机路径又是 700/600 且 owner 不匹配”这一类高危中断项。

选项：
  --env-file <path>        覆盖默认 env 文件（默认：deploy/.env）
  --compose-file <path>    覆盖 compose 文件（默认：当前运行画像 effective compose；缺失时回退 deploy/docker-compose.yml）
  -h, --help               显示帮助
USAGE
}

note() { printf '[INFO] %s\n' "$*"; }
warn() { WARNINGS=1; printf '[WARN] %s\n' "$*"; }
fail() { FAILURES=1; printf '[FAIL] %s\n' "$*" >&2; }

require_docker_ready() {
  command -v docker >/dev/null 2>&1 || { fail '未检测到 docker；无法检查 bind mount UID/GID 合同。'; return 1; }
  if ! docker info >/dev/null 2>&1; then
    local docker_info_err=''
    docker_info_err="$(docker info 2>&1 || true)"
    if printf '%s' "$docker_info_err" | grep -qiE 'permission denied|/var/run/docker\.sock|got permission denied'; then
      fail '当前用户无法访问 Docker daemon（通常是 /var/run/docker.sock 权限不足）；请先修复 Docker daemon 权限。'
      return 1
    fi
    fail '当前无法连接 Docker daemon；请先确认 dockerd 已启动。'
    return 1
  fi
  return 0
}

load_env_context() {
  IMAGE_ENV_DEPLOY_ENV_PATH="$ENV_FILE"
  export IMAGE_ENV_DEPLOY_ENV_PATH
  image_env_load
}

trim_compose_scalar() {
  local value="${1-}"
  # Windows 主机同步到 Linux 服务器时 compose/env 可能保留 CRLF；Docker inspect 必须使用去掉回车后的镜像引用。
  value="${value//$'\r'/}"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s\n' "$value"
}

compose_iter_service_entries() {
  local mode="$1"
  local file="$COMPOSE_FILE"
  awk -v mode="$mode" '
    BEGIN { in_services = 0; service = ""; in_volumes = 0 }
    /^services:[[:space:]]*$/ { in_services = 1; next }
    !in_services { next }
    /^[[:space:]]{2}[a-zA-Z0-9_.-]+:[[:space:]]*$/ {
      service = $0
      sub(/^[[:space:]]{2}/, "", service)
      sub(/:.*/, "", service)
      in_volumes = 0
      next
    }
    service == "" { next }
    /^[[:space:]]{4}volumes:[[:space:]]*$/ { in_volumes = 1; next }
    in_volumes && /^[[:space:]]{6}-[[:space:]]/ {
      if (mode == "volumes") {
        line = $0
        sub(/^[[:space:]]{6}-[[:space:]]*/, "", line)
        printf("%s\t%s\n", service, line)
      }
      next
    }
    in_volumes && /^[[:space:]]{4}[a-zA-Z0-9_.-]+:/ { in_volumes = 0 }
    /^[[:space:]]{4}image:[[:space:]]*/ {
      if (mode == "images") {
        line = $0
        sub(/^[[:space:]]{4}image:[[:space:]]*/, "", line)
        printf("%s\t%s\n", service, line)
      }
      next
    }
    /^[[:space:]]{4}user:[[:space:]]*/ {
      if (mode == "users") {
        line = $0
        sub(/^[[:space:]]{4}user:[[:space:]]*/, "", line)
        printf("%s\t%s\n", service, line)
      }
      next
    }
  ' "$file"
}

compose_find_service_image_template() {
  local target_service="$1"
  compose_iter_service_entries images | awk -F '\t' -v target="$target_service" '$1 == target { print $2; exit }'
}

compose_find_service_user_spec() {
  local target_service="$1"
  compose_iter_service_entries users | awk -F '\t' -v target="$target_service" '$1 == target { print $2; exit }'
}

resolve_compose_template() {
  local template="$1"
  local result=""
  local token=""
  local inner=""
  local key=""
  result="$(trim_compose_scalar "$template")"
  while [[ "$result" =~ (\$\{[^}]+\}) ]]; do
    token="${BASH_REMATCH[1]}"
    inner="${token:2:${#token}-3}"
    key="$inner"
    key="${key%%:?*}"
    key="${key%%:-*}"
    key="${key%%-*}"
    key="${key%%=*}"
    key="${key%%:*}"
    key="${key%%\?*}"
    if [[ -z "$key" ]]; then
      break
    fi
    result="${result//$token/${!key:-}}"
  done
  result="$(trim_compose_scalar "$result")"
  if [[ ${#result} -ge 2 ]]; then
    if [[ "${result:0:1}" == '"' && "${result: -1}" == '"' ]]; then
      result="${result:1:${#result}-2}"
    elif [[ "${result:0:1}" == "'" && "${result: -1}" == "'" ]]; then
      result="${result:1:${#result}-2}"
    fi
  fi
  result="$(trim_compose_scalar "$result")"
  printf '%s\n' "$result"
}

service_effective_image() {
  local service="$1"
  local raw=""
  raw="$(compose_find_service_image_template "$service")"
  [[ -n "$raw" ]] || return 1
  resolve_compose_template "$raw"
}

service_effective_user_spec() {
  local service="$1"
  local raw=""
  raw="$(compose_find_service_user_spec "$service")"
  [[ -n "$raw" ]] || return 1
  resolve_compose_template "$raw"
}

volume_options_imply_read_only() {
  local options="${1:-}"
  local option=""
  local -a option_list=()
  [[ -n "$options" ]] || return 1
  IFS=',' read -r -a option_list <<< "$options"
  for option in "${option_list[@]}"; do
    case "$option" in
      ro|roZ|roz)
        return 0
        ;;
    esac
  done
  return 1
}

image_present() {
  local ref="$1"
  docker image inspect "$ref" >/dev/null 2>&1
}

resolve_uid_gid_via_docker_run() {
  local image="$1"
  local user_spec="${2:-}"
  local out=''
  if [[ -n "$user_spec" ]]; then
    out="$(docker run --rm --user "$user_spec" --entrypoint sh "$image" -c 'id -u && id -g' 2>/dev/null || true)"
    if [[ -z "$out" ]]; then
      local uid='' gid=''
      uid="$(docker run --rm --user "$user_spec" --entrypoint id "$image" -u 2>/dev/null || true)"
      gid="$(docker run --rm --user "$user_spec" --entrypoint id "$image" -g 2>/dev/null || true)"
      if [[ "$uid" =~ ^[0-9]+$ && "$gid" =~ ^[0-9]+$ ]]; then
        printf '%s:%s\n' "$uid" "$gid"
        return 0
      fi
    fi
  else
    out="$(docker run --rm --entrypoint sh "$image" -c 'id -u && id -g' 2>/dev/null || true)"
    if [[ -z "$out" ]]; then
      local uid='' gid=''
      uid="$(docker run --rm --entrypoint id "$image" -u 2>/dev/null || true)"
      gid="$(docker run --rm --entrypoint id "$image" -g 2>/dev/null || true)"
      if [[ "$uid" =~ ^[0-9]+$ && "$gid" =~ ^[0-9]+$ ]]; then
        printf '%s:%s\n' "$uid" "$gid"
        return 0
      fi
    fi
  fi
  local uid_line gid_line
  uid_line="$(printf '%s\n' "$out" | sed -n '1p')"
  gid_line="$(printf '%s\n' "$out" | sed -n '2p')"
  if [[ "$uid_line" =~ ^[0-9]+$ && "$gid_line" =~ ^[0-9]+$ ]]; then
    printf '%s:%s\n' "$uid_line" "$gid_line"
    return 0
  fi
  return 1
}

resolve_image_config_user() {
  local image="$1"
  docker image inspect --format '{{.Config.User}}' "$image" 2>/dev/null || true
}

resolve_service_uid_gid() {
  local service="$1"
  local image="$2"
  local user_spec="${3:-}"
  local out=''
  if out="$(resolve_uid_gid_via_docker_run "$image" "$user_spec" 2>/dev/null)"; then
    printf '%s\n' "$out"
    return 0
  fi
  local config_user=''
  config_user="$(resolve_image_config_user "$image")"
  if [[ -z "$user_spec" ]]; then
    if [[ -z "$config_user" ]]; then
      printf '0:0\n'
      return 0
    fi
    if [[ "$config_user" =~ ^([0-9]+):([0-9]+)$ ]]; then
      printf '%s:%s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
      return 0
    fi
    if [[ "$config_user" =~ ^[0-9]+$ ]]; then
      printf '%s:%s\n' "$config_user" "$config_user"
      return 0
    fi
  else
    if [[ "$user_spec" =~ ^([0-9]+):([0-9]+)$ ]]; then
      printf '%s:%s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
      return 0
    fi
    if [[ "$user_spec" =~ ^[0-9]+$ ]]; then
      printf '%s:%s\n' "$user_spec" "$user_spec"
      return 0
    fi
  fi
  return 1
}

mode_digit_for_identity() {
  local mode="$1"
  local owner_uid="$2"
  local owner_gid="$3"
  local need_uid="$4"
  local need_gid="$5"
  local padded="$mode"
  while [[ ${#padded} -lt 3 ]]; do padded="0$padded"; done
  local owner_digit="${padded: -3:1}"
  local group_digit="${padded: -2:1}"
  local other_digit="${padded: -1:1}"
  if [[ "$need_uid" == "$owner_uid" ]]; then
    printf '%s\n' "$owner_digit"
  elif [[ "$need_gid" == "$owner_gid" ]]; then
    printf '%s\n' "$group_digit"
  else
    printf '%s\n' "$other_digit"
  fi
}

permission_digit_allows() {
  local digit="$1"
  local need="$2"
  local value=$((digit))
  case "$need" in
    w) (( (value & 2) == 2 )) ;;
    wx|xw) (( (value & 2) == 2 && (value & 1) == 1 )) ;;
    x) (( (value & 1) == 1 )) ;;
    *) return 1 ;;
  esac
}

check_bind_path_access() {
  local service="$1" image="$2" uid_gid="$3" host_path="$4" entry_type="$5"
  local need_uid="${uid_gid%%:*}"
  local need_gid="${uid_gid##*:}"
  [[ "$need_uid" =~ ^[0-9]+$ && "$need_gid" =~ ^[0-9]+$ ]] || {
    warn "$service 无法解析为明确 UID/GID：$uid_gid；请在镜像就绪后重跑 bind mount 合同检查。"
    return 0
  }
  if [[ "$need_uid" == "0" ]]; then
    note "$service($image) 以 root 运行，可写 bind mount 不受 owner-only mode bit 阻断。"
    return 0
  fi

  local inspect_path="$host_path"
  local need_mode=''
  if [[ -e "$host_path" ]]; then
    if [[ "$entry_type" == 'dir' ]]; then
      need_mode='wx'
    else
      need_mode='w'
    fi
  else
    inspect_path="$(dirname "$host_path")"
    need_mode='wx'
    [[ -d "$inspect_path" ]] || {
      fail "$service 目标路径缺少可创建父目录：$inspect_path（源自 $host_path）；当前脚本不会自动补建越级路径。"
      return 0
    }
  fi

  local stat_out=''
  stat_out="$(stat -c '%u %g %a' "$inspect_path" 2>/dev/null || true)"
  [[ -n "$stat_out" ]] || {
    warn "$service 无法读取宿主机路径 stat：$inspect_path；请手工确认 bind mount 合同。"
    return 0
  }
  local owner_uid owner_gid mode digit
  owner_uid="${stat_out%% *}"
  stat_out="${stat_out#* }"
  owner_gid="${stat_out%% *}"
  mode="${stat_out##* }"
  digit="$(mode_digit_for_identity "$mode" "$owner_uid" "$owner_gid" "$need_uid" "$need_gid")"
  if permission_digit_allows "$digit" "$need_mode"; then
    note "$service bind mount 合同通过：$host_path <- UID:GID ${need_uid}:${need_gid}（effective mode digit=$digit）"
    return 0
  fi

  fail "$service 可写 bind mount 合同不满足：$host_path 对容器 UID:GID ${need_uid}:${need_gid} 不可写；当前 effective mode digit=$digit，owner=$owner_uid:$owner_gid。请先收口宿主机 owner/UID/GID 合同，或调整运行时用户配置。"
}

check_service_bind_contract() {
  local service="$1"
  local image=''
  image="$(service_effective_image "$service" 2>/dev/null || true)"
  [[ -n "$image" ]] || {
    warn "$service 未解析到 image；跳过 bind mount 合同检查。"
    return 0
  }
  if ! image_present "$image"; then
    warn "$service 镜像当前不在本机：$image；无法解析容器运行 UID/GID，请在镜像就绪后重跑 bind mount 合同检查。"
    return 0
  fi
  local user_spec=''
  user_spec="$(service_effective_user_spec "$service" 2>/dev/null || true)"
  local uid_gid=''
  uid_gid="$(resolve_service_uid_gid "$service" "$image" "$user_spec" 2>/dev/null || true)"
  if [[ -z "$uid_gid" ]]; then
    warn "$service 无法从镜像解析运行 UID/GID（image=$image, user=${user_spec:-<default>}）；请在镜像就绪后手工确认。"
    return 0
  fi

  local compose_dir="$ROOT_DIR/deploy"
  local entry='' host_spec='' rest='' container_part='' options='' host_abs='' host_type='dir'
  while IFS=$'\t' read -r entry_service entry; do
    [[ "$entry_service" == "$service" ]] || continue
    entry="$(trim_compose_scalar "$entry")"
    [[ "$entry" == ./*:* || "$entry" == ../*:* || "$entry" == /*:* ]] || continue
    host_spec="${entry%%:*}"
    rest="${entry#*:}"
    container_part="${rest%%:*}"
    options=''
    if [[ "$rest" == *:* ]]; then
      options="${rest#*:}"
    fi
    if volume_options_imply_read_only "$options"; then
      continue
    fi
    host_abs="$(runtime_permissions_abs_path "$compose_dir" "$host_spec")"
    case "$host_abs" in
      *.json|*.jsonl|*.env|*.conf|*.crt|*.key|*.pem|*.md|*.txt|*.log) host_type='file' ;;
      *) host_type='dir' ;;
    esac
    check_bind_path_access "$service" "$image" "$uid_gid" "$host_abs" "$host_type"
  done < <(compose_iter_service_entries volumes)
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      [[ $# -ge 2 ]] || { echo '[FAIL] --env-file 缺少路径参数' >&2; exit 2; }
      ENV_FILE="$2"
      shift 2
      ;;
    --compose-file)
      [[ $# -ge 2 ]] || { echo '[FAIL] --compose-file 缺少路径参数' >&2; exit 2; }
      COMPOSE_FILE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[FAIL] 未知参数：$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$COMPOSE_FILE" ]]; then
  COMPOSE_FILE="$(runtime_compose_default_file "$ROOT_DIR" "$ENV_FILE")"
fi
[[ -f "$COMPOSE_FILE" ]] || { fail "compose 文件不存在：$COMPOSE_FILE"; exit 2; }
require_docker_ready || exit 2
load_env_context
note '检查 runtime 服务可写 bind mount 的 UID/GID 合同'

mapfile -t SERVICES < <(compose_iter_service_entries images | awk -F '\t' '{print $1}' | awk '!seen[$0]++')
for service in "${SERVICES[@]}"; do
  check_service_bind_contract "$service"
done

if [[ "$FAILURES" == "1" ]]; then
  exit 2
fi
if [[ "$WARNINGS" == "1" ]]; then
  note 'bind mount UID/GID 合同检查通过，但仍有待确认项；请在镜像就绪后重跑。'
else
  note 'bind mount UID/GID 合同检查通过。'
fi
