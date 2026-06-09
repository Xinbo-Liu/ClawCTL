#!/usr/bin/env python3
"""控制平面 group 级发布证据导出。"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from openclaw.control_plane.api import (
    _render_agent_access_log_summary_uncached,
    _render_agent_group_access_summary_uncached,
    _render_agent_group_acceptance_bindings_summary_uncached,
    _render_agent_group_release_gates_summary_uncached,
    _render_run_ledger_summary_uncached,
)
from openclaw.lib.control_plane.object_families import get_entry
from openclaw.lib.repo.static_truth import ROOT_DIR


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _object_file_from_object_family(entry_id: str, *, base_root: Path = ROOT_DIR) -> Path:
    entry = get_entry('runtime_evidence', entry_id, base_root)
    resolved = str(entry.get('resolved_path') or '').strip()
    path = Path(resolved)
    return path if path.is_absolute() else (base_root / resolved).resolve()


@contextmanager
def _override_scheduler_state_root(state_root: Path | None) -> Iterator[None]:
    if state_root is None:
        yield
        return
    previous = os.environ.get('OPENCLAW_STATE_DIR')
    previous_view = os.environ.get('OPENCLAW_RUNTIME_PATH_VIEW')
    os.environ['OPENCLAW_STATE_DIR'] = str(Path(state_root).resolve())
    os.environ['OPENCLAW_RUNTIME_PATH_VIEW'] = 'scheduler'
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop('OPENCLAW_STATE_DIR', None)
        else:
            os.environ['OPENCLAW_STATE_DIR'] = previous
        if previous_view is None:
            os.environ.pop('OPENCLAW_RUNTIME_PATH_VIEW', None)
        else:
            os.environ['OPENCLAW_RUNTIME_PATH_VIEW'] = previous_view


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def export_agent_group_evidence(
    registry: dict[str, Any],
    *,
    state_root: Path | None = None,
    base_root: Path = ROOT_DIR,
    agent_access_limit: int = 200,
    group_access_limit: int = 200,
    timeline_limit: int = 20,
) -> dict[str, Any]:
    state_root_resolved = Path(state_root).resolve() if state_root is not None else None
    with _override_scheduler_state_root(state_root_resolved):
        agent_access_path = _object_file_from_object_family('control_plane_agent_access_log', base_root=base_root)
        group_access_path = _object_file_from_object_family('control_plane_agent_group_access', base_root=base_root)
        group_acceptance_bindings_path = _object_file_from_object_family('control_plane_agent_group_acceptance_bindings', base_root=base_root)
        group_release_gates_path = _object_file_from_object_family('control_plane_agent_group_release_gates', base_root=base_root)
        run_ledger_path = _object_file_from_object_family('control_plane_run_ledger', base_root=base_root)
        run_ledger = _render_run_ledger_summary_uncached(registry)
        agent_access = _render_agent_access_log_summary_uncached(registry, limit=max(0, int(agent_access_limit)))
        group_access = _render_agent_group_access_summary_uncached(
            registry,
            limit=max(0, int(group_access_limit)),
            timeline_limit=max(0, int(timeline_limit)),
        )

    metadata = {
        'schemaVersion': 1,
        'generatedAt': _now_iso(),
        'baseRoot': str(base_root),
        'stateRoot': str(state_root_resolved) if state_root_resolved is not None else '',
        'producer': 'openclaw.control_plane.evidence_export',
    }
    run_ledger_payload = {**metadata, 'kind': 'controlPlaneRunLedger', **run_ledger}
    agent_access_payload = {**metadata, 'kind': 'controlPlaneAgentAccessLog', **agent_access}
    group_access_payload = {**metadata, 'kind': 'controlPlaneAgentGroupAccess', **group_access}
    _write_json(run_ledger_path, run_ledger_payload)
    _write_json(agent_access_path, agent_access_payload)
    _write_json(group_access_path, group_access_payload)

    with _override_scheduler_state_root(state_root_resolved):
        group_acceptance_bindings = _render_agent_group_acceptance_bindings_summary_uncached(registry)
    group_acceptance_bindings_payload = {**metadata, 'kind': 'controlPlaneAgentGroupAcceptanceBindings', **group_acceptance_bindings}
    _write_json(group_acceptance_bindings_path, group_acceptance_bindings_payload)

    with _override_scheduler_state_root(state_root_resolved):
        group_release_gates = _render_agent_group_release_gates_summary_uncached(registry)
    group_release_gates_payload = {**metadata, 'kind': 'controlPlaneAgentGroupReleaseGates', **group_release_gates}
    _write_json(group_release_gates_path, group_release_gates_payload)

    return {
        'status': 'exported',
        'generatedAt': metadata['generatedAt'],
        'stateRoot': metadata['stateRoot'],
        'paths': {
            'runLedger': str(run_ledger_path),
            'agentAccessLog': str(agent_access_path),
            'agentGroupAccess': str(group_access_path),
            'agentGroupAcceptanceBindings': str(group_acceptance_bindings_path),
            'agentGroupReleaseGates': str(group_release_gates_path),
        },
        'counts': {
            'runLedgerItems': int((run_ledger.get('counts') or {}).get('jobs') or 0),
            'agentAccessItems': int((agent_access.get('counts') or {}).get('items') or 0),
            'agentGroupAccessGroups': int((group_access.get('counts') or {}).get('groups') or 0),
            'agentGroupAcceptanceBindings': int((group_acceptance_bindings.get('counts') or {}).get('items') or 0),
            'agentGroupReleaseGates': int((group_release_gates.get('counts') or {}).get('items') or 0),
        },
    }
