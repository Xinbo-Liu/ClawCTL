#!/usr/bin/env python3
"""控制平面 registry 加载所需的配置与路径辅助。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.control_plane.config_loader import (
    ControlPlaneConfigError,
    control_plane_service_schema_path,
    load_control_plane_service_payload,
)
from openclaw.control_plane.extensions.api import (
    discover_known_extension_ids,
    load_enabled_extensions,
    load_extension_manifests,
)
from openclaw.control_plane.extensions.normalization import ExtensionError
from openclaw.control_plane.manifest_fields import (
    DISPATCH_PROVIDER_REGISTRY_PATHS_KEY,
    DISPATCH_TARGET_REGISTRY_PATHS_KEY,
    RUNTIME_ADAPTER_REGISTRY_PATHS_KEY,
)
from openclaw.control_plane.schema import SchemaValidationError, load_schema, validate_payload_against_schema
from openclaw.lib.cli.common import CliError
from openclaw.lib.io.json_access import json_object
from openclaw.lib.repo.control_plane_service_scope import (
    classify_control_plane_service_scope,
    validate_control_plane_service_boundary,
)


def resolve_relative(base: Path, value: str, label: str) -> Path:
    """解析并校验必填相对路径。"""
    target = (base / value).resolve()
    if not target.exists():
        raise CliError(f'{label} 不存在：{target}', 2)
    return target


def resolve_optional_relative(base: Path, value: Any, label: str) -> Path | None:
    """解析可选相对路径。"""
    text = str(value or '').strip()
    if not text:
        return None
    return resolve_relative(base, text, label)


def resolve_collection_dir(base: Path, value: Any, label: str) -> Path:
    """解析 collection 目录路径。"""
    text = str(value or '').strip()
    if not text:
        raise CliError(f'{label} 不能为空', 2)
    return (base / text).resolve()


def describe_registry_path(path: Path, *, expected_kind: str) -> dict[str, Any]:
    """描述单个 registry 路径的存在性与类型。"""
    resolved = Path(path).resolve()
    exists = resolved.exists()
    return {
        'path': str(resolved),
        'expectedKind': expected_kind,
        'exists': exists,
        'isDir': resolved.is_dir(),
        'isFile': resolved.is_file(),
        'configuredButMissing': not exists,
    }


def describe_registry_path_list(paths: list[Path], *, expected_kind: str) -> list[dict[str, Any]]:
    """描述路径列表的存在性与类型。"""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in paths:
        resolved = Path(path).resolve()
        marker = str(resolved)
        if marker in seen:
            continue
        seen.add(marker)
        rows.append(describe_registry_path(resolved, expected_kind=expected_kind))
    return rows


def resolve_schema_path(
    *,
    base: Path,
    service_schemas: dict[str, Any],
    extensions: list[dict[str, Any]],
    schema_key: str,
    label: str,
    required: bool,
) -> Path | None:
    """从 base 与 extension 中解析单一 schema 真源。"""
    candidates: list[Path] = []
    service_value = resolve_optional_relative(base, service_schemas.get(schema_key), label)
    if service_value is not None:
        candidates.append(service_value)
    for extension in extensions:
        schemas = extension.get('schemas') if isinstance(extension.get('schemas'), dict) else {}
        candidate = schemas.get(schema_key)
        if isinstance(candidate, Path) and candidate not in candidates:
            candidates.append(candidate)
    if not candidates:
        if required:
            raise CliError(f'{label} 缺失；请在 base service 或已启用 extension 中提供 {schema_key}', 2)
        return None
    if len(candidates) > 1:
        normalized = {str(item.resolve()) for item in candidates}
        if len(normalized) > 1:
            raise CliError(f'{label} 存在多个候选 schema，当前只允许单一真源：{", ".join(sorted(normalized))}', 2)
    return candidates[0]


def dedupe_extension_registry_paths(candidates: list[Path]) -> list[Path]:
    """按绝对路径对 extension registry 路径去重。"""
    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        marker = str(resolved)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(resolved)
    return deduped


def _record_registry_path_owner(owner_map: dict[Path, str], path: Path, owner: str) -> None:
    resolved = path.resolve()
    existing = owner_map.get(resolved)
    if existing is None:
        owner_map[resolved] = owner
        return
    if existing != owner:
        owner_map[resolved] = ''


def collect_registry_inputs(*, base: Path, registry: dict[str, Any], extensions: list[dict[str, Any]]) -> dict[str, Any]:
    """收集 base 与 extension 的 registry 输入路径。"""
    jobs_dirs = [resolve_collection_dir(base, registry.get('jobsDir') or 'jobs', 'jobsDir')]
    models_dirs = [resolve_collection_dir(base, registry.get('modelsDir') or 'models', 'modelsDir')]
    targets_dirs = [resolve_collection_dir(base, registry.get('targetsDir') or 'targets', 'targetsDir')]
    owner_by_directory: dict[str, dict[Path, str]] = {
        'jobs': {},
        'models': {},
        'targets': {},
        'agentGroups': {},
        'agentModules': {},
    }
    for collection, directories in (
        ('jobs', jobs_dirs),
        ('models', models_dirs),
        ('targets', targets_dirs),
    ):
        for directory in directories:
            _record_registry_path_owner(owner_by_directory[collection], directory, 'base')
    agent_groups_dirs: list[Path] = []
    agent_modules_dirs: list[Path] = []
    runtime_adapter_registry_paths: list[Path] = []
    dispatch_target_registry_candidates: list[Path] = []
    dispatch_provider_registry_candidates: list[Path] = []
    for extension in extensions:
        extension_id = str(extension.get('id') or '').strip()
        extension_registry = extension.get('registry') if isinstance(extension.get('registry'), dict) else {}
        for item in (extension_registry.get('jobsDirs') or []):
            if isinstance(item, Path):
                jobs_dirs.append(item)
                _record_registry_path_owner(owner_by_directory['jobs'], item, extension_id)
        for item in (extension_registry.get('modelsDirs') or []):
            if isinstance(item, Path):
                models_dirs.append(item)
                _record_registry_path_owner(owner_by_directory['models'], item, extension_id)
        for item in (extension_registry.get('targetsDirs') or []):
            if isinstance(item, Path):
                targets_dirs.append(item)
                _record_registry_path_owner(owner_by_directory['targets'], item, extension_id)
        for item in (extension_registry.get('agentGroupsDirs') or []):
            if isinstance(item, Path):
                agent_groups_dirs.append(item)
                _record_registry_path_owner(owner_by_directory['agentGroups'], item, extension_id)
        for item in (extension_registry.get('agentModulesDirs') or []):
            if isinstance(item, Path):
                agent_modules_dirs.append(item)
                _record_registry_path_owner(owner_by_directory['agentModules'], item, extension_id)
        runtime_adapter_registry_paths.extend(item for item in (extension_registry.get(RUNTIME_ADAPTER_REGISTRY_PATHS_KEY) or []) if isinstance(item, Path))
        dispatch_target_registry_candidates.extend(item for item in (extension_registry.get(DISPATCH_TARGET_REGISTRY_PATHS_KEY) or []) if isinstance(item, Path))
        dispatch_provider_registry_candidates.extend(item for item in (extension_registry.get(DISPATCH_PROVIDER_REGISTRY_PATHS_KEY) or []) if isinstance(item, Path))
    return {
        'jobs_dirs': dedupe_extension_registry_paths(jobs_dirs),
        'models_dirs': dedupe_extension_registry_paths(models_dirs),
        'targets_dirs': dedupe_extension_registry_paths(targets_dirs),
        'agent_groups_dirs': dedupe_extension_registry_paths(agent_groups_dirs),
        'agent_modules_dirs': dedupe_extension_registry_paths(agent_modules_dirs),
        'runtime_adapter_registry_paths': dedupe_extension_registry_paths(runtime_adapter_registry_paths),
        'dispatch_target_registry_paths': dedupe_extension_registry_paths(dispatch_target_registry_candidates),
        'dispatch_provider_registry_paths': dedupe_extension_registry_paths(dispatch_provider_registry_candidates),
        'owner_by_directory': owner_by_directory,
    }


def resolve_registry_schema_paths(
    *,
    base: Path,
    schemas: dict[str, Any],
    extensions: list[dict[str, Any]],
    registry_inputs: dict[str, list[Path]],
) -> dict[str, Path | None]:
    """解析 registry 装配与 agent 派生面所需的 schema 路径。"""
    agent_groups_dirs = registry_inputs['agent_groups_dirs']
    agent_modules_dirs = registry_inputs['agent_modules_dirs']
    runtime_adapter_registry_paths = registry_inputs['runtime_adapter_registry_paths']
    return {
        'jobs': resolve_schema_path(base=base, service_schemas=schemas, extensions=extensions, schema_key='jobsSchema', label='jobsSchema', required=True),
        'models': resolve_schema_path(base=base, service_schemas=schemas, extensions=extensions, schema_key='modelsSchema', label='modelsSchema', required=True),
        'targets': resolve_schema_path(base=base, service_schemas=schemas, extensions=extensions, schema_key='targetsSchema', label='targetsSchema', required=True),
        'agentGroups': resolve_schema_path(base=base, service_schemas=schemas, extensions=extensions, schema_key='agentGroupsSchema', label='agentGroupsSchema', required=bool(agent_groups_dirs)),
        'agentModules': resolve_schema_path(base=base, service_schemas=schemas, extensions=extensions, schema_key='agentModulesSchema', label='agentModulesSchema', required=bool(agent_modules_dirs)),
        'agents': resolve_schema_path(base=base, service_schemas=schemas, extensions=extensions, schema_key='agentsSchema', label='agentsSchema', required=bool(agent_modules_dirs)),
        'implementations': resolve_schema_path(base=base, service_schemas=schemas, extensions=extensions, schema_key='implementationsSchema', label='implementationsSchema', required=bool(agent_modules_dirs)),
        'skillSets': resolve_schema_path(base=base, service_schemas=schemas, extensions=extensions, schema_key='skillSetsSchema', label='skillSetsSchema', required=bool(agent_modules_dirs)),
        'permissionPolicies': resolve_schema_path(base=base, service_schemas=schemas, extensions=extensions, schema_key='permissionPoliciesSchema', label='permissionPoliciesSchema', required=bool(agent_modules_dirs)),
        'toolsets': resolve_schema_path(base=base, service_schemas=schemas, extensions=extensions, schema_key='toolsetsSchema', label='toolsetsSchema', required=bool(agent_modules_dirs)),
        'runtimeAdapters': resolve_schema_path(base=base, service_schemas=schemas, extensions=extensions, schema_key='runtimeAdaptersSchema', label='runtimeAdaptersSchema', required=bool(runtime_adapter_registry_paths)),
    }


def load_registry_service_context(config_path: Path) -> dict[str, Any]:
    """Load the config-scoped service context used by loader entrypoints."""
    path = Path(config_path).resolve()
    try:
        _, payload = load_control_plane_service_payload(path)
    except ControlPlaneConfigError as exc:
        raise CliError(str(exc), 2) from exc
    base = path.parent
    service_schema_path = control_plane_service_schema_path(path)
    service_schema = load_schema(service_schema_path)
    try:
        validate_payload_against_schema(
            payload,
            service_schema,
            label=f'控制平面 service {path.name}',
            strict_dependency=True,
        )
    except SchemaValidationError as exc:
        raise CliError(str(exc), 2) from exc
    service_boundary_issues = validate_control_plane_service_boundary(path, payload)
    if service_boundary_issues:
        raise CliError('control-plane service boundary invalid: ' + '; '.join(service_boundary_issues), 2)
    service_scope = classify_control_plane_service_scope(path).to_payload()
    try:
        extensions = load_enabled_extensions(payload, service_base_dir=base)
    except ExtensionError as exc:
        raise CliError(str(exc), 2) from exc
    try:
        configured_manifests = load_extension_manifests(payload, service_base_dir=base)
    except ExtensionError as exc:
        raise CliError(str(exc), 2) from exc
    registry = json_object(payload.get('registry'))
    schemas = json_object(payload.get('schemas'))
    enabled_extension_ids = [
        str(item).strip()
        for item in (((payload.get('extensions') or {}).get('enabledExtensionIds')) or [])
        if str(item).strip()
    ]
    registry_inputs = collect_registry_inputs(base=base, registry=registry, extensions=extensions)
    schema_paths = resolve_registry_schema_paths(
        base=base,
        schemas=schemas,
        extensions=extensions,
        registry_inputs=registry_inputs,
    )
    schema_paths['service'] = service_schema_path
    return {
        'path': path,
        'payload': payload,
        'base': base,
        'serviceScope': service_scope,
        'extensions': extensions,
        'enabledExtensionIds': enabled_extension_ids,
        'knownExtensionIds': sorted(
            {str(item.get('id') or '').strip() for item in configured_manifests if isinstance(item, dict) and str(item.get('id') or '').strip()}
            | discover_known_extension_ids(path)
            | set(enabled_extension_ids)
        ),
        'registryInputs': registry_inputs,
        'schemaPaths': schema_paths,
    }
