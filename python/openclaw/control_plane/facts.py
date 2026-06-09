#!/usr/bin/env python3
"""维护事实总览的只读控制面入口。"""
from __future__ import annotations

import argparse
from copy import deepcopy
from functools import lru_cache
import json
import re
import sys
from pathlib import Path
from typing import Any

from openclaw.lib.control_plane import object_families
from openclaw.lib.repo.contracts import (
    CONTRACTS_TRUTH_REL_PATH,
    repo_contract_path,
    repo_contracts,
)
from openclaw.lib.repo.layout import (
    DEFAULT_RUNTIME_CONTROL_PLANE_PROFILE_ID,
    CONTROL_PLANE_REPO_COMBINATION_PROFILES_REL_PATH,
    control_plane_profile_registry_path,
    control_plane_profile_status_rows,
    resolve_repo_root,
    resolve_selected_control_plane_config_path,
    resolve_selected_control_plane_profile_id,
)
from openclaw.lib.repo.managed_extensions import load_managed_extensions_index
from openclaw.lib.repo.static_truth import service_registry_targets
from openclaw.lib.repo.verification_tiers import verification_tier_rows
from openclaw.lib.runtime.resolver_loader import require_path_resolver

SCHEMA_VERSION = 1
ROOT_DIR = resolve_repo_root(Path(__file__))
MAINTENANCE_MAP_DOC = 'docs/operations/maintenance-map.md'
FACTS_OVERVIEW_COMMAND = 'bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane facts overview'
SECRET_KEY_PATTERN = re.compile(
    r'(TOKEN|SECRET|PASSWORD|PASSWD|PASS|WEBHOOK|HOOK|SIGN|SIGNATURE|CREDENTIAL|AUTH|API[_-]?KEY|PRIVATE)',
    re.IGNORECASE,
)
RUNTIME_EVIDENCE_FAMILIES = {'acceptance_state', 'runtime_evidence', 'flow_summary_state'}
KEY_TRUTH_SURFACE_IDS = {
    'runtime.paths',
    'runtime.service_registry',
    'runtime.testing_manifest',
    'runtime.runtime_contract',
    'deploy_env.schema',
    'control_plane.object_families',
    'governance.docs_registry',
    'governance.script_catalog_surface',
    'governance.summary_manifest',
    'governance.absent_surfaces',
    'governance.local_workspace_policy',
    'governance.verification_tiers',
}


class FactsOverviewError(RuntimeError):
    """维护事实总览无法构建时抛出的错误。"""


def _cache_path_key(path: Path) -> str:
    return str(Path(path).resolve())


def _repo_relative(path: Path, *, root_dir: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(root_dir.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _path_row(path: Path, *, root_dir: Path, label: str = '') -> dict[str, Any]:
    resolved = Path(path).resolve()
    return {
        'label': label,
        'path': str(resolved),
        'relpath': _repo_relative(resolved, root_dir=root_dir),
        'exists': resolved.exists(),
        'is_file': resolved.is_file(),
        'is_dir': resolved.is_dir(),
    }


def _resolve_host_path(value: str, *, root_dir: Path) -> Path:
    path = Path(str(value or '').strip())
    return path if path.is_absolute() else (root_dir / path).resolve()


def _display_path(value: str, *, root_dir: Path) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    path = Path(text)
    if not path.is_absolute():
        return path.as_posix()
    return _repo_relative(path, root_dir=root_dir)


def _private_env_candidate(path: Path, *, root_dir: Path) -> bool:
    try:
        relpath = path.resolve().relative_to(root_dir.resolve()).as_posix()
    except ValueError:
        return True
    return (
        relpath in {'deploy/.env', 'deploy/site.env'}
        or (relpath.startswith('deploy/targets.d/') and relpath.endswith('.env'))
        or relpath.startswith('state/')
        or relpath.startswith('tmp/')
        or relpath.startswith('artifacts/')
        or relpath.startswith('release/history/')
    )


def _sensitive_key(name: str) -> bool:
    return bool(SECRET_KEY_PATTERN.search(str(name or '')))


def _parse_env_keys(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    seen: set[str] = set()
    for line_no, raw_line in enumerate(path.read_text(encoding='utf-8-sig', errors='replace').splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        name = key.strip()
        if not name:
            continue
        rows.append(
            {
                'name': name,
                'line': line_no,
                'present': True,
                'value_present': bool(value.strip().strip('"').strip("'")),
                'sensitive': _sensitive_key(name),
                'duplicate': name in seen,
            }
        )
        seen.add(name)
    return rows


def _load_selected_config(
    *,
    root_dir: Path,
    config_path: str | Path | None,
    control_plane_profile: str | None,
) -> tuple[Path, str]:
    try:
        selected_path = resolve_selected_control_plane_config_path(
            config_path,
            control_plane_profile=control_plane_profile,
            start_path=root_dir,
            default_profile=DEFAULT_RUNTIME_CONTROL_PLANE_PROFILE_ID,
        )
        profile_id = resolve_selected_control_plane_profile_id(
            selected_path,
            start_path=root_dir,
            default_profile=DEFAULT_RUNTIME_CONTROL_PLANE_PROFILE_ID,
        )
    except ValueError as exc:
        raise FactsOverviewError(str(exc)) from exc
    return selected_path.resolve(), profile_id or 'custom'


def _selected_config_payload(
    *,
    root_dir: Path,
    selected_path: Path,
    profile_id: str,
    requested_config_path: str | Path | None,
    requested_profile: str | None,
) -> dict[str, Any]:
    return {
        'profile_id': profile_id,
        'config_path': str(selected_path),
        'config_relpath': _repo_relative(selected_path, root_dir=root_dir),
        'exists': selected_path.is_file(),
        'requested_config_path': str(requested_config_path or '').strip(),
        'requested_profile': str(requested_profile or '').strip(),
    }


@lru_cache(maxsize=16)
def _profile_payload_cached(root_dir_key: str) -> dict[str, Any]:
    root_dir = Path(root_dir_key)
    rows = list(control_plane_profile_status_rows(root_dir, allow_env_override=False))
    registry_path = control_plane_profile_registry_path(root_dir, allow_env_override=False)
    return {
        'registry': _path_row(registry_path, root_dir=root_dir),
        'items': rows,
        'counts': {
            'total': len(rows),
            'valid': sum(1 for row in rows if row.get('status') == 'valid'),
            'invalid': sum(1 for row in rows if row.get('status') == 'invalid'),
            'registry': sum(1 for row in rows if row.get('source') == 'registry'),
            'discovered': sum(1 for row in rows if row.get('source') == 'discovered'),
        },
    }


def _profile_payload(root_dir: Path) -> dict[str, Any]:
    return deepcopy(_profile_payload_cached(_cache_path_key(root_dir)))


def _extension_registry_summary(extension: dict[str, Any], *, root_dir: Path) -> dict[str, Any]:
    registry = extension.get('registry') if isinstance(extension.get('registry'), dict) else {}
    rows: dict[str, list[dict[str, Any]]] = {}
    for key, value in registry.items():
        if not isinstance(value, list):
            continue
        path_rows = [_path_row(item, root_dir=root_dir) for item in value if isinstance(item, Path)]
        if path_rows:
            rows[str(key)] = path_rows
    return rows


def _extensions_payload(context: dict[str, Any], *, root_dir: Path) -> dict[str, Any]:
    managed = load_managed_extensions_index(root_dir)
    enabled_rows: list[dict[str, Any]] = []
    for extension in context.get('extensions') or []:
        if not isinstance(extension, dict):
            continue
        enabled_rows.append(
            {
                'id': str(extension.get('id') or '').strip(),
                'title': str(extension.get('title') or '').strip(),
                'version': str(extension.get('version') or '').strip(),
                'registry': _extension_registry_summary(extension, root_dir=root_dir),
                'surface_fragments': sorted((extension.get('surfaceFragments') or {}).keys())
                if isinstance(extension.get('surfaceFragments'), dict)
                else [],
                'governance_surfaces': sorted((extension.get('governanceSurfaces') or {}).keys())
                if isinstance(extension.get('governanceSurfaces'), dict)
                else [],
            }
        )
    return {
        'enabled_extension_ids': list(context.get('enabledExtensionIds') or []),
        'known_extension_ids': list(context.get('knownExtensionIds') or []),
        'enabled': enabled_rows,
        'registry_inputs': _registry_inputs_payload(context, root_dir=root_dir),
        'managed_explicit': [
            {
                'id': row.id,
                'title': row.title,
                'root_dir': _repo_relative(row.root_dir, root_dir=root_dir),
                'default_service_config_path': _repo_relative(row.default_service_config_path, root_dir=root_dir),
                'manifest_dir': _repo_relative(row.manifest_dir, root_dir=root_dir),
                'status': row.status,
            }
            for row in managed
        ],
    }


def _registry_inputs_payload(context: dict[str, Any], *, root_dir: Path) -> dict[str, list[dict[str, Any]]]:
    registry_inputs = context.get('registryInputs') if isinstance(context.get('registryInputs'), dict) else {}
    result: dict[str, list[dict[str, Any]]] = {}
    for key in (
        'jobs_dirs',
        'models_dirs',
        'targets_dirs',
        'agent_groups_dirs',
        'agent_modules_dirs',
        'runtime_adapter_registry_paths',
        'dispatch_target_registry_paths',
        'dispatch_provider_registry_paths',
    ):
        values = registry_inputs.get(key) if isinstance(registry_inputs.get(key), list) else []
        result[key] = [_path_row(item, root_dir=root_dir) for item in values if isinstance(item, Path)]
    return result


@lru_cache(maxsize=16)
def _truth_surfaces_payload_cached(root_dir_key: str) -> list[dict[str, Any]]:
    root_dir = Path(root_dir_key)
    rows: list[dict[str, Any]] = [
        {
            'id': 'governance.repo_contracts',
            'kind': 'repo_contract_index',
            'format': 'json',
            **_path_row(root_dir / CONTRACTS_TRUTH_REL_PATH, root_dir=root_dir),
            'key_surface': True,
        },
        {
            'id': 'control_plane.profile_registry',
            'kind': 'profile_registry',
            'format': 'tsv',
            **_path_row(control_plane_profile_registry_path(root_dir, allow_env_override=False), root_dir=root_dir),
            'key_surface': True,
        },
        {
            'id': 'control_plane.repo_combination_profiles',
            'kind': 'repo_combination_profile_registry',
            'format': 'json',
            **_path_row(root_dir / CONTROL_PLANE_REPO_COMBINATION_PROFILES_REL_PATH, root_dir=root_dir),
            'key_surface': True,
        },
        {
            'id': 'agent.extensions.index',
            'kind': 'managed_extension_index',
            'format': 'json',
            **_path_row(root_dir / 'agent' / 'extensions' / 'index.json', root_dir=root_dir),
            'key_surface': True,
        },
    ]
    for contract in repo_contracts(root_dir).values():
        path = repo_contract_path(contract.id, root_dir=root_dir)
        rows.append(
            {
                'id': contract.id,
                'kind': 'repo_contract',
                'format': contract.format,
                **_path_row(path, root_dir=root_dir),
                'key_surface': contract.id in KEY_TRUTH_SURFACE_IDS,
            }
        )
    return rows


def _truth_surfaces_payload(root_dir: Path) -> list[dict[str, Any]]:
    return deepcopy(_truth_surfaces_payload_cached(_cache_path_key(root_dir)))


@lru_cache(maxsize=16)
def _generated_artifacts_payload_cached(root_dir_key: str) -> list[dict[str, Any]]:
    root_dir = Path(root_dir_key)
    rows: list[dict[str, Any]] = []
    for path in sorted(root_dir.rglob('*.json')):
        relpath = _repo_relative(path, root_dir=root_dir)
        if relpath.startswith(('.git/', 'state/', 'tmp/', 'artifacts/', 'release/history/')):
            continue
        try:
            payload = json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        generated = payload.get('generated_artifacts') if isinstance(payload, dict) else None
        if not isinstance(generated, dict):
            continue
        rows.append(
            {
                'source': relpath,
                'artifact_count': len(generated),
                'artifacts': [
                    {
                        'id': str(key),
                        'path': str(value),
                    }
                    for key, value in sorted(generated.items())
                ],
            }
        )
    return rows


def _generated_artifacts_payload(root_dir: Path) -> list[dict[str, Any]]:
    return deepcopy(_generated_artifacts_payload_cached(_cache_path_key(root_dir)))


@lru_cache(maxsize=16)
def _scripts_payload_cached(root_dir_key: str) -> dict[str, Any]:
    root_dir = Path(root_dir_key)
    path = repo_contract_path('governance.script_catalog_surface', root_dir=root_dir)
    payload = json.loads(path.read_text(encoding='utf-8'))
    groups = []
    for group in payload.get('groups') or []:
        if not isinstance(group, dict):
            continue
        files = group.get('files') if isinstance(group.get('files'), list) else []
        groups.append(
            {
                'id': str(group.get('id') or '').strip(),
                'title': str(group.get('title') or '').strip(),
                'purpose': str(group.get('purpose') or '').strip(),
                'file_count': len(files),
            }
        )
    return {
        'source': _repo_relative(path, root_dir=root_dir),
        'generated_artifacts': dict(payload.get('generated_artifacts') or {}),
        'groups': groups,
    }


def _scripts_payload(root_dir: Path) -> dict[str, Any]:
    return deepcopy(_scripts_payload_cached(_cache_path_key(root_dir)))


@lru_cache(maxsize=32)
def _runtime_services_payload_cached(root_dir_key: str, config_path_key: str) -> list[dict[str, str]]:
    root_dir = Path(root_dir_key)
    config_path = Path(config_path_key)
    return service_registry_targets(root_dir, config_path=config_path)


def _runtime_services_payload(root_dir: Path, *, config_path: Path) -> list[dict[str, str]]:
    return deepcopy(_runtime_services_payload_cached(_cache_path_key(root_dir), _cache_path_key(config_path)))


@lru_cache(maxsize=32)
def _evidence_payload_cached(root_dir_key: str, config_path_key: str) -> list[dict[str, Any]]:
    root_dir = Path(root_dir_key)
    config_path = Path(config_path_key)
    rows: list[dict[str, Any]] = []
    for family in object_families.all_families(root_dir, config_path=config_path):
        family_id = str(family.get('id') or '').strip()
        entries = []
        for entry in family.get('entries') or []:
            if not isinstance(entry, dict):
                continue
            entries.append(
                {
                    'id': str(entry.get('id') or '').strip(),
                    'title': str(entry.get('title') or '').strip(),
                    'path_kind': str(entry.get('path_kind') or '').strip(),
                    'path_ref': str(entry.get('path_ref') or '').strip(),
                    'resolved_path': str(entry.get('resolved_path') or '').strip(),
                    'display_path': _display_path(str(entry.get('resolved_path') or '').strip(), root_dir=root_dir),
                    'producer': str(entry.get('producer') or '').strip(),
                    'usage': str(entry.get('usage') or '').strip(),
                }
            )
        rows.append(
            {
                'id': family_id,
                'label': str(family.get('label') or '').strip(),
                'purpose': str(family.get('purpose') or '').strip(),
                'runtime_evidence_family': family_id in RUNTIME_EVIDENCE_FAMILIES,
                'entries': entries,
            }
        )
    return rows


def _evidence_payload(root_dir: Path, *, config_path: Path) -> list[dict[str, Any]]:
    return deepcopy(_evidence_payload_cached(_cache_path_key(root_dir), _cache_path_key(config_path)))


@lru_cache(maxsize=32)
def _runtime_evidence_path_count_cached(root_dir_key: str, config_path_key: str) -> int:
    config_path = Path(config_path_key)
    contract = object_families.load_contract(config_path=config_path)
    total = 0
    for family in contract.get('families') or []:
        if not isinstance(family, dict):
            continue
        if str(family.get('id') or '').strip() not in RUNTIME_EVIDENCE_FAMILIES:
            continue
        total += sum(1 for entry in family.get('entries') or [] if isinstance(entry, dict))
    return total


def _runtime_evidence_path_count(root_dir: Path, *, config_path: Path) -> int:
    return _runtime_evidence_path_count_cached(_cache_path_key(root_dir), _cache_path_key(config_path))


def _runtime_evidence_path_items(evidence_rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for family in evidence_rows:
        if not family.get('runtime_evidence_family'):
            continue
        for entry in family.get('entries') or []:
            if not isinstance(entry, dict):
                continue
            rows.append(
                {
                    'family': str(family.get('id') or '').strip(),
                    'id': str(entry.get('id') or '').strip(),
                    'path': str(entry.get('display_path') or entry.get('resolved_path') or '').strip(),
                }
            )
    return rows


def _registry_input_counts(registry_inputs: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    return {
        key: len(value) if isinstance(value, list) else 0
        for key, value in sorted(registry_inputs.items())
    }


@lru_cache(maxsize=16)
def _profile_overviews_payload_cached(
    root_dir_key: str,
    include_runtime_services: bool,
    include_evidence_paths: bool,
) -> list[dict[str, Any]]:
    root_dir = Path(root_dir_key)
    rows: list[dict[str, Any]] = []
    for profile_row in _profile_payload(root_dir)['items']:
        relpath = str(profile_row.get('configPath') or profile_row.get('path') or '').strip()
        profile_id = str(profile_row.get('id') or '').strip()
        row: dict[str, Any] = {
            'id': profile_id,
            'config_relpath': relpath,
            'source': str(profile_row.get('source') or '').strip(),
            'status': str(profile_row.get('status') or '').strip(),
            'issues': list(profile_row.get('issues') or []),
            'default_profile': profile_id == DEFAULT_RUNTIME_CONTROL_PLANE_PROFILE_ID,
        }
        if row['status'] != 'valid' or not relpath:
            rows.append(row)
            continue
        config_path = (root_dir / relpath).resolve()
        try:
            context = _load_registry_context(config_path)
            registry_inputs = _registry_inputs_payload(context, root_dir=root_dir)
            row.update(
                {
                    'enabled_extension_ids': list(context.get('enabledExtensionIds') or []),
                    'known_extension_ids': list(context.get('knownExtensionIds') or []),
                    'registry_inputs': registry_inputs,
                    'registry_input_counts': _registry_input_counts(registry_inputs),
                }
            )
            if include_runtime_services:
                row['runtime_services'] = _runtime_services_payload(root_dir, config_path=config_path)
            if include_evidence_paths:
                evidence_rows = _evidence_payload(root_dir, config_path=config_path)
                row['evidence_paths'] = _runtime_evidence_path_items(evidence_rows)
                row['evidence_path_count'] = len(row['evidence_paths'])
            else:
                row['evidence_path_count'] = _runtime_evidence_path_count(root_dir, config_path=config_path)
        except Exception as exc:  # pragma: no cover - defensive payload keeps facts output readable.
            row['status'] = 'invalid'
            row['issues'] = [*row.get('issues', []), f'facts overview load failed: {exc}']
        rows.append(row)
    return rows


def _profile_overviews_payload(
    root_dir: Path,
    *,
    include_runtime_services: bool = True,
    include_evidence_paths: bool = True,
) -> list[dict[str, Any]]:
    return deepcopy(
        _profile_overviews_payload_cached(
            _cache_path_key(root_dir),
            bool(include_runtime_services),
            bool(include_evidence_paths),
        )
    )


def _verification_commands_payload() -> list[dict[str, Any]]:
    return verification_tier_rows(ROOT_DIR)


def _local_environment_payload(
    *,
    root_dir: Path,
    config_path: Path,
    env_file: str | Path | None,
    probe_local: bool,
) -> dict[str, Any]:
    resolver = require_path_resolver(repo_root=root_dir, config_path=config_path)
    state_root = _resolve_host_path(resolver.resolve_path('state_root', 'host'), root_dir=root_dir)
    gateway_root = _resolve_host_path(resolver.resolve_path('gateway_host_state_dir', 'host'), root_dir=root_dir)
    control_plane_root = _resolve_host_path(
        resolver.resolve_path('control_plane_host_state_dir', 'host'),
        root_dir=root_dir,
    )
    default_env_file = root_dir / 'deploy' / '.env'
    selected_env_file = Path(env_file).resolve() if env_file is not None else default_env_file

    payload: dict[str, Any] = {
        'probed': bool(probe_local),
        'default_env_file': _repo_relative(default_env_file, root_dir=root_dir),
        'state_root': _repo_relative(state_root, root_dir=root_dir),
        'gateway_state_root': _repo_relative(gateway_root, root_dir=root_dir),
        'control_plane_state_root': _repo_relative(control_plane_root, root_dir=root_dir),
    }
    if not probe_local:
        payload['note'] = 'generated documentation render does not read local deploy env or runtime state'
        return payload

    env_keys = _parse_env_keys(selected_env_file)
    payload.update(
        {
            'deploy_env': {
                **_path_row(default_env_file, root_dir=root_dir),
                'private_gitignored': _private_env_candidate(default_env_file, root_dir=root_dir),
            },
            'selected_env_file': {
                **_path_row(selected_env_file, root_dir=root_dir),
                'private_gitignored': _private_env_candidate(selected_env_file, root_dir=root_dir),
                'key_count': len(env_keys),
                'sensitive_key_count': sum(1 for row in env_keys if row['sensitive']),
                'keys': env_keys,
            },
            'state_paths': {
                'state_root': {**_path_row(state_root, root_dir=root_dir), 'private_gitignored': True},
                'gateway_state_root': {**_path_row(gateway_root, root_dir=root_dir), 'private_gitignored': True},
                'control_plane_state_root': {
                    **_path_row(control_plane_root, root_dir=root_dir),
                    'private_gitignored': True,
                },
            },
        }
    )
    return payload


def build_overview_payload(
    *,
    config_path: str | Path | None = None,
    control_plane_profile: str | None = None,
    env_file: str | Path | None = None,
    probe_local: bool = True,
    include_all_profiles: bool = False,
    include_profile_runtime_services: bool = True,
    include_profile_evidence_paths: bool = True,
    root_dir: Path = ROOT_DIR,
) -> dict[str, Any]:
    """构建维护事实总览 JSON。"""
    repo_root = Path(root_dir).resolve()
    selected_path, profile_id = _load_selected_config(
        root_dir=repo_root,
        config_path=config_path,
        control_plane_profile=control_plane_profile,
    )
    context = _load_registry_context(selected_path)
    payload: dict[str, Any] = {
        'schema_version': SCHEMA_VERSION,
        'selected_config': _selected_config_payload(
            root_dir=repo_root,
            selected_path=selected_path,
            profile_id=profile_id,
            requested_config_path=config_path,
            requested_profile=control_plane_profile,
        ),
        'profiles': _profile_payload(repo_root),
        'extensions': _extensions_payload(context, root_dir=repo_root),
        'truth_surfaces': _truth_surfaces_payload(repo_root),
        'generated_artifacts': _generated_artifacts_payload(repo_root),
        'scripts': _scripts_payload(repo_root),
        'runtime_services': _runtime_services_payload(repo_root, config_path=selected_path),
        'evidence': _evidence_payload(repo_root, config_path=selected_path),
        'local_environment': _local_environment_payload(
            root_dir=repo_root,
            config_path=selected_path,
            env_file=env_file,
            probe_local=probe_local,
        ),
    }
    if include_all_profiles:
        payload['profile_overviews'] = _profile_overviews_payload(
            repo_root,
            include_runtime_services=include_profile_runtime_services,
            include_evidence_paths=include_profile_evidence_paths,
        )
        payload['verification_commands'] = _verification_commands_payload()
    return payload


@lru_cache(maxsize=32)
def _load_registry_context_cached(config_path_key: str) -> dict[str, Any]:
    config_path = Path(config_path_key)
    try:
        from openclaw.control_plane.registry_loader.config import load_registry_service_context

        return load_registry_service_context(config_path)
    except Exception as exc:
        raise FactsOverviewError(f'无法加载 control-plane registry context：{exc}') from exc


def _load_registry_context(config_path: Path) -> dict[str, Any]:
    return deepcopy(_load_registry_context_cached(_cache_path_key(config_path)))


def _cell(value: Any) -> str:
    text = ' '.join(str(value if value is not None else '').split())
    return text.replace('|', r'\|') or '-'


def _markdown_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    rendered_rows = [[_cell(item) for item in row] for row in rows]
    widths = [
        max(len(_cell(header)), *(len(row[index]) for row in rendered_rows)) if rendered_rows else len(_cell(header))
        for index, header in enumerate(headers)
    ]

    def render(values: list[Any]) -> str:
        cells = [_cell(value).ljust(widths[index]) for index, value in enumerate(values)]
        return '| ' + ' | '.join(cells) + ' |'

    separator = '|' + '|'.join('-' * (width + 2) for width in widths) + '|'
    return [render(headers), separator, *(render(row) for row in rendered_rows), '']


def _key_truth_surfaces(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in payload['truth_surfaces'] if row.get('key_surface')]


def _managed_extension_aliases(payload: dict[str, Any]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    managed_rows = payload.get('extensions', {}).get('managed_explicit') or []
    for index, row in enumerate(managed_rows, start=1):
        if not isinstance(row, dict):
            continue
        alias = f'managed-extension-{index}'
        for value in (
            row.get('id'),
            Path(str(row.get('root_dir') or '')).name,
        ):
            token = str(value or '').strip()
            if token:
                aliases[token] = alias
            if token.startswith('agent_'):
                aliases[token.removeprefix('agent_')] = alias
    return aliases


def _redact_managed_text(value: Any, aliases: dict[str, str]) -> str:
    text = str(value or '')
    for token, alias in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
        text = text.replace(token, alias)
    return text


def _redact_managed_path_label(value: Any, aliases: dict[str, str], *, label: str) -> str:
    text = str(value or '')
    for token, alias in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
        if token in text:
            return f'{alias} {label}'
    return text


def _registry_input_rows(
    payload: dict[str, Any],
    *,
    redact_managed_extensions: bool = False,
) -> list[list[Any]]:
    aliases = _managed_extension_aliases(payload) if redact_managed_extensions else {}
    registry_inputs = payload['extensions'].get('registry_inputs') or {}
    rows: list[list[Any]] = []
    for key in sorted(registry_inputs):
        paths = registry_inputs[key] if isinstance(registry_inputs[key], list) else []
        rendered_paths = [
            _redact_managed_path_label(row['relpath'], aliases, label='registry input')
            for row in paths
            if isinstance(row, dict)
        ]
        rows.append([key, len(paths), ', '.join(rendered_paths) or '-'])
    return rows


def _runtime_evidence_rows(payload: dict[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    for family in payload['evidence']:
        if not family.get('runtime_evidence_family'):
            continue
        for entry in family.get('entries') or []:
            rows.append(
                [
                    str(family.get('id') or ''),
                    str(entry.get('id') or ''),
                    str(entry.get('display_path') or entry.get('resolved_path') or ''),
                ]
            )
    return rows


def _profile_overview_rows(
    payload: dict[str, Any],
    *,
    redact_managed_extensions: bool = False,
) -> list[list[Any]]:
    aliases = _managed_extension_aliases(payload) if redact_managed_extensions else {}
    rows: list[list[Any]] = []
    for row in payload.get('profile_overviews') or []:
        if not isinstance(row, dict):
            continue
        raw_id = str(row.get('id') or '').strip()
        profile_label = _redact_managed_text(raw_id, aliases)
        if redact_managed_extensions:
            config_label = _redact_managed_path_label(row.get('config_relpath') or '-', aliases, label='profile config')
        else:
            config_label = str(row.get('config_relpath') or '-')
        registry_counts = row.get('registry_input_counts') if isinstance(row.get('registry_input_counts'), dict) else {}
        active_counts = [
            f'{key}={value}'
            for key, value in sorted(registry_counts.items())
            if int(value or 0) > 0
        ]
        rows.append(
            [
                f"{profile_label}{' (default)' if row.get('default_profile') else ''}",
                config_label,
                ', '.join(
                    _redact_managed_text(item, aliases)
                    for item in (row.get('enabled_extension_ids') or [])
                ) or '-',
                ', '.join(active_counts) or '-',
                int(row.get('evidence_path_count') or len(row.get('evidence_paths') or [])),
            ]
        )
    return rows


def _verification_command_rows(payload: dict[str, Any]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for group in payload.get('verification_commands') or []:
        if not isinstance(group, dict):
            continue
        commands = [str(item) for item in group.get('commands') or [] if str(item).strip()]
        status = '正式门禁' if group.get('release_required') else '诊断补充'
        description = str(group.get('description') or '').strip()
        title = f"{group.get('title') or group.get('id')}<br>{status}"
        if description:
            title = f'{title}<br>{description}'
        rows.append([title, '<br>'.join(commands)])
    return rows


def _known_extensions_label(payload: dict[str, Any]) -> str:
    extensions = payload['extensions']
    known_ids = [str(item) for item in extensions.get('known_extension_ids') or [] if str(item).strip()]
    managed_ids = {
        str(row.get('id') or '').strip()
        for row in extensions.get('managed_explicit') or []
        if isinstance(row, dict)
    }
    visible = [item for item in known_ids if item and item not in managed_ids]
    managed_count = sum(1 for item in known_ids if item in managed_ids)
    parts: list[str] = []
    if visible:
        parts.append(', '.join(visible))
    if managed_count:
        parts.append(f'{managed_count} 个仓内显式 extension')
    return ' + '.join(parts) or '-'


def render_overview_markdown(payload: dict[str, Any], *, redact_managed_extensions: bool = False) -> str:
    """把 facts overview JSON 渲染为维护者入口页。"""
    selected = payload['selected_config']
    local = payload['local_environment']
    aliases = _managed_extension_aliases(payload) if redact_managed_extensions else {}
    selected_profile_id = str(selected['profile_id'] or '').strip()
    selected_profile = _redact_managed_text(selected_profile_id, aliases)
    selected_config = (
        _redact_managed_path_label(selected['config_relpath'], aliases, label='selected profile config')
        if redact_managed_extensions
        else str(selected['config_relpath'])
    )
    enabled_extensions = [
        _redact_managed_text(item, aliases)
        for item in payload['extensions']['enabled_extension_ids']
    ]
    lines: list[str] = [
        '# OpenClaw 维护事实总览',
        '',
        '本页由 `control-plane facts overview` 的只读事实汇总生成，用于定位配置真源、生成文档、运行服务与证据路径。',
        '',
        '## 统一入口',
        '',
        '```bash',
        FACTS_OVERVIEW_COMMAND,
        f'{FACTS_OVERVIEW_COMMAND} --format json',
        f'{FACTS_OVERVIEW_COMMAND} --all-profiles --format json',
        f'{FACTS_OVERVIEW_COMMAND} --control-plane-profile <profile-id> --format markdown',
        f'{FACTS_OVERVIEW_COMMAND} --env-file deploy/.env --format json',
        '```',
        '',
        '## 当前控制面',
        '',
        *_markdown_table(
            ['项目', '值'],
            [
                ['profile', selected_profile],
                ['config', selected_config],
                ['enabled extensions', ', '.join(enabled_extensions) or '-'],
                ['known extensions', _known_extensions_label(payload)],
            ],
        ),
        '## Registry 输入',
        '',
        *_markdown_table(
            ['类别', '数量', '路径'],
            _registry_input_rows(payload, redact_managed_extensions=redact_managed_extensions),
        ),
    ]
    if payload.get('profile_overviews'):
        lines.extend(
            [
                '## 默认 profile 与 extension profile',
                '',
                *_markdown_table(
                    ['profile', 'config', 'enabled extensions', 'registry inputs', 'evidence paths'],
                    _profile_overview_rows(payload, redact_managed_extensions=redact_managed_extensions),
                ),
                '## 真源 / 派生 / Evidence 改动顺序',
                '',
                '- 配置、注册表、脚本清单和 governance surface 是真源；先改真源，再让 Python loader / renderer 消费。',
                '- 生成文档只由对应 renderer 重写，不把派生 Markdown 作为独立维护面。',
                '- 运行态 evidence 只由部署、full test、scheduler 或 evidence export 入口产生，不写入仓库真源。',
                '',
                '## 正式验证路径',
                '',
                *_markdown_table(
                    ['阶段', '命令'],
                    _verification_command_rows(payload),
                ),
            ]
        )
    lines.extend(
        [
        '## 关键真源',
        '',
        *_markdown_table(
            ['真源', '路径', '格式'],
            [
                [row['id'], row['relpath'], row.get('format') or row.get('kind')]
                for row in _key_truth_surfaces(payload)
            ],
        ),
        '## 生成文档',
        '',
        *_markdown_table(
            ['真源', '生成项', '目标'],
            [
                [row['source'], artifact['id'], artifact['path']]
                for row in payload['generated_artifacts']
                for artifact in row.get('artifacts') or []
            ],
        ),
        '## 脚本分组',
        '',
        *_markdown_table(
            ['分组', '文件数', '职责'],
            [[row['id'], row['file_count'], row['purpose']] for row in payload['scripts']['groups']],
        ),
        '## 运行服务',
        '',
        *_markdown_table(
            ['target', 'service', 'container'],
            [
                [row.get('target'), row.get('service'), row.get('container')]
                for row in payload['runtime_services']
            ],
        ),
        '## 证据路径',
        '',
        *_markdown_table(
            ['对象族', '条目', '路径'],
            _runtime_evidence_rows(payload),
        ),
        '## 本地现场',
        '',
        ]
    )
    if not local.get('probed'):
        lines.extend(
            [
                '生成文档不读取 `deploy/.env` 或运行态 state。需要查看当前机器现场时执行 facts overview，并显式追加 `--env-file deploy/.env`。',
                '',
            ]
        )
    else:
        env_info = local.get('selected_env_file') if isinstance(local.get('selected_env_file'), dict) else {}
        lines.extend(
            _markdown_table(
                ['项目', '状态'],
                [
                    ['env file', f"{env_info.get('relpath') or env_info.get('path')} exists={env_info.get('exists')}"],
                    ['env keys', env_info.get('key_count', 0)],
                    ['sensitive key names', env_info.get('sensitive_key_count', 0)],
                    [
                        'state root',
                        (
                            f"{local['state_paths']['state_root']['relpath']} "
                            f"exists={local['state_paths']['state_root']['exists']}"
                        ),
                    ],
                ],
            )
        )
    return '\n'.join(lines).rstrip() + '\n'


def build_parser() -> argparse.ArgumentParser:
    """构造 facts overview CLI 参数；支持单 profile、全 profile、JSON 和 Markdown 输出。"""
    parser = argparse.ArgumentParser(prog='control-plane facts overview')
    parser.add_argument('--config-path', default=None)
    parser.add_argument('--control-plane-profile', default=None)
    parser.add_argument('--format', choices=('json', 'markdown'), default='markdown')
    parser.add_argument('--env-file', default=None)
    parser.add_argument('--repo-root', default=None)
    parser.add_argument('--no-local-probe', action='store_true')
    parser.add_argument('--all-profiles', action='store_true')
    return parser


def overview_entry(argv: list[str] | None = None) -> int:
    """执行 facts overview 子命令，读取控制面事实并输出机器 JSON 或人类 Markdown。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ['overview']:
        args = args[1:]
    parser = build_parser()
    parsed = parser.parse_args(args)
    root_dir = Path(parsed.repo_root).resolve() if parsed.repo_root else ROOT_DIR
    try:
        payload = build_overview_payload(
            config_path=parsed.config_path,
            control_plane_profile=parsed.control_plane_profile,
            env_file=parsed.env_file,
            probe_local=not parsed.no_local_probe,
            include_all_profiles=parsed.all_profiles,
            root_dir=root_dir,
        )
    except FactsOverviewError as exc:
        sys.stderr.write(f'[control_plane_facts][FAIL] {exc}\n')
        return 2
    if parsed.format == 'json':
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    else:
        sys.stdout.write(render_overview_markdown(payload))
    return 0


def main(argv: list[str] | None = None) -> int:
    """模块命令行入口，直接委托 overview_entry。"""
    return overview_entry(argv)


if __name__ == '__main__':
    raise SystemExit(main())
