#!/usr/bin/env bash
# 用途：统一维护运行容器的 service/container 真源、存在性检查与 docker exec 调用，避免脚本各自硬编码名称。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
# shellcheck source=./runtime_target_lib.sh
source "$ROOT_DIR/scripts/runtime/runtime_target_lib.sh"
# shellcheck source=./runtime_docker_lib.sh
source "$ROOT_DIR/scripts/runtime/runtime_docker_lib.sh"

# 统一输出运行容器相关失败信息，并返回标准错误码。
runtime_container_fail() {
  echo "[runtime_container][FAIL] $*" >&2
  return 2
}

# 根据 runtime target 返回容器名。
runtime_container_name_for_target() {
  runtime_target_container_name_for_target "$1"
}

# 根据 runtime target 返回 compose service 名称。
runtime_service_name_for_target() {
  runtime_target_service_name_for_target "$1"
}

# 列出运行容器层已声明的 target。
runtime_known_targets() {
  runtime_target_known_targets
}

# 返回容器状态与 health 的组合摘要。
runtime_container_status_line() {
  runtime_docker_container_status_line "$1"
}

# 确认当前环境具备 docker CLI。
runtime_container_require_docker() {
  runtime_docker_require_cli >/dev/null || runtime_container_fail '未检测到 docker'
}

# 确认目标容器已经创建。
runtime_container_require_exists() {
  local container_name="$1"
  runtime_docker_inspect_exists "$container_name" || runtime_container_fail "未找到容器：$container_name"
}

# 确认目标容器当前处于运行态。
runtime_container_require_running() {
  local container_name="$1"
  local running=""
  running="$(runtime_docker_container_running_bool "$container_name" 2>/dev/null || true)"
  [[ "$running" == 'true' ]] || runtime_container_fail "容器未运行：$container_name"
}

# 统一封装 docker exec，并处理 shell/tty/容器存在性校验。
runtime_container_exec() {
  runtime_container_require_docker
  local tty='0'
  local shell_mode='0'
  local container_name=""
  local shell_command=""
  local -a argv=()

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tty)
        tty='1'
        shift
        ;;
      --shell)
        [[ $# -ge 2 ]] || runtime_container_fail '--shell 缺少参数'
        shell_mode='1'
        shell_command="$2"
        shift 2
        ;;
      --container)
        [[ $# -ge 2 ]] || runtime_container_fail '--container 缺少参数'
        container_name="$2"
        shift 2
        ;;
      --)
        shift
        argv=("$@")
        break
        ;;
      *)
        runtime_container_fail "未知参数：$1"
        ;;
    esac
  done

  [[ -n "$container_name" ]] || runtime_container_fail '缺少 --container'
  runtime_container_require_exists "$container_name"
  runtime_container_require_running "$container_name"

  local -a docker_args=(exec -i)
  if [[ "$tty" == '1' ]] && [[ -t 0 ]] && [[ -t 1 ]]; then
    docker_args+=( -t )
  fi

  if [[ "$shell_mode" == '1' ]]; then
    docker "${docker_args[@]}" "$container_name" sh -lc "$shell_command"
    return $?
  fi

  [[ ${#argv[@]} -gt 0 ]] || runtime_container_fail '缺少容器内命令；请使用 -- <argv...> 或 --shell'
  docker "${docker_args[@]}" "$container_name" "${argv[@]}"
}
