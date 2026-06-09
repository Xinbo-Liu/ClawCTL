#!/usr/bin/env python3
"""Agent CLI 运行时规格与帮助渲染。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from openclaw.control_plane.agent.cli_surface import load_agent_cli_surface
from openclaw.control_plane.registry import SCHEDULER_SERVICE_EXEC, build_agent_runtime_command_spec
from openclaw.control_plane.registry.command_specs import materialize_command
from openclaw.lib.cli.examples import shell_join
from openclaw.lib.repo.layout import resolve_repo_root


ROOT_DIR = resolve_repo_root(Path(__file__))


def normalize_spacing(text: str) -> str:
    return re.sub(r'\n{3,}', '\n\n', text).rstrip() + '\n'


def agent_command_specs(*, config_path: Path | None = None) -> dict[str, Any]:
    payload = load_agent_cli_surface(config_path=config_path)
    data = payload.get('agents')
    if not isinstance(data, dict):
        raise ValueError('agent_cli_surface.json -> agents 顶层必须为对象')
    return data


def get_agent_command_spec(agent_id: str, *, config_path: Path | None = None) -> dict[str, Any]:
    spec = agent_command_specs(config_path=config_path).get(agent_id)
    if not isinstance(spec, dict):
        raise KeyError(f'未知 agent command spec：{agent_id}')
    return spec


def render_agent_help(agent_id: str, *, config_path: Path | None = None) -> str:
    spec = get_agent_command_spec(agent_id, config_path=config_path)
    lines = [str(spec['heading']), '']
    if spec.get('description'):
        lines.extend([str(spec['description']), ''])
    lines.append('用法：')
    for entry in spec.get('usage') or []:
        lines.append(f'  {entry}')
    for section in spec.get('sections') or []:
        lines.extend(['', f"{section['title']}："])
        for row in section.get('lines') or []:
            lines.append(f'  - {row}')
    return normalize_spacing('\n'.join(lines))


def with_registered_agent_runner_prefix(entry: str, agent_id: str) -> str:
    trimmed = str(entry or '').strip()
    if not trimmed:
        return trimmed
    prefix = f'{agent_id} '
    repo_runner = shell_join(
        materialize_command(
            build_agent_runtime_command_spec(
                agent={'id': agent_id},
                exec_mode=SCHEDULER_SERVICE_EXEC,
            )
        )
    )
    if trimmed.startswith(prefix):
        suffix = trimmed[len(prefix):].strip()
        return f'{repo_runner} -- {suffix}'.rstrip()
    if trimmed == agent_id:
        return repo_runner
    return trimmed


def render_agent_markdown(agent_id: str, *, config_path: Path | None = None) -> str:
    spec = get_agent_command_spec(agent_id, config_path=config_path)
    lines = [f"## {spec['heading']}", '']
    if spec.get('description'):
        lines.extend([str(spec['description']), ''])
    lines.extend(['```bash', *(with_registered_agent_runner_prefix(str(entry), agent_id) for entry in (spec.get('usage') or [])), '```'])
    for section in spec.get('sections') or []:
        lines.extend(['', f"### {section['title']}", ''])
        for row in section.get('lines') or []:
            lines.append(f'- {row}')
    return normalize_spacing('\n'.join(lines))


def extract_command_name(entry: str, agent_id: str) -> str | None:
    match = re.match(rf'^{re.escape(agent_id)}\s+([^\s]+)', str(entry).strip())
    return match.group(1) if match else None


def strip_agent_prefix(entry: str, agent_id: str) -> str:
    trimmed = str(entry or '').strip()
    if not trimmed:
        return trimmed
    prefix = f'{agent_id} '
    if trimmed.startswith(prefix):
        return trimmed[len(prefix):].strip()
    return trimmed


def split_workspace_usage(spec: dict[str, Any], agent_id: str) -> dict[str, list[str]]:
    primary_set = {str(item).strip() for item in ((spec.get('workspace') or {}).get('primaryCommands') or []) if str(item).strip()}
    primary: list[str] = []
    diagnostic: list[str] = []
    for entry in spec.get('usage') or []:
        command_name = extract_command_name(str(entry), agent_id)
        (primary if command_name in primary_set else diagnostic).append(str(entry))
    return {'primary': primary, 'diagnostic': diagnostic}


def get_usage_entries_by_command_names(agent_id: str, command_names: list[str], *, config_path: Path | None = None) -> list[str]:
    spec = get_agent_command_spec(agent_id, config_path=config_path)
    wanted = {str(item).strip() for item in command_names if str(item).strip()}
    return [str(entry) for entry in spec.get('usage') or [] if extract_command_name(str(entry), agent_id) in wanted]
