#!/usr/bin/env python3
"""Audit payload writing helpers for dispatch runtime audit surfaces."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openclaw.lib.control_plane.object_families import get_entry

from openclaw.control_plane.dispatch.audit.context import payload_extension_selector


def select_audit_dir(
    entry_id: str,
    *,
    root_dir: Path,
    config_path: Path,
    extension_id: str | None = None,
    explicit_dir: str = '',
) -> Path:
    if explicit_dir:
        return Path(explicit_dir).resolve()
    entry = get_entry(
        'dispatch_governance_state',
        entry_id,
        root_dir,
        config_path=config_path,
        extension_id=extension_id,
    )
    return Path(str(entry.get('resolved_path') or '')).resolve()


def write_audit_payload(payload: dict[str, Any], *, audit_dir: Path, prefix: str) -> Path:
    audit_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = audit_dir / f'{prefix}.{timestamp}.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return path


def maybe_write_target_acceptance_audit(
    payload: dict[str, Any],
    *,
    root_dir: Path,
    config_path: Path,
    audit_dir: str = '',
) -> Path:
    return write_audit_payload(
        payload,
        audit_dir=select_audit_dir(
            'dispatch_target_acceptance_audit_dir',
            root_dir=root_dir,
            config_path=config_path,
            extension_id=payload_extension_selector(payload),
            explicit_dir=audit_dir,
        ),
        prefix=f"target_{str(payload.get('target_id') or 'unknown').strip() or 'unknown'}",
    )


def maybe_write_batch_acceptance_audit(
    payload: dict[str, Any],
    *,
    root_dir: Path,
    config_path: Path,
    audit_dir: str = '',
) -> Path:
    return write_audit_payload(
        payload,
        audit_dir=select_audit_dir(
            'dispatch_target_batch_acceptance_audit_dir',
            root_dir=root_dir,
            config_path=config_path,
            extension_id=payload_extension_selector(payload),
            explicit_dir=audit_dir,
        ),
        prefix=f"batch_{str(payload.get('batch_id') or 'custom').strip() or 'custom'}",
    )


def maybe_write_rotation_sequence_audit(
    payload: dict[str, Any],
    *,
    root_dir: Path,
    config_path: Path,
    audit_dir: str = '',
) -> Path:
    return write_audit_payload(
        payload,
        audit_dir=select_audit_dir(
            'dispatch_target_rotation_sequence_audit_dir',
            root_dir=root_dir,
            config_path=config_path,
            extension_id=payload_extension_selector(payload),
            explicit_dir=audit_dir,
        ),
        prefix=f"rotation_{str(payload.get('batch_id') or 'custom').strip() or 'custom'}",
    )


def maybe_write_governance_audit(
    payload: dict[str, Any],
    *,
    root_dir: Path,
    config_path: Path,
    audit_dir: str = '',
) -> Path:
    return write_audit_payload(
        payload,
        audit_dir=select_audit_dir(
            'dispatch_governance_audit_dir',
            root_dir=root_dir,
            config_path=config_path,
            extension_id=payload_extension_selector(payload),
            explicit_dir=audit_dir,
        ),
        prefix=f"governance_{str(payload.get('batch_id') or 'default').strip() or 'default'}",
    )
