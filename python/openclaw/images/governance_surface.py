#!/usr/bin/env python3
"""部署镜像治理面控制面。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from openclaw.lib.cli.examples import canonical_cli_command, usage_block
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.runtime.source_strategy import deployment_image_roles, runtime_service_image_roles
from typing import Any, NoReturn

ROOT_DIR = resolve_repo_root(Path(__file__))
CONFIG_PATH = ROOT_DIR / 'config' / 'governance' / 'docs' / 'image_governance_surface.json'


def fail(message: str, code: int = 2) -> NoReturn:
    sys.stderr.write(f'[image_governance_surface][FAIL] {message}\n')
    raise SystemExit(code)


def load_config() -> dict[str, Any]:
    payload = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        fail('image_governance_surface.json 顶层必须为对象')
    return payload


def contract_info() -> dict[str, Any]:
    contract = load_config().get('contract') or {}
    if not isinstance(contract, dict):
        fail('contract 必须为对象')
    return contract


def surfaces() -> dict[str, dict[str, Any]]:
    raw = load_config().get('surfaces') or {}
    if not isinstance(raw, dict):
        fail('surfaces 必须为对象')
    return {str(key): value for key, value in raw.items() if isinstance(value, dict)}


def surface_info(surface_id: str) -> dict[str, Any]:
    info = surfaces().get(surface_id)
    if info is None:
        fail(f'未知 surface：{surface_id}')
    return info


def parse_args(argv: list[str]) -> dict[str, Any]:
    opts: dict[str, Any] = {'surface': ''}
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in {'-h', '--help'}:
            opts['help'] = True
            index += 1
            continue
        if not arg.startswith('--'):
            fail(f'未知参数：{arg}')
        index += 1
        if index >= len(argv):
            fail(f'{arg} 缺少参数值')
        value = argv[index]
        index += 1
        match arg:
            case '--surface':
                opts['surface'] = value
            case _:
                fail(f'未知参数：{arg}')
    return opts


def render_index() -> str:
    lines = ['部署镜像治理入口', '', '- contract: 部署镜像合同角色 / 在线离线准备 / 状态命令']
    for surface_id, info in surfaces().items():
        lines.append(f'- {surface_id}: {str(info.get("title") or surface_id).strip()}')
    return '\n'.join(lines)


def render_contract() -> str:
    """渲染部署镜像治理合同，镜像角色直接从 runtime source strategy 派生。"""
    info = contract_info()
    lines = [
        f"title: {str(info.get('title') or 'contract').strip()}",
        f"purpose: {str(info.get('purpose') or '').strip()}",
        'deployment_image_roles:',
    ]
    for role in deployment_image_roles(ROOT_DIR):
        lines.append(f'  - {role.env_key} | {role.label} | {role.scope or role.role}')
    lines.append('compose_runtime_roles:')
    for role in runtime_service_image_roles(ROOT_DIR):
        lines.append(f'  - {role.env_key} | {role.label} | {role.compose_selector}')
    source_info = info.get('image_role_source') if isinstance(info.get('image_role_source'), dict) else {}
    if source_info:
        lines.append('image_role_source:')
        for key, value in source_info.items():
            lines.append(f'  - {key}: {str(value).strip()}')
    for key in ('status_commands', 'online_prepare_commands', 'offline_prepare_commands', 'notes'):
        values = [str(v).strip() for v in list(info.get(key) or []) if str(v).strip()]
        lines.append(f'{key}:')
        for value in values:
            lines.append(f'  - {value}')
    return '\n'.join(lines)


def render_entry(surface_id: str) -> str:
    info = surface_info(surface_id)
    lines = [
        f'id: {surface_id}',
        f'title: {str(info.get("title") or surface_id).strip()}',
        f'purpose: {str(info.get("purpose") or "").strip()}',
        f'trigger: {str(info.get("trigger") or "").strip()}',
        f'entry_command: {str(info.get("entry_command") or "").strip()}',
        'next_steps:',
    ]
    for step in list(info.get('next_steps') or []):
        lines.append(f'  - {str(step).strip()}')
    notes = [str(item).strip() for item in list(info.get('notes') or []) if str(item).strip()]
    if notes:
        lines.append('notes:')
        for note in notes:
            lines.append(f'  - {note}')
    return '\n'.join(lines)


def render_next_steps(surface_id: str) -> str:
    return '\n'.join(str(item).strip() for item in list(surface_info(surface_id).get('next_steps') or []) if str(item).strip())


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        fail('缺少子命令；当前支持 show-index / show-contract / show-entry / next-steps')
    command = args.pop(0)
    opts = parse_args(args)
    if opts.get('help'):
        sys.stdout.write(usage_block(
            canonical_cli_command('images', 'governance-surface', 'show-index'),
            canonical_cli_command('images', 'governance-surface', 'show-contract'),
            canonical_cli_command('images', 'governance-surface', 'show-entry') + ' --surface <id>',
            canonical_cli_command('images', 'governance-surface', 'next-steps') + ' --surface <id>',
            title='用法：',
        ))
        return 0
    if command == 'show-index':
        sys.stdout.write(render_index() + '\n')
        return 0
    if command == 'show-contract':
        sys.stdout.write(render_contract() + '\n')
        return 0
    if command == 'show-entry':
        if not opts['surface']:
            fail('show-entry 缺少 --surface')
        sys.stdout.write(render_entry(opts['surface']) + '\n')
        return 0
    if command == 'next-steps':
        if not opts['surface']:
            fail('next-steps 缺少 --surface')
        sys.stdout.write(render_next_steps(opts['surface']) + '\n')
        return 0
    fail(f'未知子命令：{command}')

if __name__ == '__main__':
    raise SystemExit(main())
