#!/usr/bin/env python3
"""扩展 manifest 标准化辅助。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from openclaw.control_plane.manifest_fields import (
    DISPATCH_PROVIDER_REGISTRY_PATHS_KEY,
    DISPATCH_TARGET_REGISTRY_PATHS_KEY,
    GOVERNANCE_SURFACE_KEYS,
    GOVERNANCE_SURFACES_FIELD,
    RUNTIME_ADAPTER_REGISTRY_PATHS_KEY,
    SURFACE_FRAGMENT_KEYS,
    SURFACE_FRAGMENTS_FIELD,
)
from openclaw.control_plane.manifest_models import ExtensionManifestModel
from openclaw.lib.repo.path_contracts import is_repo_anchored_path, resolve_path_contract
from openclaw.lib.repo.repo_root import resolve_repo_root


class ExtensionError(RuntimeError):
    """扩展 manifest 无法读取或标准化时抛出的错误。"""


_SCHEMA_KEYS = (
    'jobsSchema',
    'modelsSchema',
    'targetsSchema',
    'agentsSchema',
    'implementationsSchema',
    'agentGroupsSchema',
    'agentModulesSchema',
    'runtimeAdaptersSchema',
    'skillSetsSchema',
    'permissionPoliciesSchema',
    'toolsetsSchema',
)
_SEMVER_RE = re.compile(r'^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z_.-]+)?$')
_EXTENSION_ID_RE = re.compile(r'^[a-z0-9_]+$')
_REGISTRY_KEYS = (
    'jobsDirs',
    'modelsDirs',
    'targetsDirs',
    'agentGroupsDirs',
    'agentModulesDirs',
    RUNTIME_ADAPTER_REGISTRY_PATHS_KEY,
    DISPATCH_TARGET_REGISTRY_PATHS_KEY,
    DISPATCH_PROVIDER_REGISTRY_PATHS_KEY,
)
_MANIFEST_KEYS = (
    'id',
    'title',
    'version',
    'compat',
    'dependencies',
    'migrations',
    'registry',
    'schemas',
    SURFACE_FRAGMENTS_FIELD,
    GOVERNANCE_SURFACES_FIELD,
    'jobRunners',
    'cliCommands',
    'internalApiRoutes',
    'readyChecks',
)
_COMPAT_KEYS = ('controlPlane',)
_DEPENDENCY_KEYS = ('id', 'version', 'optional')
_MIGRATION_KEYS = ('id', 'fromVersion', 'toVersion', 'callable')
_JOB_RUNNER_KEYS = ('id', 'title', 'module', 'callable', 'handlesAgentBindings')
_CLI_COMMAND_KEYS = ('command', 'module')
_INTERNAL_API_ROUTE_KEYS = ('id', 'path', 'module', 'callable', 'authRequired')
_READY_CHECK_KEYS = ('id', 'module', 'callable', 'blocking')


def _reject_unknown_keys(payload: dict[str, Any], *, allowed: tuple[str, ...], label: str) -> None:
    unknown = sorted(str(key) for key in payload if key not in allowed)
    if unknown:
        raise ExtensionError(f'{label} contains unsupported manifest field(s): {", ".join(unknown)}')


def _optional_object(value: Any, *, label: str) -> dict[str, Any]:
    if value in (None, ''):
        return {}
    if not isinstance(value, dict):
        raise ExtensionError(f'{label} must be an object')
    return value


def _optional_list(value: Any, *, label: str) -> list[Any]:
    if value in (None, ''):
        return []
    if not isinstance(value, list):
        raise ExtensionError(f'{label} must be a list')
    return value


def _optional_bool(payload: dict[str, Any], key: str, *, default: bool, label: str) -> bool:
    if key not in payload:
        return default
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ExtensionError(f'{label}.{key} must be a boolean')
    return value


def _require_text(value: Any, *, label: str) -> str:
    text = str(value or '').strip()
    if not text:
        raise ExtensionError(f'{label} must be a non-empty string')
    return text


def _optional_text(value: Any) -> str:
    return str(value or '').strip()


def _normalize_text_list(value: Any, *, label: str) -> list[str]:
    if value in (None, ''):
        return []
    if not isinstance(value, list):
        raise ExtensionError(f'{label} must be a list')
    result: list[str] = []
    for idx, item in enumerate(value):
        text = str(item or '').strip()
        if not text:
            raise ExtensionError(f'{label}[{idx}] must be a non-empty string')
        if text in result:
            raise ExtensionError(f'{label} contains duplicate value: {text}')
        result.append(text)
    return result


def _normalize_semver(value: Any, *, label: str, required: bool = False) -> str:
    text = str(value or '').strip()
    if not text:
        if required:
            raise ExtensionError(f'{label} must be a semver string')
        return ''
    if not _SEMVER_RE.match(text):
        raise ExtensionError(f'{label} must match semver x.y.z: {text}')
    return text


def _contains_repo_anchored_path(value: Any) -> bool:
    if isinstance(value, str):
        return is_repo_anchored_path(value)
    if isinstance(value, dict):
        return any(_contains_repo_anchored_path(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_repo_anchored_path(item) for item in value)
    return False


def _resolve_optional_path(base_dir: Path, value: Any, *, repo_root: Path | None = None) -> Path | None:
    text = _optional_text(value)
    if not text:
        return None
    return resolve_path_contract(text, base_dir=base_dir, start_path=base_dir, repo_root=repo_root)


def _resolve_optional_dir(base_dir: Path, value: Any, *, label: str, repo_root: Path | None = None) -> Path | None:
    path = _resolve_optional_path(base_dir, value, repo_root=repo_root)
    if path is None:
        return None
    if not path.exists():
        raise ExtensionError(f'{label} does not exist: {path}')
    if not path.is_dir():
        raise ExtensionError(f'{label} must be a directory: {path}')
    return path


def _resolve_dir_list(base_dir: Path, value: Any, *, label: str, repo_root: Path | None = None) -> list[Path]:
    result: list[Path] = []
    for idx, item in enumerate(_normalize_text_list(value, label=label)):
        path = _resolve_optional_dir(base_dir, item, label=f'{label}[{idx}]', repo_root=repo_root)
        if path is not None and path not in result:
            result.append(path)
    return result


def _resolve_path_list(base_dir: Path, value: Any, *, label: str, repo_root: Path | None = None) -> list[Path]:
    result: list[Path] = []
    for idx, item in enumerate(_normalize_text_list(value, label=label)):
        path = _resolve_optional_path(base_dir, item, repo_root=repo_root)
        if path is None:
            continue
        if not path.exists():
            raise ExtensionError(f'{label}[{idx}] does not exist: {path}')
        if path not in result:
            result.append(path)
    return result


def _normalize_registry(payload: dict[str, Any], *, base_dir: Path, repo_root: Path | None = None) -> dict[str, Any]:
    registry = _optional_object(payload, label='extension.registry')
    if 'dispatchTargetRegistryPath' in registry:
        raise ExtensionError(
            'extension.registry.dispatchTargetRegistryPath is not supported; use '
            'extension.registry.dispatchTargetRegistryPaths'
        )
    if 'dispatchProviderRegistryPath' in registry:
        raise ExtensionError(
            'extension.registry.dispatchProviderRegistryPath is not supported; use '
            'extension.registry.dispatchProviderRegistryPaths'
        )
    _reject_unknown_keys(registry, allowed=_REGISTRY_KEYS, label='extension.registry')
    return {
        'jobsDirs': _resolve_dir_list(base_dir, registry.get('jobsDirs'), label='extension.registry.jobsDirs', repo_root=repo_root),
        'modelsDirs': _resolve_dir_list(base_dir, registry.get('modelsDirs'), label='extension.registry.modelsDirs', repo_root=repo_root),
        'targetsDirs': _resolve_dir_list(base_dir, registry.get('targetsDirs'), label='extension.registry.targetsDirs', repo_root=repo_root),
        'agentGroupsDirs': _resolve_dir_list(base_dir, registry.get('agentGroupsDirs'), label='extension.registry.agentGroupsDirs', repo_root=repo_root),
        'agentModulesDirs': _resolve_dir_list(base_dir, registry.get('agentModulesDirs'), label='extension.registry.agentModulesDirs', repo_root=repo_root),
        RUNTIME_ADAPTER_REGISTRY_PATHS_KEY: _resolve_path_list(base_dir, registry.get(RUNTIME_ADAPTER_REGISTRY_PATHS_KEY), label=f'extension.registry.{RUNTIME_ADAPTER_REGISTRY_PATHS_KEY}', repo_root=repo_root),
        DISPATCH_TARGET_REGISTRY_PATHS_KEY: _resolve_path_list(base_dir, registry.get(DISPATCH_TARGET_REGISTRY_PATHS_KEY), label=f'extension.registry.{DISPATCH_TARGET_REGISTRY_PATHS_KEY}', repo_root=repo_root),
        DISPATCH_PROVIDER_REGISTRY_PATHS_KEY: _resolve_path_list(base_dir, registry.get(DISPATCH_PROVIDER_REGISTRY_PATHS_KEY), label=f'extension.registry.{DISPATCH_PROVIDER_REGISTRY_PATHS_KEY}', repo_root=repo_root),
    }


def _normalize_schemas(value: Any, *, base_dir: Path, label: str, repo_root: Path | None = None) -> dict[str, Path]:
    payload = _optional_object(value, label=label)
    _reject_unknown_keys(payload, allowed=_SCHEMA_KEYS, label=label)
    result: dict[str, Path] = {}
    for key in _SCHEMA_KEYS:
        path = _resolve_optional_path(base_dir, payload.get(key), repo_root=repo_root)
        if path is None:
            continue
        if not path.exists():
            raise ExtensionError(f'{label}.{key} does not exist: {path}')
        result[key] = path
    return result


def _normalize_surface_fragments(value: Any, *, base_dir: Path, label: str, repo_root: Path | None = None) -> dict[str, Path]:
    payload = _optional_object(value, label=label)
    _reject_unknown_keys(payload, allowed=SURFACE_FRAGMENT_KEYS, label=label)
    result: dict[str, Path] = {}
    for key in SURFACE_FRAGMENT_KEYS:
        path = _resolve_optional_path(base_dir, payload.get(key), repo_root=repo_root)
        if path is None:
            continue
        if not path.exists():
            raise ExtensionError(f'{label}.{key} does not exist: {path}')
        result[key] = path
    return result


def _normalize_governance_surfaces(value: Any, *, base_dir: Path, label: str, repo_root: Path | None = None) -> dict[str, Path]:
    payload = _optional_object(value, label=label)
    _reject_unknown_keys(payload, allowed=GOVERNANCE_SURFACE_KEYS, label=label)
    result: dict[str, Path] = {}
    for key in GOVERNANCE_SURFACE_KEYS:
        path = _resolve_optional_path(base_dir, payload.get(key), repo_root=repo_root)
        if path is None:
            continue
        if not path.exists():
            raise ExtensionError(f'{label}.{key} does not exist: {path}')
        result[key] = path
    return result


def _normalize_job_runners(value: Any, *, label: str) -> list[dict[str, Any]]:
    rows = _optional_list(value, label=label)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ExtensionError(f'{label}[{idx}] must be an object')
        _reject_unknown_keys(row, allowed=_JOB_RUNNER_KEYS, label=f'{label}[{idx}]')
        runner_id = _require_text(row.get('id'), label=f'{label}[{idx}].id')
        if runner_id in seen:
            raise ExtensionError(f'{label} contains duplicate id: {runner_id}')
        seen.add(runner_id)
        result.append({
            'id': runner_id,
            'title': _require_text(row.get('title'), label=f'{label}[{idx}].title'),
            'module': _require_text(row.get('module'), label=f'{label}[{idx}].module'),
            'callable': _require_text(row.get('callable'), label=f'{label}[{idx}].callable'),
            'handlesAgentBindings': _optional_bool(row, 'handlesAgentBindings', default=False, label=f'{label}[{idx}]'),
        })
    return result


def _normalize_cli_commands(value: Any, *, label: str) -> list[dict[str, str]]:
    rows = _optional_list(value, label=label)
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ExtensionError(f'{label}[{idx}] must be an object')
        _reject_unknown_keys(row, allowed=_CLI_COMMAND_KEYS, label=f'{label}[{idx}]')
        command = _require_text(row.get('command'), label=f'{label}[{idx}].command')
        if command in seen:
            raise ExtensionError(f'{label} contains duplicate command: {command}')
        seen.add(command)
        result.append({
            'command': command,
            'module': _require_text(row.get('module'), label=f'{label}[{idx}].module'),
        })
    return result


def _normalize_internal_api_routes(value: Any, *, label: str) -> list[dict[str, Any]]:
    rows = _optional_list(value, label=label)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ExtensionError(f'{label}[{idx}] must be an object')
        _reject_unknown_keys(row, allowed=_INTERNAL_API_ROUTE_KEYS, label=f'{label}[{idx}]')
        route_id = _require_text(row.get('id'), label=f'{label}[{idx}].id')
        if route_id in seen:
            raise ExtensionError(f'{label} contains duplicate id: {route_id}')
        seen.add(route_id)
        result.append({
            'id': route_id,
            'path': _require_text(row.get('path'), label=f'{label}[{idx}].path'),
            'module': _require_text(row.get('module'), label=f'{label}[{idx}].module'),
            'callable': _require_text(row.get('callable'), label=f'{label}[{idx}].callable'),
            'authRequired': _optional_bool(row, 'authRequired', default=True, label=f'{label}[{idx}]'),
        })
    return result


def _normalize_ready_checks(value: Any, *, label: str) -> list[dict[str, Any]]:
    rows = _optional_list(value, label=label)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ExtensionError(f'{label}[{idx}] must be an object')
        _reject_unknown_keys(row, allowed=_READY_CHECK_KEYS, label=f'{label}[{idx}]')
        check_id = _require_text(row.get('id'), label=f'{label}[{idx}].id')
        if check_id in seen:
            raise ExtensionError(f'{label} contains duplicate id: {check_id}')
        seen.add(check_id)
        result.append({
            'id': check_id,
            'module': _require_text(row.get('module'), label=f'{label}[{idx}].module'),
            'callable': _require_text(row.get('callable'), label=f'{label}[{idx}].callable'),
            'blocking': _optional_bool(row, 'blocking', default=True, label=f'{label}[{idx}]'),
        })
    return result


def _normalize_compat(value: Any, *, label: str) -> dict[str, Any]:
    payload = _optional_object(value, label=label)
    _reject_unknown_keys(payload, allowed=_COMPAT_KEYS, label=label)
    result: dict[str, Any] = {}
    control_plane = str(payload.get('controlPlane') or '').strip()
    if control_plane:
        result['controlPlane'] = control_plane
    return result


def _normalize_dependencies(value: Any, *, label: str) -> list[dict[str, Any]]:
    rows = _optional_list(value, label=label)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ExtensionError(f'{label}[{idx}] must be an object')
        _reject_unknown_keys(row, allowed=_DEPENDENCY_KEYS, label=f'{label}[{idx}]')
        dep_id = _require_text(row.get('id'), label=f'{label}[{idx}].id')
        if dep_id in seen:
            raise ExtensionError(f'{label} contains duplicate dependency: {dep_id}')
        seen.add(dep_id)
        payload: dict[str, Any] = {'id': dep_id}
        version = str(row.get('version') or '').strip()
        if version:
            payload['version'] = version
        if _optional_bool(row, 'optional', default=False, label=f'{label}[{idx}]'):
            payload['optional'] = True
        result.append(payload)
    return result


def _normalize_migrations(value: Any, *, label: str) -> list[dict[str, Any]]:
    rows = _optional_list(value, label=label)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ExtensionError(f'{label}[{idx}] must be an object')
        _reject_unknown_keys(row, allowed=_MIGRATION_KEYS, label=f'{label}[{idx}]')
        migration_id = _require_text(row.get('id'), label=f'{label}[{idx}].id')
        if migration_id in seen:
            raise ExtensionError(f'{label} contains duplicate migration: {migration_id}')
        seen.add(migration_id)
        payload: dict[str, Any] = {'id': migration_id}
        from_version = _normalize_semver(row.get('fromVersion'), label=f'{label}[{idx}].fromVersion') if row.get('fromVersion') else ''
        to_version = _normalize_semver(row.get('toVersion'), label=f'{label}[{idx}].toVersion') if row.get('toVersion') else ''
        callable_ref = str(row.get('callable') or '').strip()
        if from_version:
            payload['fromVersion'] = from_version
        if to_version:
            payload['toVersion'] = to_version
        if callable_ref:
            payload['callable'] = callable_ref
        result.append(payload)
    return result


def _normalize_manifest(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    base_dir = path.parent
    repo_root = resolve_repo_root(base_dir) if _contains_repo_anchored_path(payload) else None
    extension_id = _require_text(payload.get('id'), label=f'extension {path.name}.id')
    if not _EXTENSION_ID_RE.match(extension_id):
        raise ExtensionError(f'extension {path.name}.id must match lowercase extension id pattern [a-z0-9_]+: {extension_id}')
    _reject_unknown_keys(payload, allowed=_MANIFEST_KEYS, label=f'extension {extension_id}')
    normalized = {
        'id': extension_id,
        'title': _require_text(payload.get('title'), label=f'extension {path.name}.title'),
        'version': _normalize_semver(payload.get('version'), label=f'extension {extension_id}.version'),
        'compat': _normalize_compat(payload.get('compat'), label=f'extension {extension_id}.compat'),
        'dependencies': _normalize_dependencies(payload.get('dependencies'), label=f'extension {extension_id}.dependencies'),
        'migrations': _normalize_migrations(payload.get('migrations'), label=f'extension {extension_id}.migrations'),
        'sourcePath': str(path),
        'baseDir': str(base_dir),
        'registry': _normalize_registry(payload.get('registry'), base_dir=base_dir, repo_root=repo_root),
        'schemas': _normalize_schemas(payload.get('schemas'), base_dir=base_dir, label=f'extension {extension_id}.schemas', repo_root=repo_root),
        SURFACE_FRAGMENTS_FIELD: _normalize_surface_fragments(
            payload.get(SURFACE_FRAGMENTS_FIELD),
            base_dir=base_dir,
            label=f'extension {extension_id}.{SURFACE_FRAGMENTS_FIELD}',
            repo_root=repo_root,
        ),
        GOVERNANCE_SURFACES_FIELD: _normalize_governance_surfaces(
            payload.get(GOVERNANCE_SURFACES_FIELD),
            base_dir=base_dir,
            label=f'extension {extension_id}.{GOVERNANCE_SURFACES_FIELD}',
            repo_root=repo_root,
        ),
        'jobRunners': _normalize_job_runners(payload.get('jobRunners'), label=f'extension {extension_id}.jobRunners'),
        'cliCommands': _normalize_cli_commands(payload.get('cliCommands'), label=f'extension {extension_id}.cliCommands'),
        'internalApiRoutes': _normalize_internal_api_routes(payload.get('internalApiRoutes'), label=f'extension {extension_id}.internalApiRoutes'),
        'readyChecks': _normalize_ready_checks(payload.get('readyChecks'), label=f'extension {extension_id}.readyChecks'),
    }
    return ExtensionManifestModel.from_normalized_payload(normalized).to_payload()
