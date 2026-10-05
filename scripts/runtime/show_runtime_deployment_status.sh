#!/usr/bin/env bash
# 用途：统一查看 effective compose、Docker MTU、runtime services、run ledger 与 acceptance 摘要。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
source "$ROOT_DIR/scripts/runtime/runtime_compose_lib.sh"
# shellcheck source=../lib/docker_mtu_contract.sh
source "$ROOT_DIR/scripts/lib/docker_mtu_contract.sh"

ENV_FILE="$ROOT_DIR/deploy/.env"
ALLOW_PARTIAL=0
STATUS_FAILURES=()

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/runtime/show_runtime_deployment_status.sh [--env-file <path>] [--allow-partial]

说明：
  - 只读聚合当前部署状态，不写 acceptance / evidence。
  - 输出 effective compose 路径、Docker MTU、runtime service status、control-plane run ledger 与 runtime acceptance summary。
  - 默认任一核心状态面查询失败时返回非 0；人工排障需要保留部分输出时显式追加 --allow-partial。
USAGE
}

warn() {
  echo "[show_runtime_deployment_status][WARN] $*" >&2
}

mark_status_failure() {
  STATUS_FAILURES+=("$*")
  warn "$*"
}

run_status_step() {
  local label="$1"
  shift
  if "$@"; then
    return 0
  else
    local rc=$?
    mark_status_failure "$label 查询失败：rc=$rc"
    return 0
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      [[ $# -ge 2 ]] || { echo "[show_runtime_deployment_status][FAIL] --env-file 缺少路径参数" >&2; exit 2; }
      ENV_FILE="$2"
      shift 2
      ;;
    --allow-partial)
      ALLOW_PARTIAL=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[show_runtime_deployment_status][FAIL] 未知参数：$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

COMPOSE_FILE="$(runtime_compose_default_file "$ROOT_DIR" "$ENV_FILE")"

echo '== runtime compose =='
echo "env_file=$ENV_FILE"
if [[ -f "$ENV_FILE" ]]; then
  echo 'env_file_exists=true'
else
  echo 'env_file_exists=false'
  mark_status_failure "deploy env 文件缺失：$ENV_FILE"
fi
echo "compose_file=$COMPOSE_FILE"
if [[ -f "$COMPOSE_FILE" ]]; then
  echo 'compose_file_exists=true'
else
  echo 'compose_file_exists=false'
  mark_status_failure "effective compose 文件缺失：$COMPOSE_FILE"
fi

echo
echo '== docker mtu =='
host_mtu="$(openclaw_docker_mtu_host_default_route_mtu || true)"
daemon_mtu="$(openclaw_docker_mtu_daemon_json_value /etc/docker/daemon.json || true)"
echo "host_default_route_mtu=${host_mtu:-unknown}"
echo "docker_daemon_mtu=${daemon_mtu:-unset}"
if [[ -z "$host_mtu" ]]; then
  mark_status_failure '无法读取宿主默认路由 MTU'
fi
if command -v docker >/dev/null 2>&1; then
  openclaw_docker_mtu_bridge_network_rows "$daemon_mtu" || mark_status_failure '无法读取 Docker bridge network MTU'
else
  mark_status_failure '未检测到 docker，无法读取 Docker network MTU'
fi

echo
echo '== runtime services =='
run_status_step 'runtime service status' bash "$ROOT_DIR/scripts/runtime/show_runtime_service_status.sh" --env-file "$ENV_FILE"

echo
echo '== control-plane run ledger =='
run_status_step 'control-plane run ledger 摘要' bash "$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh" control-plane runtime run-ledger-summary

echo
echo '== runtime acceptance =='
run_status_step 'runtime acceptance 摘要' bash "$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh" runtime acceptance acceptance-summary --strict true

echo
echo '== status result =='
if ((${#STATUS_FAILURES[@]} > 0)); then
  echo 'overall_status=partial'
  printf 'failure_count=%s\n' "${#STATUS_FAILURES[@]}"
  for failure in "${STATUS_FAILURES[@]}"; do
    printf 'failure=%s\n' "$failure"
  done
  if [[ "$ALLOW_PARTIAL" == "1" ]]; then
    echo 'allow_partial=true'
    exit 0
  fi
  echo 'allow_partial=false'
  exit 1
fi
echo 'overall_status=pass'
echo 'failure_count=0'
