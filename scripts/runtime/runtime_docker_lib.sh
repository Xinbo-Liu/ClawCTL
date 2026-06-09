#!/usr/bin/env bash
# 用途：统一封装 Docker inspect / logs 等底层调用，避免实现脚本各自散写 docker inspect 与 docker logs。
set -euo pipefail

# 统一输出 Docker 层失败信息，并返回标准错误码。
runtime_docker_fail() {
  echo "[runtime_docker][FAIL] $*" >&2
  return 2
}

# 确认当前环境具备 docker CLI。
runtime_docker_require_cli() {
  command -v docker >/dev/null 2>&1 || runtime_docker_fail '未检测到 docker'
}

# 判断给定 Docker 对象是否存在。
runtime_docker_inspect_exists() {
  runtime_docker_require_cli >/dev/null
  docker inspect "$1" >/dev/null 2>&1
}

# 以指定模板执行 docker inspect 格式化读取。
runtime_docker_inspect_format() {
  runtime_docker_require_cli >/dev/null
  local target="$1"
  local format="$2"
  docker inspect --format "$format" "$target" 2>/dev/null
}

# 读取容器的 State.Status 字段。
runtime_docker_container_status() {
  local target="$1"
  if ! runtime_docker_inspect_exists "$target"; then
    printf 'missing\n'
    return 0
  fi
  runtime_docker_inspect_format "$target" '{{.State.Status}}'
}

# 读取容器的健康检查状态；未定义 health 时返回 none。
runtime_docker_container_health() {
  local target="$1"
  if ! runtime_docker_inspect_exists "$target"; then
    printf 'missing\n'
    return 0
  fi
  runtime_docker_inspect_format "$target" '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'
}

# 读取容器的启动时间。
runtime_docker_container_started_at() {
  local target="$1"
  if ! runtime_docker_inspect_exists "$target"; then
    printf '\n'
    return 0
  fi
  runtime_docker_inspect_format "$target" '{{.State.StartedAt}}'
}

# 读取容器是否处于 Running=true。
runtime_docker_container_running_bool() {
  local target="$1"
  if ! runtime_docker_inspect_exists "$target"; then
    printf 'false\n'
    return 0
  fi
  runtime_docker_inspect_format "$target" '{{.State.Running}}'
}

# 拼接容器运行状态与 health 状态，供上层直接展示。
runtime_docker_container_status_line() {
  local target="$1"
  local line=""
  if ! line="$(runtime_docker_inspect_format "$target" '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}')"; then
    printf 'missing\n'
    return 0
  fi
  [[ -n "$line" ]] || line='unknown unknown'
  printf '%s\n' "$line"
}

# 提取最近一条 healthcheck 日志摘要。
runtime_docker_container_health_log_tail() {
  local target="$1"
  if ! runtime_docker_inspect_exists "$target"; then
    printf '\n'
    return 0
  fi
  runtime_docker_inspect_format "$target" '{{if .State.Health}}{{range .State.Health.Log}}{{printf "%s|%s|%s\n" .End .ExitCode .Output}}{{end}}{{end}}' \
    | tail -n 1 \
    | tr '\n' ' ' \
    | sed 's/[[:space:]]\+/ /g; s/[[:space:]]$//'
}

# 统一封装 docker logs，并处理 follow/lines 等参数。
runtime_docker_logs() {
  runtime_docker_require_cli >/dev/null
  local follow='0'
  local lines='200'
  local target=''

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --follow)
        follow='1'
        shift
        ;;
      --lines)
        [[ $# -ge 2 ]] || runtime_docker_fail '--lines 缺少参数'
        lines="$2"
        shift 2
        ;;
      --target)
        [[ $# -ge 2 ]] || runtime_docker_fail '--target 缺少参数'
        target="$2"
        shift 2
        ;;
      *)
        runtime_docker_fail "未知参数：$1"
        ;;
    esac
  done

  [[ -n "$target" ]] || runtime_docker_fail '缺少 --target'
  [[ "$lines" =~ ^[0-9]+$ ]] || runtime_docker_fail '--lines 必须为非负整数'
  runtime_docker_inspect_exists "$target" || runtime_docker_fail "未找到容器：$target"

  if [[ "$follow" == '1' ]]; then
    exec docker logs -f --tail="$lines" "$target"
  fi
  docker logs --tail="$lines" "$target"
}
