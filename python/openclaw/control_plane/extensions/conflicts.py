#!/usr/bin/env python3
"""提供OpenClaw 控制平面子系统的生产实现。"""
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
    """registerunique。

    参数：
        mapping（dict[str, str]）：mapping。
        key（str）：键。
        owner（str）：owner。
        label（str）：label。
    """
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
    """registerfragment记录行ids。

    参数：
        rows（Any）：记录行集合。
        key（str）：键。
        owner（str）：owner。
        mapping（dict[str, str]）：mapping。
        label（str）：label。
    """
    if not isinstance(rows, list):
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = str(row.get(key) or '').strip()
        if value:
            _register_unique(mapping, value, owner=owner, label=label)


def _reject_extension_testing_manifest_platform_fields(
    payload: dict[str, Any],
    *,
    extension_id: str,
) -> None:
    """拒绝扩展 testing manifest 重新声明平台 full test 字段。

    参数：
        payload（dict[str, Any]）：扩展 testing manifest 片段内容。
        extension_id（str）：扩展标识，用于错误信息归属。

    异常：
        当扩展声明平台 full test 分组、检查项、执行顺序或 deployment acceptance 字段时抛出 ExtensionError。
    """
    disallowed = [
        key
        for key in (
            'groups',
            'checks',
            'valid_groups',
            'execution_order',
            'acceptance_reference',
            'acceptance_contract',
        )
        if key in payload
    ]
    if disallowed:
        raise ExtensionError(
            'extension '
            + extension_id
            + ' testing_manifest contains platform full test field(s): '
            + ', '.join(sorted(disallowed))
            + '; extension testing manifests may declare release_gate_checks and live_acceptance_checks only'
        )


def _validate_enabled_manifest_conflicts(manifests: list[dict[str, Any]]) -> None:
    """校验enabledmanifestconflicts。

    参数：
        manifests（list[dict[str, Any]]）：manifests。

    异常：
        当启用扩展之间出现 CLI、路由、ready check、job runner、workspace、docs、runtime service、path entrypoint、testing manifest release gate 或 live acceptance 标识冲突，或非 agent_platform 扩展声明 full test group registry 时抛出 ExtensionError。
    """
    cli_commands: dict[str, str] = {}
    route_paths: dict[str, str] = {}
    route_ids: dict[str, str] = {}
    ready_check_ids: dict[str, str] = {}
    job_runner_ids: dict[str, str] = {}
    workspace_templates: dict[str, str] = {}
    workspace_target_entries: dict[str, str] = {}
    docs_page_paths: dict[str, str] = {}
    runtime_service_targets: dict[str, str] = {}
    path_entrypoint_ids: dict[str, str] = {}
    for manifest in manifests:
        extension_id = str(manifest.get('id') or '').strip() or '<unknown-extension>'
        governance_surfaces = manifest.get(GOVERNANCE_SURFACES_FIELD) if isinstance(manifest.get(GOVERNANCE_SURFACES_FIELD), dict) else {}
        if extension_id != 'agent_platform' and isinstance(governance_surfaces.get('fullTestGroupRegistryPath'), Path):
            raise ExtensionError(
                f'extension {extension_id} cannot declare fullTestGroupRegistryPath; platform full test group registry is owned by agent_platform'
            )
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
        _reject_extension_testing_manifest_platform_fields(testing_payload, extension_id=extension_id)

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
