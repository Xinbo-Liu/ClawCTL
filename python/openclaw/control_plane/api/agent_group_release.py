#!/usr/bin/env python3
"""Agent-group release and acceptance helper builders."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.control_plane.registry.store import read_json
from openclaw.control_plane.run_ledger import (
    row_artifact_accepted,
    row_effective_artifact_accepted,
    row_effective_execution_accepted,
    row_execution_accepted,
)
from openclaw.lib.control_plane.object_families import get_entry
from openclaw.lib.io.json_access import json_object
from openclaw.lib.repo.layout import resolve_repo_root

ROOT_DIR = resolve_repo_root(Path(__file__))

_KNOWN_AGENT_GROUP_EVIDENCE = {
    'group_access_view': 'group 访问视图已导出到 control-plane state release/evidence/control-plane-agent-group-access.json。',
    'group_access_log': 'group 成员调用访问日志摘要已导出到 control-plane state release/evidence/control-plane-agent-access-log.json。',
    'run_ledger': 'group 关联 job 的 run ledger 已导出到 control-plane state release/evidence/control-plane-run-ledger.json。',
    'group_contract_docs': 'group 单真源文档已存在且被 registry 校验。',
    'acceptance_binding': 'group 已建立到 deployment acceptance required checks、runtime evidence 与 required run ledger jobs 的正式映射。',
}


def object_file(family_id: str, entry_id: str) -> Path:
    entry = get_entry(family_id, entry_id, ROOT_DIR)
    resolved = str(entry.get('resolved_path') or '').strip()
    path = Path(resolved)
    return path if path.is_absolute() else (ROOT_DIR / resolved).resolve()


def read_object_json(family_id: str, entry_id: str, default: Any = None) -> Any:
    return read_json(object_file(family_id, entry_id), default)


def object_file_exists(family_id: str, entry_id: str) -> bool:
    return object_file(family_id, entry_id).exists()


def evidence_file(entry_id: str) -> Path:
    return object_file('runtime_evidence', entry_id)


def read_evidence_json(entry_id: str, default: Any = None) -> Any:
    return read_json(evidence_file(entry_id), default)


def evidence_file_exists(entry_id: str) -> bool:
    return evidence_file(entry_id).exists()


def deployment_acceptance_required_check_statuses() -> dict[str, str]:
    payload = read_object_json('acceptance_state', 'deployment_acceptance', None)
    statuses: dict[str, str] = {}
    for item in (payload.get('required_checks') or []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        check_id = str(item.get('id') or '').strip()
        if check_id:
            statuses[check_id] = str(item.get('status') or '').strip() or 'NOT_RECORDED'
    return statuses


def exported_group_evidence_presence(group_ref: str) -> dict[str, bool]:
    normalized_group_ref = str(group_ref or '').strip()
    if not normalized_group_ref:
        return {'groupAccessView': False, 'groupAccessLog': False, 'runLedger': False, 'groupReleaseGates': False, 'acceptanceBindings': False}
    group_access_payload = read_evidence_json('control_plane_agent_group_access', None)
    group_access_items = list(group_access_payload.get('items') or []) if isinstance(group_access_payload, dict) else []
    group_access_ok = any(str(item.get('groupRef') or '').strip() == normalized_group_ref for item in group_access_items if isinstance(item, dict))
    agent_access_payload = read_evidence_json('control_plane_agent_access_log', None)
    agent_access_items = list(agent_access_payload.get('items') or []) if isinstance(agent_access_payload, dict) else []
    access_log_ok = False
    for item in agent_access_items:
        if not isinstance(item, dict):
            continue
        refs = [str(x).strip() for x in (item.get('agentGroupRefs') or []) if str(x).strip()]
        if normalized_group_ref in refs:
            access_log_ok = True
            break
    run_ledger_payload = read_evidence_json('control_plane_run_ledger', None)
    run_ledger_items = list(run_ledger_payload.get('items') or []) if isinstance(run_ledger_payload, dict) else []
    run_ledger_ok = bool(run_ledger_items)
    release_gates_payload = read_evidence_json('control_plane_agent_group_release_gates', None)
    release_gate_items = list(release_gates_payload.get('items') or []) if isinstance(release_gates_payload, dict) else []
    group_release_gates_ok = any(str(item.get('groupRef') or '').strip() == normalized_group_ref for item in release_gate_items if isinstance(item, dict))
    acceptance_bindings_payload = read_evidence_json('control_plane_agent_group_acceptance_bindings', None)
    acceptance_binding_items = list(acceptance_bindings_payload.get('items') or []) if isinstance(acceptance_bindings_payload, dict) else []
    acceptance_bindings_ok = any(str(item.get('groupRef') or '').strip() == normalized_group_ref for item in acceptance_binding_items if isinstance(item, dict))
    return {'groupAccessView': group_access_ok, 'groupAccessLog': access_log_ok, 'runLedger': run_ledger_ok, 'groupReleaseGates': group_release_gates_ok, 'acceptanceBindings': acceptance_bindings_ok}


def _effective_status(row: dict[str, Any], accepted: Any) -> str:
    if row.get('effectiveStatus'):
        return str(row.get('effectiveStatus') or '')
    return 'accepted' if accepted is True else ('missing' if not row else ('failed' if accepted is False else 'pending'))


def group_run_ledger_status(job_refs: list[str], run_ledger_summary: dict[str, Any]) -> dict[str, Any]:
    items = list(run_ledger_summary.get('items') or []) if isinstance(run_ledger_summary, dict) else []
    rows = {str(item.get('id') or ''): item for item in items if isinstance(item, dict)}
    missing_job_ids = [job_id for job_id in job_refs if job_id not in rows]
    failing_job_ids = [job_id for job_id in job_refs if job_id in rows and row_effective_execution_accepted(rows[job_id]) is False]
    pending_job_ids = [job_id for job_id in job_refs if job_id in rows and row_effective_execution_accepted(rows[job_id]) is None]
    artifact_failing_job_ids = [job_id for job_id in job_refs if job_id in rows and row_effective_artifact_accepted(rows[job_id]) is False]
    artifact_pending_job_ids = [job_id for job_id in job_refs if job_id in rows and row_effective_artifact_accepted(rows[job_id]) is None]
    recovered_job_ids = [job_id for job_id in job_refs if job_id in rows and row_execution_accepted(rows[job_id]) is False and row_effective_execution_accepted(rows[job_id]) is True]
    return {
        'jobRefs': list(job_refs),
        'missingJobIds': missing_job_ids,
        'failingJobIds': failing_job_ids,
        'pendingJobIds': pending_job_ids,
        'artifactFailingJobIds': artifact_failing_job_ids,
        'artifactPendingJobIds': artifact_pending_job_ids,
        'recoveredJobIds': recovered_job_ids,
        'accepted': bool(job_refs and not missing_job_ids and not failing_job_ids and not pending_job_ids and not artifact_failing_job_ids and not artifact_pending_job_ids),
    }


def group_required_run_ledger_job_rows(job_refs: list[str], run_ledger_summary: dict[str, Any]) -> list[dict[str, Any]]:
    items = list(run_ledger_summary.get('items') or []) if isinstance(run_ledger_summary, dict) else []
    rows = {str(item.get('id') or ''): item for item in items if isinstance(item, dict)}
    result: list[dict[str, Any]] = []
    for job_ref in job_refs:
        row = rows.get(job_ref) if isinstance(rows.get(job_ref), dict) else {}
        accepted = row.get('accepted') if isinstance(row, dict) else None
        artifact_accepted = row_artifact_accepted(row) if isinstance(row, dict) else None
        artifact_effective_accepted = row_effective_artifact_accepted(row) if isinstance(row, dict) else None
        execution_accepted = row_execution_accepted(row) if isinstance(row, dict) else None
        effective_execution_accepted = row_effective_execution_accepted(row) if isinstance(row, dict) else None
        effective_status = _effective_status(row, effective_execution_accepted) if isinstance(row, dict) else 'missing'
        result.append({
            'jobRef': job_ref,
            'accepted': accepted,
            'artifactAccepted': artifact_accepted,
            'artifactEffectiveAccepted': artifact_effective_accepted,
            'executionAccepted': execution_accepted,
            'effectiveExecutionAccepted': effective_execution_accepted,
            'status': effective_status,
            'rawStatus': 'accepted' if accepted is True else ('missing' if not row else ('failed' if accepted is False else 'pending')),
            'latestRunId': str(row.get('latestRunId') or '') if isinstance(row, dict) else '',
            'lastCompletedAt': str(row.get('lastCompletedAt') or row.get('lastFinishedAt') or '') if isinstance(row, dict) else '',
            'latestEffectiveAccess': row.get('latestEffectiveAccess') if isinstance(row, dict) else None,
        })
    return result


def group_required_evidence_rows(
    group_item: dict[str, Any],
    run_ledger_status: dict[str, Any],
    agent_access_log_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    release_policy = json_object(group_item.get('releasePolicy'))
    required_refs = [str(item).strip() for item in (release_policy.get('requiredEvidence') or []) if str(item).strip()]
    single_source_docs = [str(item).strip() for item in (release_policy.get('singleSourceDocs') or []) if str(item).strip()]
    exported_evidence = exported_group_evidence_presence(str(group_item.get('id') or '').strip())
    rows: list[dict[str, Any]] = []
    for evidence_ref in required_refs:
        detail = _KNOWN_AGENT_GROUP_EVIDENCE.get(evidence_ref, '当前未注册自动判定逻辑。')
        if evidence_ref == 'group_access_view':
            status = 'available' if bool(exported_evidence.get('groupAccessView')) else 'missing'
        elif evidence_ref == 'group_access_log':
            status = 'available' if bool(exported_evidence.get('groupAccessLog')) and str((agent_access_log_summary.get('path') or '')).strip() else 'missing'
        elif evidence_ref == 'run_ledger':
            status = 'available' if bool(exported_evidence.get('runLedger')) and bool(run_ledger_status.get('accepted')) else 'missing'
        elif evidence_ref == 'group_contract_docs':
            status = 'available' if single_source_docs else 'missing'
        else:
            status = 'declared_only'
        rows.append({'id': evidence_ref, 'status': status, 'detail': detail})
    return rows


def build_agent_group_acceptance_binding(group_item: dict[str, Any], run_ledger_summary: dict[str, Any]) -> dict[str, Any]:
    release_policy = json_object(group_item.get('releasePolicy'))
    binding = json_object(release_policy.get('acceptanceBinding'))
    deployment_statuses = deployment_acceptance_required_check_statuses()
    group_ref = str(group_item.get('id') or '').strip()
    exported_evidence = exported_group_evidence_presence(group_ref)
    deployment_rows: list[dict[str, Any]] = []
    missing_check_ids: list[str] = []
    for check_id in [str(item).strip() for item in (binding.get('deploymentAcceptanceCheckIds') or []) if str(item).strip()]:
        status = deployment_statuses.get(check_id, 'NOT_RECORDED')
        passed = status == 'PASS'
        if not passed:
            missing_check_ids.append(check_id)
        deployment_rows.append({'id': check_id, 'status': status, 'passed': passed})
    runtime_rows: list[dict[str, Any]] = []
    missing_runtime_evidence: list[str] = []
    for entry_id in [str(item).strip() for item in (binding.get('runtimeEvidenceEntryIds') or []) if str(item).strip()]:
        path = evidence_file(entry_id)
        available = evidence_file_exists(entry_id)
        if entry_id == 'control_plane_run_ledger':
            available = available and bool(group_run_ledger_status([str(x).strip() for x in (binding.get('requiredRunLedgerJobRefs') or []) if str(x).strip()], run_ledger_summary).get('accepted'))
        elif entry_id == 'control_plane_agent_access_log':
            available = bool(exported_evidence.get('groupAccessLog'))
        elif entry_id == 'control_plane_agent_group_access':
            available = bool(exported_evidence.get('groupAccessView'))
        elif entry_id == 'control_plane_agent_group_release_gates':
            available = bool(exported_evidence.get('groupReleaseGates'))
        elif entry_id == 'control_plane_agent_group_acceptance_bindings':
            available = bool(exported_evidence.get('acceptanceBindings'))
        if not available:
            missing_runtime_evidence.append(entry_id)
        runtime_rows.append({'id': entry_id, 'path': str(path), 'available': bool(available)})
    required_run_ledger_job_refs = [str(item).strip() for item in (binding.get('requiredRunLedgerJobRefs') or []) if str(item).strip()]
    run_ledger_rows = group_required_run_ledger_job_rows(required_run_ledger_job_refs, run_ledger_summary)
    failing_run_ledger_job_refs = [
        str(item.get('jobRef') or '')
        for item in run_ledger_rows
        if item.get('effectiveExecutionAccepted') is not True or item.get('artifactEffectiveAccepted') is not True
    ]
    accepted = bool(deployment_rows and runtime_rows and run_ledger_rows and not missing_check_ids and not missing_runtime_evidence and not failing_run_ledger_job_refs)
    return {
        'deploymentAcceptanceChecks': deployment_rows,
        'runtimeEvidence': runtime_rows,
        'requiredRunLedgerJobs': run_ledger_rows,
        'missingDeploymentAcceptanceCheckIds': missing_check_ids,
        'missingRuntimeEvidenceEntryIds': missing_runtime_evidence,
        'failingRunLedgerJobRefs': failing_run_ledger_job_refs,
        'accepted': accepted,
        'notes': [str(item).strip() for item in (binding.get('notes') or []) if str(item).strip()],
    }


def build_agent_group_release_gate(
    group_item: dict[str, Any],
    recent_access: dict[str, Any],
    run_ledger_status: dict[str, Any],
    agent_access_log_summary: dict[str, Any],
    acceptance_binding: dict[str, Any],
) -> dict[str, Any]:
    release_policy = json_object(group_item.get('releasePolicy'))
    release_gate = json_object(release_policy.get('releaseGate'))
    freeze_on_statuses = [str(item).strip() for item in (release_gate.get('freezeOnStatuses') or []) if str(item).strip()]
    health = json_object(group_item.get('health'))
    health_status = str(health.get('status') or '').strip()
    member_rows = [dict(item) for item in (group_item.get('members') or []) if isinstance(item, dict)]
    member_health_ok = bool(member_rows) and all(bool(item.get('configured')) and not any(str(status) in {'failed', 'blocked', 'retry_pending'} and int(count or 0) > 0 for status, count in ((item.get('jobStatusCounts') or {}).items())) for item in member_rows)
    evidence_rows = group_required_evidence_rows(group_item, run_ledger_status, agent_access_log_summary)
    evidence_ok = bool(evidence_rows) and all(str(item.get('status') or '') == 'available' for item in evidence_rows)
    recent_access_ok = int(recent_access.get('invocationCount') or 0) > 0
    checks: list[dict[str, Any]] = []
    for check_id in [str(item).strip() for item in (release_gate.get('requiredCheckIds') or []) if str(item).strip()]:
        passed = False
        detail = ''
        if check_id == 'group_health':
            passed = health_status == 'healthy'
            detail = f'group 当前 health.status={health_status or "unknown"}'
        elif check_id == 'member_health':
            passed = member_health_ok if bool(release_gate.get('requireHealthyMembers', False)) else True
            detail = '要求所有成员已配置且无失败/阻断状态。'
        elif check_id == 'run_ledger':
            passed = bool(run_ledger_status.get('accepted')) if bool(release_gate.get('requireRunLedgerCoverage', False)) else True
            detail = '要求 group 关联 job 的 run ledger 最新有效状态全部 accepted。'
        elif check_id == 'recent_access':
            passed = recent_access_ok if bool(release_gate.get('requireRecentAccess', False)) else True
            detail = '要求 group 存在最近一次访问记录。'
        elif check_id == 'required_evidence':
            passed = evidence_ok
            detail = '要求声明的 requiredEvidence 均可自动观测并处于 available。'
        elif check_id == 'acceptance_binding':
            passed = bool(acceptance_binding.get('accepted'))
            detail = '要求 group 对 deployment acceptance required checks、runtime evidence 与 required run ledger jobs 的正式映射全部闭合。'
        checks.append({'id': check_id, 'passed': bool(passed), 'detail': detail})
    failed_check_ids = [str(item.get('id') or '') for item in checks if item.get('passed') is not True]
    frozen = bool(health_status and health_status in set(freeze_on_statuses))
    status = 'passed' if checks and not failed_check_ids and not frozen else ('frozen' if frozen else 'blocked')
    rollback_contract = json_object(release_policy.get('rollbackContract'))
    return {
        'status': status,
        'changeControl': str(release_policy.get('changeControl') or '').strip(),
        'requiredCheckIds': [str(item).strip() for item in (release_gate.get('requiredCheckIds') or []) if str(item).strip()],
        'checks': checks,
        'failedCheckIds': failed_check_ids,
        'freezeOnStatuses': freeze_on_statuses,
        'frozenByHealthStatus': health_status if frozen else '',
        'requiredEvidence': evidence_rows,
        'missingEvidenceRefs': [str(item.get('id') or '') for item in evidence_rows if str(item.get('status') or '') != 'available'],
        'recentAccessRequired': bool(release_gate.get('requireRecentAccess', False)),
        'runLedgerCoverageRequired': bool(release_gate.get('requireRunLedgerCoverage', False)),
        'healthyMembersRequired': bool(release_gate.get('requireHealthyMembers', False)),
        'runLedger': run_ledger_status,
        'acceptanceBinding': acceptance_binding,
        'rollbackContract': {
            'strategy': str(rollback_contract.get('strategy') or '').strip(),
            'triggerSignals': [str(item).strip() for item in (rollback_contract.get('triggerSignals') or []) if str(item).strip()],
            'requiredEvidenceRefs': [str(item).strip() for item in (rollback_contract.get('requiredEvidenceRefs') or []) if str(item).strip()],
            'operatorSteps': [str(item).strip() for item in (rollback_contract.get('operatorSteps') or []) if str(item).strip()],
            'maxRecoveryMinutes': int(rollback_contract.get('maxRecoveryMinutes') or 0),
            'recommended': bool(status in {'blocked', 'frozen'}),
        },
        'singleSourceDocs': [str(item).strip() for item in (release_policy.get('singleSourceDocs') or []) if str(item).strip()],
    }
