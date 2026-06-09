#!/usr/bin/env python3
"""控制平面 API 摘要中的纯条目渲染器。"""
from __future__ import annotations

from typing import Any

from openclaw.control_plane.api.access import enrich_run_row as _enrich_run_row
from openclaw.control_plane.registry.job_execution_plans import execution_plan_public_payload
from openclaw.control_plane.registry.owners import qualified_registry_id, row_owner_id
from openclaw.lib.io.json_access import json_array, json_object


def _owned_index(registry: dict[str, Any], collection_key: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    qualified = registry.get(f'{collection_key}ByQualifiedId')
    if isinstance(qualified, dict):
        result.update({str(key): value for key, value in qualified.items() if isinstance(value, dict)})
    by_local_id = registry.get(f'{collection_key}ById')
    if isinstance(by_local_id, dict):
        result.update({str(key): value for key, value in by_local_id.items() if isinstance(value, dict)})
    return result


def _owner_metadata(row: dict[str, Any]) -> dict[str, str]:
    local_id = str(row.get('id') or '').strip()
    owner_id = row_owner_id(row)
    qualified_id = str(row.get('qualifiedId') or '').strip() or qualified_registry_id(owner_id, local_id)
    return {
        'ownerId': owner_id,
        'qualifiedId': qualified_id,
        'sourceExtensionId': str(row.get('sourceExtensionId') or '').strip(),
        'extensionId': str(row.get('extensionId') or '').strip(),
    }


def _state_for_job(jobs_state: dict[str, Any], key: str) -> dict[str, Any]:
    normalized = str(key or '').strip()
    payload = jobs_state.get(normalized) if normalized else None
    return json_object(payload)


def _history_for_job(history_by_job: dict[str, list[dict[str, Any]]], key: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    normalized = str(key or '').strip()
    for row in history_by_job.get(normalized, []):
        marker = id(row)
        if marker in seen:
            continue
        seen.add(marker)
        rows.append(row)
    return rows

def _agent_group_refs(agent: dict[str, Any]) -> list[str]:
    refs = json_array(agent.get('resolvedGroupRefs'))
    return [str(item).strip() for item in refs if str(item).strip()]


def _resolved_group_refs(group: dict[str, Any], resolved_field: str) -> list[str]:
    refs = json_array(group.get(resolved_field))
    return [str(item).strip() for item in refs if str(item).strip()]


def _agent_module_ref(agent: dict[str, Any]) -> str:
    governance = json_object(agent.get('governance'))
    return str(governance.get('moduleRef') or '').strip()


def _job_rows(
    registry: dict[str, Any],
    state: dict[str, Any],
    history: list[dict[str, Any]],
    manifest_memo: dict[str, dict[str, Any] | None] | None = None,
) -> list[dict[str, Any]]:
    jobs_state = json_object(state.get('jobs'))
    history_by_job: dict[str, list[dict[str, Any]]] = {}
    agents_by_id = json_object(registry.get('agentsById'))
    agents_by_ref = _owned_index(registry, 'agents')
    for row in history:
        job_id = str(row.get('job_id') or row.get('jobId') or '')
        if not job_id:
            continue
        history_by_job.setdefault(job_id, []).append(_enrich_run_row(row, manifest_memo))
    rows: list[dict[str, Any]] = []
    for job in registry.get('jobs', []):
        if not isinstance(job, dict):
            continue
        job_id = str(job.get('id') or '')
        owner_meta = _owner_metadata(job)
        qualified_id = owner_meta['qualifiedId']
        runtime_job_key = str(job.get('resolvedRuntimeJobKey') or qualified_id or job_id).strip()
        job_state = _state_for_job(jobs_state, runtime_job_key)
        agent_ref = str(job.get('agentRef') or '')
        resolved_agent_ref = str(job.get('resolvedAgentQualifiedRef') or job.get('resolvedAgentRef') or '').strip()
        agent = json_object(agents_by_ref.get(resolved_agent_ref) or agents_by_ref.get(agent_ref) or agents_by_id.get(agent_ref))
        agent_group_refs = _agent_group_refs(agent) if agent else []
        agent_module_ref = _agent_module_ref(agent) if agent else ''
        rows.append({
            'id': job_id,
            **owner_meta,
            'runtimeJobKey': runtime_job_key,
            'title': str(job.get('title') or ''),
            'enabled': bool(job.get('enabled', True)),
            'resolvedOrder': int(job.get('resolvedOrder') or job.get('order') or 0),
            'agentRef': agent_ref,
            'resolvedAgentRef': resolved_agent_ref,
            'agentGroupRefs': agent_group_refs,
            'agentModuleRef': agent_module_ref,
            'modelProfileRef': str(job.get('modelProfileRef') or ''),
            'resolvedModelProfileRef': str(job.get('resolvedModelProfileRef') or ''),
            'resolvedModelProfileQualifiedRef': str(job.get('resolvedModelProfileQualifiedRef') or ''),
            'targetBindingRef': str(job.get('targetBindingRef') or ''),
            'resolvedTargetBindingRef': str(job.get('resolvedTargetBindingRef') or ''),
            'schedule': job.get('schedule') if isinstance(job.get('schedule'), dict) else {},
            'dependsOn': job.get('resolvedDependsOn') if isinstance(job.get('resolvedDependsOn'), list) else (job.get('dependsOn') if isinstance(job.get('dependsOn'), list) else []),
            'operationRef': str(job.get('operationRef') or ''),
            'resolvedExecutionPlan': execution_plan_public_payload(job.get('resolvedExecutionPlan')) if isinstance(job.get('resolvedExecutionPlan'), dict) else {},
            'resolvedContract': job.get('resolvedContract') if isinstance(job.get('resolvedContract'), dict) else {},
            'resolvedRecoveryStep': job.get('resolvedRecoveryStep') if isinstance(job.get('resolvedRecoveryStep'), dict) else {},
            'timeoutSeconds': int(job.get('timeoutSeconds') or 0),
            'concurrencyPolicy': str(job.get('concurrencyPolicy') or 'forbid'),
            'retryPolicy': job.get('retryPolicy') if isinstance(job.get('retryPolicy'), dict) else {},
            'state': job_state,
            'latestRun': _enrich_run_row({
                'run_manifest_path': job_state.get('lastRunManifestPath'),
                'result_manifest_path': job_state.get('lastResultManifestPath'),
                'artifacts_path': job_state.get('lastArtifactsPath'),
                'run_dir': job_state.get('lastRunDir'),
                'log_path': job_state.get('lastLogPath'),
                'runId': job_state.get('lastRunId'),
            }, manifest_memo) if job_state else None,
            'recentRuns': _history_for_job(history_by_job, runtime_job_key),
        })
    return rows


def _job_runtime_status(row: dict[str, Any]) -> str:
    state = json_object(row.get('state'))
    status = str(state.get('currentStatus') or '').strip().lower()
    if status:
        return status
    return 'disabled' if not bool(row.get('enabled', True)) else 'configured'


def _group_health_status(status_counts: dict[str, int], *, enabled_job_count: int, configured_job_count: int) -> str:
    if configured_job_count <= 0:
        return 'unbound'
    if enabled_job_count <= 0:
        return 'disabled'
    for status in ('failed', 'blocked', 'retry_pending'):
        if status_counts.get(status, 0) > 0:
            return status
    if status_counts.get('running', 0) > 0:
        return 'running'
    if status_counts.get('scheduled', 0) > 0:
        return 'scheduled'
    if status_counts.get('succeeded', 0) > 0 and sum(count for key, count in status_counts.items() if key not in {'succeeded', 'disabled'}) == 0:
        return 'healthy'
    return 'configured'


def _render_agent_group_items(registry: dict[str, Any], jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    jobs_by_agent: dict[str, list[dict[str, Any]]] = {}
    for row in jobs:
        for ref in {str(row.get('agentRef') or ''), str(row.get('resolvedAgentRef') or '')}:
            if ref:
                jobs_by_agent.setdefault(ref, []).append(row)
    agents_by_id = _owned_index(registry, 'agents')
    rows: list[dict[str, Any]] = []
    for group in registry.get('agentGroups', []):
        if not isinstance(group, dict):
            continue
        member_refs = _resolved_group_refs(group, 'resolvedMembers')
        schedule_policy = json_object(group.get('schedulePolicy'))
        dependency_policy = json_object(group.get('dependencyPolicy'))
        member_rows: list[dict[str, Any]] = []
        group_jobs: list[dict[str, Any]] = []
        status_counts: dict[str, int] = {}
        failing_job_ids: list[str] = []
        for agent_ref in member_refs:
            member_jobs = list(jobs_by_agent.get(agent_ref, []))
            group_jobs.extend(member_jobs)
            agent = agents_by_id.get(agent_ref) if isinstance(agents_by_id, dict) else None
            module_ref = _agent_module_ref(agent) if isinstance(agent, dict) else ''
            member_status_counts: dict[str, int] = {}
            for row in member_jobs:
                status = _job_runtime_status(row)
                member_status_counts[status] = member_status_counts.get(status, 0) + 1
                status_counts[status] = status_counts.get(status, 0) + 1
                if status in {'failed', 'blocked', 'retry_pending'}:
                    failing_job_ids.append(str(row.get('id') or ''))
            member_rows.append({
                'agentRef': agent_ref,
                'moduleRef': module_ref,
                'jobIds': [str(item.get('id') or '') for item in member_jobs],
                'jobStatusCounts': member_status_counts,
                'configured': isinstance(agent, dict),
            })
        configured_job_count = len(group_jobs)
        enabled_job_count = sum(1 for row in group_jobs if bool(row.get('enabled', True)))
        health_status = _group_health_status(status_counts, enabled_job_count=enabled_job_count, configured_job_count=configured_job_count)
        release_policy = json_object(group.get('resolvedReleasePolicy')) or json_object(group.get('releasePolicy'))
        rows.append({
            'id': str(group.get('id') or ''),
            **_owner_metadata(group),
            'title': str(group.get('title') or ''),
            'mission': str(group.get('mission') or ''),
            'ownerDomain': str(group.get('ownerDomain') or ''),
            'memberAgentRefs': member_refs,
            'entryAgentRefs': _resolved_group_refs(group, 'resolvedEntryAgentRefs'),
            'exitAgentRefs': _resolved_group_refs(group, 'resolvedExitAgentRefs'),
            'jobRefs': [str(item).strip() for item in (schedule_policy.get('jobRefs') or []) if str(item).strip()],
            'members': member_rows,
            'dependencyPolicy': dependency_policy,
            'recoveryPolicy': group.get('resolvedRecoveryPolicy') if isinstance(group.get('resolvedRecoveryPolicy'), dict) else (group.get('recoveryPolicy') if isinstance(group.get('recoveryPolicy'), dict) else {}),
            'observabilityContract': group.get('observabilityContract') if isinstance(group.get('observabilityContract'), dict) else {},
            'releasePolicy': release_policy,
            'health': {
                'status': health_status,
                'configuredJobCount': configured_job_count,
                'enabledJobCount': enabled_job_count,
                'jobStatusCounts': status_counts,
                'failingJobIds': sorted({item for item in failing_job_ids if item}),
                'haltOnMemberFailure': bool(dependency_policy.get('haltOnMemberFailure', False)),
            },
            'sourcePath': str(group.get('sourcePath') or ''),
        })
    return rows


def _render_agent_module_items(registry: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for module in registry.get('agentModules', []):
        if not isinstance(module, dict):
            continue
        logic = json_object(module.get('logic'))
        governance = json_object(module.get('governance'))
        runtime = json_object(module.get('runtime'))
        assets = json_object(module.get('assets'))
        operations = json_object(module.get('operations'))
        control_plane = json_object(module.get('controlPlane'))
        control_plane_agent = json_object(control_plane.get('agent'))
        capabilities = json_object(control_plane_agent.get('capabilities'))
        assembly = json_object(module.get('assembly'))
        resolved_group_refs = [str(item).strip() for item in json_array(module.get('resolvedGroupRefs')) if str(item).strip()]
        entrypoint_kinds = [str(item).strip() for item in json_array(runtime.get('entrypointKinds')) if str(item).strip()]
        runtime_adapter_refs = [str(item).strip() for item in json_array(runtime.get('runtimeAdapterRefs')) if str(item).strip()]
        change_control_doc_paths = [str(item).strip() for item in json_array(governance.get('changeControlDocPaths')) if str(item).strip()]
        logic_source_paths = [str(item).strip() for item in json_array(logic.get('sourcePaths')) if str(item).strip()]
        job_refs: list[str] = []
        target_binding_refs: list[str] = []
        for payload in operations.values():
            if not isinstance(payload, dict):
                continue
            job_bindings = json_object(payload.get('jobBindings'))
            declared_job_refs = [str(item).strip() for item in json_array(payload.get('jobRefs')) if str(item).strip()]
            current_job_refs = [str(item).strip() for item in job_bindings.keys()] or declared_job_refs
            for job_ref in current_job_refs:
                if job_ref and job_ref not in job_refs:
                    job_refs.append(job_ref)
            for binding in job_bindings.values():
                if not isinstance(binding, dict):
                    continue
                target_binding_ref = str(binding.get('targetBindingRef') or '').strip()
                if target_binding_ref and target_binding_ref not in target_binding_refs:
                    target_binding_refs.append(target_binding_ref)
        rows.append({
            'id': str(module.get('id') or ''),
            **_owner_metadata(module),
            'agentRef': str(module.get('agentRef') or ''),
            'resolvedAgentRef': str(module.get('resolvedAgentRef') or ''),
            'title': str(module.get('title') or ''),
            'version': str(module.get('version') or ''),
            'ownerDomain': str(module.get('ownerDomain') or ''),
            'moduleKind': str(module.get('moduleKind') or 'worker'),
            'implementationRef': str(logic.get('implementationRef') or ''),
            'runtime': {
                'entrypointKinds': entrypoint_kinds,
                'runtimeAdapterRefs': runtime_adapter_refs,
            },
            'governance': {
                'changeControlDocPaths': change_control_doc_paths,
            },
            'resolvedGroupRefs': resolved_group_refs,
            'assembly': {
                'skillSetRef': str(assembly.get('skillSetRef') or ''),
                'permissionPolicyRef': str(assembly.get('permissionPolicyRef') or ''),
                'toolsetRef': str(assembly.get('toolsetRef') or ''),
            },
            'assets': dict(assets),
            'logic': {
                'sourcePaths': logic_source_paths,
            },
            'bindings': {
                'jobRefs': job_refs,
                'targetBindingRefs': target_binding_refs,
            },
            'pluggability': {
                'bindingMode': 'scheduler_bound' if job_refs else 'standalone',
                'dropInRegistration': not job_refs,
                'hasOperatorGuide': bool(str(assets.get('agentsMdPath') or '').strip()),
                'hasThinLauncher': bool(str(assets.get('binPath') or '').strip()),
                'externalDispatch': bool(capabilities.get('externalDispatch', False)),
            },
            'moduleDir': str(module.get('moduleDir') or ''),
            'sourcePath': str(module.get('sourcePath') or ''),
        })
    return rows


def _render_skill_set_items(registry: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in registry.get('skillSets', []):
        if not isinstance(item, dict):
            continue
        governance = json_object(item.get('governance'))
        derivation = json_object(item.get('derivation'))
        rows.append({
            'id': str(item.get('id') or ''),
            **_owner_metadata(item),
            'moduleRef': str(item.get('moduleRef') or ''),
            'resolvedModuleRef': str(item.get('resolvedModuleRef') or ''),
            'title': str(item.get('title') or ''),
            'version': str(item.get('version') or ''),
            'ownerDomain': str(item.get('ownerDomain') or ''),
            'sourcePath': str(item.get('resolvedSourcePath') or ''),
            'skills': [str(x).strip() for x in json_array(item.get('resolvedSkills')) if str(x).strip()],
            'governance': {'changeControlDocPaths': [str(x).strip() for x in json_array(governance.get('changeControlDocPaths')) if str(x).strip()]},
            'derivation': {
                'mode': str(derivation.get('mode') or ''),
                'moduleManifestPath': str(derivation.get('moduleManifestPath') or ''),
                'assetKey': str(derivation.get('assetKey') or ''),
            },
            'registrySourcePath': str(item.get('sourcePath') or ''),
        })
    return rows


def _render_permission_policy_items(registry: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in registry.get('permissionPolicies', []):
        if not isinstance(item, dict):
            continue
        governance = json_object(item.get('governance'))
        derivation = json_object(item.get('derivation'))
        resolved = json_object(item.get('resolvedPolicy'))
        rows.append({
            'id': str(item.get('id') or ''),
            **_owner_metadata(item),
            'moduleRef': str(item.get('moduleRef') or ''),
            'resolvedModuleRef': str(item.get('resolvedModuleRef') or ''),
            'title': str(item.get('title') or ''),
            'version': str(item.get('version') or ''),
            'ownerDomain': str(item.get('ownerDomain') or ''),
            'sourcePath': str(item.get('resolvedSourcePath') or ''),
            'permissions': dict(resolved),
            'governance': {'changeControlDocPaths': [str(x).strip() for x in json_array(governance.get('changeControlDocPaths')) if str(x).strip()]},
            'derivation': {
                'mode': str(derivation.get('mode') or ''),
                'moduleManifestPath': str(derivation.get('moduleManifestPath') or ''),
                'assetKey': str(derivation.get('assetKey') or ''),
            },
            'registrySourcePath': str(item.get('sourcePath') or ''),
        })
    return rows


def _render_toolset_items(registry: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in registry.get('toolsets', []):
        if not isinstance(item, dict):
            continue
        governance = json_object(item.get('governance'))
        derivation = json_object(item.get('derivation'))
        resolved = json_object(item.get('resolvedToolset'))
        rows.append({
            'id': str(item.get('id') or ''),
            **_owner_metadata(item),
            'moduleRef': str(item.get('moduleRef') or ''),
            'resolvedModuleRef': str(item.get('resolvedModuleRef') or ''),
            'title': str(item.get('title') or ''),
            'version': str(item.get('version') or ''),
            'ownerDomain': str(item.get('ownerDomain') or ''),
            'sourcePath': str(item.get('resolvedSourcePath') or ''),
            'tools': dict(resolved),
            'governance': {'changeControlDocPaths': [str(x).strip() for x in json_array(governance.get('changeControlDocPaths')) if str(x).strip()]},
            'derivation': {
                'mode': str(derivation.get('mode') or ''),
                'moduleManifestPath': str(derivation.get('moduleManifestPath') or ''),
                'assetKey': str(derivation.get('assetKey') or ''),
            },
            'registrySourcePath': str(item.get('sourcePath') or ''),
        })
    return rows
