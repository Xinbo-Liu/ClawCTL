#!/usr/bin/env python3
"""Extension manifest conflict validation helpers."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.control_plane.extensions.loading import _read_json
from openclaw.control_plane.manifest_fields import (
    GOVERNANCE_SURFACES_FIELD,
    SURFACE_FRAGMENTS_FIELD,
)
from openclaw.control_plane.extensions.normalization import ExtensionError


def _register_unique(mapping: dict[str, str], key: str, *, owner: str, label: str) -> None:
    existing_owner = mapping.get(key)
    if existing_owner is not None and existing_owner != owner:
        raise ExtensionError(f'{label} conflict: {key} ({existing_owner} vs {owner})')
    mapping[key] = owner


def _fragment_object(manifest: dict[str, Any], *, group: str, key: str, label: str) -> dict[str, Any]:
    fragments = manifest.get(group) if isinstance(manifest.get(group), dict) else {}
    path = fragments.get(key)
    if not isinstance(path, Path):
        return {}
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise ExtensionError(f'{label} root must be an object: {path}')
    return payload


def _register_fragment_row_ids(
    rows: Any,
    *,
    key: str,
    owner: str,
    mapping: dict[str, str],
    label: str,
) -> None:
    if not isinstance(rows, list):
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = str(row.get(key) or '').strip()
        if value:
            _register_unique(mapping, value, owner=owner, label=label)


def _validate_enabled_manifest_conflicts(manifests: list[dict[str, Any]]) -> None:
    cli_commands: dict[str, str] = {}
    route_paths: dict[str, str] = {}
    route_ids: dict[str, str] = {}
    ready_check_ids: dict[str, str] = {}
    job_runner_ids: dict[str, str] = {}
    workspace_templates: dict[str, str] = {}
    workspace_target_entries: dict[str, str] = {}
    docs_page_paths: dict[str, str] = {}
    testing_group_ids: dict[str, str] = {}
    testing_check_ids: dict[str, str] = {}
    runtime_service_targets: dict[str, str] = {}
    path_entrypoint_ids: dict[str, str] = {}
    for manifest in manifests:
        extension_id = str(manifest.get('id') or '').strip() or '<unknown-extension>'
        for row in manifest.get('cliCommands') or []:
            if not isinstance(row, dict):
                continue
            command = str(row.get('command') or '').strip()
            if command:
                _register_unique(cli_commands, command, owner=extension_id, label='extension CLI command')
        for row in manifest.get('internalApiRoutes') or []:
            if not isinstance(row, dict):
                continue
            route_id = str(row.get('id') or '').strip()
            route_path = str(row.get('path') or '').strip()
            if route_id:
                _register_unique(route_ids, route_id, owner=extension_id, label='extension internal API route id')
            if route_path:
                _register_unique(route_paths, route_path, owner=extension_id, label='extension internal API route path')
        for row in manifest.get('readyChecks') or []:
            if not isinstance(row, dict):
                continue
            check_id = str(row.get('id') or '').strip()
            if check_id:
                _register_unique(ready_check_ids, check_id, owner=extension_id, label='extension ready check id')
        for row in manifest.get('jobRunners') or []:
            if not isinstance(row, dict):
                continue
            runner_id = str(row.get('id') or '').strip()
            if runner_id:
                _register_unique(job_runner_ids, runner_id, owner=extension_id, label='extension job runner id')

        workspace_payload = _fragment_object(
            manifest,
            group=SURFACE_FRAGMENTS_FIELD,
            key='workspaceTemplatesManifestPath',
            label=f'extension {extension_id} workspace_templates',
        )
        for row in workspace_payload.get('control_plane') or []:
            if not isinstance(row, dict):
                continue
            template_ref = str(row.get('template') or '').strip()
            target_entry = str(row.get('target_entry') or '').strip()
            if template_ref:
                _register_unique(workspace_templates, template_ref, owner=extension_id, label='extension workspace template')
            if target_entry:
                _register_unique(workspace_target_entries, target_entry, owner=extension_id, label='extension workspace target_entry')

        docs_payload = _fragment_object(
            manifest,
            group=GOVERNANCE_SURFACES_FIELD,
            key='docsRegistryPath',
            label=f'extension {extension_id} docs_registry',
        )
        _register_fragment_row_ids(
            docs_payload.get('pages'),
            key='path',
            owner=extension_id,
            mapping=docs_page_paths,
            label='extension docs registry page path',
        )

        testing_payload = _fragment_object(
            manifest,
            group=SURFACE_FRAGMENTS_FIELD,
            key='testingManifestPath',
            label=f'extension {extension_id} testing_manifest',
        )
        _register_fragment_row_ids(
            testing_payload.get('groups'),
            key='id',
            owner=extension_id,
            mapping=testing_group_ids,
            label='extension testing manifest group id',
        )
        _register_fragment_row_ids(
            testing_payload.get('checks'),
            key='id',
            owner=extension_id,
            mapping=testing_check_ids,
            label='extension testing manifest check id',
        )

        runtime_service_payload = _fragment_object(
            manifest,
            group=SURFACE_FRAGMENTS_FIELD,
            key='runtimeServiceRegistryPath',
            label=f'extension {extension_id} runtime_service_registry',
        )
        _register_fragment_row_ids(
            runtime_service_payload.get('targets'),
            key='target',
            owner=extension_id,
            mapping=runtime_service_targets,
            label='extension runtime service registry target',
        )

        path_entrypoints_payload = _fragment_object(
            manifest,
            group=GOVERNANCE_SURFACES_FIELD,
            key='pathEntrypointsSurfacePath',
            label=f'extension {extension_id} path_entrypoints',
        )
        entrypoints = path_entrypoints_payload.get('entrypoints') if isinstance(path_entrypoints_payload.get('entrypoints'), dict) else {}
        for entry_id in entrypoints:
            normalized_entry_id = str(entry_id or '').strip()
            if normalized_entry_id:
                _register_unique(path_entrypoint_ids, normalized_entry_id, owner=extension_id, label='extension path entrypoint id')
        _register_fragment_row_ids(
            path_entrypoints_payload.get('common_entries'),
            key='entry_id',
            owner=extension_id,
            mapping=path_entrypoint_ids,
            label='extension path entrypoint id',
        )
