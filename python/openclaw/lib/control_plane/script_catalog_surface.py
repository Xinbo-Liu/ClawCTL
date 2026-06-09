#!/usr/bin/env python3
"""scripts 分组与入口面统一控制面。"""
from __future__ import annotations

import sys
from typing import Any, NoReturn

from openclaw.lib.cli.examples import canonical_cli_command, usage_block
from openclaw.lib.repo.static_truth import read_repo_contract_json, repo_contract_relpath

VISIBILITIES = ('default_entrypoint', 'supplemental_entrypoint', 'internal_support')


def fail(message: str, code: int = 2) -> NoReturn:
    sys.stderr.write(f'[script_catalog_surface][FAIL] {message}\n')
    raise SystemExit(code)


def load_config() -> dict[str, Any]:
    payload = read_repo_contract_json('governance.script_catalog_surface')
    if not isinstance(payload, dict):
        fail(f'{repo_contract_relpath("governance.script_catalog_surface")} 顶层必须为对象')
    return payload


def groups() -> list[dict[str, Any]]:
    raw = load_config().get('groups') or []
    return [row for row in raw if isinstance(row, dict)]


def parse_args(argv: list[str]) -> dict[str, str]:
    opts = {'group': '', 'level': ''}
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in {'-h', '--help'}:
            opts['help'] = '1'
            index += 1
            continue
        if not arg.startswith('--'):
            fail(f'未知参数：{arg}')
        index += 1
        if index >= len(argv):
            fail(f'{arg} 缺少参数值')
        value = argv[index]
        index += 1
        if arg == '--group':
            opts['group'] = value
        elif arg == '--level':
            opts['level'] = value
        else:
            fail(f'未知参数：{arg}')
    return opts


def render_index() -> str:
    lines = ['script catalog surface 入口', '']
    for row in groups():
        lines.append(f"- {row.get('id')}: {str(row.get('title') or '').strip()}")
    lines.append('')
    lines.append('visibility: default_entrypoint / supplemental_entrypoint / internal_support')
    return '\n'.join(lines)


def render_group(group_id: str) -> str:
    row = next((item for item in groups() if str(item.get('id') or '').strip() == str(group_id).strip()), None)
    if row is None:
        fail(f'未知 group：{group_id}')
    lines = [
        f"id: {row.get('id')}",
        f"title: {str(row.get('title') or '').strip()}",
        f"purpose: {str(row.get('purpose') or '').strip()}",
        'files:',
    ]
    for file in list(row.get('files') or []):
        if not isinstance(file, dict):
            continue
        visibility = str(file.get('visibility') or '').strip() or 'unclassified'
        lines.append(f"  - {file.get('name')} [{visibility}]: {str(file.get('summary') or '').strip()}")
    return '\n'.join(lines)


def render_level(level: str) -> str:
    normalized = str(level).strip()
    if normalized not in VISIBILITIES:
        fail(f'未知 visibility：{level}')
    rows: list[str] = []
    for group in groups():
        group_id = str(group.get('id') or '').strip()
        for file in group.get('files') or []:
            if not isinstance(file, dict):
                continue
            if str(file.get('visibility') or '').strip() != normalized:
                continue
            rows.append(f"scripts/{group_id}/{str(file.get('name') or '').strip()}")
    return '\n'.join(row for row in rows if row.strip())


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        fail('缺少子命令；当前支持 show-index / show-group / show-level')
    command = args.pop(0)
    opts = parse_args(args)
    if opts.get('help'):
        sys.stdout.write(usage_block(
            canonical_cli_command('control-plane', 'scripts', 'show-index'),
            canonical_cli_command('control-plane', 'scripts', 'show-group') + ' --group <group_id>',
            canonical_cli_command('control-plane', 'scripts', 'show-level') + ' --level <default_entrypoint|supplemental_entrypoint|internal_support>',
            title='用法：',
        ))
        return 0
    if command == 'show-index':
        sys.stdout.write(render_index() + '\n')
        return 0
    if command == 'show-group':
        if not opts['group']:
            fail('show-group 缺少 --group')
        sys.stdout.write(render_group(opts['group']) + '\n')
        return 0
    if command == 'show-level':
        if not opts['level']:
            fail('show-level 缺少 --level')
        sys.stdout.write(render_level(opts['level']) + '\n')
        return 0
    fail(f'未知子命令：{command}')

if __name__ == '__main__':
    raise SystemExit(main())
