#!/usr/bin/env python3
"""Agent module attach job surface helpers."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from openclaw.control_plane.jobs.defaults import (
    build_compact_job_manifest,
    infer_job_idempotency_key_policy,
    infer_job_retry_policy,
    infer_job_run_artifact_root,
    infer_job_timeout_seconds,
)
from openclaw.control_plane.registry import CliError
from openclaw.lib.io.json_access import json_object
from openclaw.scheduler.cron import resolve_timezone, validate_cron_expr

JOB_FILE_PATTERN = re.compile(r'^(?P<prefix>[0-9]+)_.*\.json$')


def _module_kind(module_payload: dict[str, Any]) -> str:
    return str(module_payload.get('moduleKind') or 'worker').strip() or 'worker'


def _next_job_file_prefix(jobs_dir: Path) -> str:
    max_prefix = 0
    for path in jobs_dir.glob('*.json'):
        match = JOB_FILE_PATTERN.match(path.name)
        if not match:
            continue
        max_prefix = max(max_prefix, int(match.group('prefix')))
    next_prefix = max_prefix + 10 if max_prefix else 10
    return f'{next_prefix:02d}'


def derive_artifact_root(capabilities: dict[str, Any], *, explicit: str) -> str:
    if explicit:
        return explicit
    inferred = infer_job_run_artifact_root(capabilities=capabilities)
    if inferred:
        return inferred
    raise CliError('缺少 --run-artifact-root；当前模块 capabilities.filesystemWrite 为空，无法自动推导', 2)


def derive_timeout_seconds(module_payload: dict[str, Any], capabilities: dict[str, Any], *, explicit: int | None) -> int:
    if explicit is not None:
        value = int(explicit)
        if value < 1:
            raise CliError('--timeout-seconds 必须 >= 1', 2)
        return value
    return infer_job_timeout_seconds(module_kind_value=_module_kind(module_payload), capabilities=capabilities)


def derive_retry_policy(
    capabilities: dict[str, Any],
    *,
    group_placement: str,
    retry_enabled: bool | None,
    retry_max_attempts: int | None,
    retry_backoff_seconds: list[int],
) -> dict[str, Any]:
    is_recovery_job = group_placement == 'recovery'
    default_retry = infer_job_retry_policy(capabilities=capabilities, is_recovery_job=is_recovery_job)
    enabled = bool(default_retry.get('enabled')) if retry_enabled is None else bool(retry_enabled)
    if retry_max_attempts is not None:
        max_attempts = int(retry_max_attempts)
        if max_attempts < 0:
            raise CliError('--retry-max-attempts 必须 >= 0', 2)
    else:
        max_attempts = int(default_retry.get('maxAttempts') or 0) if enabled == bool(default_retry.get('enabled')) else (1 if enabled else 0)
    backoff = [int(item) for item in (retry_backoff_seconds or [])]
    if enabled and not backoff:
        backoff = list(default_retry.get('backoffSeconds') or [300])
    if not enabled:
        max_attempts = 0
        backoff = []
    if any(item < 1 for item in backoff):
        raise CliError('--retry-backoff-seconds 仅允许 >= 1 的整数', 2)
    return {
        'enabled': enabled,
        'maxAttempts': max_attempts,
        'backoffSeconds': backoff,
    }


def derive_idempotency_scope(capabilities: dict[str, Any]) -> str:
    return str(infer_job_idempotency_key_policy(capabilities=capabilities).get('scope') or 'job_id').strip() or 'job_id'


def _activation_payload(module_payload: dict[str, Any]) -> dict[str, Any]:
    activation = json_object(module_payload.get('activation'))
    enabled_extension_ids = [
        str(item).strip()
        for item in (activation.get('enabledExtensionIds') or [])
        if str(item).strip()
    ]
    return {'enabledExtensionIds': enabled_extension_ids} if enabled_extension_ids else {}


def build_job_payload(
    *,
    job_id: str,
    title: str,
    schedule_expr: str,
    schedule_tz: str,
    order: int | None,
    model_profile_ref: str,
    timeout_seconds: int,
    retry_policy: dict[str, Any],
    run_artifact_root: str,
    latest_alias: str,
    retention_days: int,
    idempotency_scope: str,
    retryable_classes: list[str],
    terminal_classes: list[str],
) -> dict[str, Any]:
    if not title:
        raise CliError('--job-title 不能为空', 2)
    if not schedule_expr:
        raise CliError('--schedule-expr 不能为空', 2)
    if not schedule_tz:
        raise CliError('--schedule-tz 不能为空', 2)
    try:
        validate_cron_expr(schedule_expr)
        resolve_timezone(schedule_tz)
    except CliError as exc:
        raise CliError(f'job {job_id} schedule 无效：{exc}', 2) from exc
    if retention_days < 1:
        raise CliError('--retention-days 必须 >= 1', 2)
    payload: dict[str, Any] = {
        'schemaVersion': 1,
        'id': job_id,
        'title': title,
        'enabled': True,
        'schedule': {
            'kind': 'cron',
            'expr': schedule_expr,
            'tz': schedule_tz,
        },
        'concurrencyPolicy': 'forbid',
        'timeoutSeconds': timeout_seconds,
        'retryPolicy': retry_policy,
        'artifactPolicy': {
            'runArtifactRoot': run_artifact_root,
            'latestAlias': latest_alias,
            'retentionDays': retention_days,
        },
        'idempotencyKeyPolicy': {
            'strategy': 'job_schedule_slot',
            'scope': idempotency_scope,
        },
        'failureClassPolicy': {
            'retryableClasses': retryable_classes,
            'terminalClasses': terminal_classes,
        },
    }
    if order is not None:
        if int(order) < 1:
            raise CliError('--order 必须 >= 1', 2)
        payload['order'] = int(order)
    if model_profile_ref:
        payload['modelProfileRef'] = model_profile_ref
    return payload


def resolve_attach_runtime_contract(
    registry: dict[str, Any],
    module_payload: dict[str, Any],
    capabilities: dict[str, Any],
    *,
    schedule_tz: str,
    group_placement: str,
    timeout_seconds: int | None,
    retry_enabled: bool | None,
    retry_max_attempts: int | None,
    retry_backoff_seconds: list[int],
    run_artifact_root: str,
    latest_alias: str,
    model_profile_ref: str,
    job_id: str,
) -> dict[str, Any]:
    service_default_timezone = str(json_object(registry.get('defaults')).get('timezone') or '').strip()
    effective_schedule_tz = str(schedule_tz or '').strip() or service_default_timezone
    if not effective_schedule_tz:
        raise CliError('无法解析 schedule 时区；请提供 --schedule-tz', 2)
    return {
        'serviceDefaultTimezone': service_default_timezone,
        'scheduleTz': effective_schedule_tz,
        'timeoutSeconds': derive_timeout_seconds(module_payload, capabilities, explicit=timeout_seconds),
        'retryPolicy': derive_retry_policy(
            capabilities,
            group_placement=group_placement,
            retry_enabled=retry_enabled,
            retry_max_attempts=retry_max_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
        ),
        'artifactRoot': derive_artifact_root(capabilities, explicit=str(run_artifact_root or '').strip()),
        'latestAlias': str(latest_alias or '').strip() or f'latest_{job_id}',
        'modelProfileRef': str(model_profile_ref or '').strip() or str(capabilities.get('defaultModelProfileRef') or '').strip(),
    }


def build_attach_job_surface(
    registry: dict[str, Any],
    module_payload: dict[str, Any],
    capabilities: dict[str, Any],
    runtime_contract: dict[str, Any],
    *,
    jobs_dir: Path,
    job_id: str,
    job_title: str,
    schedule_expr: str,
    group_placement: str,
    order: int | None,
    retention_days: int,
    retryable_classes: list[str],
    terminal_classes: list[str],
    job_file_prefix: str,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    file_prefix = str(job_file_prefix or '').strip() or _next_job_file_prefix(jobs_dir)
    if not file_prefix.isdigit():
        raise CliError(f'--job-file-prefix 必须为纯数字：{file_prefix or "<empty>"}', 2)
    job_path = (jobs_dir / f'{file_prefix}_{job_id}.json').resolve()
    if job_path.exists():
        raise CliError(f'目标 job 文件已存在：{job_path}', 2)
    effective_order = None
    if group_placement == 'none':
        highest_existing_order = max(
            (int(item.get('resolvedOrder') or 0) for item in (registry.get('jobs') or []) if isinstance(item, dict)),
            default=0,
        )
        effective_order = int(order) if order is not None else int(highest_existing_order + 10)
    resolved_job_payload = build_job_payload(
        job_id=job_id,
        title=str(job_title or '').strip(),
        schedule_expr=str(schedule_expr or '').strip(),
        schedule_tz=str(runtime_contract.get('scheduleTz') or '').strip(),
        order=effective_order,
        model_profile_ref=str(runtime_contract.get('modelProfileRef') or '').strip(),
        timeout_seconds=int(runtime_contract.get('timeoutSeconds') or 0),
        retry_policy=dict(runtime_contract.get('retryPolicy') or {}),
        run_artifact_root=str(runtime_contract.get('artifactRoot') or '').strip(),
        latest_alias=str(runtime_contract.get('latestAlias') or '').strip(),
        retention_days=int(retention_days),
        idempotency_scope=derive_idempotency_scope(capabilities),
        retryable_classes=[str(item).strip() for item in (retryable_classes or []) if str(item).strip()],
        terminal_classes=[str(item).strip() for item in (terminal_classes or []) if str(item).strip()],
    )
    activation = _activation_payload(module_payload)
    if activation:
        resolved_job_payload['activation'] = activation
    job_payload = build_compact_job_manifest(
        {**resolved_job_payload, 'sourcePath': str(job_path)},
        agent_payload={
            'capabilities': {
                'network': bool(capabilities.get('network')),
                'filesystemWrite': list(capabilities.get('filesystemWrite') or []),
                'modelRequired': bool(capabilities.get('modelRequired')),
                'externalDispatch': bool(capabilities.get('externalDispatch')),
            },
            'defaultModelProfileRef': str(capabilities.get('defaultModelProfileRef') or '').strip(),
        },
        module_payload=module_payload,
        default_timezone=str(runtime_contract.get('serviceDefaultTimezone') or '').strip() or str(runtime_contract.get('scheduleTz') or '').strip(),
    )
    if activation:
        job_payload['activation'] = activation
    return resolved_job_payload, job_payload, job_path
