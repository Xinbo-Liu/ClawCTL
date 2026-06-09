#!/usr/bin/env python3
"""Runtime-policy normalization helpers for control-plane registry validation."""
from __future__ import annotations

from typing import Any

from openclaw.control_plane.jobs.defaults import (
    DEFAULT_JOB_ARTIFACT_RETENTION_DAYS,
    DEFAULT_JOB_CONCURRENCY_POLICY,
    DEFAULT_JOB_ENABLED,
    DEFAULT_JOB_IDEMPOTENCY_STRATEGY,
    DEFAULT_JOB_SCHEDULE_KIND,
    agent_capabilities,
    artifact_policy_fields,
    infer_job_idempotency_key_policy,
    infer_job_order_from_source_path,
    infer_job_retry_policy,
    infer_job_run_artifact_root,
    infer_job_timeout_seconds,
    module_kind,
    normalize_failure_class_policy,
    normalize_idempotency_key_policy,
    normalize_retry_policy,
)
from openclaw.control_plane.registry.support import (
    CliError,
    _ensure_unique_text_list,
    _normalized_dependencies,
)
from openclaw.lib.io.json_access import json_array, json_object
from openclaw.scheduler.cron import resolve_timezone, validate_cron_expr


def _validate_default_timezone(default_timezone: str) -> None:
    timezone_name = str(default_timezone or "").strip()
    if not timezone_name:
        raise CliError("defaults.timezone 不能为空", 2)
    try:
        resolve_timezone(timezone_name)
    except CliError as exc:
        raise CliError(f"defaults.timezone 无效：{exc}", 2) from exc


def _validate_retry_policy(job: dict[str, Any]) -> None:
    retry = normalize_retry_policy(job.get('retryPolicy'))
    enabled = bool(retry.get('enabled'))
    max_attempts = int(retry.get('maxAttempts') or 0)
    backoff = [int(item) for item in json_array(retry.get('backoffSeconds'))]
    if enabled:
        if max_attempts < 1:
            raise CliError('control-plane validation error', 2)
        if len(backoff) != max_attempts:
            raise CliError('control-plane validation error', 2)
    else:
        if max_attempts != 0 or backoff:
            raise CliError('control-plane validation error', 2)


def _normalize_job_schedule(job: dict[str, Any], *, default_timezone: str) -> None:
    schedule = json_object(job.get('schedule'))
    expr = str(schedule.get('expr') or '').strip()
    if not expr:
        raise CliError('control-plane validation error', 2)
    kind = str(schedule.get('kind') or DEFAULT_JOB_SCHEDULE_KIND).strip() or DEFAULT_JOB_SCHEDULE_KIND
    if kind != DEFAULT_JOB_SCHEDULE_KIND:
        raise CliError('control-plane validation error', 2)
    timezone_name = str(schedule.get('tz') or default_timezone).strip()
    if not timezone_name:
        raise CliError('control-plane validation error', 2)
    try:
        validate_cron_expr(expr)
        resolve_timezone(timezone_name)
    except CliError as exc:
        raise CliError(f"job {job.get('id') or '<unknown>'} schedule 无效：{exc}", 2) from exc
    job['schedule'] = {
        'kind': kind,
        'expr': expr,
        'tz': timezone_name,
    }


def _normalize_job_artifact_policy(job: dict[str, Any], *, capabilities: dict[str, Any]) -> None:
    artifact_policy = artifact_policy_fields(job.get('artifactPolicy'))
    latest_alias = artifact_policy['latestAlias']
    if not latest_alias:
        raise CliError('control-plane validation error', 2)
    run_artifact_root = artifact_policy['runArtifactRoot'] or infer_job_run_artifact_root(capabilities=capabilities)
    if not run_artifact_root:
        raise CliError('control-plane validation error', 2)
    retention_days = int(artifact_policy['retentionDays'] or DEFAULT_JOB_ARTIFACT_RETENTION_DAYS)
    if retention_days < 1:
        raise CliError('control-plane validation error', 2)
    job['artifactPolicy'] = {
        'runArtifactRoot': run_artifact_root,
        'latestAlias': latest_alias,
        'retentionDays': retention_days,
    }


def _normalize_job_runtime_policy(
    job: dict[str, Any],
    *,
    agent: dict[str, Any],
    module: dict[str, Any],
    is_recovery_job: bool,
) -> None:
    capabilities = agent_capabilities(agent)
    job['enabled'] = bool(job.get('enabled', DEFAULT_JOB_ENABLED))
    concurrency_policy = str(job.get('concurrencyPolicy') or DEFAULT_JOB_CONCURRENCY_POLICY).strip().lower() or DEFAULT_JOB_CONCURRENCY_POLICY
    if concurrency_policy != DEFAULT_JOB_CONCURRENCY_POLICY:
        raise CliError('control-plane validation error', 2)
    job['concurrencyPolicy'] = DEFAULT_JOB_CONCURRENCY_POLICY

    timeout_seconds = int(job.get('timeoutSeconds') or infer_job_timeout_seconds(module_kind_value=module_kind(module), capabilities=capabilities))
    if timeout_seconds < 1:
        raise CliError('control-plane validation error', 2)
    job['timeoutSeconds'] = timeout_seconds

    retry_payload = normalize_retry_policy(job.get('retryPolicy')) if isinstance(job.get('retryPolicy'), dict) else infer_job_retry_policy(capabilities=capabilities, is_recovery_job=is_recovery_job)
    job['retryPolicy'] = retry_payload
    _validate_retry_policy(job)

    idempotency_payload = normalize_idempotency_key_policy(job.get('idempotencyKeyPolicy')) if isinstance(job.get('idempotencyKeyPolicy'), dict) else infer_job_idempotency_key_policy(capabilities=capabilities)
    if str(idempotency_payload.get('strategy') or '').strip() not in {'job_schedule_slot', 'manual_trigger'}:
        raise CliError('control-plane validation error', 2)
    if str(idempotency_payload.get('scope') or '').strip() not in {'job_id', 'job_id_plus_target'}:
        raise CliError('control-plane validation error', 2)
    job['idempotencyKeyPolicy'] = idempotency_payload

    job['failureClassPolicy'] = normalize_failure_class_policy(job.get('failureClassPolicy'))
    _normalize_job_artifact_policy(job, capabilities=capabilities)


def _normalize_generic_job_runtime_policy(job: dict[str, Any]) -> None:
    job_id = str(job.get('id') or '').strip()
    job['enabled'] = bool(job.get('enabled', DEFAULT_JOB_ENABLED))
    concurrency_policy = str(job.get('concurrencyPolicy') or DEFAULT_JOB_CONCURRENCY_POLICY).strip().lower() or DEFAULT_JOB_CONCURRENCY_POLICY
    if concurrency_policy != DEFAULT_JOB_CONCURRENCY_POLICY:
        raise CliError('control-plane validation error', 2)
    job['concurrencyPolicy'] = DEFAULT_JOB_CONCURRENCY_POLICY

    timeout_seconds = int(job.get('timeoutSeconds') or 900)
    if timeout_seconds < 1:
        raise CliError('control-plane validation error', 2)
    job['timeoutSeconds'] = timeout_seconds

    retry_payload = normalize_retry_policy(job.get('retryPolicy')) if isinstance(job.get('retryPolicy'), dict) else {
        'enabled': False,
        'maxAttempts': 0,
        'backoffSeconds': [],
    }
    job['retryPolicy'] = retry_payload
    _validate_retry_policy(job)

    idempotency_payload = normalize_idempotency_key_policy(job.get('idempotencyKeyPolicy')) if isinstance(job.get('idempotencyKeyPolicy'), dict) else {
        'strategy': DEFAULT_JOB_IDEMPOTENCY_STRATEGY,
        'scope': 'job_id',
    }
    if str(idempotency_payload.get('strategy') or '').strip() not in {'job_schedule_slot', 'manual_trigger'}:
        raise CliError('control-plane validation error', 2)
    if str(idempotency_payload.get('scope') or '').strip() not in {'job_id', 'job_id_plus_target'}:
        raise CliError('control-plane validation error', 2)
    job['idempotencyKeyPolicy'] = idempotency_payload
    job['failureClassPolicy'] = normalize_failure_class_policy(job.get('failureClassPolicy'))

    artifact_policy = artifact_policy_fields(job.get('artifactPolicy'))
    latest_alias = artifact_policy['latestAlias']
    if not latest_alias:
        raise CliError('control-plane validation error', 2)
    run_artifact_root = artifact_policy['runArtifactRoot'] or f'jobs/{job_id}'
    retention_days = int(artifact_policy['retentionDays'] or DEFAULT_JOB_ARTIFACT_RETENTION_DAYS)
    if retention_days < 1:
        raise CliError('control-plane validation error', 2)
    job['artifactPolicy'] = {
        'runArtifactRoot': run_artifact_root,
        'latestAlias': latest_alias,
        'retentionDays': retention_days,
    }


def _resolved_job_order(job: dict[str, Any], groups_by_id: dict[str, dict[str, Any]]) -> int:
    job_id = str(job.get('resolvedRuntimeJobKey') or job.get('id') or '').strip()
    group_ref = str(job.get('resolvedGroupRef') or job.get('groupRef') or '').strip()
    if not group_ref:
        order = int(job.get('order') or infer_job_order_from_source_path(job.get('sourcePath')) or 0)
        if order < 1:
            raise CliError('control-plane validation error', 2)
        return order
    group = groups_by_id.get(group_ref)
    if not isinstance(group, dict):
        raise CliError('control-plane validation error', 2)
    if job.get('order') is not None:
        raise CliError('control-plane validation error', 2)
    schedule_policy = json_object(group.get('resolvedSchedulePolicy'))
    job_refs = _ensure_unique_text_list(schedule_policy.get('jobRefs') or [], label=f'agent group {group_ref} schedulePolicy.jobRefs')
    if job_id not in job_refs:
        raise CliError('control-plane validation error', 2)
    order_base = int(schedule_policy.get('orderBase') or 0)
    order_step = int(schedule_policy.get('orderStep') or 0)
    if order_base < 1 or order_step < 1:
        raise CliError('control-plane validation error', 2)
    return order_base + job_refs.index(job_id) * order_step


def _resolved_group_dependencies(job: dict[str, Any], group: dict[str, Any] | None) -> list[dict[str, Any]]:
    explicit = _normalized_dependencies(job)
    if not isinstance(group, dict):
        return explicit
    group_id = str(group.get('id') or '').strip()
    ordered_job_refs = _ensure_unique_text_list(group.get('resolvedOrderedJobRefs') or [], label=f'agent group {group_id} resolvedOrderedJobRefs') if group.get('resolvedOrderedJobRefs') is not None else []
    if not ordered_job_refs:
        return explicit
    job_id = str(job.get('resolvedRuntimeJobKey') or job.get('id') or '').strip()
    if job_id not in ordered_job_refs:
        return explicit
    explicit_ids = {str(item.get('jobId') or '').strip() for item in explicit}
    group_job_set = set(ordered_job_refs)
    for dep_id in sorted(explicit_ids & group_job_set):
        raise CliError('control-plane validation error', 2)
    index = ordered_job_refs.index(job_id)
    if index == 0:
        return explicit
    derived = {'jobId': ordered_job_refs[index - 1], 'requiredStatuses': ['succeeded'], 'maxAgeMinutes': 240, 'source': f'group:{group_id}'}
    return [*explicit, derived]


def _normalize_group_recovery_policy(
    group_id: str,
    recovery_policy: Any,
    *,
    retry_mode: str,
    schedule_job_refs: list[str],
    ordered_job_refs: list[str],
    jobs_by_id: dict[str, dict[str, Any]],
    job_bindings_by_job_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if retry_mode == 'stage_owned':
        if isinstance(recovery_policy, dict) and recovery_policy:
            raise CliError('control-plane validation error', 2)
        return None
    if retry_mode != 'group_owned':
        raise CliError('control-plane validation error', 2)
    if not isinstance(recovery_policy, dict):
        raise CliError('control-plane validation error', 2)
    mode = str(recovery_policy.get('mode') or '').strip()
    if mode != 'scheduled_job_sequence':
        raise CliError('control-plane validation error', 2)
    trigger_status_signals = _ensure_unique_text_list(recovery_policy.get('triggerStatusSignals') or [], label=f'agent group {group_id} recoveryPolicy.triggerStatusSignals')
    steps_input = [item for item in (recovery_policy.get('steps') or []) if isinstance(item, dict)]
    if not steps_input:
        raise CliError('control-plane validation error', 2)
    seen_job_refs: set[str] = set()
    steps: list[dict[str, Any]] = []
    prev_after_minutes = 0
    ordered_job_set = set(ordered_job_refs)
    for item in steps_input:
        job_ref = str(item.get('jobRef') or '').strip()
        if not job_ref:
            raise CliError('control-plane validation error', 2)
        if job_ref in seen_job_refs:
            raise CliError('control-plane validation error', 2)
        seen_job_refs.add(job_ref)
        if job_ref not in schedule_job_refs:
            raise CliError('control-plane validation error', 2)
        if job_ref in ordered_job_set:
            raise CliError('control-plane validation error', 2)
        action_kind = str(item.get('actionKind') or '').strip()
        if action_kind not in {'retry', 'compensate', 'replay'}:
            raise CliError('control-plane validation error', 2)
        after_minutes = int(item.get('afterMinutes') or 0)
        if after_minutes < 1:
            raise CliError('control-plane validation error', 2)
        if after_minutes <= prev_after_minutes:
            raise CliError('control-plane validation error', 2)
        prev_after_minutes = after_minutes
        binding = job_bindings_by_job_id.get(job_ref)
        if not isinstance(binding, dict):
            raise CliError('control-plane validation error', 2)
        derived_operation_ref = str(binding.get('operationRef') or '').strip()
        declared_operation_ref = str(item.get('operationRef') or '').strip()
        if declared_operation_ref and declared_operation_ref != derived_operation_ref:
            raise CliError('control-plane validation error', 2)
        operation_ref = derived_operation_ref
        if not operation_ref:
            raise CliError('control-plane validation error', 2)
        job = jobs_by_id.get(job_ref)
        if not isinstance(job, dict):
            raise CliError('control-plane validation error', 2)
        binding_group_ref = str(binding.get('groupRef') or '').strip()
        declared_group_ref = str(job.get('groupRef') or '').strip()
        effective_group_ref = declared_group_ref or binding_group_ref
        if effective_group_ref != group_id:
            raise CliError('control-plane validation error', 2)
        steps.append({
            'jobRef': job_ref,
            'actionKind': action_kind,
            'afterMinutes': after_minutes,
            'operationRef': operation_ref,
            'agentRef': str(binding.get('agentRef') or '').strip(),
            'schedule': dict(job.get('schedule') or {}) if isinstance(job.get('schedule'), dict) else {},
            'notes': [str(note).strip() for note in (item.get('notes') or []) if str(note).strip()],
        })
    return {
        'mode': mode,
        'triggerStatusSignals': trigger_status_signals,
        'haltMainlineOnRecoveryPending': bool(recovery_policy.get('haltMainlineOnRecoveryPending', False)),
        'steps': steps,
        'notes': [str(note).strip() for note in (recovery_policy.get('notes') or []) if str(note).strip()],
    }


def _resolve_model_ref(job: dict[str, Any], agent: dict[str, Any]) -> str:
    return str(job.get('modelProfileRef') or agent.get('defaultModelProfileRef') or '').strip()
