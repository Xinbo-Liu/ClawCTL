#!/usr/bin/env python3
"""Workspace-user rendering helpers for reference_specs surfaces."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from openclaw.docs.support.reference_specs_support import router as router_specs
from openclaw.docs.support.reference_specs_support.io import (
    ROOT_DIR,
    WORKSPACE_USER_TARGETS,
    escape_regexp,
    normalize_spacing,
)
from openclaw.specs.agent_cli import (
    get_agent_command_spec,
    split_workspace_usage,
    strip_agent_prefix,
)


def _render_workspace_user_target(
    root_dir: Path,
    target: dict[str, object],
    *,
    config_path: Path | None = None,
    replace_workspace_managed_block_fn: Callable[..., str] | None = None,
) -> tuple[Path, str]:
    abs_path = root_dir / str(target['relativePath'])
    current = abs_path.read_text(encoding='utf-8')
    replacer = replace_workspace_managed_block_fn or replace_workspace_managed_block
    return abs_path, replacer(current, target, config_path=config_path)


def render_workspace_command_section(target_id: str, *, config_path: Path | None = None) -> str:
    spec = get_agent_command_spec(target_id, config_path=config_path)
    usage = split_workspace_usage(spec, target_id)
    primary_entries = [strip_agent_prefix(entry, target_id) for entry in usage.get('primary') or []]
    diagnostic_entries = [strip_agent_prefix(entry, target_id) for entry in usage.get('diagnostic') or []]
    lines = [
        '## 动作速查',
    ]
    if primary_entries:
        lines.extend([
            '',
            '### 主流程动作',
            '```text',
            *primary_entries,
            '```',
        ])
    if diagnostic_entries:
        lines.extend([
            '',
            '### 查看 / 排障动作',
            '```text',
            *diagnostic_entries,
            '```',
        ])
    for section in spec.get('sections') or []:
        title = str(section.get('title') or '').strip()
        rows = [str(row).strip() for row in (section.get('lines') or []) if str(row).strip()]
        if not title or not rows:
            continue
        lines.extend(['', f'### {title}', ''])
        lines.extend(f'- {row}' for row in rows)
    return normalize_spacing('\n'.join(lines))


def begin_marker(target: dict[str, object]) -> str:
    return f"<!-- BEGIN AUTO:{target['marker']} {target['targetId']} -->"


def end_marker(target: dict[str, object]) -> str:
    return f"<!-- END AUTO:{target['marker']} {target['targetId']} -->"


def render_workspace_managed_block(
    target: dict[str, object],
    *,
    config_path: Path | None = None,
    render_router_workspace_section_fn: Callable[..., str] = router_specs.render_router_workspace_section,
    render_workspace_command_section_fn: Callable[..., str] = render_workspace_command_section,
    begin_marker_fn: Callable[[dict[str, object]], str] = begin_marker,
    end_marker_fn: Callable[[dict[str, object]], str] = end_marker,
) -> str:
    section = (
        render_router_workspace_section_fn(config_path=config_path)
        if target.get('kind') == 'router'
        else render_workspace_command_section_fn(str(target['targetId']), config_path=config_path)
    )
    return f"{begin_marker_fn(target)}\n{section.rstrip()}\n{end_marker_fn(target)}"


def replace_workspace_managed_block(
    content: str,
    target: dict[str, object],
    *,
    config_path: Path | None = None,
    begin_marker_fn: Callable[[dict[str, object]], str] = begin_marker,
    end_marker_fn: Callable[[dict[str, object]], str] = end_marker,
    render_workspace_managed_block_fn: Callable[..., str] = render_workspace_managed_block,
) -> str:
    pattern = re.compile(rf"{escape_regexp(begin_marker_fn(target))}[\s\S]*?{escape_regexp(end_marker_fn(target))}", re.M)
    if not pattern.search(content):
        raise ValueError(f"缺少自动生成标记：{target['relativePath']}")
    return pattern.sub(render_workspace_managed_block_fn(target, config_path=config_path), content)


def render_workspace_user_targets(
    root_dir: Path = ROOT_DIR,
    *,
    config_path: Path | None = None,
    workspace_user_targets: list[dict[str, object]] = WORKSPACE_USER_TARGETS,
    get_agent_command_spec_fn: Callable[..., dict[str, object]] = get_agent_command_spec,
    render_workspace_user_target_fn: Callable[..., tuple[Path, str]] = _render_workspace_user_target,
) -> dict[Path, str]:
    rendered: dict[Path, str] = {}
    for target in workspace_user_targets:
        if target.get('kind') == 'workspace':
            target_id = str(target.get('targetId') or '').strip()
            if target_id:
                try:
                    get_agent_command_spec_fn(target_id, config_path=config_path)
                except KeyError:
                    continue
        abs_path, content = render_workspace_user_target_fn(root_dir, target, config_path=config_path)
        rendered[abs_path] = content
    return rendered


def render_workspace_user_targets_for_repo(
    root_dir: Path = ROOT_DIR,
    *,
    render_workspace_user_targets_fn: Callable[..., dict[Path, str]] = render_workspace_user_targets,
) -> dict[Path, str]:
    base_config_path = (root_dir / 'config' / 'control_plane' / 'service.json').resolve()
    return render_workspace_user_targets_fn(root_dir, config_path=base_config_path)
