#!/usr/bin/env python3
"""调度周期中的 agent-group evidence 导出辅助。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


def _should_export_agent_group_evidence(config: dict[str, Any], execution: dict[str, Any]) -> tuple[bool, int, int]:
    service = dict(config.get('service') or {}) if isinstance(config.get('service'), dict) else {}
    if not bool(service.get('autoExportAgentGroupEvidence', True)):
        return False, 0, 0
    executed_count = int(execution.get('executed_count') or 0)
    blocked_count = int(execution.get('blocked_count') or 0)
    return executed_count > 0 or blocked_count > 0, executed_count, blocked_count


def _agent_group_evidence_success(
    payload: dict[str, Any],
    *,
    executed_count: int,
    blocked_count: int,
    generated_at: str,
) -> dict[str, Any]:
    return {
        'status': 'exported',
        'generatedAt': str(payload.get('generatedAt') or generated_at),
        'reason': 'scheduler_cycle_with_job_activity',
        'paths': payload.get('paths') if isinstance(payload.get('paths'), dict) else {},
        'counts': payload.get('counts') if isinstance(payload.get('counts'), dict) else {},
        'executedCount': executed_count,
        'blockedCount': blocked_count,
    }


def _agent_group_evidence_failure(
    exc: Exception,
    *,
    state_root: Path,
    base_root: Path,
    executed_count: int,
    blocked_count: int,
    generated_at: str,
) -> dict[str, Any]:
    return {
        'status': 'failed',
        'generatedAt': generated_at,
        'reason': 'scheduler_cycle_with_job_activity',
        'error': str(exc),
        'errorType': type(exc).__name__,
        'baseRoot': str(base_root),
        'stateRoot': str(state_root),
        'executedCount': executed_count,
        'blockedCount': blocked_count,
    }


def maybe_export_agent_group_evidence(
    *,
    config: dict[str, Any],
    state_root: Path,
    execution: dict[str, Any],
    exporter: Callable[[dict[str, Any]], dict[str, Any]],
    generated_at: Callable[[], str],
    base_root: Path,
    warn: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    """按条件导出 agent group evidence。"""
    should_export, executed_count, blocked_count = _should_export_agent_group_evidence(config, execution)
    if not should_export:
        return None
    try:
        payload = exporter(config, state_root=state_root, base_root=base_root)
        return _agent_group_evidence_success(
            payload,
            executed_count=executed_count,
            blocked_count=blocked_count,
            generated_at=generated_at(),
        )
    except Exception as exc:
        # 证据导出失败不能掩盖当前调度结果；失败会进入 scheduler status 供运维处理。
        failure = _agent_group_evidence_failure(
            exc,
            state_root=state_root,
            base_root=base_root,
            executed_count=executed_count,
            blocked_count=blocked_count,
            generated_at=generated_at(),
        )
        if warn is not None:
            warn(f'[control_plane_scheduler][WARN] agent group evidence export 失败：{failure["errorType"]}: {failure["error"]}\n')
        return failure
