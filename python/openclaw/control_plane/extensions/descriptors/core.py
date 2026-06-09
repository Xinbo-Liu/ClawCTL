#!/usr/bin/env python3
"""Core helpers for extension-owned fragment descriptors."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from openclaw.control_plane.extensions.fragments import iter_extension_fragment_paths
from openclaw.control_plane.extensions.merge import (
    merge_additive,
    merge_additive_rows_by_key,
    merge_unique_dict_entries,
    merge_unique_rows,
    merge_unique_values,
    read_json_object,
)
from openclaw.control_plane.extensions.ownership import (
    annotate_mapping_values,
    annotate_rows,
    mapping_to_owned_rows,
)
from openclaw.control_plane.manifest_fields import fragment_group_field

ValueMaterializer = Callable[[Any, str | None], Any]
PayloadPreparer = Callable[[dict[str, Any], str | None], dict[str, Any]]
PayloadFinalizer = Callable[[dict[str, Any]], dict[str, Any]]
ValueMerger = Callable[[Any, Any, str], Any]


@dataclass(frozen=True)
class FragmentFieldDescriptor:
    path: tuple[str, ...]
    label: str
    merge_kind: str
    key_name: str | None = None
    materialize: ValueMaterializer | None = None
    merge_value: ValueMerger | None = None


@dataclass(frozen=True)
class FragmentDescriptor:
    group: str
    key: str
    base_path: Path
    label: str
    fields: tuple[FragmentFieldDescriptor, ...] = ()
    prepare_payload: PayloadPreparer | None = None
    finalize_payload: PayloadFinalizer | None = None
    root_merge_kind: str | None = None


def _deepcopy_payload(payload: dict[str, Any], _extension_id: str | None) -> dict[str, Any]:
    return deepcopy(payload)


def _get_path(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _set_path(payload: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    current = payload
    for part in path[:-1]:
        nested = current.get(part)
        if not isinstance(nested, dict):
            nested = {}
            current[part] = nested
        current = nested
    current[path[-1]] = value


def _materialize_rows(*, label: str) -> ValueMaterializer:
    def _inner(value: Any, extension_id: str | None) -> list[dict[str, Any]]:
        return annotate_rows(value if isinstance(value, list) else [], extension_id)

    return _inner


def _materialize_mapping_values(*, label: str, id_key: str | None = None) -> ValueMaterializer:
    def _inner(value: Any, extension_id: str | None) -> dict[str, Any]:
        return annotate_mapping_values(
            value if isinstance(value, dict) else {},
            extension_id=extension_id,
            id_key=id_key,
            label=label,
        )

    return _inner


def _materialize_mapping_rows(*, label: str, id_key: str) -> ValueMaterializer:
    def _inner(value: Any, extension_id: str | None) -> list[dict[str, Any]]:
        return mapping_to_owned_rows(
            value if isinstance(value, dict) else {},
            extension_id=extension_id,
            id_key=id_key,
            label=label,
        )

    return _inner


def _merge_gateway_readonly_entries(base_value: Any, incoming_value: Any, label: str) -> list[dict[str, Any]]:
    base_rows = [dict(row) for row in base_value if isinstance(row, dict)] if isinstance(base_value, list) else []
    incoming_rows = [dict(row) for row in incoming_value if isinstance(row, dict)] if isinstance(incoming_value, list) else []
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in [*base_rows, *incoming_rows]:
        source = str(row.get('source') or '').strip()
        target = str(row.get('target') or '').strip()
        if not source or not target:
            raise ValueError(f'{label} item is missing source/target')
        key = (source, target)
        if key in seen:
            continue
        seen.add(key)
        merged.append(dict(row))
    return merged


def _merge_last_nonempty(base_value: Any, incoming_value: Any, _label: str) -> str:
    incoming = str(incoming_value or '').strip()
    if incoming:
        return incoming
    return str(base_value or '').strip()


def _merge_overlay_dict(base_value: Any, incoming_value: Any, _label: str) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    if isinstance(base_value, dict):
        merged.update(deepcopy(base_value))
    if isinstance(incoming_value, dict):
        merged.update(deepcopy(incoming_value))
    return merged


def _merge_extend(base_value: Any, incoming_value: Any, _label: str) -> list[Any]:
    merged = [deepcopy(item) for item in base_value] if isinstance(base_value, list) else []
    if isinstance(incoming_value, list):
        merged.extend(deepcopy(item) for item in incoming_value)
    return merged


def _merge_field_value(field: FragmentFieldDescriptor, base_value: Any, incoming_value: Any, *, extension_id: str) -> Any:
    label = f'extension {extension_id} {field.label}'
    if field.merge_value is not None:
        return field.merge_value(base_value, incoming_value, label)
    if field.merge_kind == 'additive':
        return merge_additive(base_value, incoming_value, label=label)
    if field.merge_kind == 'unique_rows':
        return merge_unique_rows(
            base_value if isinstance(base_value, list) else [],
            incoming_value if isinstance(incoming_value, list) else [],
            key_name=str(field.key_name or '').strip(),
            label=label,
        )
    if field.merge_kind == 'additive_rows_by_key':
        return merge_additive_rows_by_key(
            base_value if isinstance(base_value, list) else [],
            incoming_value if isinstance(incoming_value, list) else [],
            key_name=str(field.key_name or '').strip(),
            label=label,
        )
    if field.merge_kind == 'unique_values':
        return merge_unique_values(
            base_value if isinstance(base_value, list) else [],
            incoming_value if isinstance(incoming_value, list) else [],
        )
    if field.merge_kind == 'unique_dict':
        return merge_unique_dict_entries(
            base_value if isinstance(base_value, dict) else {},
            incoming_value if isinstance(incoming_value, dict) else {},
            label=label,
        )
    if field.merge_kind == 'overlay_dict':
        return _merge_overlay_dict(base_value, incoming_value, label)
    if field.merge_kind == 'extend':
        return _merge_extend(base_value, incoming_value, label)
    if field.merge_kind == 'last_nonempty':
        return _merge_last_nonempty(base_value, incoming_value, label)
    raise ValueError(f'unknown fragment merge kind: {field.merge_kind}')


def _materialize_payload(
    descriptor: FragmentDescriptor,
    payload: dict[str, Any],
    *,
    extension_id: str | None,
) -> dict[str, Any]:
    materialized = (
        descriptor.prepare_payload(payload, extension_id)
        if descriptor.prepare_payload is not None
        else _deepcopy_payload(payload, extension_id)
    )
    for field in descriptor.fields:
        if field.materialize is None:
            continue
        _set_path(
            materialized,
            field.path,
            field.materialize(_get_path(materialized, field.path), extension_id),
        )
    return materialized


def _merge_payloads(
    descriptor: FragmentDescriptor,
    payload: dict[str, Any],
    incoming: dict[str, Any],
    *,
    extension_id: str,
) -> dict[str, Any]:
    if descriptor.root_merge_kind == 'additive':
        return merge_additive(payload, incoming, label=f'extension {extension_id} {descriptor.label}')
    merged = deepcopy(payload)
    for field in descriptor.fields:
        _set_path(
            merged,
            field.path,
            _merge_field_value(field, _get_path(merged, field.path), _get_path(incoming, field.path), extension_id=extension_id),
        )
    return merged


def _fragment_paths_from_extensions(
    descriptor: FragmentDescriptor,
    extensions: list[dict[str, Any]],
) -> list[tuple[str, Path]]:
    field = fragment_group_field(descriptor.group)
    rows: list[tuple[str, Path]] = []
    for extension in extensions:
        extension_id = str(extension.get('id') or '').strip()
        fragments = extension.get(field) if isinstance(extension.get(field), dict) else {}
        fragment_path = fragments.get(descriptor.key)
        if extension_id and isinstance(fragment_path, Path):
            rows.append((extension_id, fragment_path))
    return rows


def load_fragment_payload(
    descriptor: FragmentDescriptor,
    *,
    path: Path | None = None,
    config_path: Path | None = None,
    extensions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = _materialize_payload(
        descriptor,
        read_json_object(path or descriptor.base_path),
        extension_id=None,
    )
    fragment_paths = (
        _fragment_paths_from_extensions(descriptor, extensions)
        if extensions is not None
        else list(
            iter_extension_fragment_paths(
                config_path=config_path,
                group=descriptor.group,
                key=descriptor.key,
            )
        )
    )
    for extension_id, fragment_path in fragment_paths:
        payload = _merge_payloads(
            descriptor,
            payload,
            _materialize_payload(descriptor, read_json_object(fragment_path), extension_id=extension_id),
            extension_id=extension_id,
        )
    if descriptor.finalize_payload is not None:
        payload = descriptor.finalize_payload(payload)
    return payload
