#!/usr/bin/env python3
"""控制平面 job 缺省推导与最小清单收缩。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from openclaw.lib.io.json_access import json_object

DEFAULT_JOB_ENABLED = True
DEFAULT_JOB_SCHEDULE_KIND = 'cron'
DEFAULT_JOB_CONCURRENCY_POLICY = 'forbid'
DEFAULT_JOB_ARTIFACT_RETENTION_DAYS = 14
DEFAULT_JOB_IDEMPOTENCY_STRATEGY = 'job_schedule_slot'
DEFAULT_JOB_RETRY_ENABLED_MAX_ATTEMPTS = 1
DEFAULT_JOB_RETRY_ENABLED_BACKOFF_SECONDS = [300]
DEFAULT_JOB_RETRY_DISABLED_MAX_ATTEMPTS = 0
DEFAULT_JOB_RETRY_DISABLED_BACKOFF_SECONDS: list[int] = []
JOB_FILE_PATTERN = re.compile(r'^(?P<prefix>[0-9]+)_.*\.json$')


def module_kind(module_payload: dict[str, Any]) -> str:
    return str(module_payload.get('moduleKind') or 'worker').strip() or 'worker'


def agent_capabilities(agent_payload: dict[str, Any]) -> dict[str, Any]:
    capabilities = json_object(agent_payload.get('capabilities'))
    filesystem_write = [str(item).strip() for item in (capabilities.get('filesystemWrite') or []) if str(item).strip()]
    return {
        'network': bool(capabilities.get('network', False)),
        'filesystemWrite': filesystem_write,
        'modelRequired': bool(capabilities.get('modelRequired', False)),
        'externalDispatch': bool(capabilities.get('externalDispatch', False)),
    }


def agent_default_model_profile_ref(agent_payload: dict[str, Any]) -> str:
    return str(agent_payload.get('defaultModelProfileRef') or '').strip()


def infer_job_timeout_seconds(*, module_kind_value: str, capabilities: dict[str, Any]) -> int:
    normalized_module_kind = str(module_kind_value or 'worker').strip() or 'worker'
    if normalized_module_kind == 'control_check':
        return 600
    if bool(capabilities.get('modelRequired')):
        return 2400
    if bool(capabilities.get('externalDispatch')) or bool(capabilities.get('network')):
        return 900
    return 900


def infer_job_retry_policy(*, capabilities: dict[str, Any], is_recovery_job: bool) -> dict[str, Any]:
    enabled = bool(capabilities.get('modelRequired')) or bool(capabilities.get('network')) or bool(capabilities.get('externalDispatch'))
    if is_recovery_job:
        enabled = False
    if enabled:
        return {
            'enabled': True,
            'maxAttempts': DEFAULT_JOB_RETRY_ENABLED_MAX_ATTEMPTS,
            'backoffSeconds': list(DEFAULT_JOB_RETRY_ENABLED_BACKOFF_SECONDS),
        }
    return {
        'enabled': False,
        'maxAttempts': DEFAULT_JOB_RETRY_DISABLED_MAX_ATTEMPTS,
        'backoffSeconds': list(DEFAULT_JOB_RETRY_DISABLED_BACKOFF_SECONDS),
    }


def infer_job_idempotency_key_policy(*, capabilities: dict[str, Any]) -> dict[str, Any]:
    return {
        'strategy': DEFAULT_JOB_IDEMPOTENCY_STRATEGY,
        'scope': 'job_id_plus_target' if bool(capabilities.get('externalDispatch')) else 'job_id',
    }


def infer_job_run_artifact_root(*, capabilities: dict[str, Any]) -> str:
    filesystem_write = [str(item).strip() for item in (capabilities.get('filesystemWrite') or []) if str(item).strip()]
    for item in filesystem_write:
        if item.endswith('_dir') or item.endswith('_root'):
            return item
    return filesystem_write[0] if filesystem_write else ''


def infer_job_order_from_source_path(source_path: str | Path | None) -> int | None:
    text = str(source_path or '').strip()
    if not text:
        return None
    match = JOB_FILE_PATTERN.match(Path(text).name)
    if not match:
        return None
    try:
        value = int(match.group('prefix'))
    except ValueError:
        return None
    return value if value >= 1 else None


def normalize_retry_policy(value: Any) -> dict[str, Any]:
    retry = value if isinstance(value, dict) else {}
    enabled = bool(retry.get('enabled'))
    max_attempts = int(retry.get('maxAttempts') or 0)
    backoff_seconds = [int(item) for item in (retry.get('backoffSeconds') or [])]
    return {
        'enabled': enabled,
        'maxAttempts': max_attempts,
        'backoffSeconds': backoff_seconds,
    }


def normalize_idempotency_key_policy(value: Any) -> dict[str, Any]:
    policy = value if isinstance(value, dict) else {}
    return {
        'strategy': str(policy.get('strategy') or '').strip(),
        'scope': str(policy.get('scope') or '').strip(),
    }


def normalize_failure_class_policy(value: Any) -> dict[str, Any]:
    policy = value if isinstance(value, dict) else {}
    retryable = [str(item).strip() for item in (policy.get('retryableClasses') or []) if str(item).strip()]
    terminal = [str(item).strip() for item in (policy.get('terminalClasses') or []) if str(item).strip()]
    return {
        'retryableClasses': retryable,
        'terminalClasses': terminal,
    }


def normalize_artifact_policy(value: Any) -> dict[str, Any]:
    policy = value if isinstance(value, dict) else {}
    return {
        'runArtifactRoot': str(policy.get('runArtifactRoot') or '').strip(),
        'latestAlias': str(policy.get('latestAlias') or '').strip(),
        'retentionDays': int(policy.get('retentionDays') or 0),
    }


def artifact_policy_fields(value: Any) -> dict[str, Any]:
    policy = normalize_artifact_policy(value)
    return {
        'runArtifactRoot': str(policy.get('runArtifactRoot') or '').strip(),
        'latestAlias': str(policy.get('latestAlias') or '').strip(),
        'retentionDays': int(policy.get('retentionDays') or 0),
    }


def build_compact_job_manifest(
    job_payload: dict[str, Any],
    *,
    agent_payload: dict[str, Any],
    module_payload: dict[str, Any],
    default_timezone: str,
) -> dict[str, Any]:
    capabilities = agent_capabilities(agent_payload)
    compact: dict[str, Any] = {
        'schemaVersion': 1,
        'id': str(job_payload.get('id') or '').strip(),
        'title': str(job_payload.get('title') or '').strip(),
    }
    activation = json_object(job_payload.get('activation'))
    enabled_extension_ids = [
        str(item).strip()
        for item in (activation.get('enabledExtensionIds') or [])
        if str(item).strip()
    ]
    if enabled_extension_ids:
        compact['activation'] = {'enabledExtensionIds': enabled_extension_ids}
    if bool(job_payload.get('enabled', DEFAULT_JOB_ENABLED)) is not DEFAULT_JOB_ENABLED:
        compact['enabled'] = bool(job_payload.get('enabled'))

    schedule = json_object(job_payload.get('schedule'))
    compact_schedule: dict[str, Any] = {
        'expr': str(schedule.get('expr') or '').strip(),
    }
    timezone_value = str(schedule.get('tz') or default_timezone).strip() or default_timezone
    if timezone_value != default_timezone:
        compact_schedule['tz'] = timezone_value
    compact['schedule'] = compact_schedule

    group_ref = str(job_payload.get('groupRef') or '').strip()
    resolved_order = int(job_payload.get('resolvedOrder') or job_payload.get('order') or 0)
    inferred_order = infer_job_order_from_source_path(job_payload.get('sourcePath'))
    if not group_ref and 1 <= resolved_order != inferred_order:
        compact['order'] = resolved_order

    resolved_model_ref = str(job_payload.get('resolvedModelProfileRef') or job_payload.get('modelProfileRef') or '').strip()
    default_model_ref = agent_default_model_profile_ref(agent_payload)
    if resolved_model_ref and resolved_model_ref != default_model_ref:
        compact['modelProfileRef'] = resolved_model_ref

    timeout_seconds = int(job_payload.get('timeoutSeconds') or 0)
    default_timeout_seconds = infer_job_timeout_seconds(module_kind_value=module_kind(module_payload), capabilities=capabilities)
    if timeout_seconds and timeout_seconds != default_timeout_seconds:
        compact['timeoutSeconds'] = timeout_seconds

    is_recovery_job = bool(job_payload.get('resolvedRecoveryStep'))
    retry_policy = normalize_retry_policy(job_payload.get('retryPolicy'))
    default_retry_policy = infer_job_retry_policy(capabilities=capabilities, is_recovery_job=is_recovery_job)
    if retry_policy != default_retry_policy:
        compact['retryPolicy'] = retry_policy

    artifact_policy = artifact_policy_fields(job_payload.get('artifactPolicy'))
    compact_artifact_policy: dict[str, Any] = {
        'latestAlias': artifact_policy['latestAlias'],
    }
    default_run_artifact_root = infer_job_run_artifact_root(capabilities=capabilities)
    if artifact_policy['runArtifactRoot'] and artifact_policy['runArtifactRoot'] != default_run_artifact_root:
        compact_artifact_policy['runArtifactRoot'] = artifact_policy['runArtifactRoot']
    retention_days = int(artifact_policy['retentionDays'] or DEFAULT_JOB_ARTIFACT_RETENTION_DAYS)
    if retention_days != DEFAULT_JOB_ARTIFACT_RETENTION_DAYS:
        compact_artifact_policy['retentionDays'] = retention_days
    compact['artifactPolicy'] = compact_artifact_policy

    idempotency_key_policy = normalize_idempotency_key_policy(job_payload.get('idempotencyKeyPolicy'))
    default_idempotency_key_policy = infer_job_idempotency_key_policy(capabilities=capabilities)
    if idempotency_key_policy != default_idempotency_key_policy:
        compact['idempotencyKeyPolicy'] = idempotency_key_policy

    failure_class_policy = normalize_failure_class_policy(job_payload.get('failureClassPolicy'))
    if failure_class_policy['retryableClasses'] or failure_class_policy['terminalClasses']:
        compact['failureClassPolicy'] = failure_class_policy
    return compact


def _diff_dict_paths(lhs: Any, rhs: Any, *, base_path: str = '') -> list[str]:
    if isinstance(lhs, dict) and isinstance(rhs, dict):
        result: list[str] = []
        for key in sorted(set(lhs) | set(rhs)):
            child_path = f'{base_path}.{key}' if base_path else str(key)
            if key not in lhs or key not in rhs:
                result.append(child_path)
                continue
            result.extend(_diff_dict_paths(lhs[key], rhs[key], base_path=child_path))
        return result
    if lhs != rhs:
        return [base_path or '$']
    return []


def manifest_diff_paths(lhs: dict[str, Any], rhs: dict[str, Any]) -> list[str]:
    return _diff_dict_paths(lhs, rhs)
