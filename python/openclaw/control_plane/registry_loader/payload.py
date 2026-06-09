#!/usr/bin/env python3
"""Control-plane registry payload assembly helpers."""
from __future__ import annotations

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
    filter_path_mapping,
)
from openclaw.control_plane.registry_loader.config import describe_registry_path, describe_registry_path_list


def _build_extension_rows(extensions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build extension metadata rows for the materialized registry payload."""
    extension_rows: list[dict[str, Any]] = []
    for extension in extensions:
        registry_payload = extension.get('registry') if isinstance(extension.get('registry'), dict) else {}
        schemas_payload = extension.get('schemas') if isinstance(extension.get('schemas'), dict) else {}
        surface_fragments = filter_path_mapping(extension.get(SURFACE_FRAGMENTS_FIELD), keys=SURFACE_FRAGMENT_KEYS)
        governance_surfaces = filter_path_mapping(extension.get(GOVERNANCE_SURFACES_FIELD), keys=GOVERNANCE_SURFACE_KEYS)
        extension_rows.append({
            'id': str(extension.get('id') or ''),
            'title': str(extension.get('title') or ''),
            'sourcePath': str(extension.get('sourcePath') or ''),
            'registry': {
                'jobsDirs': [str(item) for item in (registry_payload.get('jobsDirs') or []) if isinstance(item, Path)],
                'agentGroupsDirs': [str(item) for item in (registry_payload.get('agentGroupsDirs') or []) if isinstance(item, Path)],
                'agentModulesDirs': [str(item) for item in (registry_payload.get('agentModulesDirs') or []) if isinstance(item, Path)],
                'modelsDirs': [str(item) for item in (registry_payload.get('modelsDirs') or []) if isinstance(item, Path)],
                'targetsDirs': [str(item) for item in (registry_payload.get('targetsDirs') or []) if isinstance(item, Path)],
                RUNTIME_ADAPTER_REGISTRY_PATHS_KEY: [str(item) for item in (registry_payload.get(RUNTIME_ADAPTER_REGISTRY_PATHS_KEY) or []) if isinstance(item, Path)],
                DISPATCH_TARGET_REGISTRY_PATHS_KEY: [str(item) for item in (registry_payload.get(DISPATCH_TARGET_REGISTRY_PATHS_KEY) or []) if isinstance(item, Path)],
                DISPATCH_PROVIDER_REGISTRY_PATHS_KEY: [str(item) for item in (registry_payload.get(DISPATCH_PROVIDER_REGISTRY_PATHS_KEY) or []) if isinstance(item, Path)],
            },
            'schemas': {key: str(value) for key, value in schemas_payload.items() if isinstance(value, Path)},
            SURFACE_FRAGMENTS_FIELD: {key: str(value) for key, value in surface_fragments.items()},
            GOVERNANCE_SURFACES_FIELD: {key: str(value) for key, value in governance_surfaces.items()},
            'jobRunners': [dict(item) for item in (extension.get('jobRunners') or []) if isinstance(item, dict)],
            'cliCommands': [dict(item) for item in (extension.get('cliCommands') or []) if isinstance(item, dict)],
            'internalApiRoutes': [dict(item) for item in (extension.get('internalApiRoutes') or []) if isinstance(item, dict)],
            'readyChecks': [dict(item) for item in (extension.get('readyChecks') or []) if isinstance(item, dict)],
        })
    return extension_rows


def _build_registry_metadata(context: dict[str, Any], extension_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Build registry metadata without attaching collection rows."""
    payload = dict(context['payload'])
    registry_inputs = context['registryInputs']
    schema_paths = context['schemaPaths']
    path = context['path']
    payload['configPath'] = str(path)
    payload['serviceScope'] = dict(context.get('serviceScope') or {})
    payload['extensions'] = extension_rows
    payload['registryPaths'] = {
        'jobs': [str(item) for item in registry_inputs['jobs_dirs']],
        'agents': str(path),
        'agentGroups': [str(item) for item in registry_inputs['agent_groups_dirs']],
        'agentModules': [str(item) for item in registry_inputs['agent_modules_dirs']],
        'skillSets': str(path),
        'permissionPolicies': str(path),
        'toolsets': str(path),
        'runtimeAdapters': [str(item) for item in registry_inputs['runtime_adapter_registry_paths']],
        'models': [str(item) for item in registry_inputs['models_dirs']],
        'targets': [str(item) for item in registry_inputs['targets_dirs']],
        'implementations': str(path),
        DISPATCH_TARGET_REGISTRY_PATHS_KEY: [str(item) for item in registry_inputs['dispatch_target_registry_paths']],
        DISPATCH_PROVIDER_REGISTRY_PATHS_KEY: [str(item) for item in registry_inputs['dispatch_provider_registry_paths']],
    }
    payload['registryPathDetails'] = {
        'jobs': describe_registry_path_list(registry_inputs['jobs_dirs'], expected_kind='directory'),
        'agents': describe_registry_path(path, expected_kind='file'),
        'agentGroups': describe_registry_path_list(registry_inputs['agent_groups_dirs'], expected_kind='directory'),
        'agentModules': describe_registry_path_list(registry_inputs['agent_modules_dirs'], expected_kind='directory'),
        'skillSets': describe_registry_path(path, expected_kind='file'),
        'permissionPolicies': describe_registry_path(path, expected_kind='file'),
        'toolsets': describe_registry_path(path, expected_kind='file'),
        'runtimeAdapters': describe_registry_path_list(registry_inputs['runtime_adapter_registry_paths'], expected_kind='file'),
        'models': describe_registry_path_list(registry_inputs['models_dirs'], expected_kind='directory'),
        'targets': describe_registry_path_list(registry_inputs['targets_dirs'], expected_kind='directory'),
        'implementations': describe_registry_path(path, expected_kind='file'),
        DISPATCH_TARGET_REGISTRY_PATHS_KEY: describe_registry_path_list(registry_inputs['dispatch_target_registry_paths'], expected_kind='file'),
        DISPATCH_PROVIDER_REGISTRY_PATHS_KEY: describe_registry_path_list(registry_inputs['dispatch_provider_registry_paths'], expected_kind='file'),
    }
    payload['schemaPaths'] = {
        'service': str(schema_paths['service']),
        'jobs': str(schema_paths['jobs']),
        'agents': str(schema_paths['agents'] or ''),
        'agentGroups': str(schema_paths['agentGroups'] or ''),
        'agentModules': str(schema_paths['agentModules'] or ''),
        'skillSets': str(schema_paths['skillSets'] or ''),
        'permissionPolicies': str(schema_paths['permissionPolicies'] or ''),
        'toolsets': str(schema_paths['toolsets'] or ''),
        'runtimeAdapters': str(schema_paths['runtimeAdapters'] or ''),
        'models': str(schema_paths['models']),
        'targets': str(schema_paths['targets']),
        'implementations': str(schema_paths['implementations'] or ''),
    }
    return payload


def _attach_registry_collections(
    payload: dict[str, Any],
    collections: dict[str, Any],
    runtime_state: dict[str, Any],
) -> dict[str, Any]:
    """Attach materialized collections, indexes, and runtime state to metadata."""
    payload['jobs'] = collections['jobs']
    payload['jobsById'] = collections['jobsById']
    payload['agents'] = collections['agents']
    payload['agentsById'] = collections['agentsById']
    payload['agentGroups'] = collections['agentGroups']
    payload['agentGroupsById'] = collections['agentGroupsById']
    payload['agentModules'] = collections['agentModules']
    payload['agentModulesById'] = collections['agentModulesById']
    payload['skillSets'] = collections['skillSets']
    payload['skillSetsById'] = collections['skillSetsById']
    payload['permissionPolicies'] = collections['permissionPolicies']
    payload['permissionPoliciesById'] = collections['permissionPoliciesById']
    payload['toolsets'] = collections['toolsets']
    payload['toolsetsById'] = collections['toolsetsById']
    payload['runtimeAdapters'] = collections['runtimeAdapters']
    payload['runtimeAdaptersById'] = collections['runtimeAdaptersById']
    payload['jobRunners'] = runtime_state['jobRunners']
    payload['jobRunnersById'] = runtime_state['jobRunnersById']
    payload['models'] = collections['models']
    payload['modelsById'] = collections['modelsById']
    payload['targets'] = collections['targets']
    payload['targetsById'] = collections['targetsById']
    payload['implementations'] = collections['implementations']
    payload['implementationsById'] = collections['implementationsById']
    for key in (
        'jobsByQualifiedId', 'jobsByOwner', 'jobsAmbiguousIds',
        'agentsByQualifiedId', 'agentsByOwner', 'agentsAmbiguousIds',
        'agentGroupsByQualifiedId', 'agentGroupsByOwner', 'agentGroupsAmbiguousIds',
        'agentModulesByQualifiedId', 'agentModulesByOwner', 'agentModulesAmbiguousIds',
        'skillSetsByQualifiedId', 'skillSetsByOwner', 'skillSetsAmbiguousIds',
        'permissionPoliciesByQualifiedId', 'permissionPoliciesByOwner', 'permissionPoliciesAmbiguousIds',
        'toolsetsByQualifiedId', 'toolsetsByOwner', 'toolsetsAmbiguousIds',
        'modelsByQualifiedId', 'modelsByOwner', 'modelsAmbiguousIds',
        'targetsByQualifiedId', 'targetsByOwner', 'targetsAmbiguousIds',
        'implementationsByQualifiedId', 'implementationsByOwner', 'implementationsAmbiguousIds',
    ):
        if key in collections:
            payload[key] = collections[key]
    return payload
