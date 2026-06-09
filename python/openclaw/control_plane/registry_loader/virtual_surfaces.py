#!/usr/bin/env python3
"""Registry loader helpers for module-derived virtual surfaces."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from openclaw.control_plane.agent.surface_derivation import build_expected_agent_control_plane_registry
from openclaw.control_plane.registry.owners import (
    annotate_owned_row,
    qualified_registry_id,
    row_owner_id,
    split_registry_ref,
)
from openclaw.control_plane.registry_loader.collection_sources import _load_agent_module_dirs
from openclaw.control_plane.registry_loader.config import load_registry_service_context
from openclaw.control_plane.registry.support import _ensure_unique_text_list, _module_asset_path
from openclaw.control_plane.schema import SchemaValidationError, load_schema, validate_payload_against_schema
from openclaw.lib.cli.common import CliError
from openclaw.lib.io.json_access import json_object


def _relative_posix_path(base_dir: Path, target: Path) -> str:
    return Path(os.path.relpath(target.resolve(), start=base_dir.resolve())).as_posix()


def _module_change_control_doc_paths(module: dict[str, Any]) -> list[str]:
    governance = json_object(module.get('governance'))
    return _ensure_unique_text_list(
        governance.get('changeControlDocPaths') or [],
        label=f"agent module {module.get('id')} governance.changeControlDocPaths",
    )


def _attach_module_extension_metadata(row: dict[str, Any], module: dict[str, Any] | None) -> None:
    if not isinstance(module, dict):
        return
    extension_id = str(module.get('extensionId') or '').strip()
    if extension_id:
        row['extensionId'] = extension_id
    active_ids = [
        str(item).strip()
        for item in (module.get('resolvedActiveExtensionIds') or [])
        if str(item).strip()
    ]
    if active_ids:
        row['resolvedActiveExtensionIds'] = active_ids


def _build_virtual_assembly_row(
    *,
    module: dict[str, Any],
    registry_base_dir: Path,
    item_id: str,
    asset_key: str,
    item_title_suffix: str,
    source_format: str,
) -> dict[str, Any]:
    module_id = str(module.get('id') or '').strip()
    source_path = Path(str(module.get('sourcePath') or '')).resolve()
    asset_path = _module_asset_path(module, asset_key)
    title = str(module.get('title') or '').strip()
    version = str(module.get('version') or '').strip()
    owner_domain = str(module.get('ownerDomain') or '').strip()
    if not module_id or not title or not version or not owner_domain:
        raise CliError(f'agent module {module_id or "<unknown>"} missing derivation fields', 2)
    return {
        'schemaVersion': 1,
        'id': item_id,
        'moduleRef': module_id,
        'title': f'{title} {item_title_suffix}',
        'version': version,
        'ownerDomain': owner_domain,
        'source': {
            'path': _relative_posix_path(registry_base_dir, asset_path),
            'format': source_format,
        },
        'governance': {
            'changeControlDocPaths': _module_change_control_doc_paths(module),
        },
        'derivation': {
            'mode': 'agent_internal',
            'moduleManifestPath': _relative_posix_path(registry_base_dir, source_path),
            'assetKey': asset_key,
        },
    }


def _build_expected_agent_internal_assembly_registry(
    modules: list[dict[str, Any]],
    *,
    registry_base_dir: Path,
) -> dict[str, dict[str, dict[str, Any]]]:
    expected: dict[str, dict[str, dict[str, Any]]] = {
        'skillSets': {},
        'permissionPolicies': {},
        'toolsets': {},
    }
    for module in modules:
        module_id = str(module.get('id') or '').strip()
        assembly = json_object(module.get('assembly'))
        rows = [
            (
                'skillSets',
                qualified_registry_id(row_owner_id(module), str(assembly.get('skillSetRef') or '').strip()),
                _build_virtual_assembly_row(
                    module=module,
                    registry_base_dir=registry_base_dir,
                    item_id=str(assembly.get('skillSetRef') or '').strip(),
                    asset_key='skillsPath',
                    item_title_suffix='skill set',
                    source_format='markdown',
                ),
            ),
            (
                'permissionPolicies',
                qualified_registry_id(row_owner_id(module), str(assembly.get('permissionPolicyRef') or '').strip()),
                _build_virtual_assembly_row(
                    module=module,
                    registry_base_dir=registry_base_dir,
                    item_id=str(assembly.get('permissionPolicyRef') or '').strip(),
                    asset_key='permissionsPath',
                    item_title_suffix='permission policy',
                    source_format='json',
                ),
            ),
            (
                'toolsets',
                qualified_registry_id(row_owner_id(module), str(assembly.get('toolsetRef') or '').strip()),
                _build_virtual_assembly_row(
                    module=module,
                    registry_base_dir=registry_base_dir,
                    item_id=str(assembly.get('toolsetRef') or '').strip(),
                    asset_key='toolsPath',
                    item_title_suffix='toolset',
                    source_format='json',
                ),
            ),
        ]
        for bucket, item_id, payload in rows:
            _, local_item_id = split_registry_ref(item_id)
            if not local_item_id:
                raise CliError(f'agent module {module_id} missing derivation reference: {bucket}', 2)
            if item_id in expected[bucket]:
                owner = expected[bucket][item_id].get('moduleRef')
                raise CliError(f'{bucket} derivation id conflict: {item_id} ({owner} vs {module_id})', 2)
            expected[bucket][item_id] = payload
    return expected


def _load_agent_derivation_surface(config_path: Path) -> dict[str, Any]:
    context = load_registry_service_context(config_path)
    registry_inputs = context['registryInputs']
    schema_paths = context['schemaPaths']
    if not registry_inputs['agent_modules_dirs']:
        return {
            'configPath': context['path'],
            'registryBaseDir': context['base'],
            'modules': [],
            'schemas': {
                'agents': schema_paths['agents'],
                'implementations': schema_paths['implementations'],
                'skillSets': schema_paths['skillSets'],
                'permissionPolicies': schema_paths['permissionPolicies'],
                'toolsets': schema_paths['toolsets'],
            },
        }
    agent_modules_schema = load_schema(schema_paths['agentModules'])
    modules, _ = _load_agent_module_dirs(
        registry_inputs['agent_modules_dirs'],
        'control-plane agent modules',
        agent_modules_schema,
        shared_directories={
            item.resolve()
            for extension in context['extensions']
            for item in ((extension.get('registry') or {}).get('agentModulesDirs') or [])
            if isinstance(item, Path)
        },
        owner_by_directory=((registry_inputs.get('owner_by_directory') or {}).get('agentModules') or {}),
        enabled_extension_ids=list(context.get('enabledExtensionIds') or []),
        known_extension_ids=set(context.get('knownExtensionIds') or []),
    )
    return {
        'configPath': context['path'],
        'registryBaseDir': context['base'],
        'modules': modules,
        'schemas': {
            'agents': load_schema(schema_paths['agents']),
            'implementations': load_schema(schema_paths['implementations']),
            'skillSets': load_schema(schema_paths['skillSets']),
            'permissionPolicies': load_schema(schema_paths['permissionPolicies']),
            'toolsets': load_schema(schema_paths['toolsets']),
        },
    }


def _materialize_virtual_collection(
    *,
    expected: dict[str, dict[str, Any]],
    schema: dict[str, Any],
    label: str,
    source_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    for item_id, payload in sorted(expected.items()):
        try:
            validate_payload_against_schema(
                payload,
                schema,
                label=f'{label} {item_id}',
                strict_dependency=True,
            )
        except SchemaValidationError as exc:
            raise CliError(str(exc), 2) from exc
        row = dict(payload)
        row['sourcePath'] = str(source_path)
        owner_id, _ = split_registry_ref(item_id)
        if owner_id:
            annotate_owned_row(row, owner_id=owner_id)
        rows.append(row)
        index[str(row.get('id') or item_id)] = row
    return rows, index


def _ensure_agent_internal_assembly_registry(config_path: Path, *, sync: bool = False) -> dict[str, Any]:
    surface = _load_agent_derivation_surface(config_path)
    modules = list(surface['modules'])
    if not modules:
        return {
            'status': 'ok',
            'configPath': str(surface['configPath']),
            'mode': 'virtualized_sync' if sync else 'virtualized_check',
            'counts': {
                'agentModules': 0,
                'skillSets': 0,
                'permissionPolicies': 0,
                'toolsets': 0,
            },
            'writtenPaths': [],
            'removedPaths': [],
        }
    schemas = dict(surface['schemas'])
    expected = _build_expected_agent_internal_assembly_registry(modules, registry_base_dir=surface['registryBaseDir'])
    _materialize_virtual_collection(expected=expected['skillSets'], schema=schemas['skillSets'], label='derived skill set', source_path=surface['configPath'])
    _materialize_virtual_collection(expected=expected['permissionPolicies'], schema=schemas['permissionPolicies'], label='derived permission policy', source_path=surface['configPath'])
    _materialize_virtual_collection(expected=expected['toolsets'], schema=schemas['toolsets'], label='derived toolset', source_path=surface['configPath'])
    return {
        'status': 'ok',
        'configPath': str(surface['configPath']),
        'mode': 'virtualized_sync' if sync else 'virtualized_check',
        'counts': {
            'agentModules': len(modules),
            'skillSets': len(expected['skillSets']),
            'permissionPolicies': len(expected['permissionPolicies']),
            'toolsets': len(expected['toolsets']),
        },
        'writtenPaths': [],
        'removedPaths': [],
    }


def _ensure_agent_control_plane_registry(config_path: Path, *, sync: bool = False) -> dict[str, Any]:
    surface = _load_agent_derivation_surface(config_path)
    modules = list(surface['modules'])
    if not modules:
        return {
            'status': 'ok',
            'configPath': str(surface['configPath']),
            'mode': 'virtualized_sync' if sync else 'virtualized_check',
            'counts': {
                'agentModules': 0,
                'agents': 0,
                'implementations': 0,
            },
            'writtenPaths': [],
            'removedPaths': [],
        }
    schemas = dict(surface['schemas'])
    expected = build_expected_agent_control_plane_registry(modules)
    _materialize_virtual_collection(expected=expected['agents'], schema=schemas['agents'], label='derived agent', source_path=surface['configPath'])
    _materialize_virtual_collection(expected=expected['implementations'], schema=schemas['implementations'], label='derived implementation', source_path=surface['configPath'])
    return {
        'status': 'ok',
        'configPath': str(surface['configPath']),
        'mode': 'virtualized_sync' if sync else 'virtualized_check',
        'counts': {
            'agentModules': len(modules),
            'agents': len(expected['agents']),
            'implementations': len(expected['implementations']),
        },
        'writtenPaths': [],
        'removedPaths': [],
    }


def _materialize_virtual_agent_surfaces(
    *,
    agent_modules: list[dict[str, Any]],
    base: Path,
    path: Path,
    schema_paths: dict[str, Path | None],
) -> dict[str, Any]:
    if not agent_modules:
        return {
            'agents': [],
            'agentsById': {},
            'implementations': [],
            'implementationsById': {},
            'skillSets': [],
            'skillSetsById': {},
            'permissionPolicies': [],
            'permissionPoliciesById': {},
            'toolsets': [],
            'toolsetsById': {},
        }
    expected_agent_surface = build_expected_agent_control_plane_registry(agent_modules)
    agents, agents_by_id = _materialize_virtual_collection(
        expected=expected_agent_surface['agents'],
        schema=load_schema(schema_paths['agents']),
        label='control-plane agents',
        source_path=path,
    )
    implementations, implementations_by_id = _materialize_virtual_collection(
        expected=expected_agent_surface['implementations'],
        schema=load_schema(schema_paths['implementations']),
        label='control-plane implementations',
        source_path=path,
    )
    modules_by_qualified_id = {
        qualified_registry_id(row_owner_id(module), str(module.get('id') or '').strip()): module
        for module in agent_modules
    }
    modules_by_implementation_id = {
        qualified_registry_id(row_owner_id(module), str(json_object(module.get('logic')).get('implementationRef') or '').strip()): module
        for module in agent_modules
    }
    for agent in agents:
        governance = json_object(agent.get('governance'))
        _attach_module_extension_metadata(
            agent,
            modules_by_qualified_id.get(qualified_registry_id(row_owner_id(agent), str(governance.get('moduleRef') or '').strip())),
        )
    for implementation in implementations:
        _attach_module_extension_metadata(
            implementation,
            modules_by_implementation_id.get(qualified_registry_id(row_owner_id(implementation), str(implementation.get('id') or '').strip())),
        )
    expected_internal_assembly = _build_expected_agent_internal_assembly_registry(agent_modules, registry_base_dir=base)
    skill_sets, skill_sets_by_id = _materialize_virtual_collection(
        expected=expected_internal_assembly['skillSets'],
        schema=load_schema(schema_paths['skillSets']),
        label='control-plane skill sets',
        source_path=path,
    )
    permission_policies, permission_policies_by_id = _materialize_virtual_collection(
        expected=expected_internal_assembly['permissionPolicies'],
        schema=load_schema(schema_paths['permissionPolicies']),
        label='control-plane permission policies',
        source_path=path,
    )
    toolsets, toolsets_by_id = _materialize_virtual_collection(
        expected=expected_internal_assembly['toolsets'],
        schema=load_schema(schema_paths['toolsets']),
        label='control-plane toolsets',
        source_path=path,
    )
    for rows in (skill_sets, permission_policies, toolsets):
        for row in rows:
            _attach_module_extension_metadata(
                row,
                modules_by_qualified_id.get(qualified_registry_id(row_owner_id(row), str(row.get('moduleRef') or '').strip())),
            )
    return {
        'agents': agents,
        'agentsById': agents_by_id,
        'implementations': implementations,
        'implementationsById': implementations_by_id,
        'skillSets': skill_sets,
        'skillSetsById': skill_sets_by_id,
        'permissionPolicies': permission_policies,
        'permissionPoliciesById': permission_policies_by_id,
        'toolsets': toolsets,
        'toolsetsById': toolsets_by_id,
    }
