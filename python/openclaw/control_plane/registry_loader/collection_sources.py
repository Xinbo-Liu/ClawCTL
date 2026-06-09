#!/usr/bin/env python3
"""Registry collection source loading helpers."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.control_plane.registry.owners import (
    BASE_OWNER_ID,
    annotate_owned_row,
    owned_index_bundle,
    qualified_registry_id,
    row_owner_id,
)
from openclaw.control_plane.registry_loader.activation import resolve_object_activation
from openclaw.control_plane.registry.store import read_json
from openclaw.control_plane.schema import SchemaValidationError, validate_payload_against_schema
from openclaw.lib.cli.common import CliError


def _validate_activation_owner(
    activation_state: dict[str, Any],
    *,
    label: str,
    owner_id: str,
    require_activation: bool,
) -> None:
    if not require_activation:
        return
    normalized_owner = str(owner_id or BASE_OWNER_ID).strip() or BASE_OWNER_ID
    if normalized_owner == BASE_OWNER_ID:
        return
    configured_ids = [
        str(item).strip()
        for item in (activation_state.get('configuredExtensionIds') or [])
        if str(item).strip()
    ]
    if configured_ids != [normalized_owner]:
        configured_text = ', '.join(configured_ids) or '<empty>'
        raise CliError(
            f'{label} activation.enabledExtensionIds must exactly match directory owner '
            f'{normalized_owner}: {configured_text}',
            2,
        )


def _load_collection(
    directory: Path,
    label: str,
    schema: dict[str, Any],
    *,
    allow_missing: bool = False,
    enabled_extension_ids: list[str] | None = None,
    known_extension_ids: set[str] | None = None,
    require_activation: bool = False,
    owner_id: str = BASE_OWNER_ID,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if not directory.exists() or not directory.is_dir():
        if allow_missing:
            return [], {}
        raise CliError(f'{label} directory does not exist: {directory}', 2)
    rows: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob('*.json')):
        payload = read_json(path, None)
        if not isinstance(payload, dict):
            raise CliError(f'{label} JSON cannot be parsed: {path}', 2)
        try:
            validate_payload_against_schema(
                payload,
                schema,
                label=f'{label} {path.name}',
                strict_dependency=True,
            )
        except SchemaValidationError as exc:
            raise CliError(str(exc), 2) from exc
        activation_state = resolve_object_activation(
            payload,
            label=f'{label} {path.name}',
            enabled_extension_ids=list(enabled_extension_ids or []),
            known_extension_ids=set(known_extension_ids or set()),
            require_activation=require_activation,
        )
        _validate_activation_owner(
            activation_state,
            label=f'{label} {path.name}',
            owner_id=owner_id,
            require_activation=require_activation,
        )
        if not bool(activation_state.get('visible')):
            continue
        item_id = str(payload.get('id') or '').strip()
        row = dict(payload)
        row['sourcePath'] = str(path)
        effective_owner_id = str(activation_state.get('primaryActiveExtensionId') or '').strip() or owner_id
        if activation_state.get('activeExtensionIds'):
            row['resolvedActiveExtensionIds'] = list(activation_state['activeExtensionIds'])
            row['extensionId'] = str(activation_state.get('primaryActiveExtensionId') or '')
        annotate_owned_row(row, owner_id=effective_owner_id)
        qualified_id = str(row.get('qualifiedId') or qualified_registry_id(row_owner_id(row), item_id))
        if qualified_id in index:
            raise CliError(f'{label} id duplicated for owner {row_owner_id(row)}: {item_id}', 2)
        rows.append(row)
        index[qualified_id] = row
    return rows, owned_index_bundle(rows, label=label)['byId']


def _load_agent_modules(
    directory: Path,
    label: str,
    schema: dict[str, Any],
    *,
    enabled_extension_ids: list[str] | None = None,
    known_extension_ids: set[str] | None = None,
    require_activation: bool = False,
    owner_id: str = BASE_OWNER_ID,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if not directory.exists() or not directory.is_dir():
        raise CliError(f'{label} directory does not exist: {directory}', 2)
    rows: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob('*/module.json')):
        payload = read_json(path, None)
        if not isinstance(payload, dict):
            raise CliError(f'{label} JSON cannot be parsed: {path}', 2)
        try:
            validate_payload_against_schema(
                payload,
                schema,
                label=f'{label} {path.parent.name}/module.json',
                strict_dependency=True,
            )
        except SchemaValidationError as exc:
            raise CliError(str(exc), 2) from exc
        activation_state = resolve_object_activation(
            payload,
            label=f'{label} {path.parent.name}/module.json',
            enabled_extension_ids=list(enabled_extension_ids or []),
            known_extension_ids=set(known_extension_ids or set()),
            require_activation=require_activation,
        )
        _validate_activation_owner(
            activation_state,
            label=f'{label} {path.parent.name}/module.json',
            owner_id=owner_id,
            require_activation=require_activation,
        )
        if not bool(activation_state.get('visible')):
            continue
        item_id = str(payload.get('id') or '').strip()
        row = dict(payload)
        row['sourcePath'] = str(path)
        row['moduleDir'] = str(path.parent)
        effective_owner_id = str(activation_state.get('primaryActiveExtensionId') or '').strip() or owner_id
        if activation_state.get('activeExtensionIds'):
            row['resolvedActiveExtensionIds'] = list(activation_state['activeExtensionIds'])
            row['extensionId'] = str(activation_state.get('primaryActiveExtensionId') or '')
        annotate_owned_row(row, owner_id=effective_owner_id)
        qualified_id = str(row.get('qualifiedId') or qualified_registry_id(row_owner_id(row), item_id))
        if qualified_id in index:
            raise CliError(f'{label} id duplicated for owner {row_owner_id(row)}: {item_id}', 2)
        rows.append(row)
        index[qualified_id] = row
    return rows, owned_index_bundle(rows, label=label)['byId']


def _merge_collection_rows(
    *,
    rows: list[dict[str, Any]],
    index: dict[str, dict[str, Any]],
    new_rows: list[dict[str, Any]],
    label: str,
) -> None:
    for row in new_rows:
        item_id = str(row.get('id') or '').strip()
        materialized = dict(row)
        qualified_id = str(materialized.get('qualifiedId') or qualified_registry_id(row_owner_id(materialized), item_id))
        if qualified_id in index:
            raise CliError(f'{label} id duplicated for owner {row_owner_id(materialized)}: {item_id}', 2)
        rows.append(materialized)
        index[qualified_id] = materialized


def _load_collection_dirs(
    directories: list[Path],
    label: str,
    schema: dict[str, Any],
    *,
    allow_missing: bool = False,
    shared_directories: set[Path] | None = None,
    owner_by_directory: dict[Path, str] | None = None,
    enabled_extension_ids: list[str] | None = None,
    known_extension_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    owner_map = {Path(path).resolve(): str(owner or BASE_OWNER_ID) for path, owner in (owner_by_directory or {}).items()}
    for directory in directories:
        resolved_directory = directory.resolve()
        dir_rows, _ = _load_collection(
            directory,
            label,
            schema,
            allow_missing=allow_missing,
            enabled_extension_ids=enabled_extension_ids,
            known_extension_ids=known_extension_ids,
            require_activation=resolved_directory in set(shared_directories or set()),
            owner_id=owner_map.get(resolved_directory, BASE_OWNER_ID),
        )
        _merge_collection_rows(rows=rows, index=index, new_rows=dir_rows, label=label)
    return rows, owned_index_bundle(rows, label=label)['byId']


def _load_agent_module_dirs(
    directories: list[Path],
    label: str,
    schema: dict[str, Any],
    *,
    shared_directories: set[Path] | None = None,
    owner_by_directory: dict[Path, str] | None = None,
    enabled_extension_ids: list[str] | None = None,
    known_extension_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    owner_map = {Path(path).resolve(): str(owner or BASE_OWNER_ID) for path, owner in (owner_by_directory or {}).items()}
    for directory in directories:
        resolved_directory = directory.resolve()
        dir_rows, _ = _load_agent_modules(
            directory,
            label,
            schema,
            enabled_extension_ids=enabled_extension_ids,
            known_extension_ids=known_extension_ids,
            require_activation=resolved_directory in set(shared_directories or set()),
            owner_id=owner_map.get(resolved_directory, BASE_OWNER_ID),
        )
        _merge_collection_rows(rows=rows, index=index, new_rows=dir_rows, label=label)
    return rows, owned_index_bundle(rows, label=label)['byId']
