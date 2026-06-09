#!/usr/bin/env python3
"""Helpers for extension-owned rows and ambiguity-aware lookups."""
from __future__ import annotations

from copy import deepcopy
from typing import Any


def normalize_extension_id(value: Any) -> str:
    return str(value or '').strip()


def with_extension_owner(row: dict[str, Any], extension_id: str | None) -> dict[str, Any]:
    materialized = deepcopy(row)
    owner = normalize_extension_id(extension_id)
    existing_owner = normalize_extension_id(materialized.get('extensionId'))
    if owner:
        if existing_owner and existing_owner != owner:
            raise ValueError(f'extensionId conflict: {existing_owner} != {owner}')
        materialized['extensionId'] = owner
    elif not existing_owner:
        materialized.pop('extensionId', None)
    return materialized


def annotate_rows(rows: list[dict[str, Any]], extension_id: str | None) -> list[dict[str, Any]]:
    return [with_extension_owner(row, extension_id) for row in rows if isinstance(row, dict)]


def mapping_to_owned_rows(
    mapping: dict[str, Any],
    *,
    extension_id: str | None,
    id_key: str,
    label: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_key, raw_value in mapping.items():
        row_id = str(raw_key or '').strip()
        if not row_id:
            raise ValueError(f'{label} contains an empty key')
        if not isinstance(raw_value, dict):
            raise ValueError(f'{label}.{row_id} must be an object')
        materialized = dict(raw_value)
        existing = str(materialized.get(id_key) or '').strip()
        if existing and existing != row_id:
            raise ValueError(f'{label}.{row_id} {id_key} conflict: {existing}')
        materialized[id_key] = row_id
        rows.append(with_extension_owner(materialized, extension_id))
    return rows


def annotate_mapping_values(
    mapping: dict[str, Any],
    *,
    extension_id: str | None,
    id_key: str | None = None,
    label: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw_key, raw_value in mapping.items():
        normalized_key = str(raw_key or '').strip()
        if not normalized_key:
            raise ValueError(f'{label} contains an empty key')
        if not isinstance(raw_value, dict):
            raise ValueError(f'{label}.{normalized_key} must be an object')
        materialized = dict(raw_value)
        if id_key:
            existing = str(materialized.get(id_key) or '').strip()
            if existing and existing != normalized_key:
                raise ValueError(f'{label}.{normalized_key} {id_key} conflict: {existing}')
            materialized[id_key] = normalized_key
        result[normalized_key] = with_extension_owner(materialized, extension_id)
    return result


def qualify_owned_id(local_id: str, extension_id: str | None) -> str:
    owner = normalize_extension_id(extension_id)
    normalized_local_id = str(local_id or '').strip()
    return f'{owner}:{normalized_local_id}' if owner else normalized_local_id


def extension_matches(row: dict[str, Any], extension_id: str | None) -> bool:
    owner = normalize_extension_id(row.get('extensionId'))
    selector = normalize_extension_id(extension_id)
    if selector:
        return owner == selector
    return True


def filter_rows_by_extension(rows: list[dict[str, Any]], extension_id: str | None = None) -> list[dict[str, Any]]:
    return [dict(row) for row in rows if isinstance(row, dict) and extension_matches(row, extension_id)]


def resolve_owned_row(
    rows: list[dict[str, Any]],
    local_id: str,
    *,
    extension_id: str | None = None,
    id_key: str = 'id',
    label: str,
) -> dict[str, Any]:
    wanted = str(local_id or '').strip()
    if not wanted:
        raise ValueError(f'{label} id cannot be empty')
    candidates = [
        dict(row)
        for row in rows
        if isinstance(row, dict)
        and str(row.get(id_key) or '').strip() == wanted
        and extension_matches(row, extension_id)
    ]
    if not candidates:
        selector = normalize_extension_id(extension_id)
        suffix = f' under extension {selector}' if selector else ''
        raise KeyError(f'unknown {label}: {wanted}{suffix}')
    if len(candidates) > 1:
        owners = sorted({normalize_extension_id(row.get('extensionId')) or '<base>' for row in candidates})
        raise ValueError(f'ambiguous {label}: {wanted} ({", ".join(owners)})')
    return candidates[0]
