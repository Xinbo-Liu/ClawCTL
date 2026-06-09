#!/usr/bin/env python3
"""dispatch 运行态审计 surface 的结构化 payload 构造。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from openclaw.control_plane.dispatch.targets import build_target_summary, target_publishes_latest

from openclaw.control_plane.dispatch.audit.context import (
    DispatchAuditContext,
    extension_label,
    shared_extension_label,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _normalize_extension_id(value: object) -> str:
    return str(value or '').strip()


def _status_from_issues(*, blocking_issues: list[str], warnings: list[str]) -> str:
    if blocking_issues:
        return 'fail'
    if warnings:
        return 'warn'
    return 'pass'


def target_acceptance_payload(context: DispatchAuditContext, target_id: str) -> dict[str, Any]:
    normalized_target = str(target_id or '').strip()
    target = context.targets_by_id.get(normalized_target)
    row = context.target_rows_by_id.get(normalized_target)
    if target is None or row is None:
        raise KeyError(f'unknown dispatch target: {normalized_target}')
    policy = dict(context.policies_by_id.get(normalized_target) or {})
    blocking_issues = [str(item).strip() for item in list(policy.get('blocking_issues') or []) if str(item).strip()]
    warnings: list[str] = []
    if not target.enabled:
        warnings.append('target_disabled')
    if not target.endpoint_present:
        warnings.append('endpoint_missing')
    if target.secret_required and not target.secret_present and 'enabled_but_secret_missing' not in blocking_issues:
        warnings.append('secret_missing')
    status = _status_from_issues(blocking_issues=blocking_issues, warnings=warnings)
    extension_id = _normalize_extension_id(row.get('extensionId') or target.extension_id)
    return {
        'schemaVersion': 1,
        'kind': 'dispatch_target_acceptance',
        'updated_at': utc_now(),
        'extensionId': extension_label(extension_id),
        'config_path': str(context.config_path),
        'target_id': normalized_target,
        'status': status,
        'blocking_issues': blocking_issues,
        'warnings': warnings,
        'endpoint_validation': dict(policy.get('endpoint_validation') or {}),
        'security_warnings': [str(item).strip() for item in list(policy.get('security_warnings') or []) if str(item).strip()],
        'target': {
            'title': str(getattr(target, 'title', '') or '').strip(),
            'display_name': str(getattr(target, 'display_name', '') or '').strip(),
            'role_description': str(getattr(target, 'role_description', '') or '').strip(),
            'audience_description': str(getattr(target, 'audience_description', '') or '').strip(),
            'dispatch_lane': str(getattr(target, 'dispatch_lane', '') or '').strip(),
            'payload_scope': str(getattr(target, 'payload_scope', '') or '').strip(),
            'publish_latest': target_publishes_latest(target),
            'boundary_description': str(getattr(target, 'boundary_description', '') or '').strip(),
            'target_group': str(row.get('targetGroup') or '').strip(),
            'delivery_tier': str(row.get('deliveryTier') or '').strip(),
            'message_profile': str(row.get('messageProfile') or '').strip(),
            'verification_order': int(row.get('verificationOrderDefault') or 0),
            'verification_batch_ids': [str(item).strip() for item in list(row.get('verificationBatchIds') or []) if str(item).strip()],
            'release_policy_id': str(row.get('releasePolicyId') or '').strip(),
            'lifecycle_state': str(row.get('lifecycleState') or '').strip(),
            'source_registry_path': str(row.get('sourceRegistryPath') or '').strip() or None,
            'enabled': bool(target.enabled),
            'enabled_default': bool(target.enabled_default),
            'endpoint_present': bool(target.endpoint_present),
            'secret_required': bool(target.secret_required),
            'secret_present': bool(target.secret_present),
            'allowed_release_levels': list(target.allowed_release_levels),
            'format': str(target.msg_format or '').strip(),
            'silence_enabled': bool(target.silence_enabled),
            'silence_min_delta': float(target.silence_min_delta),
        },
        'summary': build_target_summary(list(context.targets_by_id.values()), context.policies_by_id),
    }


def _selected_target_ids(
    context: DispatchAuditContext,
    *,
    batch_id: str = '',
    targets_csv: str = '',
) -> tuple[list[str], dict[str, Any] | None]:
    explicit_ids = [item.strip() for item in str(targets_csv or '').split(',') if item.strip()]
    if explicit_ids:
        return explicit_ids, None
    wanted_batch = str(batch_id or '').strip()
    if not wanted_batch:
        wanted_batch = str(((context.registry_payload.get('verificationBatches') or {}).get('defaultRotationBatchId') or '')).strip()
    for row in list(((context.registry_payload.get('verificationBatches') or {}).get('batches') or [])):
        if isinstance(row, dict) and str(row.get('id') or '').strip() == wanted_batch:
            return [str(item).strip() for item in list(row.get('targetIds') or []) if str(item).strip()], row
    raise KeyError(f'unknown verification batch: {wanted_batch}')


def batch_acceptance_payload(
    context: DispatchAuditContext,
    *,
    batch_id: str = '',
    targets_csv: str = '',
) -> dict[str, Any]:
    target_ids, batch_row = _selected_target_ids(context, batch_id=batch_id, targets_csv=targets_csv)
    results = [target_acceptance_payload(context, target_id) for target_id in target_ids]
    statuses = [str(item.get('status') or '').strip() for item in results]
    overall_status = 'fail' if 'fail' in statuses else 'warn' if 'warn' in statuses else 'pass'
    required_groups = [str(item).strip() for item in list((batch_row or {}).get('requiredTargetGroups') or []) if str(item).strip()]
    present_groups = sorted({
        str(((item.get('target') or {}).get('target_group') or '')).strip()
        for item in results
        if str(((item.get('target') or {}).get('target_group') or '')).strip()
    })
    missing_groups = [group for group in required_groups if group not in present_groups]
    if missing_groups and overall_status == 'pass':
        overall_status = 'fail'
    return {
        'schemaVersion': 1,
        'kind': 'dispatch_target_batch_acceptance',
        'updated_at': utc_now(),
        'extensionId': shared_extension_label([str(item.get('extensionId') or '') for item in results]),
        'config_path': str(context.config_path),
        'batch_id': str((batch_row or {}).get('id') or batch_id or '').strip() or None,
        'overall_status': overall_status,
        'target_count': len(results),
        'required_target_groups': required_groups,
        'present_target_groups': present_groups,
        'missing_target_groups': missing_groups,
        'target_ids': target_ids,
        'results': results,
    }


def rotation_sequence_payload(batch_payload: dict[str, Any]) -> dict[str, Any]:
    ordered_results = sorted(
        list(batch_payload.get('results') or []),
        key=lambda row: int(((row.get('target') or {}).get('verification_order') or 0)),
    )
    return {
        'schemaVersion': 1,
        'kind': 'dispatch_target_rotation_sequence',
        'updated_at': utc_now(),
        'extensionId': shared_extension_label([str(item.get('extensionId') or '') for item in ordered_results]),
        'config_path': str(batch_payload.get('config_path') or ''),
        'batch_id': batch_payload.get('batch_id'),
        'overall_status': batch_payload.get('overall_status'),
        'target_count': batch_payload.get('target_count'),
        'required_target_groups': batch_payload.get('required_target_groups'),
        'target_ids': [str(item.get('target_id') or '').strip() for item in ordered_results],
        'ordered_results': ordered_results,
        'batch_acceptance': batch_payload,
    }


def health_overview_payload(context: DispatchAuditContext) -> dict[str, Any]:
    batches = [
        {
            'id': str(row.get('id') or '').strip(),
            'required_target_groups': [str(item).strip() for item in list(row.get('requiredTargetGroups') or []) if str(item).strip()],
            'target_ids': [str(item).strip() for item in list(row.get('targetIds') or []) if str(item).strip()],
        }
        for row in list(((context.registry_payload.get('verificationBatches') or {}).get('batches') or []))
        if isinstance(row, dict) and str(row.get('id') or '').strip()
    ]
    target_summary = build_target_summary(list(context.targets_by_id.values()), context.policies_by_id)
    missing: list[str] = []
    if not context.targets_by_id:
        missing.append('dispatch_targets')
    if not batches:
        missing.append('verification_batches')
    overall_status = 'fail' if missing else ('warn' if target_summary.get('blocking_enabled_count') else 'pass')
    return {
        'schemaVersion': 1,
        'kind': 'dispatch_health_overview',
        'updated_at': utc_now(),
        'extensionId': shared_extension_label([
            _normalize_extension_id(row.get('extensionId'))
            for row in context.target_rows_by_id.values()
        ]),
        'config_path': str(context.config_path),
        'overall_status': overall_status,
        'missing': missing,
        'dispatch_target_summary': target_summary,
        'verification_batches': {
            'default_rotation_batch_id': str(((context.registry_payload.get('verificationBatches') or {}).get('defaultRotationBatchId') or '')).strip(),
            'batches': batches,
        },
    }
