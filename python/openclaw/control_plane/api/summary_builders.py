#!/usr/bin/env python3
"""控制平面 API 视图的摘要构建器。"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from openclaw.control_plane.api.access import (
    agent_group_recent_access_map as _agent_group_recent_access_map_impl,
    build_agent_group_access_items as _build_agent_group_access_items_impl,
    enrich_run_row as _enrich_run_row,
    render_agent_access_log_summary_uncached as _render_agent_access_log_summary_uncached_impl,
)
from openclaw.control_plane.api.registry_items import (
    _job_rows,
    _render_agent_group_items,
    _render_agent_module_items,
    _render_permission_policy_items,
    _render_skill_set_items,
    _render_toolset_items,
)
from openclaw.control_plane.api.agent_group_release import (
    build_agent_group_acceptance_binding as _build_agent_group_acceptance_binding,
    build_agent_group_release_gate as _build_agent_group_release_gate,
    group_run_ledger_status as _group_run_ledger_status,
)
from openclaw.control_plane.registry.store import read_json, runtime_files, tail_history
from openclaw.control_plane.registry.owners import qualified_registry_id, resolve_collection_ref, row_owner_id
from openclaw.control_plane.run_ledger import apply_latest_agent_access_overlay, build_run_ledger_summary
from openclaw.control_plane.state_paths import resolve_control_plane_state_root


def _now_epoch() -> int:
    return int(time.time())


def _state_root() -> Path:
    return resolve_control_plane_state_root()


def _state_payload(registry: dict[str, Any]) -> dict[str, Any]:
    files = runtime_files(_state_root(), registry)
    state = read_json(files.state_dir / 'state.json', {'jobs': {}})
    status = read_json(files.status_path, {})
    heartbeat = read_json(files.heartbeat_path, {})
    history_limit = int(((registry.get('defaults') or {}).get('recentHistoryLimit') or 20))
    history = tail_history(files.history_path, history_limit)
    return {
        'files': files,
        'state': state if isinstance(state, dict) else {'jobs': {}},
        'status': status if isinstance(status, dict) else {},
        'heartbeat': heartbeat if isinstance(heartbeat, dict) else {},
        'history': history,
    }


def _build_agent_group_access_items(
    registry: dict[str, Any],
    *,
    limit: int = 200,
    timeline_limit: int = 20,
    group_ref: str = '',
    status: str = '',
    source: str = '',
) -> list[dict[str, Any]]:
    return _build_agent_group_access_items_impl(
        registry,
        state_payload_builder=_state_payload,
        limit=limit,
        timeline_limit=timeline_limit,
        group_ref=group_ref,
        status=status,
        source=source,
    )


def _agent_group_recent_access_map(
    registry: dict[str, Any],
    *,
    limit: int = 200,
    timeline_limit: int = 5,
) -> dict[str, dict[str, Any]]:
    return _agent_group_recent_access_map_impl(
        registry,
        state_payload_builder=_state_payload,
        limit=limit,
        timeline_limit=timeline_limit,
    )


def _render_agent_access_log_summary_uncached(
    registry: dict[str, Any],
    *,
    limit: int = 50,
    agent_ref: str = '',
    group_ref: str = '',
    job_id: str = '',
    status: str = '',
    source: str = '',
) -> dict[str, Any]:
    return _render_agent_access_log_summary_uncached_impl(
        registry,
        state_payload_builder=_state_payload,
        limit=limit,
        agent_ref=agent_ref,
        group_ref=group_ref,
        job_id=job_id,
        status=status,
        source=source,
    )


def _run_ledger_with_latest_access(registry: dict[str, Any], runtime: dict[str, Any] | None = None) -> dict[str, Any]:
    runtime_payload = runtime or _state_payload(registry)
    run_ledger = build_run_ledger_summary(registry, runtime_payload['state'])
    access_limit = max(200, int(((registry.get('defaults') or {}).get('recentHistoryLimit') or 20)) * 10)
    agent_access_log = _render_agent_access_log_summary_uncached(registry, limit=access_limit)
    return apply_latest_agent_access_overlay(run_ledger, agent_access_log)


def _render_control_plane_summary_uncached(registry: dict[str, Any]) -> dict[str, Any]:
    runtime = _state_payload(registry)
    manifest_memo: dict[str, dict[str, Any] | None] = {}
    heartbeat_epoch = int(runtime['heartbeat'].get('updated_at_epoch') or 0)
    age = max(0, _now_epoch() - heartbeat_epoch) if heartbeat_epoch else None
    jobs = _job_rows(registry, runtime['state'], runtime['history'], manifest_memo)
    agent_groups = _render_agent_groups_summary_uncached(registry).get('items', [])
    agent_modules = _render_agent_module_items(registry)
    skill_sets = _render_skill_set_items(registry)
    permission_policies = _render_permission_policy_items(registry)
    toolsets = _render_toolset_items(registry)
    agent_access_log = _render_agent_access_log_summary_uncached(registry, limit=max(20, int(((registry.get('defaults') or {}).get('recentHistoryLimit') or 20))))
    agent_group_access = _render_agent_group_access_summary_uncached(registry, limit=max(100, int(((registry.get('defaults') or {}).get('recentHistoryLimit') or 20)) * 10), timeline_limit=10)
    run_ledger_summary = apply_latest_agent_access_overlay(build_run_ledger_summary(registry, runtime['state']), agent_access_log)
    acceptance_bindings_summary = _render_agent_group_acceptance_bindings_summary_uncached(registry)
    release_gates_summary = _render_agent_group_release_gates_summary_uncached(registry)
    runtime_adapters_summary = _render_runtime_adapters_summary_uncached(registry)
    return {
        'service': str((registry.get('service') or {}).get('name') or 'openclaw-control-plane'),
        'configPath': str(registry.get('configPath') or ''),
        'registryPaths': registry.get('registryPaths') if isinstance(registry.get('registryPaths'), dict) else {},
        'registryPathDetails': registry.get('registryPathDetails') if isinstance(registry.get('registryPathDetails'), dict) else {},
        'scheduler': {
            'healthy': bool(heartbeat_epoch and age is not None and age <= 900),
            'heartbeatAgeSeconds': age,
            'heartbeat': runtime['heartbeat'],
            'status': runtime['status'],
            'evidenceExport': runtime['status'].get('evidenceExport') if isinstance(runtime['status'], dict) and isinstance(runtime['status'].get('evidenceExport'), dict) else None,
        },
        'counts': {
            'jobs': len(registry.get('jobs', [])),
            'agents': len(registry.get('agents', [])),
            'jobRunners': len(registry.get('jobRunners', [])),
            'extensions': len(registry.get('extensions', [])),
            'agentGroups': len(registry.get('agentGroups', [])),
            'agentModules': len(registry.get('agentModules', [])),
            'skillSets': len(registry.get('skillSets', [])),
            'permissionPolicies': len(registry.get('permissionPolicies', [])),
            'toolsets': len(registry.get('toolsets', [])),
            'runtimeAdapters': len(registry.get('runtimeAdapters', [])),
            'recentAgentAccesses': int((agent_access_log.get('counts') or {}).get('items') or 0),
            'recentAgentAccessGroups': int((agent_group_access.get('counts') or {}).get('activeGroups') or 0),
            'agentGroupAcceptanceBindings': int((acceptance_bindings_summary.get('counts') or {}).get('items') or 0),
            'agentGroupReleaseGates': int((release_gates_summary.get('counts') or {}).get('items') or 0),
            'models': len(registry.get('models', [])),
            'targets': len(registry.get('targets', [])),
            'implementations': len(registry.get('implementations', [])),
        },
        'jobs': jobs,
        'extensions': registry.get('extensions', []),
        'agentGroups': agent_groups,
        'agentModules': agent_modules,
        'skillSets': skill_sets,
        'permissionPolicies': permission_policies,
        'toolsets': toolsets,
        'runtimeAdapters': runtime_adapters_summary.get('items', []),
        'agentAccessLog': agent_access_log,
        'agentGroupAccess': agent_group_access,
        'agentGroupAcceptanceBindings': acceptance_bindings_summary,
        'agentGroupReleaseGates': release_gates_summary,
        'runLedger': run_ledger_summary,
        'recentRuns': [_enrich_run_row(row, manifest_memo) for row in runtime['history']],
    }


def _render_jobs_summary_uncached(registry: dict[str, Any]) -> dict[str, Any]:
    runtime = _state_payload(registry)
    manifest_memo: dict[str, dict[str, Any] | None] = {}
    return {
        'service': str((registry.get('service') or {}).get('name') or 'openclaw-control-plane'),
        'items': _job_rows(registry, runtime['state'], runtime['history'], manifest_memo),
    }


def _render_run_ledger_summary_uncached(registry: dict[str, Any]) -> dict[str, Any]:
    return _run_ledger_with_latest_access(registry)


def _render_agent_group_acceptance_bindings_summary_uncached(registry: dict[str, Any], *, group_ref: str = '') -> dict[str, Any]:
    runtime = _state_payload(registry)
    manifest_memo: dict[str, dict[str, Any] | None] = {}
    jobs = _job_rows(registry, runtime['state'], runtime['history'], manifest_memo)
    group_items = _render_agent_group_items(registry, jobs)
    run_ledger_summary = _run_ledger_with_latest_access(registry, runtime)
    normalized_group_ref = str(group_ref or '').strip()
    items: list[dict[str, Any]] = []
    for item in group_items:
        current_group_ref = str(item.get('id') or '').strip()
        if normalized_group_ref and current_group_ref != normalized_group_ref:
            continue
        binding = _build_agent_group_acceptance_binding(item, run_ledger_summary)
        items.append({
            'groupRef': current_group_ref,
            'title': str(item.get('title') or '').strip(),
            'acceptanceBinding': binding,
        })
    items.sort(key=lambda row: (0 if bool(((row.get('acceptanceBinding') or {}).get('accepted'))) is False else 1, str(row.get('groupRef') or '')))
    return {
        'filters': {'groupRef': normalized_group_ref},
        'counts': {
            'items': len(items),
            'accepted': sum(1 for item in items if bool((item.get('acceptanceBinding') or {}).get('accepted'))),
            'blocked': sum(1 for item in items if not bool((item.get('acceptanceBinding') or {}).get('accepted'))),
        },
        'items': items,
    }


def _render_agent_group_release_gates_summary_uncached(registry: dict[str, Any], *, group_ref: str = '') -> dict[str, Any]:
    runtime = _state_payload(registry)
    manifest_memo: dict[str, dict[str, Any] | None] = {}
    jobs = _job_rows(registry, runtime['state'], runtime['history'], manifest_memo)
    group_items = _render_agent_group_items(registry, jobs)
    recent_access = _agent_group_recent_access_map(registry, limit=max(50, int(((registry.get('defaults') or {}).get('recentHistoryLimit') or 20)) * 10), timeline_limit=5)
    run_ledger_summary = _run_ledger_with_latest_access(registry, runtime)
    agent_access_log_summary = _render_agent_access_log_summary_uncached(registry, limit=max(20, int(((registry.get('defaults') or {}).get('recentHistoryLimit') or 20))))
    normalized_group_ref = str(group_ref or '').strip()
    items: list[dict[str, Any]] = []
    for item in group_items:
        current_group_ref = str(item.get('id') or '').strip()
        if normalized_group_ref and current_group_ref != normalized_group_ref:
            continue
        acceptance_binding = _build_agent_group_acceptance_binding(item, run_ledger_summary)
        gate = _build_agent_group_release_gate(
            item,
            dict(recent_access.get(current_group_ref) or {}),
            _group_run_ledger_status([str(x).strip() for x in (item.get('jobRefs') or []) if str(x).strip()], run_ledger_summary),
            agent_access_log_summary,
            acceptance_binding,
        )
        items.append({
            'groupRef': current_group_ref,
            'title': str(item.get('title') or '').strip(),
            'health': dict(item.get('health') or {}),
            'recentAccess': dict(recent_access.get(current_group_ref) or {}),
            'acceptanceBinding': acceptance_binding,
            'releaseGate': gate,
        })
    items.sort(key=lambda row: (0 if str(((row.get('releaseGate') or {}).get('status') or '')) in {'blocked', 'frozen'} else 1, str(row.get('groupRef') or '')))
    return {
        'filters': {'groupRef': normalized_group_ref},
        'counts': {
            'items': len(items),
            'passed': sum(1 for item in items if str(((item.get('releaseGate') or {}).get('status') or '')) == 'passed'),
            'blocked': sum(1 for item in items if str(((item.get('releaseGate') or {}).get('status') or '')) == 'blocked'),
            'frozen': sum(1 for item in items if str(((item.get('releaseGate') or {}).get('status') or '')) == 'frozen'),
        },
        'items': items,
    }


def _render_agent_groups_summary_uncached(registry: dict[str, Any]) -> dict[str, Any]:
    runtime = _state_payload(registry)
    manifest_memo: dict[str, dict[str, Any] | None] = {}
    jobs = _job_rows(registry, runtime['state'], runtime['history'], manifest_memo)
    items = _render_agent_group_items(registry, jobs)
    recent_access = _agent_group_recent_access_map(registry, limit=max(50, int(((registry.get('defaults') or {}).get('recentHistoryLimit') or 20)) * 10), timeline_limit=5)
    run_ledger_summary = _run_ledger_with_latest_access(registry, runtime)
    agent_access_log_summary = _render_agent_access_log_summary_uncached(registry, limit=max(20, int(((registry.get('defaults') or {}).get('recentHistoryLimit') or 20))))
    for item in items:
        group_ref = str(item.get('id') or '')
        recent = dict(recent_access.get(group_ref, {
            'lastAccessAt': '',
            'invocationCount': 0,
            'statusCounts': {},
            'sourceCounts': {},
            'topAgents': [],
            'failureHotspots': [],
            'timeline': [],
        }))
        item['recentAccess'] = recent
        acceptance_binding = _build_agent_group_acceptance_binding(item, run_ledger_summary)
        item['acceptanceBinding'] = acceptance_binding
        item['releaseGate'] = _build_agent_group_release_gate(
            item,
            recent,
            _group_run_ledger_status([str(x).strip() for x in (item.get('jobRefs') or []) if str(x).strip()], run_ledger_summary),
            agent_access_log_summary,
            acceptance_binding,
        )
    return {'items': items}


def _render_agent_modules_summary_uncached(registry: dict[str, Any]) -> dict[str, Any]:
    return {'items': _render_agent_module_items(registry)}


def _render_skill_sets_summary_uncached(registry: dict[str, Any]) -> dict[str, Any]:
    return {'items': _render_skill_set_items(registry)}


def _render_permission_policies_summary_uncached(registry: dict[str, Any]) -> dict[str, Any]:
    return {'items': _render_permission_policy_items(registry)}


def _render_toolsets_summary_uncached(registry: dict[str, Any]) -> dict[str, Any]:
    return {'items': _render_toolset_items(registry)}


def _render_runtime_adapters_summary_uncached(registry: dict[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for adapter in registry.get('runtimeAdapters', []):
        if not isinstance(adapter, dict):
            continue
        items.append({
            'id': str(adapter.get('id') or ''),
            'title': str(adapter.get('title') or ''),
            'description': str(adapter.get('description') or ''),
            'module': str(adapter.get('module') or ''),
            'supportedEntrypointKinds': list(adapter.get('supportedEntrypointKinds') or []),
            'supportedExecutorKinds': list(adapter.get('supportedExecutorKinds') or []),
        })
    return {'items': items}


def _render_job_detail_uncached(registry: dict[str, Any], job_id: str) -> dict[str, Any]:
    runtime = _state_payload(registry)
    manifest_memo: dict[str, dict[str, Any] | None] = {}
    job = resolve_collection_ref(registry, 'jobs', job_id, label='job')
    owner_id = row_owner_id(job)
    qualified_id = str(job.get('qualifiedId') or '').strip() or qualified_registry_id(owner_id, job.get('id'))
    runtime_job_key = str(job.get('resolvedRuntimeJobKey') or qualified_id).strip()
    rows = _job_rows(registry, runtime['state'], runtime['history'], manifest_memo)
    for row in rows:
        selectors = {
            str(row.get('id') or '').strip(),
            str(row.get('qualifiedId') or '').strip(),
            str(row.get('runtimeJobKey') or '').strip(),
        }
        if qualified_id in selectors or runtime_job_key in selectors:
            return row
    return {'error': 'job_not_found', 'jobId': str(job_id or '')}


def _render_agent_group_access_summary_uncached(
    registry: dict[str, Any],
    *,
    limit: int = 200,
    timeline_limit: int = 20,
    group_ref: str = '',
    status: str = '',
    source: str = '',
) -> dict[str, Any]:
    items = _build_agent_group_access_items(
        registry,
        limit=limit,
        timeline_limit=timeline_limit,
        group_ref=group_ref,
        status=status,
        source=source,
    )
    return {
        'limit': int(limit),
        'timelineLimit': int(timeline_limit),
        'filters': {
            'groupRef': str(group_ref or '').strip(),
            'status': str(status or '').strip(),
            'source': str(source or '').strip(),
        },
        'counts': {
            'groups': len(items),
            'activeGroups': sum(1 for item in items if int(((item.get('counts') or {}).get('items') or 0)) > 0),
            'items': sum(int(((item.get('counts') or {}).get('items') or 0)) for item in items),
        },
        'items': items,
    }
