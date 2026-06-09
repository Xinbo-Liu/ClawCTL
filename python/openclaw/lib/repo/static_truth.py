#!/usr/bin/env python3
"""Read shared repo-tracked static truth surfaces."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from openclaw.control_plane.manifest_fields import (
    DISPATCH_PROVIDER_REGISTRY_PATHS_KEY,
    DISPATCH_TARGET_REGISTRY_PATHS_KEY,
)
from openclaw.control_plane.runtime.truth_surfaces import load_runtime_contract
from openclaw.lib.repo.contracts import (
    REPO_CONTRACTS,
    repo_contract,
    repo_contract_path,
    repo_contract_root,
    repo_contract_relpath,
)
from openclaw.lib.repo.install_defaults import (
    host_install_default,
    host_state_root_default,
    host_state_root_path,
    read_repo_contract_json,
)
from openclaw.lib.runtime.resolver_loader import build_path_resolver

ROOT_DIR = repo_contract_root()


RUNTIME_CONTRACT_PATH = repo_contract_path('runtime.runtime_contract')
IMAGE_PINS_PATH = repo_contract_path('image_pins.openclaw')
RUNTIME_PATHS_PATH = repo_contract_path('runtime.paths')
SERVICE_REGISTRY_PATH = repo_contract_path('runtime.service_registry')
HOST_INSTALL_DEFAULTS_PATH = repo_contract_path('governance.install_defaults')
GOVERNANCE_SUMMARY_MANIFEST_PATH = repo_contract_path('governance.summary_manifest')
GOVERNANCE_SETUP_ENTRYPOINTS_PATH = repo_contract_path('governance.setup_entrypoints')


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        values[key.strip()] = value.strip()
    return values


def read_repo_contract_env(contract_id: str, *, root_dir: Path = ROOT_DIR) -> dict[str, str]:
    contract = repo_contract(contract_id, root_dir=root_dir)
    if contract.format != 'env':
        raise ValueError(f'repo contract {contract_id} 不是 env：{contract.format}')
    return parse_env_file(repo_contract_path(contract_id, root_dir=root_dir))


def _current_official_gateway_tag(official_gateway_ref: str) -> str:
    match = re.search(r':([^:@]+)@sha256:', official_gateway_ref or '')
    return match.group(1) if match else ''


def runtime_contract_summary(root_dir: Path = ROOT_DIR) -> dict[str, Any]:
    contract = load_runtime_contract(
        repo_contract_path('runtime.runtime_contract', root_dir=root_dir),
        config_path=control_plane_service_config_path(root_dir),
    )
    pins = read_repo_contract_env('image_pins.openclaw', root_dir=root_dir)
    upstream = contract.get('upstream_release') or {}
    discovery = upstream.get('release_discovery') or {}
    repos = upstream.get('image_repositories') or {}
    model_runtime = contract.get('model_runtime') or {}
    provider = model_runtime.get('provider') or {}
    defaults = model_runtime.get('defaults') or {}
    official_gateway_ref = str(pins.get('OPENCLAW_OFFICIAL_GATEWAY_IMAGE') or '').strip()
    official_gateway_tag = _current_official_gateway_tag(official_gateway_ref)
    release_template = str(discovery.get('github_release_url_template') or '').strip()
    current_release_url = release_template.replace('{tag}', official_gateway_tag) if release_template and official_gateway_tag else ''
    return {
        'current_release_tag': official_gateway_tag,
        'current_release_url': current_release_url,
        'official_release_image_repo': str(repos.get('official_release_image_repo') or '').strip(),
        'default_official_gateway_image_repo': str(repos.get('default_official_gateway_image_repo') or '').strip(),
        'allowed_candidate_image_repos': list(repos.get('allowed_candidate_image_repos') or []),
        'official_gateway_ref': official_gateway_ref,
        'official_gateway_tag': _current_official_gateway_tag(official_gateway_ref),
        'provider_id': str(provider.get('id') or '').strip(),
        'primary_model': str(defaults.get('primary') or '').strip(),
        'pins': pins,
        'contract': contract,
    }


def host_state_child_default(*parts: str, root_dir: Path = ROOT_DIR) -> str:
    return str(host_state_root_path(root_dir).joinpath(*parts))


def runtime_paths_manifest(root_dir: Path = ROOT_DIR) -> dict[str, Any]:
    from openclaw.control_plane.surfaces import load_runtime_paths_manifest

    return load_runtime_paths_manifest(
        repo_contract_path('runtime.paths', root_dir=root_dir),
        config_path=control_plane_service_config_path(root_dir),
    )


def runtime_paths_host_entry(entry_id: str, root_dir: Path = ROOT_DIR) -> str:
    resolver = build_path_resolver(repo_root=root_dir)
    entry = resolver.resolve_entry(entry_id)
    paths = entry.get('paths') if isinstance(entry.get('paths'), dict) else {}
    host_path = paths.get('host')
    if host_path is None:
        raise KeyError(f'runtime_paths entry has no host path: {entry_id}')
    return str(host_path)


def host_state_root(root_dir: Path = ROOT_DIR) -> str:
    return runtime_paths_host_entry('state_root', root_dir)


def host_gateway_state_dir(root_dir: Path = ROOT_DIR) -> str:
    return runtime_paths_host_entry('gateway_host_state_dir', root_dir)


def host_control_plane_state_dir(root_dir: Path = ROOT_DIR) -> str:
    return runtime_paths_host_entry('control_plane_host_state_dir', root_dir)


def host_gateway_file(rel_path: str, root_dir: Path = ROOT_DIR) -> str:
    return str(Path(host_gateway_state_dir(root_dir)) / rel_path)


def host_control_plane_file(rel_path: str, root_dir: Path = ROOT_DIR) -> str:
    return str(Path(host_control_plane_state_dir(root_dir)) / rel_path)


def control_plane_service_config_path(root_dir: Path = ROOT_DIR) -> Path:
    from openclaw.lib.repo.layout import resolve_default_runtime_control_plane_service_config_path

    return resolve_default_runtime_control_plane_service_config_path(root_dir)


def _extension_registry_paths(root_dir: Path, *, key_plural: str) -> list[Path]:
    from openclaw.control_plane.config_loader import load_control_plane_service_payload
    from openclaw.control_plane.extensions.api import load_enabled_extensions

    config_path, payload = load_control_plane_service_payload(control_plane_service_config_path(root_dir))
    base_dir = config_path.parent
    result: list[Path] = []
    seen: set[str] = set()
    for extension in load_enabled_extensions(payload, service_base_dir=base_dir):
        extension_registry = extension.get('registry') if isinstance(extension.get('registry'), dict) else {}
        candidates = list(extension_registry.get(key_plural) or [])
        for candidate in candidates:
            if not isinstance(candidate, Path):
                continue
            resolved = candidate.resolve()
            marker = str(resolved)
            if marker in seen:
                continue
            seen.add(marker)
            result.append(resolved)
    return result


def dispatch_target_registry_paths(root_dir: Path = ROOT_DIR) -> list[Path]:
    result = _extension_registry_paths(root_dir, key_plural=DISPATCH_TARGET_REGISTRY_PATHS_KEY)
    if not result:
        raise ValueError(f'control plane active profile 未提供 {DISPATCH_TARGET_REGISTRY_PATHS_KEY}')
    return result


def dispatch_target_registry_path(root_dir: Path = ROOT_DIR) -> Path:
    paths = dispatch_target_registry_paths(root_dir)
    return paths[0]


def dispatch_provider_registry_paths(root_dir: Path = ROOT_DIR) -> list[Path]:
    result = _extension_registry_paths(root_dir, key_plural=DISPATCH_PROVIDER_REGISTRY_PATHS_KEY)
    if not result:
        raise ValueError(f'control plane active profile 未提供 {DISPATCH_PROVIDER_REGISTRY_PATHS_KEY}')
    return result


def dispatch_provider_registry_path(root_dir: Path = ROOT_DIR) -> Path:
    paths = dispatch_provider_registry_paths(root_dir)
    return paths[0]


def host_state_file(rel_path: str, root_dir: Path = ROOT_DIR) -> str:
    return str(Path(host_state_root(root_dir)) / rel_path)


def governance_summary_manifest(root_dir: Path = ROOT_DIR) -> dict[str, Any]:
    payload = read_repo_contract_json('governance.summary_manifest', root_dir=root_dir)
    if not isinstance(payload, dict):
        raise ValueError('governance.summary_manifest 顶层必须为对象')
    return payload


def governance_summary_manifest_profile(profile_id: str, root_dir: Path = ROOT_DIR) -> dict[str, Any]:
    payload = governance_summary_manifest(root_dir)
    return dict((payload.get('profiles') or {}).get(profile_id) or {})


def resolve_governance_path_ref(ref: str, *, root_dir: Path = ROOT_DIR) -> str:
    normalized = str(ref or '').strip()
    if not normalized:
        return ''
    if normalized.startswith('host_control_plane:'):
        return host_control_plane_file(normalized.split(':', 1)[1], root_dir)
    if normalized.startswith('host_state:'):
        return host_state_file(normalized.split(':', 1)[1], root_dir)
    if normalized.startswith('runtime_path:'):
        return runtime_paths_host_entry(normalized.split(':', 1)[1], root_dir)
    return normalized


def governance_default_path(key: str, *, profile_id: str = '', root_dir: Path = ROOT_DIR) -> str:
    payload = governance_summary_manifest(root_dir)
    profile = governance_summary_manifest_profile(profile_id, root_dir) if profile_id else {}
    return resolve_governance_path_ref(str(profile.get(key) or (payload.get('paths') or {}).get(key) or ''), root_dir=root_dir)


def governance_setup_entrypoints(root_dir: Path = ROOT_DIR) -> dict[str, Any]:
    payload = read_repo_contract_json('governance.setup_entrypoints', root_dir=root_dir)
    if not isinstance(payload, dict):
        raise ValueError('governance.setup_entrypoints 顶层必须为对象')
    return payload


def governance_setup_entrypoint(entry_id: str, root_dir: Path = ROOT_DIR) -> dict[str, Any]:
    payload = governance_setup_entrypoints(root_dir)
    return dict((payload.get('entrypoints') or {}).get(entry_id) or {})


def service_registry(root_dir: Path = ROOT_DIR, *, config_path: Path | None = None) -> dict[str, Any]:
    from openclaw.control_plane.surfaces import load_runtime_service_registry

    resolved_config = Path(config_path).resolve() if config_path is not None else control_plane_service_config_path(root_dir)
    return load_runtime_service_registry(
        repo_contract_path('runtime.service_registry', root_dir=root_dir),
        config_path=resolved_config,
    )


def service_registry_targets(root_dir: Path = ROOT_DIR, *, config_path: Path | None = None) -> list[dict[str, str]]:
    payload = service_registry(root_dir, config_path=config_path)
    rows = payload.get('targets') if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError('service_registry.targets 必须为数组')
    normalized: list[dict[str, str]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        target = str(item.get('target') or '').strip()
        service = str(item.get('service') or '').strip()
        container = str(item.get('container') or '').strip()
        purpose = str(item.get('purpose') or '').strip()
        if target and service and container:
            normalized.append({
                'target': target,
                'service': service,
                'container': container,
                'purpose': purpose,
            })
    return normalized


def service_registry_target_map(root_dir: Path = ROOT_DIR, *, config_path: Path | None = None) -> dict[str, dict[str, str]]:
    return {item['target']: item for item in service_registry_targets(root_dir, config_path=config_path)}


def _usage() -> str:
    return '\n'.join([
        '用法：',
        '  python -m openclaw.lib.repo.static_truth path --id <contract_id>',
        '  python -m openclaw.lib.repo.static_truth relpath --id <contract_id>',
        '',
    ])


def _fail_cli(message: str) -> int:
    sys.stderr.write(f'[static_truth][FAIL] {message}\n')
    sys.stderr.write(f'{_usage()}\n')
    return 2


def _parse_cli(argv: list[str]) -> tuple[str, str] | None:
    if not argv or argv[0] in {'-h', '--help'}:
        sys.stdout.write(_usage())
        return None
    command = argv[0]
    if command not in {'path', 'relpath'}:
        raise SystemExit(_fail_cli(f'未知命令：{command}'))
    contract_id = ''
    index = 1
    while index < len(argv):
        arg = argv[index]
        if arg in {'-h', '--help'}:
            sys.stdout.write(_usage())
            return None
        if arg == '--id':
            index += 1
            if index >= len(argv):
                raise SystemExit(_fail_cli('--id 缺少参数值'))
            contract_id = argv[index].strip()
        elif arg.startswith('--id='):
            contract_id = arg.split('=', 1)[1].strip()
        else:
            raise SystemExit(_fail_cli(f'未知参数：{arg}'))
        index += 1
    if not contract_id:
        raise SystemExit(_fail_cli('缺少 --id <contract_id>'))
    return command, contract_id


def main(argv: list[str] | None = None) -> int:
    parsed = _parse_cli(list(sys.argv[1:] if argv is None else argv))
    if parsed is None:
        return 0
    command, contract_id = parsed
    try:
        if command == 'path':
            sys.stdout.write(str(repo_contract_path(contract_id)) + '\n')
            return 0
        sys.stdout.write(repo_contract_relpath(contract_id) + '\n')
        return 0
    except Exception as exc:
        return _fail_cli(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
