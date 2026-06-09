#!/usr/bin/env python3
"""Agent 模块生命周期操作的可选模板面检查辅助。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.control_plane.modules.scaffold_support import (
    has_boilerplate_surface_marker,
    normalize_boilerplate_text,
    optional_surface_template_variants,
)
from openclaw.lib.io.json_access import json_object
from openclaw.lib.repo.layout import resolve_repo_root


OPTIONAL_SURFACE_RELPATHS = (
    'AGENTS.md',
    'contracts/README.md',
    'constraints/README.md',
    'docs/README.md',
    'tests/README.md',
)


def _normalize_optional_surface_text(text: str) -> str:
    return normalize_boilerplate_text(text)


def _launcher_display_path(module_payload: dict[str, Any]) -> str:
    source_path = Path(str(module_payload.get('sourcePath') or '')).resolve()
    module_dir = source_path.parent
    repo_root = resolve_repo_root(source_path)
    assets = json_object(module_payload.get('assets'))
    launcher_rel = str(assets.get('binPath') or '').strip() or f'bin/{module_payload.get("id")}'
    return (module_dir / launcher_rel).resolve().relative_to(repo_root.resolve()).as_posix()


def _is_optional_surface_boilerplate(module_payload: dict[str, Any], text: str, *, surface: str) -> bool:
    if not has_boilerplate_surface_marker(text, surface=surface):
        return False
    module_ref = str(module_payload.get('id') or '').strip()
    launcher_display_path = _launcher_display_path(module_payload) if surface == 'agents' else ''
    return _normalize_optional_surface_text(text) in optional_surface_template_variants(
        module_ref,
        surface=surface,
        launcher_display_path=launcher_display_path,
    )


def is_agents_boilerplate(module_payload: dict[str, Any], text: str) -> bool:
    return _is_optional_surface_boilerplate(module_payload, text, surface='agents')


def is_contracts_boilerplate(module_payload: dict[str, Any], text: str) -> bool:
    return _is_optional_surface_boilerplate(module_payload, text, surface='contracts')


def is_constraints_boilerplate(module_payload: dict[str, Any], text: str) -> bool:
    return _is_optional_surface_boilerplate(module_payload, text, surface='constraints')


def is_docs_boilerplate(module_payload: dict[str, Any], text: str) -> bool:
    return _is_optional_surface_boilerplate(module_payload, text, surface='docs')


def is_tests_boilerplate(module_payload: dict[str, Any], text: str) -> bool:
    return _is_optional_surface_boilerplate(module_payload, text, surface='tests')


def inspect_module_optional_surfaces(module_payload: dict[str, Any]) -> list[dict[str, Any]]:
    module_ref = str(module_payload.get('id') or '').strip()
    source_path = Path(str(module_payload.get('sourcePath') or '')).resolve()
    module_dir = source_path.parent
    assets = json_object(module_payload.get('assets'))
    result: list[dict[str, Any]] = []
    for rel_path in OPTIONAL_SURFACE_RELPATHS:
        target = (module_dir / rel_path).resolve()
        if not target.exists() or not target.is_file():
            continue
        try:
            text = target.read_text(encoding='utf-8')
        except OSError:
            continue
        if rel_path == 'AGENTS.md':
            kind = 'agents_doc'
            boilerplate = is_agents_boilerplate(module_payload, text)
        elif rel_path == 'contracts/README.md':
            kind = 'contracts_readme'
            boilerplate = is_contracts_boilerplate(module_payload, text)
        elif rel_path == 'constraints/README.md':
            kind = 'constraints_readme'
            boilerplate = is_constraints_boilerplate(module_payload, text)
        elif rel_path == 'docs/README.md':
            kind = 'docs_readme'
            boilerplate = is_docs_boilerplate(module_payload, text)
        elif rel_path == 'tests/README.md':
            kind = 'tests_readme'
            boilerplate = is_tests_boilerplate(module_payload, text)
        else:
            continue
        asset_key = ''
        if rel_path == str(assets.get('agentsMdPath') or '').strip():
            asset_key = 'agentsMdPath'
        result.append({
            'moduleRef': module_ref,
            'relPath': rel_path,
            'path': target,
            'kind': kind,
            'boilerplate': boilerplate,
            'assetKey': asset_key,
        })
    return result
