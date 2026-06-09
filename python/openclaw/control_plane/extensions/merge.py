#!/usr/bin/env python3
"""Helpers for merging extension-provided surface fragments."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


EMPTY_SCALARS = ('', None)


def read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(payload, dict):
        raise ValueError(f'config root must be an object: {path}')
    return payload


def merge_unique_dict_entries(base: dict[str, Any], incoming: dict[str, Any], *, label: str) -> dict[str, Any]:
    merged = {str(key): deepcopy(value) for key, value in base.items()}
    for key, value in incoming.items():
        normalized = str(key).strip()
        if not normalized:
            raise ValueError(f'{label} contains an empty key')
        materialized = deepcopy(value)
        if normalized in merged and merged[normalized] != materialized:
            raise ValueError(f'{label} key conflict: {normalized}')
        merged[normalized] = materialized
    return merged


def merge_unique_rows(base_rows: list[dict[str, Any]], incoming_rows: list[dict[str, Any]], *, key_name: str, label: str) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = [dict(row) for row in base_rows if isinstance(row, dict)]
    index = {str(row.get(key_name) or '').strip(): row for row in merged if str(row.get(key_name) or '').strip()}
    for row in incoming_rows:
        if not isinstance(row, dict):
            continue
        row_id = str(row.get(key_name) or '').strip()
        if not row_id:
            raise ValueError(f'{label} missing {key_name}')
        existing = index.get(row_id)
        materialized = dict(row)
        if existing is None:
            merged.append(materialized)
            index[row_id] = materialized
            continue
        if existing != materialized:
            raise ValueError(f'{label} {key_name} conflict: {row_id}')
    return merged


def merge_unique_values(base_rows: list[Any], incoming_rows: list[Any]) -> list[Any]:
    merged: list[Any] = []
    seen: set[str] = set()
    for row in [*base_rows, *incoming_rows]:
        value = str(row).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        merged.append(value)
    return merged


def fingerprint(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return f'scalar:{value!r}'


def merge_additive(base: Any, incoming: Any, *, label: str) -> Any:
    if isinstance(base, dict) and isinstance(incoming, dict):
        merged = deepcopy(base)
        for key, value in incoming.items():
            normalized = str(key).strip()
            if not normalized:
                raise ValueError(f'{label} contains an empty key')
            if normalized in merged:
                merged[normalized] = merge_additive(merged[normalized], value, label=f'{label}.{normalized}')
            else:
                merged[normalized] = deepcopy(value)
        return merged
    if isinstance(base, list) and isinstance(incoming, list):
        merged = [deepcopy(item) for item in base]
        seen = {fingerprint(item) for item in merged}
        for item in incoming:
            marker = fingerprint(item)
            if marker in seen:
                continue
            seen.add(marker)
            merged.append(deepcopy(item))
        return merged
    if base in EMPTY_SCALARS:
        return deepcopy(incoming)
    if incoming in EMPTY_SCALARS:
        return deepcopy(base)
    if base == incoming:
        return deepcopy(base)
    raise ValueError(f'{label} conflict: {base!r} != {incoming!r}')


def merge_additive_rows_by_key(base_rows: list[dict[str, Any]], incoming_rows: list[dict[str, Any]], *, key_name: str, label: str) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = [deepcopy(row) for row in base_rows if isinstance(row, dict)]
    index: dict[str, int] = {str(row.get(key_name) or '').strip(): idx for idx, row in enumerate(merged) if str(row.get(key_name) or '').strip()}
    for row in incoming_rows:
        if not isinstance(row, dict):
            continue
        row_id = str(row.get(key_name) or '').strip()
        if not row_id:
            raise ValueError(f'{label} missing {key_name}')
        materialized = deepcopy(row)
        existing_idx = index.get(row_id)
        if existing_idx is None:
            merged.append(materialized)
            index[row_id] = len(merged) - 1
            continue
        merged[existing_idx] = merge_additive(merged[existing_idx], materialized, label=f'{label}.{row_id}')
    return merged
