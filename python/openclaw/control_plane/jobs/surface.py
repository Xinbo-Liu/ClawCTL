#!/usr/bin/env python3
"""控制平面 job 最小清单收缩与冗余面检查。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.control_plane.modules.change_set import (
    apply_staged_writes,
    build_write,
    read_json_object,
    repo_root_from_inputs,
    summarize_files,
)
from openclaw.control_plane.jobs.defaults import build_compact_job_manifest, manifest_diff_paths
from openclaw.control_plane.registry import CliError, load_registry
from openclaw.control_plane.registry.owners import resolve_collection_ref, row_owner_id
from openclaw.lib.io.json_access import json_object


def _resolve_jobs(registry: dict[str, Any], *, job_id: str) -> list[dict[str, Any]]:
    normalized_job_id = str(job_id or '').strip()
    if normalized_job_id:
        job = resolve_collection_ref(registry, 'jobs', normalized_job_id, label='jobId')
        return [job]
    return [dict(item) for item in (registry.get('jobs') or []) if isinstance(item, dict)]


def inspect_job_surface(job_payload: dict[str, Any], *, registry: dict[str, Any]) -> dict[str, Any]:
    job_id = str(job_payload.get('id') or '').strip()
    agent_ref = str(job_payload.get('agentRef') or '').strip()
    agent_payload = resolve_collection_ref(
        registry,
        'agents',
        str(job_payload.get('resolvedAgentQualifiedRef') or job_payload.get('resolvedAgentRef') or agent_ref),
        owner_id=row_owner_id(job_payload),
        label='agentRef',
    )
    module_ref = str(agent_payload.get('resolvedModuleRef') or agent_ref).strip()
    module_payload = resolve_collection_ref(registry, 'agentModules', module_ref, owner_id=row_owner_id(agent_payload), label='moduleRef')
    source_path = Path(str(job_payload.get('sourcePath') or '')).resolve()
    raw_payload = read_json_object(source_path)
    default_timezone = str(json_object(registry.get('defaults')).get('timezone') or '').strip()
    compact_payload = build_compact_job_manifest(
        job_payload,
        agent_payload=agent_payload,
        module_payload=module_payload,
        default_timezone=default_timezone,
    )
    drift_paths = manifest_diff_paths(raw_payload, compact_payload)
    return {
        'jobId': job_id,
        'agentRef': agent_ref,
        'moduleRef': module_ref,
        'sourcePath': str(source_path),
        'rawPayload': raw_payload,
        'compactPayload': compact_payload,
        'driftPaths': drift_paths,
        'ok': not drift_paths,
    }


def _build_job_prune_plan(
    *,
    config_path: Path,
    repo_root: Path | None,
    job_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], Path, Path]:
    registry = load_registry(Path(config_path).resolve())
    effective_repo_root = repo_root_from_inputs(repo_root=repo_root, config_path=Path(config_path).resolve())
    jobs = _resolve_jobs(registry, job_id=job_id)
    items: list[dict[str, Any]] = []
    writes: list[dict[str, Any]] = []
    for job_payload in jobs:
        inspection = inspect_job_surface(job_payload, registry=registry)
        rel_source_path = Path(str(inspection.get('sourcePath') or '')).resolve()
        items.append({
            'jobId': str(inspection.get('jobId') or ''),
            'agentRef': str(inspection.get('agentRef') or ''),
            'moduleRef': str(inspection.get('moduleRef') or ''),
            'path': rel_source_path.relative_to(effective_repo_root.resolve()).as_posix(),
            'driftPaths': list(inspection.get('driftPaths') or []),
            'ok': bool(inspection.get('ok')),
        })
        if bool(inspection.get('ok')):
            continue
        writes.append(build_write(
            rel_source_path,
            action='update',
            payload=inspection.get('compactPayload') if isinstance(inspection.get('compactPayload'), dict) else {},
            summary=f'收紧 job {inspection.get("jobId")} manifest 到最小合同',
        ))
    offenders = [item for item in items if not bool(item.get('ok'))]
    payload = {
        'status': 'ok',
        'mode': 'plan',
        'jobCount': len(items),
        'offenderCount': len(offenders),
        'items': items,
        'files': summarize_files(effective_repo_root, writes),
    }
    return payload, writes, effective_repo_root, Path(config_path).resolve()


def plan_job_surface_prune(**kwargs: Any) -> dict[str, Any]:
    payload, _, _, _ = _build_job_prune_plan(**kwargs)
    return payload


def apply_job_surface_prune(**kwargs: Any) -> dict[str, Any]:
    payload, writes, _, config_path = _build_job_prune_plan(**kwargs)
    apply_staged_writes(writes=writes, config_path=config_path, error_prefix='应用 job surface 变更失败')
    return {**payload, 'mode': 'apply'}
