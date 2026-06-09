#!/usr/bin/env bash
# 用途：统一封装 docker compose 调用，避免实现脚本各自散写 compose ps / up / exec。
set -euo pipefail

# 输出 runtime compose 库的标准失败消息。
runtime_compose_fail() {
  echo "[runtime_compose][FAIL] $*" >&2
  return 2
}

# 校验 docker CLI 可用，供所有 compose 调用入口复用。
runtime_compose_require_cli() {
  command -v docker >/dev/null 2>&1 || runtime_compose_fail '未检测到 docker'
}

# 用统一 project/env/compose 参数调用 docker compose，并自动清理 transient env 文件。
runtime_compose_command() {
  runtime_compose_require_cli >/dev/null
  local env_file="$1"
  local compose_file="$2"
  shift 2
  local compose_env_file=""
  local rc=0
  compose_env_file="$(runtime_compose_rebased_state_env_file "$ROOT_DIR" "$env_file" "$compose_file")" || return 2
  set +e
  docker compose --project-directory "$ROOT_DIR/deploy" --project-name deploy --env-file "$compose_env_file" -f "$compose_file" "$@"
  rc=$?
  set -e
  rm -f "$compose_env_file"
  return "$rc"
}

# 读取 env 文件中指定 key 的最后一次赋值，保持与 Docker Compose 覆盖语义一致。
runtime_compose_env_value() {
  local env_file="$1"
  local key="$2"
  [[ -f "$env_file" ]] || return 1
  awk -F= -v expected="$key" '
    BEGIN { found = 0; result = "" }
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
        result = value
        found = 1
      }
    }
    END {
      if (found == 1) {
        print result
      }
    }
  ' "$env_file"
}

# 返回 bundle load 写出的 verified local refs 环境文件位置，记录 pin、managed tag 与 image id。
runtime_compose_local_refs_env_file() {
  local root="$1"
  if [[ -n "${DEPLOYMENT_IMAGE_LOCAL_REFS_ENV:-}" ]]; then
    printf '%s\n' "$DEPLOYMENT_IMAGE_LOCAL_REFS_ENV"
    return 0
  fi
  printf '%s/state/image_artifacts/deployment-images.local-refs.env\n' "$root"
}

# 从 runtime.source_strategy 派生 compose 运行镜像 env key，供 verified local refs 注入复用。
runtime_compose_runtime_image_vars() {
  local root="$1"
  local image_env_script="$root/scripts/lib/image_env.sh"
  [[ -f "$image_env_script" ]] || runtime_compose_fail "缺少镜像环境 helper：$image_env_script" || return 2
  ROOT_DIR="$root" bash -c 'source "$1"; image_env_runtime_service_image_vars' _ "$image_env_script" | tr -d '\r'
}

# 将当前 pin、managed role tag 与合同 image id 都匹配的 local ref 追加到 transient compose env。
runtime_compose_append_verified_local_image_refs() {
  local root="$1"
  local source_env_file="$2"
  local target_env_file="$3"
  local refs_file=''
  local key='' pin_ref='' recorded_pin='' local_ref='' recorded_image_id='' actual_image_id='' image_vars_text=''
  refs_file="$(runtime_compose_local_refs_env_file "$root")"
  [[ -f "$refs_file" ]] || return 0
  image_vars_text="$(runtime_compose_runtime_image_vars "$root")" || return $?
  [[ -n "$image_vars_text" ]] || runtime_compose_fail 'runtime.source_strategy 未声明 compose 运行镜像变量集合' || return 2
  while IFS= read -r key; do
    [[ -n "$key" ]] || continue
    pin_ref="$(runtime_compose_env_value "$source_env_file" "$key" 2>/dev/null || true)"
    recorded_pin="$(runtime_compose_env_value "$refs_file" "${key}_PIN_REF" 2>/dev/null || true)"
    local_ref="$(runtime_compose_env_value "$refs_file" "${key}_LOCAL_REF" 2>/dev/null || true)"
    [[ -n "$pin_ref" && "$pin_ref" == "$recorded_pin" && -n "$local_ref" ]] || continue
    recorded_image_id="$(runtime_compose_env_value "$refs_file" "${key}_IMAGE_ID" 2>/dev/null || true)"
    [[ -n "$recorded_image_id" ]] || continue
    actual_image_id="$(docker image inspect "$local_ref" --format '{{.Id}}' 2>/dev/null || true)"
    [[ "$actual_image_id" == "$recorded_image_id" ]] || continue
    printf '%s=%s\n' "$key" "$local_ref" >> "$target_env_file"
  done <<< "$image_vars_text"
}

# 解析宿主机 state 根目录，兼容 HOST_STATE_DIR 覆盖、env 文件值和默认 state/openclaw。
runtime_compose_host_state_root() {
  local root="$1"
  local env_file="$2"
  local host_state_root="${HOST_STATE_DIR:-}"
  if [[ -z "$host_state_root" ]]; then
    host_state_root="$(runtime_compose_env_value "$env_file" HOST_STATE_ROOT 2>/dev/null || true)"
  fi
  [[ -n "$host_state_root" ]] || host_state_root="state/openclaw"
  case "$host_state_root" in
    /*) printf '%s\n' "${host_state_root%/}" ;;
    *) printf '%s/%s\n' "$root" "${host_state_root%/}" ;;
  esac
}

# 返回 target 相对 base 的路径，确保 compose 文件可以从 project directory 解析挂载路径。
runtime_compose_relative_path() {
  local target="$1"
  local base="$2"
  if realpath --help 2>/dev/null | grep -q -- '--relative-to'; then
    realpath --relative-to="$base" "$target"
    return $?
  fi
  runtime_compose_fail '无法计算 compose 相对 HOST_STATE_ROOT；需要 GNU realpath --relative-to'
}

# 将宿主机 state 根目录转换为 compose env 中的 HOST_STATE_ROOT 值。
runtime_compose_state_value_for_compose() {
  local root="$1"
  local env_file="$2"
  local compose_file="$3"
  local host_state_root=""
  host_state_root="$(runtime_compose_host_state_root "$root" "$env_file")"
  [[ -f "$compose_file" ]] || runtime_compose_fail "compose 文件不存在：$compose_file" || return 2
  runtime_compose_relative_path "$host_state_root" "$root"
}

# 基于当前 env 文件生成重写 HOST_STATE_ROOT 且可注入本地镜像 ref 的 transient env 文件。
runtime_compose_rebased_state_env_file() {
  local root="$1"
  local env_file="$2"
  local compose_file="$3"
  local host_state_root=""
  local state_value=""
  local tmp_parent=""
  local tmp_env=""
  [[ -f "$env_file" ]] || runtime_compose_fail "env 文件不存在：$env_file" || return 2
  [[ -f "$compose_file" ]] || runtime_compose_fail "compose 文件不存在：$compose_file" || return 2
  host_state_root="$(runtime_compose_host_state_root "$root" "$env_file")"
  state_value="$(runtime_compose_state_value_for_compose "$root" "$env_file" "$compose_file")" || return 2
  tmp_parent="$host_state_root/control_plane/tmp"
  mkdir -p "$tmp_parent"
  tmp_env="$(mktemp "$tmp_parent/compose-env.XXXXXX")" || return 2
  awk -v host_state_root="$state_value" '
    BEGIN { written = 0 }
    /^[[:space:]]*HOST_STATE_ROOT[[:space:]]*=/ {
      print "HOST_STATE_ROOT=" host_state_root
      written = 1
      next
    }
    { print }
    END {
      if (written == 0) {
        print "HOST_STATE_ROOT=" host_state_root
      }
    }
  ' "$env_file" > "$tmp_env"
  runtime_compose_append_verified_local_image_refs "$root" "$env_file" "$tmp_env"
  chmod 600 "$tmp_env"
  printf '%s\n' "$tmp_env"
}

# 返回 deploy 渲染出的 effective runtime compose 文件路径。
runtime_compose_effective_file() {
  local root="$1"
  local env_file="${2:-$root/deploy/.env}"
  local host_state_root
  host_state_root="$(runtime_compose_host_state_root "$root" "$env_file")"
  printf '%s/control_plane/setup/docker-compose.effective.yml\n' "$host_state_root"
}

# 返回应使用的 runtime compose 文件；effective 文件缺失时回退到仓库默认 compose。
runtime_compose_default_file() {
  local root="$1"
  local env_file="${2:-$root/deploy/.env}"
  local effective_file
  effective_file="$(runtime_compose_effective_file "$root" "$env_file")"
  if [[ -f "$effective_file" ]]; then
    printf '%s\n' "$effective_file"
    return 0
  fi
  printf '%s/deploy/docker-compose.yml\n' "$root"
}

# 为静态合同检查准备隔离 state 目录和 transient compose env 文件。
runtime_compose_prepare_transient_env_files() {
  local root="$1"
  local env_file="$2"
  local label="${3:-runtime-compose-config}"
  local tmp_parent="$root/state/openclaw/control_plane/tmp"
  local tmp_dir=""
  local state_rel=""
  local compose_env_file=""
  [[ -f "$env_file" ]] || runtime_compose_fail "env 文件不存在：$env_file" || return 2
  mkdir -p "$tmp_parent"
  tmp_dir="$(mktemp -d "$tmp_parent/${label}.XXXXXX")" || return 2
  state_rel="state/openclaw/control_plane/tmp/$(basename "$tmp_dir")/host_state"
  mkdir -p \
    "$root/$state_rel/gateway" \
    "$root/$state_rel/control_plane"
  : > "$root/$state_rel/gateway/runtime.gateway.env"
  : > "$root/$state_rel/control_plane/runtime.internal-api.env"
  : > "$root/$state_rel/control_plane/runtime.internal-api.app.env"
  : > "$root/$state_rel/control_plane/runtime.scheduler.env"
  : > "$root/$state_rel/control_plane/runtime.scheduler.app.env"
  compose_env_file="$tmp_dir/compose.env"
  awk -v host_state_root="$state_rel" '
    BEGIN { written = 0 }
    /^[[:space:]]*HOST_STATE_ROOT[[:space:]]*=/ {
      print "HOST_STATE_ROOT=" host_state_root
      written = 1
      next
    }
    { print }
    END {
      if (written == 0) {
        print "HOST_STATE_ROOT=" host_state_root
      }
    }
  ' "$env_file" > "$compose_env_file"
  runtime_compose_append_verified_local_image_refs "$root" "$env_file" "$compose_env_file"
  chmod 600 "$compose_env_file"
  printf '%s\t%s\n' "$compose_env_file" "$tmp_dir"
}

# 清理 runtime_compose_prepare_transient_env_files 创建的隔离临时目录。
runtime_compose_cleanup_transient_env_files() {
  local root="$1"
  local tmp_dir="${2:-}"
  [[ -n "$tmp_dir" ]] || return 0
  case "$tmp_dir" in
    "$root/state/openclaw/control_plane/tmp/"*)
      rm -rf "$tmp_dir"
      ;;
    *)
      runtime_compose_fail "拒绝清理非 compose 临时目录：$tmp_dir"
      return 2
      ;;
  esac
}

# 查询指定 compose service 当前容器 id。
runtime_compose_service_container_id() {
  local env_file="$1"
  local compose_file="$2"
  local service_name="$3"
  runtime_compose_command "$env_file" "$compose_file" ps -q "$service_name" 2>/dev/null | head -n 1 | tr -d '[:space:]'
}

# 启动指定 runtime compose service。
runtime_compose_up_services() {
  local env_file="$1"
  local compose_file="$2"
  shift 2
  runtime_compose_command "$env_file" "$compose_file" up -d "$@"
}

# 在指定 runtime compose service 容器内执行命令。
runtime_compose_exec_service() {
  local env_file="$1"
  local compose_file="$2"
  local service_name="$3"
  shift 3
  runtime_compose_command "$env_file" "$compose_file" exec -T "$service_name" "$@"
}

# 输出 runtime compose 当前服务状态。
runtime_compose_ps() {
  local env_file="$1"
  local compose_file="$2"
  shift 2
  runtime_compose_command "$env_file" "$compose_file" ps "$@"
}
