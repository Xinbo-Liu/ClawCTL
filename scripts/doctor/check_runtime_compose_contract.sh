#!/usr/bin/env bash
# 用途：渲染当前 deploy compose，并校验服务集合、镜像真源与最小容器治理合同，避免问题拖到 compose up 才暴露。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
# shellcheck source=../lib/repo_python_env.sh
source "$ROOT_DIR/scripts/lib/repo_python_env.sh"
source "$ROOT_DIR/scripts/lib/image_env.sh"
source "$ROOT_DIR/scripts/runtime/runtime_compose_lib.sh"
source "$ROOT_DIR/scripts/setup/lib/deploy_env_shell.sh"
PYTHON_RUNNER="$ROOT_DIR/scripts/runtime/run_python_container.sh"

ENV_FILE="$ROOT_DIR/deploy/.env"
COMPOSE_FILE=""
COMPOSE_FILE_EXPLICIT=0

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/doctor/check_runtime_compose_contract.sh [选项]

说明：
  - 只检查当前唯一部署路径对应的 compose 渲染结果；不会启动容器。
  - 重点校验服务集合、运行镜像、低端口 ingress 能力补充、非 ingress 服务 user 合同、ingress 双网络代理边界，以及 /local_ro 挂载是否仍在预期位置。

选项：
  --env-file <path>        覆盖默认 env 文件（默认：deploy/.env）
  --compose-file <path>    覆盖 compose 文件（默认：当前运行画像 effective compose；缺失时回退 deploy/docker-compose.yml）
  -h, --help               显示帮助
USAGE
}

fail() {
  echo "[FAIL] $*" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      [[ $# -ge 2 ]] || fail "--env-file 缺少路径参数"
      ENV_FILE="$2"
      shift 2
      ;;
    --compose-file)
      [[ $# -ge 2 ]] || fail "--compose-file 缺少路径参数"
      COMPOSE_FILE="$2"
      COMPOSE_FILE_EXPLICIT=1
      shift 2
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

[[ -f "$ENV_FILE" ]] || fail "env 文件不存在：$ENV_FILE"
if [[ "$COMPOSE_FILE_EXPLICIT" != "1" ]]; then
  COMPOSE_FILE="$(runtime_compose_default_file "$ROOT_DIR" "$ENV_FILE")"
fi
[[ -f "$COMPOSE_FILE" ]] || fail "compose 文件不存在：$COMPOSE_FILE"
runtime_compose_require_cli >/dev/null

runtime_image_env_vars_text="$(image_env_runtime_service_image_vars)" || fail '无法从 source_strategy 解析 compose 运行镜像变量集合'
mapfile -t RUNTIME_IMAGE_ENV_KEYS <<< "$runtime_image_env_vars_text"

deploy_env_shell_load_keys "$ENV_FILE" \
  "${RUNTIME_IMAGE_ENV_KEYS[@]}" \
  OPENCLAW_RUNTIME_UID \
  OPENCLAW_RUNTIME_GID \
  OPENCLAW_CONTROL_PLANE_PROFILE \
  OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH
CONTRACT_ENV_ARGS=()
for env_key in "${RUNTIME_IMAGE_ENV_KEYS[@]}"; do
  CONTRACT_ENV_ARGS+=(--env "$env_key=${!env_key}")
done
CONTRACT_ENV_ARGS+=(--env "OPENCLAW_RUNTIME_UID=$OPENCLAW_RUNTIME_UID")
CONTRACT_ENV_ARGS+=(--env "OPENCLAW_RUNTIME_GID=$OPENCLAW_RUNTIME_GID")
[[ -z "${OPENCLAW_CONTROL_PLANE_PROFILE:-}" ]] || CONTRACT_ENV_ARGS+=(--env "OPENCLAW_CONTROL_PLANE_PROFILE=$OPENCLAW_CONTROL_PLANE_PROFILE")
[[ -z "${OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH:-}" ]] || CONTRACT_ENV_ARGS+=(--env "OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH=$OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH")

rendered_compose="$(mktemp)"
compose_env_file=""
compose_tmp_dir=""
cleanup() {
  rm -f "$rendered_compose"
  runtime_compose_cleanup_transient_env_files "$ROOT_DIR" "$compose_tmp_dir"
}
trap cleanup EXIT

IFS=$'\t' read -r compose_env_file compose_tmp_dir < <(
  runtime_compose_prepare_transient_env_files "$ROOT_DIR" "$ENV_FILE" runtime-compose-contract
)
for env_key in "${RUNTIME_IMAGE_ENV_KEYS[@]}"; do
  printf -v "$env_key" '%s' "$(runtime_compose_env_value "$compose_env_file" "$env_key")"
done
CONTRACT_ENV_ARGS=()
for env_key in "${RUNTIME_IMAGE_ENV_KEYS[@]}"; do
  CONTRACT_ENV_ARGS+=(--env "$env_key=${!env_key}")
done
CONTRACT_ENV_ARGS+=(--env "OPENCLAW_RUNTIME_UID=$OPENCLAW_RUNTIME_UID")
CONTRACT_ENV_ARGS+=(--env "OPENCLAW_RUNTIME_GID=$OPENCLAW_RUNTIME_GID")
[[ -z "${OPENCLAW_CONTROL_PLANE_PROFILE:-}" ]] || CONTRACT_ENV_ARGS+=(--env "OPENCLAW_CONTROL_PLANE_PROFILE=$OPENCLAW_CONTROL_PLANE_PROFILE")
[[ -z "${OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH:-}" ]] || CONTRACT_ENV_ARGS+=(--env "OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH=$OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH")
runtime_compose_command "$compose_env_file" "$COMPOSE_FILE" config --format json > "$rendered_compose"

REPO_PYTHON_ENV_ARGS=()
while IFS= read -r -d '' item; do
  REPO_PYTHON_ENV_ARGS+=("$item")
done < <(openclaw_repo_python_env_args "$ROOT_DIR")

bash "$PYTHON_RUNNER" --workdir "$ROOT_DIR" "${CONTRACT_ENV_ARGS[@]}" "${REPO_PYTHON_ENV_ARGS[@]+"${REPO_PYTHON_ENV_ARGS[@]}"}" --mount "$rendered_compose" -- - "$rendered_compose" "$ROOT_DIR" <<'PY'
import json
import os
import re
import sys
from pathlib import Path

from openclaw.control_plane.surfaces import load_runtime_service_registry
from openclaw.lib.repo.layout import resolve_default_runtime_control_plane_service_config_path
from openclaw.lib.runtime.source_strategy import runtime_service_image_roles
from openclaw.lib.repo.static_truth import repo_contract_path

rendered_path = Path(sys.argv[1])
repo_root = Path(sys.argv[2]).resolve()
text = rendered_path.read_text(encoding='utf-8')
obj = json.loads(text) or {}
services = obj.get('services') or {}
if not isinstance(services, dict):
    raise SystemExit('[FAIL] compose 渲染结果缺少 services 对象')

config_path = resolve_default_runtime_control_plane_service_config_path(repo_root)
registry = load_runtime_service_registry(repo_contract_path('runtime.service_registry', root_dir=repo_root), config_path=config_path)
rows = registry.get('targets') if isinstance(registry, dict) else None
if not isinstance(rows, list):
    raise SystemExit('[FAIL] runtime service registry.targets 必须为数组')

service_rows = []
for item in rows:
    if not isinstance(item, dict):
        continue
    target = str(item.get('target') or '').strip()
    service = str(item.get('service') or '').strip()
    if target and service:
        service_rows.append((target, service))

expected_services = {service for _, service in service_rows}
actual_services = set(services.keys())
if actual_services != expected_services:
    missing = ', '.join(sorted(expected_services - actual_services)) or '<none>'
    extra = ', '.join(sorted(actual_services - expected_services)) or '<none>'
    raise SystemExit(f'[FAIL] compose 服务集合漂移：missing={missing}; extra={extra}')

expected_images = {}
selector_to_env_key = {}
for role in runtime_service_image_roles(repo_root):
    selector = str(role.compose_selector or '').strip()
    if not selector:
        raise SystemExit(f'[FAIL] runtime source strategy 中 {role.image_id} 缺少 compose target_selector')
    if selector in selector_to_env_key:
        raise SystemExit(f'[FAIL] compose target_selector 重复：{selector}')
    selector_to_env_key[selector] = role.env_key
default_env_key = selector_to_env_key.get('default')
if not default_env_key:
    raise SystemExit('[FAIL] runtime source strategy 必须声明 compose_runtime target_selector=default')
for target, service_name in service_rows:
    env_key = selector_to_env_key.get(target) or default_env_key
    image_ref = os.environ.get(env_key)
    if not image_ref:
        raise SystemExit(f'[FAIL] compose 镜像变量未透传：{env_key}')
    expected_images[service_name] = image_ref
expected_user = f"{os.environ['OPENCLAW_RUNTIME_UID']}:{os.environ['OPENCLAW_RUNTIME_GID']}"


def ensure(cond: bool, message: str) -> None:
    if not cond:
        raise SystemExit(f'[FAIL] {message}')


def ensure_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def normalized_volume_targets(service_obj):
    items = []
    for entry in ensure_list(service_obj.get('volumes')):
        if isinstance(entry, str):
            parts = entry.split(':')
            target = parts[1] if len(parts) >= 2 else ''
            read_only = False
            if len(parts) >= 3:
                read_only = 'ro' in parts[2].split(',')
            items.append((target, read_only))
        elif isinstance(entry, dict):
            target = entry.get('target') or ''
            read_only = bool(entry.get('read_only'))
            items.append((target, read_only))
    return items


def healthcheck_shell(service_obj):
    healthcheck = service_obj.get('healthcheck') or {}
    test = healthcheck.get('test') if isinstance(healthcheck, dict) else None
    if isinstance(test, list):
        if test and str(test[0]).upper() == 'CMD-SHELL':
            return ' '.join(str(item) for item in test[1:])
        return ' '.join(str(item) for item in test)
    if isinstance(test, str):
        return test
    return ''


def duration_seconds(value) -> float:
    if isinstance(value, (int, float)):
        raw = float(value)
        return raw / 1_000_000_000 if raw > 10_000 else raw
    text = str(value or '').strip()
    if not text:
        return 0.0
    if re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', text):
        raw = float(text)
        return raw / 1_000_000_000 if raw > 10_000 else raw
    unit_seconds = {
        'h': 3600.0,
        'm': 60.0,
        's': 1.0,
        'ms': 0.001,
        'us': 0.000001,
        'µs': 0.000001,
        'ns': 0.000000001,
    }
    total = 0.0
    consumed = ''
    for match in re.finditer(r'([0-9]+(?:\.[0-9]+)?)(ns|us|µs|ms|h|m|s)', text):
        consumed += match.group(0)
        total += float(match.group(1)) * unit_seconds[match.group(2)]
    if total > 0 and consumed == text:
        return total
    raise SystemExit(f'[FAIL] 无法解析 healthcheck duration：{text}')


def port_mapping_matches(entry, port: int) -> bool:
    if isinstance(entry, str):
        return f':{port}:{port}' in entry or entry.startswith(f'{port}:{port}')
    if isinstance(entry, dict):
        return (
            str(entry.get('target') or '') == str(port)
            and str(entry.get('published') or '') == str(port)
            and str(entry.get('protocol') or 'tcp') == 'tcp'
        )
    return False


for service_name, image in expected_images.items():
    service = services[service_name]
    ensure(service.get('image') == image, f'{service_name} 运行镜像漂移：期望 {image}，实际 {service.get("image")}')
    ensure('build' not in service, f'{service_name} 不得使用 build:；当前唯一运行路径必须使用前置准备好的镜像')
    ensure(service.get('restart') == 'unless-stopped', f'{service_name} restart 必须为 unless-stopped')
    ensure(service.get('init') is True, f'{service_name} 必须显式启用 init: true')
    ensure(service.get('pull_policy') == 'never', f'{service_name} pull_policy 必须为 never')
    cgroup_value = service.get('cgroup')
    if Path('/proc/self/ns/cgroup').exists():
        ensure(cgroup_value in {None, 'private'}, f'{service_name} cgroup 只能省略或声明为 private')
    else:
        ensure(cgroup_value is None, f'{service_name} 当前宿主机不支持 cgroup namespace，不得声明 cgroup: {cgroup_value}')
    ensure(service.get('read_only') is True, f'{service_name} read_only 必须为 true')
    security_opt = ensure_list(service.get('security_opt'))
    cap_drop = ensure_list(service.get('cap_drop'))
    ensure('no-new-privileges:true' in security_opt, f'{service_name} 必须保留 no-new-privileges:true')
    ensure('ALL' in cap_drop, f'{service_name} 必须保留 cap_drop: ALL')
    ensure('healthcheck' in service, f'{service_name} 必须保留 healthcheck')

for service_name in expected_services - {'openclaw-private-ingress'}:
    service = services[service_name]
    ensure(service.get('user') == expected_user, f'{service_name} user 合同漂移：期望 {expected_user}，实际 {service.get("user")}')
    cap_add = ensure_list(service.get('cap_add'))
    ensure(not cap_add, f'{service_name} 不得额外声明 cap_add；当前最小能力例外只允许 private ingress')

ingress_name = next((service for target, service in service_rows if target == 'ingress'), 'openclaw-private-ingress')
ingress = services[ingress_name]
internal_api_name = next((service for target, service in service_rows if target == 'internal-api'), 'openclaw-internal-api')
ensure(ingress.get('user') == '0:0', f'{ingress_name} 必须显式声明 user=0:0；实际 {ingress.get("user")}')
ensure('NET_BIND_SERVICE' in ensure_list(ingress.get('cap_add')), f'{ingress_name} 缺少 NET_BIND_SERVICE；在保留 cap_drop: ALL 且容器内监听 80/443 时会导致低端口绑定失败')
ports = ensure_list(ingress.get('ports'))
ensure(any(port_mapping_matches(item, 80) for item in ports), f'{ingress_name} 必须保留 80:80 监听映射')
ensure(any(port_mapping_matches(item, 443) for item in ports), f'{ingress_name} 必须保留 443:443 监听映射')
depends_on = ingress.get('depends_on') or {}
if isinstance(depends_on, list):
    ingress_dependency_names = set(str(item) for item in depends_on)
elif isinstance(depends_on, dict):
    ingress_dependency_names = set(str(item) for item in depends_on.keys())
else:
    ingress_dependency_names = set()
ensure(internal_api_name in ingress_dependency_names, f'{ingress_name} 必须等待 {internal_api_name}，避免只读控制面 API 代理在 internal-api 未就绪时启动')
ingress_networks = ingress.get('networks') or {}
if isinstance(ingress_networks, list):
    ingress_network_names = set(str(item) for item in ingress_networks)
elif isinstance(ingress_networks, dict):
    ingress_network_names = set(str(item) for item in ingress_networks.keys())
else:
    ingress_network_names = set()
ensure('openclaw_ingress_net' in ingress_network_names, f'{ingress_name} 必须连接 openclaw_ingress_net 以承载宿主机入口')
ensure('openclaw_internal_net' in ingress_network_names, f'{ingress_name} 必须连接 openclaw_internal_net 以代理 internal-api 只读控制面 API')

gateway_name = next((service for target, service in service_rows if target == 'gateway'), 'openclaw-official-gateway')
gateway_service = services[gateway_name]
gateway_health = gateway_service.get('healthcheck') or {}
gateway_healthcheck = healthcheck_shell(gateway_service)
gateway_healthcheck_script = repo_root / 'config' / 'gateway' / 'healthchecks' / 'gateway-tcp-liveness.cjs'
ensure(gateway_healthcheck_script.is_file(), f'{gateway_healthcheck_script} 不存在')
gateway_healthcheck_source = gateway_healthcheck_script.read_text(encoding='utf-8')
ensure('/home/node/.openclaw/healthchecks/gateway-tcp-liveness.cjs' in gateway_healthcheck, f'{gateway_name} Docker healthcheck 必须执行 Gateway state 中的 TCP liveness 脚本')
ensure('node -e' not in gateway_healthcheck, f'{gateway_name} Docker healthcheck 不得继续在 compose 中内联 node -e')
ensure('net.connect' in gateway_healthcheck_source, f'{gateway_healthcheck_script} 必须使用 TCP liveness 探测')
ensure('127.0.0.1' in gateway_healthcheck_source and '18789' in gateway_healthcheck_source, f'{gateway_healthcheck_script} 必须在容器内探测 127.0.0.1:18789 TCP 监听')
ensure('/healthz' not in gateway_healthcheck, f'{gateway_name} Docker healthcheck 不得用 /healthz HTTP 响应作为容器健康门槛')
ensure('/readyz' not in gateway_healthcheck, f'{gateway_name} Docker healthcheck 不得用 /readyz readiness 作为容器健康门槛')
ensure('openclaw gateway status' not in gateway_healthcheck, f'{gateway_name} Docker healthcheck 不得复用 gateway status 深查命令')
ensure('--require-rpc' not in gateway_healthcheck, f'{gateway_name} Docker healthcheck 不得依赖 WebSocket RPC 就绪深查')
ensure('/healthz' not in gateway_healthcheck_source, f'{gateway_healthcheck_script} 不得用 /healthz HTTP 响应作为容器健康门槛')
ensure('/readyz' not in gateway_healthcheck_source, f'{gateway_healthcheck_script} 不得用 /readyz readiness 作为容器健康门槛')
ensure('openclaw gateway status' not in gateway_healthcheck_source, f'{gateway_healthcheck_script} 不得复用 gateway status 深查命令')
ensure('--require-rpc' not in gateway_healthcheck_source, f'{gateway_healthcheck_script} 不得依赖 WebSocket RPC 就绪深查')
gateway_start_period = duration_seconds(gateway_health.get('start_period'))
gateway_interval = duration_seconds(gateway_health.get('interval'))
gateway_timeout = duration_seconds(gateway_health.get('timeout'))
gateway_retries = int(gateway_health.get('retries') or 0)
ensure(gateway_start_period >= 480, f'{gateway_name} start_period 必须覆盖官方 Gateway 首次插件依赖 staging；当前={gateway_health.get("start_period")}')
ensure(0 < gateway_interval <= 30, f'{gateway_name} interval 必须在首启 staging 完成后快速收敛；当前={gateway_health.get("interval")}')
ensure(0 < gateway_timeout <= 20, f'{gateway_name} timeout 必须保持 TCP liveness 快失败；当前={gateway_health.get("timeout")}')
ensure(gateway_retries >= 8, f'{gateway_name} retries 必须给 start_period 后短时抖动留出恢复窗口；当前={gateway_retries}')
gateway_volumes = normalized_volume_targets(services[gateway_name])
ensure(any(target == '/local_ro' and ro for target, ro in gateway_volumes), f'{gateway_name} 必须以只读方式挂载 /local_ro')
ensure(any(target == '/home/node/.openclaw' and not ro for target, ro in gateway_volumes), f'{gateway_name} 必须以读写方式整根挂载 /home/node/.openclaw')
gateway_submounts = [target for target, _ in gateway_volumes if target.startswith('/home/node/.openclaw/')]
ensure(not gateway_submounts, f'{gateway_name} 不得继续声明 /home/node/.openclaw 子挂载：{gateway_submounts}')

print('[INFO] compose 渲染通过。')
print(f'[INFO] 服务集合：{", ".join(sorted(actual_services))}')
print(f'[INFO] runtime bind user：{expected_user}')
print('[INFO] private ingress 已显式声明 user=0:0、补齐 NET_BIND_SERVICE、等待 internal-api，并同时连接 ingress/internal 网络。')
print('[INFO] 其他服务未引入额外 cap_add。')
print('[INFO] Gateway Docker healthcheck 使用 TCP liveness，并保留首启插件依赖 staging warmup 窗口；不依赖 HTTP /healthz、/readyz 或 WebSocket RPC 深查。')
print('[INFO] Gateway 单根状态目录与 /local_ro 挂载合同校验通过。')
print('[INFO] 当前部署 compose 合同校验通过。')
PY
