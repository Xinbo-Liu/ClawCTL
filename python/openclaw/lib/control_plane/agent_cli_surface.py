#!/usr/bin/env python3
"""Unified CLI surface for agent command reference output."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, NoReturn

from openclaw.control_plane.agent.cli_surface import load_agent_cli_surface
from openclaw.lib.cli.examples import canonical_cli_command, usage_block
from openclaw.lib.repo.layout import (
    resolve_default_runtime_control_plane_service_config_path,
)
from openclaw.lib.repo.static_truth import repo_contract_path
DEFAULT_RUNTIME_CONFIG_PATH = resolve_default_runtime_control_plane_service_config_path(Path(__file__))


def fail(message: str, code: int = 2) -> NoReturn:
    sys.stderr.write(f'[agent_cli_surface][FAIL] {message}\n')
    raise SystemExit(code)


def load_config(*, config_path: Path | None = None) -> dict[str, Any]:
    payload = load_agent_cli_surface(repo_contract_path('control_plane.agent_cli_surface'), config_path=config_path)
    if not isinstance(payload, dict):
        fail('agent_cli_surface.json top-level payload must be an object')
    return payload


def agents(*, config_path: Path | None = None) -> dict[str, dict[str, Any]]:
    raw = load_config(config_path=config_path).get('agents') or {}
    if not isinstance(raw, dict):
        fail('agents must be an object')
    return {str(key): value for key, value in raw.items() if isinstance(value, dict)}


def agent_info(agent_id: str, *, config_path: Path | None = None) -> dict[str, Any]:
    info = agents(config_path=config_path).get(agent_id)
    if info is None:
        fail(f'unknown agent: {agent_id}')
    return info


def parse_args(argv: list[str]) -> dict[str, Any]:
    opts: dict[str, Any] = {'agent': '', 'config_path': None}
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in {'-h', '--help'}:
            opts['help'] = True
            index += 1
            continue
        if not arg.startswith('--'):
            fail(f'unknown arg: {arg}')
        index += 1
        if index >= len(argv):
            fail(f'{arg} requires a value')
        value = argv[index]
        index += 1
        if arg == '--agent':
            opts['agent'] = value
        elif arg == '--config-path':
            opts['config_path'] = Path(value).resolve()
        else:
            fail(f'unknown arg: {arg}')
    return opts


def render_index(*, config_path: Path | None = None) -> str:
    lines = ['agent CLI reference', '']
    for agent_id, info in agents(config_path=config_path).items():
        lines.append(f'- {agent_id}: {str(info.get("heading") or agent_id).strip()}')
    return '\n'.join(lines)


def render_agent(agent_id: str, *, config_path: Path | None = None) -> str:
    info = agent_info(agent_id, config_path=config_path)
    lines = [
        f'id: {agent_id}',
        f'heading: {str(info.get("heading") or agent_id).strip()}',
        f'description: {str(info.get("description") or "").strip()}',
        'usage:',
    ]
    for entry in list(info.get('usage') or []):
        lines.append(f'  - {str(entry).rstrip()}')
    for section in list(info.get('sections') or []):
        if not isinstance(section, dict):
            continue
        lines.append(f'section: {str(section.get("title") or "").strip()}')
        for row in list(section.get('lines') or []):
            lines.append(f'  - {str(row).rstrip()}')
    return '\n'.join(lines)


def render_commands(agent_id: str, *, config_path: Path | None = None) -> str:
    info = agent_info(agent_id, config_path=config_path)
    return '\n'.join(str(item).rstrip() for item in list(info.get('usage') or []) if str(item).strip())


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        fail('missing subcommand; supported: show-index / show-agent / commands')
    command = args.pop(0)
    opts = parse_args(args)
    if opts.get('help'):
        sys.stdout.write(usage_block(
            canonical_cli_command('control-plane', 'agent-cli', 'show-index'),
            canonical_cli_command('control-plane', 'agent-cli', 'show-agent') + ' --agent <id>',
            canonical_cli_command('control-plane', 'agent-cli', 'commands') + ' --agent <id>',
            canonical_cli_command('control-plane', 'agent-cli', 'show-index') + f' --config-path {DEFAULT_RUNTIME_CONFIG_PATH}',
            title='Usage:',
        ))
        return 0
    config_path = opts.get('config_path')
    if command == 'show-index':
        sys.stdout.write(render_index(config_path=config_path) + '\n')
        return 0
    if command == 'show-agent':
        if not opts['agent']:
            fail('show-agent requires --agent')
        sys.stdout.write(render_agent(opts['agent'], config_path=config_path) + '\n')
        return 0
    if command == 'commands':
        if not opts['agent']:
            fail('commands requires --agent')
        sys.stdout.write(render_commands(opts['agent'], config_path=config_path) + '\n')
        return 0
    fail(f'unknown subcommand: {command}')


if __name__ == '__main__':
    raise SystemExit(main())
