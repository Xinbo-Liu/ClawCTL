#!/usr/bin/env python3
"""Group-level runtime validation helpers for the control-plane registry."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.control_plane.registry.owners import qualified_registry_id, row_owner_id
from openclaw.control_plane.registry.binding_topology import _derive_group_agent_views
from openclaw.control_plane.registry_validation.runtime_policy import _normalize_group_recovery_policy
from openclaw.control_plane.registry.support import _AGENT_GROUP_RELEASE_CHECK_IDS, _ensure_unique_text_list
from openclaw.control_plane.surfaces import load_testing_manifest
from openclaw.lib.cli.common import CliError
from openclaw.lib.control_plane.object_families import get_family
from openclaw.lib.io.json_access import json_object
from openclaw.lib.repo.static_truth import repo_contract_path
from openclaw.scheduler.cron import resolve_timezone


def _path_is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _runtime_acceptance_truth(
    repo_root: Path,
    *,
    config_path: Path | None = None,
    extensions: list[dict[str, Any]] | None = None,
) -> dict[str, set[str]]:
    """加载运行态 acceptance 真源集合。"""
    testing_manifest_path = repo_contract_path('runtime.testing_manifest', root_dir=repo_root)
    payload = load_testing_manifest(testing_manifest_path, config_path=config_path, extensions=extensions)
    acceptance = json_object(payload.get('acceptance_reference'))
    required_checks = set(
        _ensure_unique_text_list(
            acceptance.get('required_checks'),
            label='runtime testing manifest acceptance_reference.required_checks',
        )
    )
    required_run_ledger_jobs = set(
        _ensure_unique_text_list(
            acceptance.get('required_run_ledger_jobs'),
            label='runtime testing manifest acceptance_reference.required_run_ledger_jobs',
        )
    )
    runtime_evidence_ids = {
        str(item.get('id') or '').strip()
        for item in (
            get_family(
                'runtime_evidence',
                repo_root,
                config_path=config_path,
                resolve_paths=False,
                extensions=extensions,
            ).get('entries') or []
        )
        if isinstance(item, dict) and str(item.get('id') or '').strip()
    }
    return {
        'required_checks': required_checks,
        'required_run_ledger_jobs': required_run_ledger_jobs,
        'runtime_evidence_ids': runtime_evidence_ids,
    }


def _validate_group_topology_and_members(
    group: dict[str, Any],
    agents_by_id: dict[str, dict[str, Any]],
    jobs_by_id: dict[str, dict[str, Any]],
    *,
    job_bindings_by_job_id: dict[str, dict[str, Any]],
    group_topologies_by_group_id: dict[str, dict[str, Any]],
    collections: dict[str, Any] | None = None,
) -> dict[str, Any]:
    group_id = str(group.get('id') or '')
    group_owner_id = row_owner_id(group)
    group_qualified_id = str(group.get('qualifiedId') or qualified_registry_id(group_owner_id, group_id))
    dependency_policy = json_object(group.get('dependencyPolicy'))
    schedule_policy = json_object(group.get('schedulePolicy'))
    timezone_name = str(schedule_policy.get('timezone') or '').strip()
    if not timezone_name:
        raise CliError(f'agent group {group_id} schedulePolicy.timezone cannot be empty', 2)
    try:
        resolve_timezone(timezone_name)
    except CliError as exc:
        raise CliError(f'agent group {group_id} schedulePolicy.timezone 无效：{exc}', 2) from exc
    schedule_policy['timezone'] = timezone_name
    topology = json_object(group_topologies_by_group_id.get(group_qualified_id) or group_topologies_by_group_id.get(group_id))
    schedule_job_refs = _ensure_unique_text_list(
        topology.get('scheduleJobRefs') or [],
        label=f'agent group {group_id} schedulePolicy.jobRefs',
    )
    ordered_job_refs = _ensure_unique_text_list(
        topology.get('orderedJobRefs') or [],
        label=f'agent group {group_id} dependencyPolicy.orderedJobRefs',
    )
    if not schedule_job_refs:
        raise CliError(f'agent group {group_id} schedulePolicy.jobRefs cannot be empty', 2)
    if not ordered_job_refs:
        raise CliError(f'agent group {group_id} dependencyPolicy.orderedJobRefs cannot be empty', 2)
    order_base = int(schedule_policy.get('orderBase') or 0)
    order_step = int(schedule_policy.get('orderStep') or 0)
    if order_base < 1 or order_step < 1:
        raise CliError(f'agent group {group_id} orderBase/orderStep must be >= 1', 2)
    derived_group_agents = _derive_group_agent_views(
        group_id=group_id,
        schedule_job_refs=schedule_job_refs,
        ordered_job_refs=ordered_job_refs,
        jobs_by_id=(collections.get('jobsByQualifiedId') if isinstance(collections, dict) and isinstance(collections.get('jobsByQualifiedId'), dict) else jobs_by_id),
        job_bindings_by_job_id=job_bindings_by_job_id,
    )
    members = list(derived_group_agents['memberAgentRefs'])
    ordered_members = list(derived_group_agents['orderedMembers'])
    entry_agents = list(derived_group_agents['entryAgentRefs'])
    exit_agents = list(derived_group_agents['exitAgentRefs'])
    for agent_ref in [*members, *entry_agents, *exit_agents]:
        if agent_ref not in agents_by_id and not (isinstance(collections, dict) and agent_ref in (collections.get('agentsByQualifiedId') or {})):
            raise CliError(f'agent group {group_id} references unknown agent {agent_ref}', 2)
    if not set(entry_agents).issubset(set(members)):
        raise CliError(f'agent group {group_id} entryAgentRefs must be subset of members', 2)
    if not set(exit_agents).issubset(set(members)):
        raise CliError(f'agent group {group_id} exitAgentRefs must be subset of members', 2)
    retry_mode = str(dependency_policy.get('retryMode') or '').strip()
    recovery_policy_payload = group.get('recoveryPolicy')
    if isinstance(recovery_policy_payload, dict) and topology.get('recoverySteps') is not None:
        recovery_policy_payload = {
            **recovery_policy_payload,
            'steps': [dict(item) for item in (topology.get('recoverySteps') or []) if isinstance(item, dict)],
        }
    normalized_recovery_policy = _normalize_group_recovery_policy(
        group_id,
        recovery_policy_payload,
        retry_mode=retry_mode,
        schedule_job_refs=schedule_job_refs,
        ordered_job_refs=ordered_job_refs,
        jobs_by_id=(collections.get('jobsByQualifiedId') if isinstance(collections, dict) and isinstance(collections.get('jobsByQualifiedId'), dict) else jobs_by_id),
        job_bindings_by_job_id=job_bindings_by_job_id,
    )
    return {
        'groupId': group_id,
        'schedulePolicy': schedule_policy,
        'scheduleJobRefs': schedule_job_refs,
        'localScheduleJobRefs': _ensure_unique_text_list(topology.get('localScheduleJobRefs') or [], label=f'agent group {group_id} localScheduleJobRefs') or schedule_job_refs,
        'orderedJobRefs': ordered_job_refs,
        'localOrderedJobRefs': _ensure_unique_text_list(topology.get('localOrderedJobRefs') or [], label=f'agent group {group_id} localOrderedJobRefs') or ordered_job_refs,
        'orderBase': order_base,
        'orderStep': order_step,
        'members': members,
        'orderedMembers': ordered_members,
        'entryAgentRefs': entry_agents,
        'exitAgentRefs': exit_agents,
        'normalizedRecoveryPolicy': normalized_recovery_policy,
    }


def _resolve_group_release_gate(group_id: str, release_policy: dict[str, Any]) -> dict[str, Any]:
    release_gate = json_object(release_policy.get('releaseGate'))
    required_check_ids = _ensure_unique_text_list(
        release_gate.get('requiredCheckIds'),
        label=f'agent group {group_id} releasePolicy.releaseGate.requiredCheckIds',
    )
    unknown_check_ids = sorted(set(required_check_ids) - _AGENT_GROUP_RELEASE_CHECK_IDS)
    if unknown_check_ids:
        raise CliError(f'agent group {group_id} unknown release gate checks: {", ".join(unknown_check_ids)}', 2)
    freeze_statuses = _ensure_unique_text_list(
        release_gate.get('freezeOnStatuses'),
        label=f'agent group {group_id} releasePolicy.releaseGate.freezeOnStatuses',
    )
    return {
        'requiredCheckIds': required_check_ids,
        'freezeOnStatuses': freeze_statuses,
        'requireHealthyMembers': bool(release_gate.get('requireHealthyMembers', False)),
        'requireRecentAccess': bool(release_gate.get('requireRecentAccess', False)),
        'requireRunLedgerCoverage': bool(release_gate.get('requireRunLedgerCoverage', False)),
    }


def _resolve_group_acceptance_binding(
    group_id: str,
    release_policy: dict[str, Any],
    *,
    acceptance_truth: dict[str, set[str]],
    schedule_job_refs: list[str],
) -> dict[str, Any]:
    acceptance_binding = json_object(release_policy.get('acceptanceBinding'))
    deployment_acceptance_check_ids = _ensure_unique_text_list(
        acceptance_binding.get('deploymentAcceptanceCheckIds'),
        label=f'agent group {group_id} releasePolicy.acceptanceBinding.deploymentAcceptanceCheckIds',
    )
    unknown_acceptance_check_ids = sorted(set(deployment_acceptance_check_ids) - acceptance_truth['required_checks'])
    if unknown_acceptance_check_ids:
        raise CliError(f'agent group {group_id} unknown deployment acceptance checks: {", ".join(unknown_acceptance_check_ids)}', 2)
    runtime_evidence_entry_ids = _ensure_unique_text_list(
        acceptance_binding.get('runtimeEvidenceEntryIds'),
        label=f'agent group {group_id} releasePolicy.acceptanceBinding.runtimeEvidenceEntryIds',
    )
    unknown_runtime_evidence_ids = sorted(set(runtime_evidence_entry_ids) - acceptance_truth['runtime_evidence_ids'])
    if unknown_runtime_evidence_ids:
        raise CliError(f'agent group {group_id} unknown runtime evidence ids: {", ".join(unknown_runtime_evidence_ids)}', 2)
    required_run_ledger_job_refs = _ensure_unique_text_list(
        acceptance_binding.get('requiredRunLedgerJobRefs'),
        label=f'agent group {group_id} releasePolicy.acceptanceBinding.requiredRunLedgerJobRefs',
    )
    if not set(required_run_ledger_job_refs).issubset(set(schedule_job_refs)):
        raise CliError(f'agent group {group_id} requiredRunLedgerJobRefs must be subset of scheduleJobRefs', 2)
    unknown_required_run_ledger_job_refs = sorted(set(required_run_ledger_job_refs) - acceptance_truth['required_run_ledger_jobs'])
    if unknown_required_run_ledger_job_refs:
        raise CliError(f'agent group {group_id} unknown required_run_ledger_jobs: {", ".join(unknown_required_run_ledger_job_refs)}', 2)
    return {
        'deploymentAcceptanceCheckIds': deployment_acceptance_check_ids,
        'runtimeEvidenceEntryIds': runtime_evidence_entry_ids,
        'requiredRunLedgerJobRefs': required_run_ledger_job_refs,
        'notes': [str(item).strip() for item in (acceptance_binding.get('notes') or []) if str(item).strip()],
    }


def _resolve_group_rollback_contract(group_id: str, release_policy: dict[str, Any]) -> dict[str, Any]:
    rollback_contract = json_object(release_policy.get('rollbackContract'))
    trigger_signals = _ensure_unique_text_list(
        rollback_contract.get('triggerSignals'),
        label=f'agent group {group_id} releasePolicy.rollbackContract.triggerSignals',
    )
    required_evidence_refs = _ensure_unique_text_list(
        rollback_contract.get('requiredEvidenceRefs'),
        label=f'agent group {group_id} releasePolicy.rollbackContract.requiredEvidenceRefs',
    )
    operator_steps = _ensure_unique_text_list(
        rollback_contract.get('operatorSteps'),
        label=f'agent group {group_id} releasePolicy.rollbackContract.operatorSteps',
    )
    max_recovery_minutes = int(rollback_contract.get('maxRecoveryMinutes') or 0)
    if max_recovery_minutes < 1:
        raise CliError(f'agent group {group_id} rollback maxRecoveryMinutes must be >= 1', 2)
    return {
        'strategy': str(rollback_contract.get('strategy') or '').strip(),
        'triggerSignals': trigger_signals,
        'requiredEvidenceRefs': required_evidence_refs,
        'operatorSteps': operator_steps,
        'maxRecoveryMinutes': max_recovery_minutes,
    }


def _resolve_group_release_policy(
    group_id: str,
    group: dict[str, Any],
    *,
    repo_root: Path,
    acceptance_truth: dict[str, set[str]],
    schedule_job_refs: list[str],
) -> dict[str, Any]:
    release_policy = json_object(group.get('releasePolicy'))
    single_source_docs = _ensure_unique_text_list(
        release_policy.get('singleSourceDocs'),
        label=f'agent group {group_id} releasePolicy.singleSourceDocs',
    )
    for rel_doc in single_source_docs:
        doc_path = (repo_root / rel_doc).resolve()
        if Path(rel_doc).is_absolute() or not _path_is_relative_to(doc_path, repo_root):
            raise CliError(f'agent group {group_id} singleSourceDocs must stay inside repository: {rel_doc}', 2)
        if not doc_path.exists() or not doc_path.is_file():
            raise CliError(f'agent group {group_id} singleSourceDocs file missing: {rel_doc}', 2)
    return {
        'changeControl': str(release_policy.get('changeControl') or '').strip(),
        'singleSourceDocs': single_source_docs,
        'requiredEvidence': _ensure_unique_text_list(
            release_policy.get('requiredEvidence'),
            label=f'agent group {group_id} releasePolicy.requiredEvidence',
        ),
        'releaseGate': _resolve_group_release_gate(group_id, release_policy),
        'acceptanceBinding': _resolve_group_acceptance_binding(
            group_id,
            release_policy,
            acceptance_truth=acceptance_truth,
            schedule_job_refs=schedule_job_refs,
        ),
        'rollbackContract': _resolve_group_rollback_contract(group_id, release_policy),
    }


def _materialize_group_resolved_fields(
    group: dict[str, Any],
    *,
    topology_state: dict[str, Any],
    release_policy: dict[str, Any],
) -> None:
    group['resolvedMembers'] = topology_state['members']
    group['resolvedEntryAgentRefs'] = topology_state['entryAgentRefs']
    group['resolvedExitAgentRefs'] = topology_state['exitAgentRefs']
    group['resolvedOrderedMembers'] = topology_state['orderedMembers']
    group['resolvedSchedulePolicy'] = {
        'timezone': str(topology_state['schedulePolicy'].get('timezone') or '').strip(),
        'windowRef': str(topology_state['schedulePolicy'].get('windowRef') or '').strip(),
        'orderBase': topology_state['orderBase'],
        'orderStep': topology_state['orderStep'],
        'jobRefs': topology_state['scheduleJobRefs'],
    }
    group['resolvedOrderedJobRefs'] = topology_state['orderedJobRefs']
    group['resolvedRecoveryPolicy'] = topology_state['normalizedRecoveryPolicy']
    group['resolvedReleasePolicy'] = release_policy
