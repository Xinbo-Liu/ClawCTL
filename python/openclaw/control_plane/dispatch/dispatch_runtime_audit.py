#!/usr/bin/env python3
"""Shared dispatch target acceptance and governance helpers."""
from __future__ import annotations

from pathlib import Path

from openclaw.control_plane.dispatch.audit.context import (
    BASE_EXTENSION_ID,
    MIXED_EXTENSION_ID,
    DispatchAuditContext,
)
from openclaw.control_plane.dispatch.audit import context as audit_context
from openclaw.control_plane.dispatch.audit import payloads as audit_payloads
from openclaw.control_plane.dispatch.audit.render import (
    exit_code_from_status as _exit_code_from_status,
    render_text,
)
from openclaw.control_plane.dispatch.audit.writers import (
    maybe_write_batch_acceptance_audit as _maybe_write_batch_acceptance_audit,
    maybe_write_governance_audit as _maybe_write_governance_audit,
    maybe_write_rotation_sequence_audit as _maybe_write_rotation_sequence_audit,
    maybe_write_target_acceptance_audit as _maybe_write_target_acceptance_audit,
    write_audit_payload,
)
from openclaw.control_plane.registry import load_registry
from openclaw.lib.repo.layout import (
    CONTROL_PLANE_AGENT_PLATFORM_PROFILE_ID,
    resolve_repo_root,
    resolve_selected_control_plane_config_path,
)

ROOT_DIR = resolve_repo_root(Path(__file__))


def load_context(config_path: Path | None = None) -> DispatchAuditContext:
    return audit_context.load_context(
        config_path,
        root_dir=ROOT_DIR,
        default_profile=CONTROL_PLANE_AGENT_PLATFORM_PROFILE_ID,
        resolve_config_path=resolve_selected_control_plane_config_path,
        load_registry_fn=load_registry,
    )


def target_acceptance_payload(
    target_id: str,
    *,
    config_path: Path | None = None,
) -> dict[str, object]:
    return audit_payloads.target_acceptance_payload(load_context(config_path), target_id)


def batch_acceptance_payload(
    *,
    config_path: Path | None = None,
    batch_id: str = '',
    targets_csv: str = '',
) -> dict[str, object]:
    return audit_payloads.batch_acceptance_payload(
        load_context(config_path),
        batch_id=batch_id,
        targets_csv=targets_csv,
    )


def rotation_sequence_payload(
    *,
    config_path: Path | None = None,
    batch_id: str = '',
    targets_csv: str = '',
) -> dict[str, object]:
    return audit_payloads.rotation_sequence_payload(
        batch_acceptance_payload(
            config_path=config_path,
            batch_id=batch_id,
            targets_csv=targets_csv,
        )
    )


def health_overview_payload(
    *,
    config_path: Path | None = None,
) -> dict[str, object]:
    return audit_payloads.health_overview_payload(load_context(config_path))


def maybe_write_target_acceptance_audit(
    payload: dict[str, object],
    *,
    config_path: Path,
    audit_dir: str = '',
) -> Path:
    return _maybe_write_target_acceptance_audit(
        payload,
        root_dir=ROOT_DIR,
        config_path=config_path,
        audit_dir=audit_dir,
    )


def maybe_write_batch_acceptance_audit(
    payload: dict[str, object],
    *,
    config_path: Path,
    audit_dir: str = '',
) -> Path:
    return _maybe_write_batch_acceptance_audit(
        payload,
        root_dir=ROOT_DIR,
        config_path=config_path,
        audit_dir=audit_dir,
    )


def maybe_write_rotation_sequence_audit(
    payload: dict[str, object],
    *,
    config_path: Path,
    audit_dir: str = '',
) -> Path:
    return _maybe_write_rotation_sequence_audit(
        payload,
        root_dir=ROOT_DIR,
        config_path=config_path,
        audit_dir=audit_dir,
    )


def maybe_write_governance_audit(
    payload: dict[str, object],
    *,
    config_path: Path,
    audit_dir: str = '',
) -> Path:
    return _maybe_write_governance_audit(
        payload,
        root_dir=ROOT_DIR,
        config_path=config_path,
        audit_dir=audit_dir,
    )


__all__ = [
    'BASE_EXTENSION_ID',
    'MIXED_EXTENSION_ID',
    'batch_acceptance_payload',
    'health_overview_payload',
    'load_context',
    'maybe_write_batch_acceptance_audit',
    'maybe_write_governance_audit',
    'maybe_write_rotation_sequence_audit',
    'maybe_write_target_acceptance_audit',
    'render_text',
    'rotation_sequence_payload',
    'target_acceptance_payload',
    'write_audit_payload',
    '_exit_code_from_status',
]
