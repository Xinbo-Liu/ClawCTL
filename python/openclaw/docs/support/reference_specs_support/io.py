#!/usr/bin/env python3
"""Spec loading and shared constants for reference_specs surfaces."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from openclaw.control_plane.agent.cli_surface import load_agent_cli_surface
from openclaw.control_plane.governance_surfaces import load_router_route_surface
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.static_truth import repo_contract_path

ROOT_DIR = resolve_repo_root(Path(__file__))
AUTO_NOTICE = ''
EMBEDDED_SPECS: dict[str, Any] = {}

SURFACE_LEVELS = ['default_entrypoint', 'supplemental_entrypoint', 'internal_support']
SURFACE_LABELS = {
    'default_entrypoint': '默认入口',
    'supplemental_entrypoint': '补充入口',
    'internal_support': '内部复用',
}
SURFACE_TITLES = dict(SURFACE_LABELS)

WORKSPACE_USER_TARGETS = [
    {
        'targetId': 'router_local_ro',
        'relativePath': 'config/workspace_templates/router_local_ro/USER.md',
        'marker': 'ROUTER_ROUTE_REFERENCE',
        'kind': 'router',
    }
]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def _script_surface_manifest_from_groups(groups: list[Any]) -> dict[str, list[str]]:
    manifest: dict[str, list[str]] = {level: [] for level in SURFACE_LEVELS}
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_id = str(group.get('id') or '').strip()
        for item in group.get('files') or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get('name') or '').strip()
            visibility = str(item.get('visibility') or '').strip()
            if group_id and name and visibility in manifest:
                manifest[visibility].append(f'scripts/{group_id}/{name}')
    return manifest


def load_specs(name: str) -> Any:
    if name == 'agent_command_specs.json':
        payload = load_agent_cli_surface(repo_contract_path('control_plane.agent_cli_surface'))
        data = payload.get('agents')
        if not isinstance(data, dict):
            raise ValueError('agent_cli_surface.json -> agents 顶层必须为对象')
        return data
    if name == 'router_route_specs.json':
        data = load_router_route_surface()
        if not isinstance(data, dict):
            raise ValueError('router_route_surface.json 顶层必须为对象')
        return data
    if name in {'script_groups.json', 'script_surface_manifest.json'}:
        payload = read_json(repo_contract_path('governance.script_catalog_surface'))
        groups = payload.get('groups')
        if not isinstance(groups, list):
            raise ValueError('script_catalog_surface.json -> groups 顶层必须为数组')
        if name == 'script_groups.json':
            return groups
        return _script_surface_manifest_from_groups(groups)
    if name not in EMBEDDED_SPECS:
        raise KeyError(f'未知 docs spec：{name}')
    return EMBEDDED_SPECS[name]


def normalize_spacing(text: str) -> str:
    return re.sub(r'\n{3,}', '\n\n', text).rstrip() + '\n'


def escape_regexp(value: str) -> str:
    return re.escape(str(value))
