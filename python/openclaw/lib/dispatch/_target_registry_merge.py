#!/usr/bin/env python3
"""Merge/load helpers for dispatch target registries."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from ._target_registry_shared import (
    DispatchRegistryValidationError,
    _normalize_registry_paths,
    _read_json,
    _require_list,
    _require_non_empty_text,
    _require_object,
    load_dispatch_registry_schema,
)
from ._target_registry_validation import validate_dispatch_registry_payload


_MERGED_ROW_METADATA_KEYS = {'sourceRegistryPath'}


def _strip_merged_row_metadata(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key not in _MERGED_ROW_METADATA_KEYS
    }


def _merge_unique_object_rows(
    base_rows: list[dict[str, Any]],
    incoming_rows: list[dict[str, Any]],
    *,
    key_name: str,
    label: str,
    source_path: Path | None = None,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = [dict(row) for row in base_rows if isinstance(row, dict)]
    index = {str(row.get(key_name) or '').strip(): row for row in merged if str(row.get(key_name) or '').strip()}
    for row in incoming_rows:
        if not isinstance(row, dict):
            continue
        row_id = _require_non_empty_text(row.get(key_name), label=f'{label}.{key_name}')
        materialized = dict(row)
        if source_path is not None:
            materialized.setdefault('sourceRegistryPath', str(source_path.resolve()))
        existing = index.get(row_id)
        if existing is not None:
            if _strip_merged_row_metadata(existing) != _strip_merged_row_metadata(materialized):
                raise DispatchRegistryValidationError(f'{label} 冲突：{row_id}')
            continue
        merged.append(materialized)
        index[row_id] = materialized
    return merged


def _merge_dispatch_registry_payloads(paths: list[Path]) -> dict[str, Any]:
    payloads = [_read_json(item) for item in paths]
    if not payloads:
        raise DispatchRegistryValidationError('dispatch target 注册表路径不能为空')
    merged: dict[str, Any] = {}
    version = 0
    defaults: dict[str, Any] = {}
    release_policies: list[dict[str, Any]] = []
    lifecycle_states: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    verification_batches_root: dict[str, Any] = {}
    merged_batches: list[dict[str, Any]] = []
    default_rotation_batch_id = ''
    for path, payload in zip(paths, payloads, strict=False):
        current_version = payload.get('version')
        if not isinstance(current_version, int) or current_version < 1:
            raise DispatchRegistryValidationError('dispatch target 注册表.version 必须为正整数')
        version = max(version, current_version)
        current_defaults = _require_object(payload.get('defaults'), label='dispatch target 注册表.defaults')
        if not defaults:
            defaults = dict(current_defaults)
        elif defaults != current_defaults:
            raise DispatchRegistryValidationError('多个 dispatch target 注册表.defaults 不一致')
        release_policies = _merge_unique_object_rows(
            release_policies,
            _require_list(payload.get('releasePolicies'), label='dispatch target 注册表.releasePolicies'),
            key_name='id',
            label='dispatch target 注册表.releasePolicies',
        )
        lifecycle_states = _merge_unique_object_rows(
            lifecycle_states,
            _require_list(payload.get('lifecycleStates'), label='dispatch target 注册表.lifecycleStates'),
            key_name='id',
            label='dispatch target 注册表.lifecycleStates',
        )
        targets = _merge_unique_object_rows(
            targets,
            _require_list(payload.get('targets'), label='dispatch target 注册表.targets'),
            key_name='id',
            label='dispatch target 注册表.targets',
            source_path=path,
        )
        current_batches_root = _require_object(
            payload.get('verificationBatches'),
            label='dispatch target 注册表.verificationBatches',
        )
        current_default_rotation = _require_non_empty_text(
            current_batches_root.get('defaultRotationBatchId'),
            label='dispatch target 注册表.verificationBatches.defaultRotationBatchId',
        )
        if default_rotation_batch_id and default_rotation_batch_id != current_default_rotation:
            raise DispatchRegistryValidationError('多个 dispatch target 注册表.defaultRotationBatchId 不一致')
        default_rotation_batch_id = current_default_rotation
        merged_batches = _merge_unique_object_rows(
            merged_batches,
            _require_list(
                current_batches_root.get('batches'),
                label='dispatch target 注册表.verificationBatches.batches',
            ),
            key_name='id',
            label='dispatch target 注册表.verificationBatches.batches',
        )
        verification_batches_root = {'defaultRotationBatchId': default_rotation_batch_id, 'batches': merged_batches}
    merged.update({
        'version': version,
        'defaults': defaults,
        'releasePolicies': release_policies,
        'lifecycleStates': lifecycle_states,
        'verificationBatches': verification_batches_root,
        'targets': targets,
    })
    return merged


def load_dispatch_registry(
    registry_path: Path | Sequence[Path] | None = None,
    schema_path: Path | None = None,
    provider_registry_path: Path | Sequence[Path] | None = None,
) -> dict[str, Any]:
    payload = _merge_dispatch_registry_payloads(_normalize_registry_paths(registry_path))
    schema_payload = load_dispatch_registry_schema(schema_path)
    validate_dispatch_registry_payload(payload, schema_payload, provider_registry_path=provider_registry_path)
    return payload
