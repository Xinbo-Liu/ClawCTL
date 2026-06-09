#!/usr/bin/env python3
"""Control-plane registry runtime derivation and validation helpers."""
from __future__ import annotations

from typing import Any

from openclaw.control_plane import registry_validation as registry_validation_lib
from openclaw.control_plane.registry_validation import runtime as registry_validation_runtime_lib
from openclaw.control_plane.registry.binding_topology import (
    _derive_group_topologies_from_groups,
    _derive_job_bindings_from_modules,
)
from openclaw.control_plane.registry_loader.collections import _merge_job_runners
from openclaw.control_plane.registry.support import _ensure_unique_text_list
from openclaw.lib.cli.common import CliError
from openclaw.lib.io.json_access import json_object
from openclaw.lib.repo.layout import resolve_repo_root


def _combined_owned_index(collections: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    local = collections.get(f'{key}ById') if isinstance(collections.get(f'{key}ById'), dict) else {}
    qualified = collections.get(f'{key}ByQualifiedId') if isinstance(collections.get(f'{key}ByQualifiedId'), dict) else {}
    return {**qualified, **local}


def _derive_registry_runtime_state(context: dict[str, Any], collections: dict[str, Any]) -> dict[str, Any]:
    """Derive runtime-only state needed by registry validation and rendering."""
    agent_modules = collections['agentModules']
    job_bindings_by_job_id = (
        _derive_job_bindings_from_modules(agent_modules, extensions=context['extensions'], collections=collections)
        if agent_modules else {}
    )
    grouped_job_bindings_by_job_id: dict[str, dict[str, Any]] = {}
    group_topologies_by_group_id: dict[str, dict[str, Any]] = {}
    if collections['agentGroups']:
        grouped_job_bindings_by_job_id, group_topologies_by_group_id = _derive_group_topologies_from_groups(
            collections['agentGroups'],
            collections['jobsById'],
            job_bindings_by_job_id,
            collections=collections,
        )
        for job_id, binding in grouped_job_bindings_by_job_id.items():
            if job_id in job_bindings_by_job_id:
                job_bindings_by_job_id[job_id] = dict(binding)
    job_runners, job_runners_by_id, binding_runner_ids = _merge_job_runners(context['extensions'])
    return {
        'jobBindingsByJobId': job_bindings_by_job_id,
        'groupTopologiesByGroupId': group_topologies_by_group_id,
        'jobRunners': job_runners,
        'jobRunnersById': job_runners_by_id,
        'bindingRunnerIds': binding_runner_ids,
    }


def _validate_implementation_rows(
    collections: dict[str, Any],
) -> None:
    registry_validation_lib._validate_implementation_rows(
        collections['implementations'],
        collections['runtimeAdaptersById'],
        collections['runtimeAdapterSpecsById'],
    )


def _validate_group_and_target_runtime_rows(
    context: dict[str, Any],
    collections: dict[str, Any],
    runtime_state: dict[str, Any],
) -> None:
    path = context['path']
    registry_inputs = context['registryInputs']
    registry_validation_runtime_lib._validate_agent_group_rows(
        collections['agentGroups'],
        collections['agentsById'],
        _combined_owned_index(collections, 'jobs'),
        job_bindings_by_job_id=runtime_state['jobBindingsByJobId'],
        group_topologies_by_group_id=runtime_state['groupTopologiesByGroupId'],
        repo_root=resolve_repo_root(path),
        config_path=path,
        extensions=context['extensions'],
        collections=collections,
    )
    if not collections['targets']:
        return
    if not registry_inputs['dispatch_target_registry_paths'] or not registry_inputs['dispatch_provider_registry_paths']:
        raise CliError('targets 已启用，但当前 profile/extension 未提供 dispatch registry 真源', 2)
    registry_validation_runtime_lib._validate_target_rows(
        collections['targets'],
        _combined_owned_index(collections, 'agents'),
        dispatch_target_registry_paths=registry_inputs['dispatch_target_registry_paths'],
        dispatch_provider_registry_paths=registry_inputs['dispatch_provider_registry_paths'],
    )


def _validate_module_agent_and_assembly_rows(
    collections: dict[str, Any],
) -> None:
    registry_validation_runtime_lib._validate_model_rows(collections['models'])
    registry_validation_lib._validate_agent_module_rows(
        collections['agentModules'],
        _combined_owned_index(collections, 'agents'),
        _combined_owned_index(collections, 'implementations'),
        _combined_owned_index(collections, 'agentGroups'),
        collections['runtimeAdaptersById'],
        _combined_owned_index(collections, 'jobs'),
    )
    registry_validation_runtime_lib._validate_agent_rows(
        collections['agents'],
        _combined_owned_index(collections, 'models'),
        _combined_owned_index(collections, 'agentModules'),
        _combined_owned_index(collections, 'implementations'),
        _combined_owned_index(collections, 'agentGroups'),
    )
    registry_validation_lib._validate_skill_set_rows(collections['skillSets'], _combined_owned_index(collections, 'agentModules'))
    registry_validation_lib._validate_permission_policy_rows(collections['permissionPolicies'], _combined_owned_index(collections, 'agentModules'))
    registry_validation_lib._validate_toolset_rows(collections['toolsets'], _combined_owned_index(collections, 'agentModules'))


def _validate_and_sort_job_rows(
    context: dict[str, Any],
    collections: dict[str, Any],
    runtime_state: dict[str, Any],
) -> None:
    payload = context['payload']
    registry_validation_runtime_lib._validate_job_rows(
        collections['jobs'],
        _combined_owned_index(collections, 'jobs'),
        _combined_owned_index(collections, 'agents'),
        _combined_owned_index(collections, 'models'),
        _combined_owned_index(collections, 'targets'),
        _combined_owned_index(collections, 'agentModules'),
        _combined_owned_index(collections, 'agentGroups'),
        runtime_state['jobBindingsByJobId'],
        runtime_state['jobRunnersById'],
        runtime_state['bindingRunnerIds'],
        default_timezone=str((payload.get('defaults') or {}).get('timezone') or '').strip(),
        config_path=context['path'],
    )
    collections['jobs'] = sorted(
        collections['jobs'],
        key=lambda row: (int(row.get('resolvedOrder') or 1000), str(row.get('id') or '')),
    )


def _validate_group_schedule_job_refs(
    collections: dict[str, Any],
) -> None:
    jobs_by_ref = _combined_owned_index(collections, 'jobs')
    for group in collections['agentGroups']:
        group_owner_id = str(group.get('ownerId') or group.get('extensionId') or '')
        schedule_policy = json_object(group.get('schedulePolicy'))
        for job_ref in _ensure_unique_text_list(
            schedule_policy.get('jobRefs') or [],
            label=f"agent group {group.get('id')} schedulePolicy.jobRefs",
        ):
            qualified_job_ref = f'{group_owner_id}:{job_ref}' if group_owner_id and ':' not in job_ref else job_ref
            if job_ref not in jobs_by_ref and qualified_job_ref not in jobs_by_ref:
                raise CliError(f"agent group {group.get('id')} schedulePolicy.jobRefs 未注册：{job_ref}", 2)


def _validate_registry_collections(
    context: dict[str, Any],
    collections: dict[str, Any],
    runtime_state: dict[str, Any],
) -> None:
    """Validate registry collections in fixed staged order."""
    payload = context.get('payload') if isinstance(context.get('payload'), dict) else {}
    registry_validation_lib._validate_default_timezone(
        str((payload.get('defaults') or {}).get('timezone') or '').strip()
    )
    _validate_implementation_rows(collections)
    _validate_group_and_target_runtime_rows(context, collections, runtime_state)
    _validate_module_agent_and_assembly_rows(collections)
    _validate_and_sort_job_rows(context, collections, runtime_state)
    _validate_group_schedule_job_refs(collections)
