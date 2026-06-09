#!/usr/bin/env bash
# 用途：为 one_click_deploy 提供 runtime context / offline archive helper，避免主脚本继续内联环境与归档解析细节。
set -euo pipefail

# 从 bootstrap 风格 env 文件中读取指定键值。
deploy_read_bootstrap_env_key() {
  local file_path="$1"
  local key="$2"
  local raw_line=""
  local line=""
  local current_key=""
  local value=""

  [[ -f "$file_path" ]] || {
    printf ''
    return 0
  }

  while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    line="$raw_line"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"

    [[ -z "$line" ]] && continue
    [[ "${line:0:1}" == "#" ]] && continue
    [[ "$line" != *"="* ]] && continue

    current_key="${line%%=*}"
    value="${line#*=}"
    current_key="${current_key#"${current_key%%[![:space:]]*}"}"
    current_key="${current_key%"${current_key##*[![:space:]]}"}"
    [[ "$current_key" == "$key" ]] || continue

    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    if [[ ${#value} -ge 2 ]]; then
      if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; then
        value="${value:1:${#value}-2}"
      elif [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
        value="${value:1:${#value}-2}"
      fi
    fi
    printf '%s' "$value"
    return 0
  done < "$file_path"

  printf ''
}

# 解析 one_click_deploy 应写入的日志目录。
deploy_resolve_log_dir() {
  local runtime_host_env="$RUNTIME_HOST_ENV_PATH"
  local host_logs_dir=""
  if [[ -f "$runtime_host_env" ]]; then
    host_logs_dir="$(deploy_read_bootstrap_env_key "$runtime_host_env" HOST_LOGS_DIR)"
  fi
  if [[ -z "$host_logs_dir" ]]; then
    if [[ "$DEFAULT_LOG_DIR_REL" = /* ]]; then
      printf '%s' "$DEFAULT_LOG_DIR_REL"
      return 0
    fi
    printf '%s' "$ROOT_DIR/$DEFAULT_LOG_DIR_REL"
    return 0
  fi
  if [[ "$host_logs_dir" = /* ]]; then
    printf '%s' "$host_logs_dir"
    return 0
  fi
  printf '%s' "$ROOT_DIR/$host_logs_dir"
}


# 校验并解析离线部署所需的镜像归档。
deploy_resolve_offline_archives() {
  # shellcheck source=scripts/lib/deployment_images.sh
  source "$ROOT_DIR/scripts/lib/deployment_images.sh"

  local resolved_archive=''
  if resolved_archive="$(deployment_images_try_resolve_archive_path "$IMAGE_ARCHIVE_PATH")"; then
    IMAGE_ARCHIVE_PATH="$resolved_archive"
  else
    local resolve_status=$?
    if [[ "$resolve_status" -ne 1 ]]; then
      return "$resolve_status"
    fi
  fi

  if [[ -z "$IMAGE_ARCHIVE_PATH" ]]; then
    log "[FAIL] 离线模式缺少 deployment_images_*.tar；请先执行 scripts/images/export_deployment_images.sh，或通过 --image-archive 显式指定。"
    return 31
  fi

  local required_images=()
  local archive_missing=''
  local required_images_text=''
  required_images_text="$(deployment_images_list_images)" || return $?
  mapfile -t required_images <<< "$required_images_text"
  if archive_missing="$(deployment_images_archive_verify_required_refs "$IMAGE_ARCHIVE_PATH" "${required_images[@]}" || true)"; [[ -n "$archive_missing" ]]; then
    log "[FAIL] 离线 runtime 归档与当前 pin 不一致，缺少镜像：$archive_missing（archive=$IMAGE_ARCHIVE_PATH）"
    return 31
  fi

  log "[INFO] 离线 runtime 归档：$IMAGE_ARCHIVE_PATH"
}

# 初始化部署日志与摘要文件路径。
deploy_init_summary_paths() {
  LOG_DIR="$(deploy_resolve_log_dir)"
  LOG_PATH="$LOG_DIR/one_click_deploy-$TS.log"
  SUMMARY_PATH="$LOG_DIR/one_click_deploy-$TS.summary.md"
  SUMMARY_JSON_PATH="$LOG_DIR/one_click_deploy-$TS.summary.json"
}

# 完成镜像环境与摘要路径等运行上下文初始化。
deploy_init_runtime_context() {
  # 约束：--help / --explain 必须在任何运行态初始化之前就能返回，因此镜像 helper 延后到参数解析之后再加载。
  IMAGE_ENV_DEPLOY_ENV_PATH="$ENV_FILE"
  export IMAGE_ENV_DEPLOY_ENV_PATH
  # shellcheck source=scripts/lib/image_env.sh
  source "$IMAGE_ENV_SCRIPT"
  image_env_load
  deploy_init_summary_paths
}
