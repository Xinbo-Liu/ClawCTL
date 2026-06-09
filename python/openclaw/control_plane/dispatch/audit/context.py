#!/usr/bin/env python3
"""Context loading helpers for dispatch runtime audit surfaces."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from openclaw.control_plane.dispatch.targets import (
    ResolvedTarget,
    evaluate_target_policies,
    load_targets_payload,
)
from openclaw.control_plane.manifest_fields import (
    DISPATCH_PROVIDER_REGISTRY_PATHS_KEY,
    DISPATCH_TARGET_REGISTRY_PATHS_KEY,
)
from openclaw.lib.dispatch.target_registry import load_dispatch_registry

BASE_EXTENSION_ID = '<base>'
MIXED_EXTENSION_ID = '<mixed>'


def _normalize_extension_id(value: object) -> str:
    return str(value or '').strip()


def extension_label(extension_id: str) -> str:
    normalized = _normalize_extension_id(extension_id)
    return normalized or BASE_EXTENSION_ID


def payload_extension_selector(payload: dict[str, Any]) -> str | None:
    normalized = _normalize_extension_id(payload.get('extensionId'))
    if normalized in {'', BASE_EXTENSION_ID, MIXED_EXTENSION_ID}:
        return None
    return normalized


def shared_extension_label(labels: list[str]) -> str:
    normalized = sorted({extension_label(label) for label in labels})
    if not normalized:
        return BASE_EXTENSION_ID
    if len(normalized) == 1:
        return normalized[0]
    return MIXED_EXTENSION_ID


@dataclass(frozen=True)
class DispatchAuditContext:
    config_path: Path
    target_registry_paths: tuple[Path, ...]
    target_rows_by_id: dict[str, dict[str, Any]]
    targets_by_id: dict[str, ResolvedTarget]
    policies_by_id: dict[str, dict[str, Any]]
    registry_payload: dict[str, Any]


def target_registry_paths(
    config_path: Path,
    *,
    load_registry_fn: Callable[[Path], dict[str, Any]],
) -> tuple[dict[str, Any], list[Path], list[Path]]:
    registry = load_registry_fn(config_path)
    registry_paths = registry.get('registryPaths') if isinstance(registry.get('registryPaths'), dict) else {}
    target_paths = [
        Path(item).resolve()
        for item in list((registry_paths or {}).get(DISPATCH_TARGET_REGISTRY_PATHS_KEY) or [])
        if str(item).strip()
    ]
    provider_paths = [
        Path(item).resolve()
        for item in list((registry_paths or {}).get(DISPATCH_PROVIDER_REGISTRY_PATHS_KEY) or [])
        if str(item).strip()
    ]
    if not target_paths:
        raise ValueError('当前 profile 未启用 dispatch target registry')
    if not provider_paths:
        raise ValueError('当前 profile 未启用 dispatch provider registry')
    return registry, target_paths, provider_paths


def dispatch_target_registry_owners(registry: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for extension in list(registry.get('extensions') or []):
        if not isinstance(extension, dict):
            continue
        extension_id = _normalize_extension_id(extension.get('id'))
        registry_payload = extension.get('registry') if isinstance(extension.get('registry'), dict) else {}
        for raw_path in list(registry_payload.get(DISPATCH_TARGET_REGISTRY_PATHS_KEY) or []):
            candidate = str(raw_path or '').strip()
            if not candidate:
                continue
            mapping[str(Path(candidate).resolve())] = extension_id
    return mapping


def annotate_registry_targets(
    payload: dict[str, Any],
    *,
    registry_owners: dict[str, str],
) -> dict[str, Any]:
    materialized = dict(payload)
    target_rows: list[dict[str, Any]] = []
    for row in list(payload.get('targets') or []):
        if not isinstance(row, dict):
            continue
        item = dict(row)
        source_registry_path = str(item.get('sourceRegistryPath') or '').strip()
        if source_registry_path:
            resolved_source = str(Path(source_registry_path).resolve())
            item['sourceRegistryPath'] = resolved_source
            extension_id = registry_owners.get(resolved_source, '')
            if extension_id and not _normalize_extension_id(item.get('extensionId')):
                item['extensionId'] = extension_id
        target_rows.append(item)
    materialized['targets'] = target_rows
    return materialized


def resolve_targets(
    target_paths: list[Path],
    provider_paths: list[Path],
    *,
    registry_owners: dict[str, str],
) -> tuple[list[ResolvedTarget], dict[str, dict[str, Any]], dict[str, Any]]:
    payload = load_dispatch_registry(target_paths, provider_registry_path=provider_paths)
    annotated_payload = annotate_registry_targets(payload, registry_owners=registry_owners)
    _, targets = load_targets_payload(annotated_payload, env=dict(os.environ))
    policies = evaluate_target_policies(targets)
    return targets, policies, annotated_payload


def index_targets(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get('id') or '').strip(): row
        for row in list(payload.get('targets') or [])
        if isinstance(row, dict) and str(row.get('id') or '').strip()
    }


def load_context(
    config_path: Path | None = None,
    *,
    root_dir: Path,
    default_profile: str,
    resolve_config_path: Callable[..., Path],
    load_registry_fn: Callable[[Path], dict[str, Any]],
) -> DispatchAuditContext:
    resolved_config = resolve_config_path(
        config_path,
        start_path=root_dir,
        default_profile=default_profile,
    )
    registry, target_paths, provider_paths = target_registry_paths(
        resolved_config,
        load_registry_fn=load_registry_fn,
    )
    targets, policies, payload = resolve_targets(
        target_paths,
        provider_paths,
        registry_owners=dispatch_target_registry_owners(registry),
    )
    return DispatchAuditContext(
        config_path=resolved_config,
        target_registry_paths=tuple(target_paths),
        target_rows_by_id=index_targets(payload),
        targets_by_id={target.target_id: target for target in targets},
        policies_by_id=policies,
        registry_payload=payload,
    )
