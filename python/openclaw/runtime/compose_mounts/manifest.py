#!/usr/bin/env python3
"""Manifest loading and merge helpers for runtime compose mounts."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from openclaw.control_plane.extensions.fragments import enabled_extension_ids, iter_surface_fragment_paths
from openclaw.lib.repo.layout import (
    resolve_default_runtime_control_plane_service_config_path,
    resolve_selected_control_plane_config_path,
)


def read_json(path: Path, *, root_dir: Path, fail: Callable[[str, int], None]) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(payload, dict):
        fail(f'{path.relative_to(root_dir)} 顶层必须为对象', 2)
    return payload


def merge_unique_dict_entries(
    base: dict[str, Any],
    incoming: dict[str, Any],
    *,
    label: str,
    fail: Callable[[str, int], None],
) -> dict[str, Any]:
    merged = dict(base)
    for key, value in incoming.items():
        normalized = str(key).strip()
        if not normalized:
            fail(f'{label} 包含空 key', 2)
        materialized = deepcopy(value)
        if normalized in merged and merged[normalized] != materialized:
            fail(f'{label} key 冲突：{normalized}', 2)
        merged[normalized] = materialized
    return merged


def merge_mount_rows(base_rows: list[dict[str, Any]], incoming_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    fingerprints: set[str] = set()
    for row in [*base_rows, *incoming_rows]:
        if not isinstance(row, dict):
            continue
        fingerprint = json.dumps(row, ensure_ascii=False, sort_keys=True)
        if fingerprint in fingerprints:
            continue
        fingerprints.add(fingerprint)
        merged.append(deepcopy(row))
    return merged


def merge_service_rows(
    base_rows: list[dict[str, Any]],
    incoming_rows: list[dict[str, Any]],
    *,
    label: str,
    fail: Callable[[str, int], None],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = [deepcopy(row) for row in base_rows if isinstance(row, dict)]
    index = {str(row.get('service') or '').strip(): row for row in merged if str(row.get('service') or '').strip()}
    for row in incoming_rows:
        if not isinstance(row, dict):
            continue
        service_name = str(row.get('service') or '').strip()
        if not service_name:
            fail(f'{label} service 条目缺少 service', 2)
        materialized = deepcopy(row)
        existing = index.get(service_name)
        if existing is None:
            merged.append(materialized)
            index[service_name] = materialized
            continue
        merged_mounts = merge_mount_rows(
            existing.get('mounts') if isinstance(existing.get('mounts'), list) else [],
            materialized.get('mounts') if isinstance(materialized.get('mounts'), list) else [],
        )
        candidate = dict(existing)
        candidate['mounts'] = merged_mounts
        for key, value in materialized.items():
            if key in {'service', 'mounts'}:
                continue
            if key in candidate and candidate[key] != value:
                fail(f'{label} service 冲突：{service_name}.{key}', 2)
            candidate[key] = deepcopy(value)
        existing.clear()
        existing.update(candidate)
    return merged


def resolve_config_path(root_dir: Path, config_path: Path | None = None) -> Path:
    return (
        resolve_default_runtime_control_plane_service_config_path(root_dir)
        if config_path is None
        else resolve_selected_control_plane_config_path(config_path, start_path=root_dir)
    )


def enabled_extension_ids_for(root_dir: Path, *, config_path: Path | None = None) -> set[str]:
    return enabled_extension_ids(config_path=resolve_config_path(root_dir, config_path))


def required_extension_ids(
    payload: dict[str, Any],
    *,
    label: str,
    fail: Callable[[str, int], None],
) -> set[str]:
    required = payload.get('requiresExtensionIds')
    if required in (None, ''):
        return set()
    if not isinstance(required, list):
        fail(f'{label}.requiresExtensionIds 必须为数组', 2)
    return {str(item).strip() for item in required if str(item).strip()}


def service_is_enabled(
    payload: dict[str, Any],
    enabled_ids: set[str],
    *,
    fail: Callable[[str, int], None],
) -> bool:
    required = required_extension_ids(payload, label=f'service {payload.get("service") or "<unknown>"}', fail=fail)
    return not required or required.issubset(enabled_ids)


def mount_is_enabled(
    mount: dict[str, Any],
    enabled_ids: set[str],
    *,
    fail: Callable[[str, int], None],
) -> bool:
    required = required_extension_ids(mount, label='mount', fail=fail)
    return not required or required.issubset(enabled_ids)


def load_manifest(
    *,
    root_dir: Path,
    manifest_path: Path,
    fail: Callable[[str, int], None],
    path: Path | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    payload = deepcopy(read_json(path or manifest_path, root_dir=root_dir, fail=fail))
    payload['generated_artifacts'] = dict(payload.get('generated_artifacts') or {})
    payload['services'] = [deepcopy(row) for row in payload.get('services') or [] if isinstance(row, dict)]
    for extension_id, fragment_path in iter_surface_fragment_paths(
        config_path=resolve_config_path(root_dir, config_path),
        key='runtimeMountsPath',
    ):
        extension_payload = read_json(fragment_path, root_dir=root_dir, fail=fail)
        payload['generated_artifacts'] = merge_unique_dict_entries(
            payload['generated_artifacts'],
            extension_payload.get('generated_artifacts') if isinstance(extension_payload.get('generated_artifacts'), dict) else {},
            label=f'extension {extension_id or "<unknown>"} runtime_mounts.generated_artifacts',
            fail=fail,
        )
        if isinstance(extension_payload.get('compose'), dict):
            compose = dict(payload.get('compose') or {})
            for key, value in dict(extension_payload.get('compose') or {}).items():
                if key in compose and compose[key] != value:
                    fail(f'extension {extension_id or "<unknown>"} runtime_mounts.compose 冲突：{key}', 2)
                compose[key] = value
            payload['compose'] = compose
        payload['services'] = merge_service_rows(
            payload['services'],
            extension_payload.get('services') if isinstance(extension_payload.get('services'), list) else [],
            label=f'extension {extension_id or "<unknown>"} runtime_mounts.services',
            fail=fail,
        )
    return payload


def services(
    *,
    root_dir: Path,
    manifest_path: Path,
    fail: Callable[[str, int], None],
    config_path: Path | None = None,
) -> list[dict[str, Any]]:
    rows = load_manifest(root_dir=root_dir, manifest_path=manifest_path, fail=fail, config_path=config_path).get('services') or []
    if not isinstance(rows, list):
        fail('services 顶层必须为数组', 2)
    return [row for row in rows if isinstance(row, dict)]


def compose_file_path(
    *,
    root_dir: Path,
    manifest_path: Path,
    fail: Callable[[str, int], None],
    config_path: Path | None = None,
) -> Path:
    rel = str((load_manifest(root_dir=root_dir, manifest_path=manifest_path, fail=fail, config_path=config_path).get('compose') or {}).get('file') or '').strip()
    if not rel:
        fail('compose.file 不能为空', 2)
    return root_dir / rel


def marker_prefix(
    *,
    root_dir: Path,
    manifest_path: Path,
    fail: Callable[[str, int], None],
    config_path: Path | None = None,
) -> str:
    value = str((load_manifest(root_dir=root_dir, manifest_path=manifest_path, fail=fail, config_path=config_path).get('compose') or {}).get('marker_prefix') or '').strip()
    return value or 'RUNTIME_MOUNTS'
