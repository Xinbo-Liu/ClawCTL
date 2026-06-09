#!/usr/bin/env python3
"""Load extension-aware deploy/testing/runtime/gateway surfaces."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from openclaw.control_plane.extensions.fragment_descriptors import (
    DEPLOY_ENV_SCHEMA_DESCRIPTOR,
    GATEWAY_EXEC_APPROVALS_DESCRIPTOR,
    GATEWAY_READONLY_MANIFEST_DESCRIPTOR,
    RUNTIME_PATHS_DESCRIPTOR,
    RUNTIME_SERVICE_REGISTRY_DESCRIPTOR,
    TESTING_MANIFEST_DESCRIPTOR,
    load_fragment_payload,
)
from openclaw.control_plane.extensions.fragments import iter_surface_fragment_paths
from openclaw.control_plane.extensions.merge import merge_unique_rows, merge_unique_values, read_json_object
from openclaw.control_plane.extensions.ownership import annotate_rows
from openclaw.control_plane.manifest_models import (
    WorkspaceTemplateBindingModel,
    WorkspaceTemplatesManifestModel,
)
from openclaw.lib.repo.contracts import repo_contract_path

DEPLOY_ENV_SCHEMA_PATH = DEPLOY_ENV_SCHEMA_DESCRIPTOR.base_path
TESTING_MANIFEST_PATH = TESTING_MANIFEST_DESCRIPTOR.base_path
RUNTIME_PATHS_MANIFEST_PATH = RUNTIME_PATHS_DESCRIPTOR.base_path
RUNTIME_SERVICE_REGISTRY_PATH = RUNTIME_SERVICE_REGISTRY_DESCRIPTOR.base_path
GATEWAY_READONLY_MANIFEST_PATH = GATEWAY_READONLY_MANIFEST_DESCRIPTOR.base_path
GATEWAY_EXEC_APPROVALS_PATH = GATEWAY_EXEC_APPROVALS_DESCRIPTOR.base_path
WORKSPACE_TEMPLATES_MANIFEST_PATH = repo_contract_path('workspace_templates.manifest')


def load_runtime_paths_manifest(
    path: Path | None = None,
    *,
    config_path: Path | None = None,
    extensions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return load_fragment_payload(RUNTIME_PATHS_DESCRIPTOR, path=path, config_path=config_path, extensions=extensions)


def load_deploy_env_schema(path: Path | None = None, *, config_path: Path | None = None) -> dict[str, Any]:
    return load_fragment_payload(DEPLOY_ENV_SCHEMA_DESCRIPTOR, path=path, config_path=config_path)


def load_testing_manifest(
    path: Path | None = None,
    *,
    config_path: Path | None = None,
    extensions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return load_fragment_payload(TESTING_MANIFEST_DESCRIPTOR, path=path, config_path=config_path, extensions=extensions)


def load_runtime_service_registry(path: Path | None = None, *, config_path: Path | None = None) -> dict[str, Any]:
    return load_fragment_payload(RUNTIME_SERVICE_REGISTRY_DESCRIPTOR, path=path, config_path=config_path)


def load_gateway_readonly_manifest(path: Path | None = None, *, config_path: Path | None = None) -> dict[str, Any]:
    return load_fragment_payload(GATEWAY_READONLY_MANIFEST_DESCRIPTOR, path=path, config_path=config_path)


def load_gateway_exec_approvals(path: Path | None = None, *, config_path: Path | None = None) -> dict[str, Any]:
    return load_fragment_payload(GATEWAY_EXEC_APPROVALS_DESCRIPTOR, path=path, config_path=config_path)


def _assert_unique_target_entries(rows: list[dict[str, Any]], *, label: str) -> None:
    seen: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        template_ref = str(row.get('template') or '').strip()
        target_entry = str(row.get('target_entry') or '').strip()
        if not template_ref or not target_entry:
            continue
        owner = seen.get(target_entry)
        if owner is None:
            seen[target_entry] = template_ref
            continue
        if owner != template_ref:
            raise ValueError(f'{label} target_entry conflict: {target_entry}')


def _normalize_workspace_template_rows(rows: list[dict[str, Any]], *, label: str, extension_id: str | None) -> list[dict[str, Any]]:
    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        binding = WorkspaceTemplateBindingModel.from_payload(row, label=label).to_payload()
        extras = {key: value for key, value in row.items() if key not in {'template', 'target_entry'}}
        normalized_rows.append({**extras, **binding})
    normalized = annotate_rows(normalized_rows, extension_id)
    _assert_unique_target_entries(normalized, label=label)
    return normalized


def _normalize_stale_dirs(values: list[Any], *, label: str) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for row in values:
        value = str(row or '').strip()
        if not value:
            raise ValueError(f'{label} stale_dirs contains an empty entry')
        if value in seen:
            raise ValueError(f'{label} stale_dirs conflict: {value}')
        seen.add(value)
        normalized.append(value)
    return normalized


def load_workspace_templates_manifest(path: Path | None = None, *, config_path: Path | None = None) -> dict[str, Any]:
    payload = WorkspaceTemplatesManifestModel.from_payload(
        deepcopy(read_json_object(path or WORKSPACE_TEMPLATES_MANIFEST_PATH)),
        label='workspace_templates',
    ).to_payload()
    payload['control_plane'] = _normalize_workspace_template_rows(
        payload.get('control_plane') if isinstance(payload.get('control_plane'), list) else [],
        label='workspace_templates.control_plane',
        extension_id=None,
    )
    payload['stale_dirs'] = _normalize_stale_dirs(
        payload.get('stale_dirs') if isinstance(payload.get('stale_dirs'), list) else [],
        label='workspace_templates',
    )
    base_template_refs = {str(row.get('template') or '').strip() for row in payload['control_plane']}
    base_stale_dirs = set(payload['stale_dirs'])
    extension_template_owners: dict[str, str] = {}
    extension_stale_dir_owners: dict[str, str] = {}
    for extension_id, fragment_path in iter_surface_fragment_paths(config_path=config_path, key='workspaceTemplatesManifestPath'):
        extension_payload = WorkspaceTemplatesManifestModel.from_payload(
            read_json_object(fragment_path),
            label=f'extension {extension_id} workspace_templates',
        ).to_payload()
        incoming_rows = _normalize_workspace_template_rows(
            extension_payload.get('control_plane') if isinstance(extension_payload.get('control_plane'), list) else [],
            label=f'extension {extension_id} workspace_templates.control_plane',
            extension_id=extension_id,
        )
        incoming_stale_dirs = _normalize_stale_dirs(
            extension_payload.get('stale_dirs') if isinstance(extension_payload.get('stale_dirs'), list) else [],
            label=f'extension {extension_id} workspace_templates',
        )
        for row in incoming_rows:
            template_ref = str(row.get('template') or '').strip()
            if template_ref in base_template_refs:
                raise ValueError(f'extension {extension_id} workspace_templates.control_plane duplicates base template: {template_ref}')
            owner = extension_template_owners.get(template_ref)
            if owner is not None and owner != extension_id:
                raise ValueError(f'workspace_templates.control_plane template conflict: {template_ref}')
            extension_template_owners[template_ref] = extension_id
        for stale_dir in incoming_stale_dirs:
            if stale_dir in base_stale_dirs:
                raise ValueError(f'extension {extension_id} workspace_templates.stale_dirs duplicates base stale_dir: {stale_dir}')
            owner = extension_stale_dir_owners.get(stale_dir)
            if owner is not None and owner != extension_id:
                raise ValueError(f'workspace_templates.stale_dirs conflict: {stale_dir}')
            extension_stale_dir_owners[stale_dir] = extension_id
        payload['control_plane'] = merge_unique_rows(
            payload.get('control_plane') if isinstance(payload.get('control_plane'), list) else [],
            incoming_rows,
            key_name='template',
            label=f'extension {extension_id} workspace_templates.control_plane',
        )
        _assert_unique_target_entries(
            payload.get('control_plane') if isinstance(payload.get('control_plane'), list) else [],
            label=f'extension {extension_id} workspace_templates.control_plane',
        )
        payload['stale_dirs'] = merge_unique_values(
            payload.get('stale_dirs') if isinstance(payload.get('stale_dirs'), list) else [],
            incoming_stale_dirs,
        )
        for key in ('module', 'version'):
            value = str(extension_payload.get(key) or '').strip()
            if value:
                current = str(payload.get(key) or '').strip()
                if current and current != value:
                    raise ValueError(f'extension {extension_id} workspace_templates.{key} conflict: {current} != {value}')
                payload[key] = value
    payload['control_plane'] = _normalize_workspace_template_rows(
        payload.get('control_plane') if isinstance(payload.get('control_plane'), list) else [],
        label='workspace_templates.control_plane',
        extension_id=None,
    )
    payload['stale_dirs'] = _normalize_stale_dirs(
        payload.get('stale_dirs') if isinstance(payload.get('stale_dirs'), list) else [],
        label='workspace_templates',
    )
    return payload
