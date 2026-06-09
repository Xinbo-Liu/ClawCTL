#!/usr/bin/env python3
"""Control-plane registry collection loading entry points."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.control_plane.registry.owners import owned_index_bundle
from openclaw.control_plane.registry_loader.collection_sources import (
    _load_agent_module_dirs,
    _load_agent_modules,
    _load_collection,
    _load_collection_dirs,
    _merge_collection_rows,
)
from openclaw.control_plane.registry_loader.extension_runtime import (
    _merge_job_runners,
    _merge_runtime_adapter_registries,
)
from openclaw.control_plane.registry_loader.virtual_surfaces import (
    _build_expected_agent_internal_assembly_registry,
    _build_virtual_assembly_row,
    _ensure_agent_control_plane_registry,
    _ensure_agent_internal_assembly_registry,
    _load_agent_derivation_surface,
    _materialize_virtual_agent_surfaces,
    _materialize_virtual_collection,
    _module_change_control_doc_paths,
    _relative_posix_path,
)
from openclaw.control_plane.schema import load_schema


_OWNED_COLLECTION_KEYS = {
    'jobs': ('jobsById', 'jobsByQualifiedId', 'jobsByOwner', 'jobsAmbiguousIds', 'control-plane jobs'),
    'models': ('modelsById', 'modelsByQualifiedId', 'modelsByOwner', 'modelsAmbiguousIds', 'control-plane models'),
    'targets': ('targetsById', 'targetsByQualifiedId', 'targetsByOwner', 'targetsAmbiguousIds', 'control-plane targets'),
    'agentGroups': ('agentGroupsById', 'agentGroupsByQualifiedId', 'agentGroupsByOwner', 'agentGroupsAmbiguousIds', 'control-plane agent groups'),
    'agentModules': ('agentModulesById', 'agentModulesByQualifiedId', 'agentModulesByOwner', 'agentModulesAmbiguousIds', 'control-plane agent modules'),
    'agents': ('agentsById', 'agentsByQualifiedId', 'agentsByOwner', 'agentsAmbiguousIds', 'control-plane agents'),
    'implementations': ('implementationsById', 'implementationsByQualifiedId', 'implementationsByOwner', 'implementationsAmbiguousIds', 'control-plane implementations'),
    'skillSets': ('skillSetsById', 'skillSetsByQualifiedId', 'skillSetsByOwner', 'skillSetsAmbiguousIds', 'control-plane skill sets'),
    'permissionPolicies': ('permissionPoliciesById', 'permissionPoliciesByQualifiedId', 'permissionPoliciesByOwner', 'permissionPoliciesAmbiguousIds', 'control-plane permission policies'),
    'toolsets': ('toolsetsById', 'toolsetsByQualifiedId', 'toolsetsByOwner', 'toolsetsAmbiguousIds', 'control-plane toolsets'),
}


def _attach_owned_indexes(payload: dict[str, Any], collection_key: str) -> None:
    rows = [row for row in (payload.get(collection_key) or []) if isinstance(row, dict)]
    by_id_key, by_qualified_key, by_owner_key, ambiguous_key, label = _OWNED_COLLECTION_KEYS[collection_key]
    bundle = owned_index_bundle(rows, label=label)
    payload[by_id_key] = bundle['byId']
    payload[by_qualified_key] = bundle['byQualifiedId']
    payload[by_owner_key] = bundle['byOwner']
    payload[ambiguous_key] = bundle['ambiguousIds']


def _load_registry_collections(context: dict[str, Any]) -> dict[str, Any]:
    registry_inputs = context['registryInputs']
    schema_paths = context['schemaPaths']
    enabled_extension_ids = list(context.get('enabledExtensionIds') or [])
    known_extension_ids = set(context.get('knownExtensionIds') or [])
    owner_by_directory = registry_inputs.get('owner_by_directory') if isinstance(registry_inputs.get('owner_by_directory'), dict) else {}
    shared_directories = {
        'jobs': {
            item.resolve()
            for extension in context['extensions']
            for item in ((extension.get('registry') or {}).get('jobsDirs') or [])
            if isinstance(item, Path)
        },
        'models': {
            item.resolve()
            for extension in context['extensions']
            for item in ((extension.get('registry') or {}).get('modelsDirs') or [])
            if isinstance(item, Path)
        },
        'targets': {
            item.resolve()
            for extension in context['extensions']
            for item in ((extension.get('registry') or {}).get('targetsDirs') or [])
            if isinstance(item, Path)
        },
        'agentGroups': {
            item.resolve()
            for extension in context['extensions']
            for item in ((extension.get('registry') or {}).get('agentGroupsDirs') or [])
            if isinstance(item, Path)
        },
        'agentModules': {
            item.resolve()
            for extension in context['extensions']
            for item in ((extension.get('registry') or {}).get('agentModulesDirs') or [])
            if isinstance(item, Path)
        },
    }
    jobs, jobs_by_id = _load_collection_dirs(
        registry_inputs['jobs_dirs'],
        'control-plane jobs',
        load_schema(schema_paths['jobs']),
        allow_missing=True,
        shared_directories=shared_directories['jobs'],
        owner_by_directory=owner_by_directory.get('jobs') if isinstance(owner_by_directory.get('jobs'), dict) else {},
        enabled_extension_ids=enabled_extension_ids,
        known_extension_ids=known_extension_ids,
    )
    models, models_by_id = _load_collection_dirs(
        registry_inputs['models_dirs'],
        'control-plane models',
        load_schema(schema_paths['models']),
        allow_missing=True,
        shared_directories=shared_directories['models'],
        owner_by_directory=owner_by_directory.get('models') if isinstance(owner_by_directory.get('models'), dict) else {},
        enabled_extension_ids=enabled_extension_ids,
        known_extension_ids=known_extension_ids,
    )
    targets, targets_by_id = _load_collection_dirs(
        registry_inputs['targets_dirs'],
        'control-plane targets',
        load_schema(schema_paths['targets']),
        allow_missing=True,
        shared_directories=shared_directories['targets'],
        owner_by_directory=owner_by_directory.get('targets') if isinstance(owner_by_directory.get('targets'), dict) else {},
        enabled_extension_ids=enabled_extension_ids,
        known_extension_ids=known_extension_ids,
    )

    agent_groups: list[dict[str, Any]] = []
    agent_groups_by_id: dict[str, dict[str, Any]] = {}
    if registry_inputs['agent_groups_dirs']:
        agent_groups, agent_groups_by_id = _load_collection_dirs(
            registry_inputs['agent_groups_dirs'],
            'control-plane agent groups',
            load_schema(schema_paths['agentGroups']),
            shared_directories=shared_directories['agentGroups'],
            owner_by_directory=owner_by_directory.get('agentGroups') if isinstance(owner_by_directory.get('agentGroups'), dict) else {},
            enabled_extension_ids=enabled_extension_ids,
            known_extension_ids=known_extension_ids,
        )

    agent_modules: list[dict[str, Any]] = []
    agent_modules_by_id: dict[str, dict[str, Any]] = {}
    if registry_inputs['agent_modules_dirs']:
        agent_modules, agent_modules_by_id = _load_agent_module_dirs(
            registry_inputs['agent_modules_dirs'],
            'control-plane agent modules',
            load_schema(schema_paths['agentModules']),
            shared_directories=shared_directories['agentModules'],
            owner_by_directory=owner_by_directory.get('agentModules') if isinstance(owner_by_directory.get('agentModules'), dict) else {},
            enabled_extension_ids=enabled_extension_ids,
            known_extension_ids=known_extension_ids,
        )

    runtime_adapters: list[dict[str, Any]] = []
    runtime_adapters_by_id: dict[str, dict[str, Any]] = {}
    runtime_adapter_specs_by_id: dict[str, Any] = {}
    if registry_inputs['runtime_adapter_registry_paths']:
        runtime_adapters, runtime_adapters_by_id, runtime_adapter_specs_by_id = _merge_runtime_adapter_registries(
            registry_inputs['runtime_adapter_registry_paths'],
            schema_path=schema_paths['runtimeAdapters'],
        )

    payload = {
        'jobs': jobs,
        'jobsById': jobs_by_id,
        'models': models,
        'modelsById': models_by_id,
        'targets': targets,
        'targetsById': targets_by_id,
        'agentGroups': agent_groups,
        'agentGroupsById': agent_groups_by_id,
        'agentModules': agent_modules,
        'agentModulesById': agent_modules_by_id,
        'runtimeAdapters': runtime_adapters,
        'runtimeAdaptersById': runtime_adapters_by_id,
        'runtimeAdapterSpecsById': runtime_adapter_specs_by_id,
        **_materialize_virtual_agent_surfaces(
            agent_modules=agent_modules,
            base=context['base'],
            path=context['path'],
            schema_paths=schema_paths,
        ),
    }
    for collection_key in _OWNED_COLLECTION_KEYS:
        if collection_key in payload:
            _attach_owned_indexes(payload, collection_key)
    return payload
