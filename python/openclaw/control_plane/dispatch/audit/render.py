#!/usr/bin/env python3
"""Text rendering and exit-code policy for dispatch runtime audit surfaces."""
from __future__ import annotations

import json
from typing import Any


def exit_code_from_status(status: str, *, fail_on_warn: bool, fail_on_fail: bool) -> int:
    normalized = str(status or '').strip().lower()
    if normalized == 'fail' and fail_on_fail:
        return 1
    if normalized == 'warn' and fail_on_warn:
        return 1
    return 0


def render_text(payload: dict[str, Any]) -> str:
    kind = str(payload.get('kind') or '').strip()
    if kind == 'dispatch_target_acceptance':
        return '\n'.join([
            f"target_id: {payload.get('target_id')}",
            f"status: {payload.get('status')}",
            f"blocking_issues: {', '.join(payload.get('blocking_issues') or []) or '<none>'}",
            f"warnings: {', '.join(payload.get('warnings') or []) or '<none>'}",
        ])
    if kind == 'dispatch_target_batch_acceptance':
        return '\n'.join([
            f"batch_id: {payload.get('batch_id') or '<custom>'}",
            f"overall_status: {payload.get('overall_status')}",
            f"target_count: {payload.get('target_count')}",
            f"missing_target_groups: {', '.join(payload.get('missing_target_groups') or []) or '<none>'}",
        ])
    if kind == 'dispatch_target_rotation_sequence':
        return '\n'.join([
            f"batch_id: {payload.get('batch_id') or '<custom>'}",
            f"overall_status: {payload.get('overall_status')}",
            f"target_ids: {', '.join(payload.get('target_ids') or [])}",
        ])
    if kind == 'dispatch_health_overview':
        return '\n'.join([
            f"overall_status: {payload.get('overall_status')}",
            f"missing: {', '.join(payload.get('missing') or []) or '<none>'}",
        ])
    return json.dumps(payload, ensure_ascii=False, indent=2)
