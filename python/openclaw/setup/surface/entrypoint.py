#!/usr/bin/env python3
"""setup 一键入口控制面。"""
from __future__ import annotations

import sys
from typing import Any, NoReturn

from openclaw.lib.cli.examples import canonical_cli_command, usage_block
from openclaw.lib.repo.static_truth import read_repo_contract_json, repo_contract_relpath
from openclaw.setup.surface import deployment_baseline as deployment_baseline_surface

def fail(message: str, code: int = 2) -> NoReturn:
    sys.stderr.write(f'[setup_entrypoint_surface][FAIL] {message}\n')
    raise SystemExit(code)


def load_config() -> dict[str, Any]:
    payload = read_repo_contract_json('governance.setup_entrypoints')
    if not isinstance(payload, dict):
        fail(f'{repo_contract_relpath("governance.setup_entrypoints")} 顶层必须为对象')
    return payload


def entrypoints() -> dict[str, dict[str, Any]]:
    raw = load_config().get('entrypoints') or {}
    if not isinstance(raw, dict):
        fail('entrypoints 必须为对象')
    return {str(key): value for key, value in raw.items() if isinstance(value, dict)}


def entry_info(entry_id: str) -> dict[str, Any]:
    info = entrypoints().get(entry_id)
    if info is None:
        fail(f'未知 entry_id：{entry_id}')
    return info


def help_surface_contract() -> dict[str, Any]:
    raw = load_config().get('help_surface_contract') or {}
    if not isinstance(raw, dict):
        fail('help_surface_contract 必须为对象')
    return raw


def parse_args(argv: list[str]) -> dict[str, Any]:
    opts: dict[str, Any] = {'entry': ''}
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
        if arg == '--entry':
            opts['entry'] = value
        else:
            fail(f'未知参数：{arg}')
    return opts


def usage() -> str:
    return usage_block(
        canonical_cli_command('setup', 'surface', 'entrypoints', 'show-index'),
        canonical_cli_command('setup', 'surface', 'entrypoints', 'show-entry') + ' --entry <id>',
        canonical_cli_command('setup', 'surface', 'entrypoints', 'default-flow'),
        canonical_cli_command('setup', 'surface', 'entrypoints', 'show-help-contract'),
        title='用法：',
    )


def render_index() -> str:
    lines = ['setup 一键入口索引', '']
    for entry_id, info in entrypoints().items():
        lines.append(f'- {entry_id}: {str(info.get("title") or entry_id).strip()}')
    return '\n'.join(lines)


def render_entry(entry_id: str) -> str:
    info = entry_info(entry_id)
    lines = [
        f'id: {entry_id}',
        f'title: {str(info.get("title") or entry_id).strip()}',
        f'command: {str(info.get("command") or "").strip()}',
        f'purpose: {str(info.get("purpose") or "").strip()}',
        'when_to_use:',
    ]
    for item in list(info.get('when_to_use') or []):
        lines.append(f'  - {str(item).strip()}')
    boundaries = [str(item).strip() for item in list(info.get('boundaries') or []) if str(item).strip()]
    if boundaries:
        lines.append('boundaries:')
        for item in boundaries:
            lines.append(f'  - {item}')
    refs = [str(item).strip() for item in list(info.get('references') or []) if str(item).strip()]
    if refs:
        lines.append('references:')
        for item in refs:
            lines.append(f'  - {item}')
    return '\n'.join(lines)


def render_help_contract() -> str:
    contract = help_surface_contract()
    title = str(contract.get('title') or '帮助面与执行面边界').strip()
    lines = [title, '']
    for item in [str(item).strip() for item in list(contract.get('guarantees') or []) if str(item).strip()]:
        lines.append(f'- {item}')
    examples = [str(item).strip() for item in list(contract.get('command_examples') or []) if str(item).strip()]
    if examples:
        lines.extend(['', 'command_examples:'])
        for item in examples:
            lines.append(f'- {item}')
    notes = [str(item).strip() for item in list(contract.get('notes') or []) if str(item).strip()]
    if notes:
        lines.extend(['', 'notes:'])
        for item in notes:
            lines.append(f'- {item}')
    refs = [str(item).strip() for item in list(contract.get('references') or []) if str(item).strip()]
    if refs:
        lines.extend(['', 'references:'])
        for item in refs:
            lines.append(f'- {item}')
    return '\n'.join(lines)


def render_default_flow() -> str:
    baseline = deployment_baseline_surface.load_baseline()
    lines = [deployment_baseline_surface.default_flow_title(baseline), '']
    for index, item in enumerate(deployment_baseline_surface.default_flow_steps(baseline), start=1):
        lines.append(f"{index}. {item['command']}")
        lines.append(f"   - {item['purpose']}")
    notes = deployment_baseline_surface.default_flow_notes(baseline)
    if notes:
        lines.append('')
        lines.append('notes:')
        for item in notes:
            lines.append(f'- {item}')
    return '\n'.join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {'-h', '--help'}:
        sys.stdout.write(usage())
        return 0
    command = args.pop(0)
    opts = parse_args(args)
    if opts.get('help'):
        sys.stdout.write(usage())
        return 0
    if command == 'show-index':
        sys.stdout.write(render_index() + '\n')
        return 0
    if command == 'show-entry':
        if not opts['entry']:
            fail('show-entry 缺少 --entry')
        sys.stdout.write(render_entry(opts['entry']) + '\n')
        return 0
    if command == 'default-flow':
        sys.stdout.write(render_default_flow() + '\n')
        return 0
    if command == 'show-help-contract':
        sys.stdout.write(render_help_contract() + '\n')
        return 0
    fail(f'未知子命令：{command}')

if __name__ == '__main__':
    raise SystemExit(main())
