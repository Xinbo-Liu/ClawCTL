#!/usr/bin/env python3
"""control-plane agent 调用访问日志。"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openclaw.control_plane.registry.store import append_jsonl, runtime_files


_SCHEDULER_JOB_ID_ENV = 'OPENCLAW_CONTROL_PLANE_JOB_ID'
_SCHEDULER_RUN_ID_ENV = 'OPENCLAW_CONTROL_PLANE_RUN_ID'
_SCHEDULER_TRIGGER_ENV = 'OPENCLAW_CONTROL_PLANE_TRIGGER'
_CALL_SOURCE_ENV = 'OPENCLAW_AGENT_CALL_SOURCE'
_CALLER_ENV = 'OPENCLAW_AGENT_CALLER'


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def infer_call_context() -> dict[str, str]:
    job_id = str(os.environ.get(_SCHEDULER_JOB_ID_ENV, '') or '').strip()
    run_id = str(os.environ.get(_SCHEDULER_RUN_ID_ENV, '') or '').strip()
    trigger = str(os.environ.get(_SCHEDULER_TRIGGER_ENV, '') or '').strip()
    source = str(os.environ.get(_CALL_SOURCE_ENV, '') or '').strip()
    if not source:
        source = 'scheduler' if job_id else 'manual_cli'
    caller = str(os.environ.get(_CALLER_ENV, '') or '').strip() or source
    return {
        'source': source,
        'caller': caller,
        'jobId': job_id,
        'runId': run_id,
        'trigger': trigger,
    }


def append_agent_access_log(
    registry: dict[str, Any],
    *,
    state_root: Path,
    agent_ref: str,
    implementation_ref: str,
    runtime_adapter_ref: str,
    agent_group_refs: list[str],
    agent_module_ref: str,
    runtime_args: list[str],
    started_at: str,
    finished_at: str | None = None,
    duration_ms: int | None = None,
    exit_code: int | None = None,
    status: str,
    error: str = '',
    source: str = '',
    caller: str = '',
    job_id: str = '',
    run_id: str = '',
    trigger: str = '',
) -> dict[str, Any]:
    context = infer_call_context()
    files = runtime_files(state_root, registry)
    payload = {
        'schemaVersion': 1,
        'recordedAt': _now_iso(),
        'startedAt': str(started_at or '').strip(),
        'finishedAt': str(finished_at or '').strip(),
        'durationMs': int(duration_ms or 0),
        'status': str(status or '').strip(),
        'exitCode': int(exit_code) if exit_code is not None else None,
        'source': str(source or context['source']).strip(),
        'caller': str(caller or context['caller']).strip(),
        'jobId': str(job_id or context['jobId']).strip(),
        'runId': str(run_id or context['runId']).strip(),
        'trigger': str(trigger or context['trigger']).strip(),
        'agentRef': str(agent_ref or '').strip(),
        'agentGroupRefs': [str(item).strip() for item in agent_group_refs if str(item).strip()],
        'agentModuleRef': str(agent_module_ref or '').strip(),
        'implementationRef': str(implementation_ref or '').strip(),
        'runtimeAdapterRef': str(runtime_adapter_ref or '').strip(),
        'runtimeArgs': [str(item) for item in runtime_args],
        'stateRoot': str(state_root),
        'error': str(error or '').strip(),
    }
    append_jsonl(files.agent_access_log_path, payload)
    return payload
