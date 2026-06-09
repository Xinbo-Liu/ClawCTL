"""运行态 env 派生产物生成。"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Dict, List

from openclaw.lib.repo.layout import resolve_default_runtime_control_plane_service_config_path
from openclaw.lib.runtime.path_resolver import PathResolver

from .constants import CONTROL_PLANE_CONTAINER_ROOT
from .gateway.config import build_gateway_model_runtime_env_lines
from .io import read_json, write_text
from .registry import _load_registry

def internal_api_contract_path(repo_root: Path) -> Path:
    return repo_root / 'config' / 'services' / 'internal_api.json'


def load_internal_api_contract(repo_root: Path) -> Dict[str, Any]:
    return read_json(internal_api_contract_path(repo_root))


def build_internal_api_runtime_contract(repo_root: Path) -> Dict[str, str]:
    contract = load_internal_api_contract(repo_root)
    service_value = contract.get('service')
    service: Dict[str, Any] = service_value if isinstance(service_value, dict) else {}

    service_name = str(service.get('name') or 'openclaw-internal-api').strip() or 'openclaw-internal-api'
    bind_env = str(service.get('bindEnv') or 'OPENCLAW_INTERNAL_API_BIND').strip() or 'OPENCLAW_INTERNAL_API_BIND'
    port_env = str(service.get('portEnv') or 'OPENCLAW_INTERNAL_API_PORT').strip() or 'OPENCLAW_INTERNAL_API_PORT'
    token_env = str(service.get('tokenEnv') or 'OPENCLAW_INTERNAL_API_TOKEN').strip() or 'OPENCLAW_INTERNAL_API_TOKEN'

    port = '18081'
    return {
        'service_name': service_name,
        'base_url': f'http://{service_name}:{port}',
        bind_env: '0.0.0.0',
        port_env: port,
        'token_env_name': token_env,
    }


def build_internal_api_env_output(repo_root: Path, resolver: PathResolver) -> str:
    contract = build_internal_api_runtime_contract(repo_root)
    token_env_name = contract.pop('token_env_name')
    service_name = contract.pop('service_name')
    base_url = contract.pop('base_url')
    lines = [
        '# 由 runtime paths render-generated 生成；internal-api 运行态合同',
        f'# service={service_name}',
        f'# OPENCLAW_INTERNAL_API_BASE_URL={base_url}',
        f'# {token_env_name} 由服务级应用 env 注入；不写入 runtime.internal-api.env。',
        'OPENCLAW_RUNTIME_PATH_VIEW=scheduler',
        f'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH={container_config_path(repo_root, resolver.config_path)}',
        f"OPENCLAW_STATE_DIR={resolver.resolve_path('state_root', 'scheduler')}",
    ]
    for key, value in contract.items():
        lines.append(f'{key}={value}')
    return '\n'.join(lines) + '\n'


def build_internal_api_runtime_exports(repo_root: Path, *, consumer: str) -> List[str]:
    contract = build_internal_api_runtime_contract(repo_root)
    return [
        f'# 由 config/services/internal_api.json 派生；{consumer} 只通过 internal API 服务名访问业务面',
        f"OPENCLAW_INTERNAL_API_BASE_URL={contract['base_url']}",
    ]


def env_lines(view: str, resolver: PathResolver) -> List[str]:
    index = resolver.resolve_all()
    lines = [f'# 由 runtime paths render-generated 生成；视角={view}']
    if view == 'host':
        lines.append('HOST_REPO_ROOT=__PROJECT_ROOT__')
    else:
        lines.append(f'OPENCLAW_RUNTIME_PATH_VIEW={view}')
    for entry_id in resolver.entries:
        entry = index[entry_id]
        value = entry['paths'].get(view)
        if value is None:
            continue
        lines.append(f"{entry['env_names'][view]}={value}")
    return lines


def container_config_path(repo_root: Path, config_path: Path | None) -> str:
    selected = Path(config_path).resolve() if config_path is not None else resolve_default_runtime_control_plane_service_config_path(repo_root)
    try:
        rel = selected.relative_to(repo_root.resolve())
    except ValueError:
        return str(selected)
    return str(CONTROL_PLANE_CONTAINER_ROOT / PurePosixPath(rel.as_posix()))


def _view_runtime_env_specs(resolver: PathResolver) -> Dict[str, tuple[str, str]]:
    specs: Dict[str, tuple[str, str]] = {}
    for view in resolver.internal_views:
        entry_id = f"runtime_{view.replace('-', '_')}_env"
        if entry_id not in resolver.entries:
            continue
        filename = Path(resolver.resolve_entry(entry_id)['paths']['host']).name
        specs[filename] = (view, entry_id)
    return specs


def env_targets(resolver: PathResolver) -> Dict[str, Path]:
    targets: Dict[str, Path] = {}
    for filename, (_, entry_id) in _view_runtime_env_specs(resolver).items():
        targets[filename] = resolver.absolute_host_path(entry_id)
    if 'runtime_internal_api_env' in resolver.entries:
        filename = Path(resolver.resolve_entry('runtime_internal_api_env')['paths']['host']).name
        targets[filename] = resolver.absolute_host_path('runtime_internal_api_env')
    return targets


def build_env_outputs(repo_root: Path, resolver: PathResolver) -> Dict[str, str]:
    outputs: Dict[str, str] = {}
    for filename, (view, _) in _view_runtime_env_specs(resolver).items():
        lines = env_lines(view, resolver)
        if view == 'gateway':
            lines.extend(build_gateway_model_runtime_env_lines(_load_registry(resolver.config_path), repo_root))
        if view == 'scheduler':
            lines.append(f'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH={container_config_path(repo_root, resolver.config_path)}')
            lines.extend(['', *build_internal_api_runtime_exports(repo_root, consumer='scheduler')])
        outputs[filename] = '\n'.join(lines) + '\n'
    if 'runtime_internal_api_env' in resolver.entries:
        filename = Path(resolver.resolve_entry('runtime_internal_api_env')['paths']['host']).name
        outputs[filename] = build_internal_api_env_output(repo_root, resolver)
    return outputs


def render_envs(repo_root: Path, resolver: PathResolver) -> None:
    targets = env_targets(resolver)
    for name, content in build_env_outputs(repo_root, resolver).items():
        write_text(targets[name], content)
