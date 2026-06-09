#!/usr/bin/env python3
"""Scheduler status and heartbeat helpers."""
from __future__ import annotations

import json
from datetime import timezone
from pathlib import Path
from typing import Any

from openclaw.lib.io.json_access import json_array, json_object
from openclaw.lib.runtime.time import parse_iso_datetime


def payload_fingerprint(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def write_json_if_dirty(path: Path, payload: Any, previous_fingerprint: str | None, *, write_json) -> tuple[bool, str]:
    fingerprint = payload_fingerprint(payload)
    if previous_fingerprint is not None and previous_fingerprint == fingerprint:
        return False, fingerprint
    write_json(path, payload)
    return True, fingerprint


def build_status_payload(
    *,
    config: dict[str, Any],
    state_root: Path,
    config_path: str,
    interval_seconds: float,
    execution: dict[str, Any],
    state: dict[str, Any],
    updated_at: str,
    updated_at_epoch: int,
) -> dict[str, Any]:
    service = json_object(config.get('service'))
    jobs = json_array(config.get('jobs'))
    agents = json_array(config.get('agents'))
    models = json_array(config.get('models'))
    targets = json_array(config.get('targets'))
    implementations = json_array(config.get('implementations'))
    evidence_export = json_object(state.get('evidenceExport')) or None
    return {
        'service': str(service.get('name') or 'openclaw-control-plane'),
        'schedulerService': str(service.get('schedulerServiceName') or 'openclaw-control-plane-scheduler'),
        'status': 'running',
        'config_path': str(config_path),
        'state_root': str(state_root),
        'updated_at': updated_at,
        'updated_at_epoch': int(updated_at_epoch),
        'interval_seconds': interval_seconds,
        'jobs_total': len([job for job in jobs if isinstance(job, dict)]),
        'agents_total': len([row for row in agents if isinstance(row, dict)]),
        'models_total': len([row for row in models if isinstance(row, dict)]),
        'targets_total': len([row for row in targets if isinstance(row, dict)]),
        'implementations_total': len([row for row in implementations if isinstance(row, dict)]),
        'execution': execution,
        'jobs': json_object(state.get('jobs')),
        'evidenceExport': evidence_export,
    }


def parse_iso(value: object) -> datetime | None:
    return parse_iso_datetime(value)


def next_due_sleep_seconds(state: dict[str, Any], *, now_epoch: float, heartbeat_interval_seconds: float) -> float:
    jobs_state = json_object(state.get('jobs'))
    candidate_seconds: list[float] = []
    for job_state in jobs_state.values():
        if not isinstance(job_state, dict):
            continue
        target = parse_iso(job_state.get('nextScheduledRunAt'))
        if target is not None:
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            delta = target.astimezone(timezone.utc).timestamp() - now_epoch
            candidate_seconds.append(max(0.0, delta))
        pending_retry = json_object(job_state.get('pendingRetry'))
        if pending_retry:
            retry_target = parse_iso(pending_retry.get('nextRunAt'))
            if retry_target is not None:
                if retry_target.tzinfo is None:
                    retry_target = retry_target.replace(tzinfo=timezone.utc)
                delta = retry_target.astimezone(timezone.utc).timestamp() - now_epoch
                candidate_seconds.append(max(0.0, delta))
    heartbeat_cap = max(1.0, float(heartbeat_interval_seconds))
    if not candidate_seconds:
        return heartbeat_cap
    nearest = min(candidate_seconds)
    return max(1.0, min(heartbeat_cap, nearest))
