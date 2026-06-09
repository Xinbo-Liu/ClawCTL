#!/usr/bin/env python3
"""控制平面 API 的访问日志与运行 manifest 视图辅助。"""
from __future__ import annotations

from typing import Any, Callable

from openclaw.control_plane.registry.store import tail_history
from openclaw.control_plane.registry.runtime_manifests import read_runtime_manifest_json
from openclaw.lib.io.json_access import json_array


StatePayloadBuilder = Callable[[dict[str, Any]], dict[str, Any]]


def read_optional_json(path_value: object, manifest_memo: dict[str, dict[str, Any] | None] | None = None) -> dict[str, Any] | None:
    return read_runtime_manifest_json(path_value, manifest_memo=manifest_memo)


def enrich_run_row(row: dict[str, Any], manifest_memo: dict[str, dict[str, Any] | None] | None = None) -> dict[str, Any]:
    enriched = dict(row)
    run_manifest = read_optional_json(row.get('run_manifest_path'), manifest_memo)
    result_manifest = read_optional_json(row.get('result_manifest_path'), manifest_memo)
    artifacts_manifest = read_optional_json(row.get('artifacts_path'), manifest_memo)
    if run_manifest is not None:
        enriched['run'] = run_manifest
    if result_manifest is not None:
        enriched['result'] = result_manifest
    if artifacts_manifest is not None:
        enriched['artifacts'] = artifacts_manifest
    return enriched


def read_agent_access_rows(files: Any, limit: int) -> list[dict[str, Any]]:
    rows = tail_history(files.agent_access_log_path, max(0, int(limit)))
    rows.reverse()
    return [dict(row) for row in rows if isinstance(row, dict)]


def filter_agent_access_rows(
    rows: list[dict[str, Any]],
    *,
    agent_ref: str = '',
    group_ref: str = '',
    job_id: str = '',
    status: str = '',
    source: str = '',
) -> list[dict[str, Any]]:
    normalized_agent_ref = str(agent_ref or '').strip()
    normalized_group_ref = str(group_ref or '').strip()
    normalized_job_id = str(job_id or '').strip()
    normalized_status = str(status or '').strip()
    normalized_source = str(source or '').strip()
    filtered: list[dict[str, Any]] = []
    for row in rows:
        if normalized_agent_ref and str(row.get('agentRef') or '').strip() != normalized_agent_ref:
            continue
        group_refs = [str(item).strip() for item in (row.get('agentGroupRefs') or []) if str(item).strip()]
        if normalized_group_ref and normalized_group_ref not in group_refs:
            continue
        if normalized_job_id and str(row.get('jobId') or '').strip() != normalized_job_id:
            continue
        if normalized_status and str(row.get('status') or '').strip() != normalized_status:
            continue
        if normalized_source and str(row.get('source') or '').strip() != normalized_source:
            continue
        filtered.append(dict(row))
    return filtered


def agent_access_aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    agent_counts: dict[str, int] = {}
    job_counts: dict[str, int] = {}
    group_counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get('status') or '').strip() or 'unknown'
        source = str(row.get('source') or '').strip() or 'unknown'
        agent_ref = str(row.get('agentRef') or '').strip() or 'unknown'
        job_ref = str(row.get('jobId') or '').strip()
        status_counts[status] = status_counts.get(status, 0) + 1
        source_counts[source] = source_counts.get(source, 0) + 1
        agent_counts[agent_ref] = agent_counts.get(agent_ref, 0) + 1
        if job_ref:
            job_counts[job_ref] = job_counts.get(job_ref, 0) + 1
        for group_ref in [str(item).strip() for item in (row.get('agentGroupRefs') or []) if str(item).strip()]:
            group_counts[group_ref] = group_counts.get(group_ref, 0) + 1
    return {
        'statusCounts': status_counts,
        'sourceCounts': source_counts,
        'agentCounts': agent_counts,
        'jobCounts': job_counts,
        'groupCounts': group_counts,
    }


def top_counts(mapping: dict[str, int], *, limit: int = 10) -> list[dict[str, Any]]:
    rows = [{'key': key, 'count': count} for key, count in mapping.items() if str(key).strip()]
    rows.sort(key=lambda item: (-int(item.get('count') or 0), str(item.get('key') or '')))
    return rows[:max(0, int(limit))]


def member_access_waterfall(member_refs: list[str], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered_rows: list[dict[str, Any]] = []
    for agent_ref in member_refs:
        member_rows = [dict(row) for row in rows if str(row.get('agentRef') or '').strip() == agent_ref]
        status_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}
        duration_values: list[int] = []
        last_access_at = ''
        last_status = ''
        last_error = ''
        for row in member_rows:
            status = str(row.get('status') or '').strip() or 'unknown'
            source = str(row.get('source') or '').strip() or 'unknown'
            status_counts[status] = status_counts.get(status, 0) + 1
            source_counts[source] = source_counts.get(source, 0) + 1
            duration_values.append(int(row.get('durationMs') or 0))
            if not last_access_at:
                last_access_at = str(row.get('recordedAt') or row.get('finishedAt') or row.get('startedAt') or '').strip()
                last_status = status
                last_error = str(row.get('error') or '').strip()
        total = len(member_rows)
        failure_count = sum(status_counts.get(name, 0) for name in ('failed', 'error', 'blocked', 'retry_pending'))
        ordered_rows.append({
            'agentRef': agent_ref,
            'invocationCount': total,
            'failureCount': failure_count,
            'statusCounts': status_counts,
            'sourceCounts': source_counts,
            'lastAccessAt': last_access_at,
            'lastStatus': last_status,
            'lastError': last_error,
            'avgDurationMs': int(sum(duration_values) / len(duration_values)) if duration_values else 0,
            'maxDurationMs': max(duration_values) if duration_values else 0,
        })
    return ordered_rows


def group_access_timeline(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    for row in rows[:max(0, int(limit))]:
        timeline.append({
            'recordedAt': str(row.get('recordedAt') or '').strip(),
            'startedAt': str(row.get('startedAt') or '').strip(),
            'finishedAt': str(row.get('finishedAt') or '').strip(),
            'agentRef': str(row.get('agentRef') or '').strip(),
            'agentModuleRef': str(row.get('agentModuleRef') or '').strip(),
            'jobId': str(row.get('jobId') or '').strip(),
            'runId': str(row.get('runId') or '').strip(),
            'trigger': str(row.get('trigger') or '').strip(),
            'source': str(row.get('source') or '').strip(),
            'status': str(row.get('status') or '').strip(),
            'exitCode': row.get('exitCode'),
            'durationMs': int(row.get('durationMs') or 0),
            'runtimeAdapterRef': str(row.get('runtimeAdapterRef') or '').strip(),
            'error': str(row.get('error') or '').strip(),
        })
    return timeline


def group_failure_hotspots(rows: list[dict[str, Any]], *, limit: int = 10) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        status = str(row.get('status') or '').strip()
        if status not in {'failed', 'error', 'blocked', 'retry_pending'}:
            continue
        agent_ref = str(row.get('agentRef') or '').strip() or 'unknown'
        job_id = str(row.get('jobId') or '').strip()
        key = (agent_ref, job_id)
        bucket = buckets.setdefault(key, {
            'agentRef': agent_ref,
            'jobId': job_id,
            'count': 0,
            'statusCounts': {},
            'lastFailureAt': '',
            'lastError': '',
        })
        bucket['count'] += 1
        status_counts = bucket['statusCounts']
        status_counts[status] = status_counts.get(status, 0) + 1
        if not bucket['lastFailureAt']:
            bucket['lastFailureAt'] = str(row.get('recordedAt') or row.get('finishedAt') or row.get('startedAt') or '').strip()
            bucket['lastError'] = str(row.get('error') or '').strip()
    items = list(buckets.values())
    items.sort(key=lambda item: (-int(item.get('count') or 0), str(item.get('agentRef') or ''), str(item.get('jobId') or '')))
    return items[:max(0, int(limit))]


def build_agent_group_access_items(
    registry: dict[str, Any],
    *,
    state_payload_builder: StatePayloadBuilder,
    limit: int = 200,
    timeline_limit: int = 20,
    group_ref: str = '',
    status: str = '',
    source: str = '',
) -> list[dict[str, Any]]:
    runtime = state_payload_builder(registry)
    files = runtime['files']
    base_rows = filter_agent_access_rows(
        read_agent_access_rows(files, max(0, int(limit))),
        status=status,
        source=source,
    )
    normalized_group_ref = str(group_ref or '').strip()
    items: list[dict[str, Any]] = []
    for group in registry.get('agentGroups', []):
        if not isinstance(group, dict):
            continue
        current_group_ref = str(group.get('id') or '').strip()
        if normalized_group_ref and current_group_ref != normalized_group_ref:
            continue
        member_refs = [
            str(item).strip()
            for item in json_array(group.get('resolvedMembers'))
            if str(item).strip()
        ]
        group_rows = [
            dict(row) for row in base_rows
            if current_group_ref in [str(item).strip() for item in (row.get('agentGroupRefs') or []) if str(item).strip()]
        ]
        aggregate = agent_access_aggregate(group_rows)
        timeline = group_access_timeline(group_rows, limit=timeline_limit)
        waterfall = member_access_waterfall(member_refs, group_rows)
        items.append({
            'groupRef': current_group_ref,
            'title': str(group.get('title') or '').strip(),
            'memberAgentRefs': member_refs,
            'filters': {
                'status': str(status or '').strip(),
                'source': str(source or '').strip(),
            },
            'counts': {
                'items': len(group_rows),
                **aggregate,
            },
            'lastAccessAt': str(group_rows[0].get('recordedAt') or group_rows[0].get('finishedAt') or group_rows[0].get('startedAt') or '').strip() if group_rows else '',
            'waterfall': waterfall,
            'failureHotspots': group_failure_hotspots(group_rows),
            'timeline': timeline,
            'topAgents': top_counts(aggregate.get('agentCounts') or {}, limit=min(10, len(member_refs) or 10)),
            'topJobs': top_counts(aggregate.get('jobCounts') or {}, limit=10),
        })
    items.sort(key=lambda item: str(item.get('lastAccessAt') or ''), reverse=True)
    items.sort(key=lambda item: 0 if str(item.get('lastAccessAt') or '').strip() else 1)
    return items


def agent_group_recent_access_map(
    registry: dict[str, Any],
    *,
    state_payload_builder: StatePayloadBuilder,
    limit: int = 200,
    timeline_limit: int = 5,
) -> dict[str, dict[str, Any]]:
    items = build_agent_group_access_items(registry, state_payload_builder=state_payload_builder, limit=limit, timeline_limit=timeline_limit)
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        result[str(item.get('groupRef') or '')] = {
            'lastAccessAt': str(item.get('lastAccessAt') or '').strip(),
            'invocationCount': int(((item.get('counts') or {}).get('items') or 0)),
            'statusCounts': dict((item.get('counts') or {}).get('statusCounts') or {}),
            'sourceCounts': dict((item.get('counts') or {}).get('sourceCounts') or {}),
            'topAgents': list(item.get('topAgents') or []),
            'failureHotspots': list(item.get('failureHotspots') or [])[:3],
            'timeline': list(item.get('timeline') or [])[:max(0, int(timeline_limit))],
        }
    return result


def render_agent_access_log_summary_uncached(
    registry: dict[str, Any],
    *,
    state_payload_builder: StatePayloadBuilder,
    limit: int = 50,
    agent_ref: str = '',
    group_ref: str = '',
    job_id: str = '',
    status: str = '',
    source: str = '',
) -> dict[str, Any]:
    runtime = state_payload_builder(registry)
    files = runtime['files']
    rows = filter_agent_access_rows(
        read_agent_access_rows(files, max(0, int(limit))),
        agent_ref=agent_ref,
        group_ref=group_ref,
        job_id=job_id,
        status=status,
        source=source,
    )
    return {
        'path': str(files.agent_access_log_path),
        'limit': int(limit),
        'filters': {
            'agentRef': str(agent_ref or '').strip(),
            'groupRef': str(group_ref or '').strip(),
            'jobId': str(job_id or '').strip(),
            'status': str(status or '').strip(),
            'source': str(source or '').strip(),
        },
        'counts': {
            'items': len(rows),
            **agent_access_aggregate(rows),
        },
        'items': rows,
    }
