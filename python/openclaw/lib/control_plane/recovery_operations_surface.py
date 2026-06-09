#!/usr/bin/env python3
"""Recovery operations surface with extension-aware lookup."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, NoReturn

from openclaw.control_plane.extensions.ownership import filter_rows_by_extension, resolve_owned_row
from openclaw.control_plane.governance_surfaces import load_recovery_operations_surface
from openclaw.lib.cli.examples import canonical_cli_command, usage_block
from openclaw.lib.control_plane.object_families import get_family
from openclaw.lib.repo.layout import resolve_repo_root

ROOT_DIR = resolve_repo_root(Path(__file__))


def fail(message: str, code: int = 2) -> NoReturn:
    sys.stderr.write(f'[recovery_operations_surface][FAIL] {message}\n')
    raise SystemExit(code)


def load_config(*, config_path: Path | None = None) -> dict[str, Any]:
    payload = load_recovery_operations_surface(config_path=config_path)
    if not isinstance(payload, dict):
        fail('recovery_operations_surface.json 顶层必须为对象')
    return payload


def entries(*, config_path: Path | None = None, extension_id: str | None = None) -> list[dict[str, Any]]:
    rows = load_config(config_path=config_path).get('entries') or []
    if not isinstance(rows, list):
        fail('entries 必须为数组')
    return filter_rows_by_extension([row for row in rows if isinstance(row, dict)], extension_id)


def entry_info(entry_id: str, *, config_path: Path | None = None, extension_id: str | None = None) -> dict[str, Any]:
    try:
        return resolve_owned_row(
            [row for row in load_config(config_path=config_path).get('entries') or [] if isinstance(row, dict)],
            entry_id,
            extension_id=extension_id,
            id_key='id',
            label='recovery operation entry',
        )
    except KeyError as exc:
        fail(str(exc))
    except ValueError as exc:
        fail(str(exc))


def _string_list(value: Any) -> list[str]:
    return [str(item).strip() for item in list(value or []) if str(item).strip()]


def _example_rows(value: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in list(value or []):
        if not isinstance(item, dict):
            continue
        title = str(item.get('title') or '').strip()
        command = str(item.get('command') or '').rstrip()
        if not title or not command:
            continue
        rows.append({'title': title, 'command': command})
    return rows


def parse_args(argv: list[str]) -> dict[str, Any]:
    opts: dict[str, Any] = {'entry': '', 'extension': '', 'config_path': ''}
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
        elif arg == '--extension':
            opts['extension'] = value
        elif arg == '--config-path':
            opts['config_path'] = value
        else:
            fail(f'未知参数：{arg}')
    return opts


def render_index(*, config_path: Path | None = None, extension_id: str | None = None) -> str:
    lines = ['恢复动作入口', '']
    for info in entries(config_path=config_path, extension_id=extension_id):
        entry_id = str(info.get('id') or '').strip()
        owner = str(info.get('extensionId') or '').strip()
        owner_suffix = f' [{owner}]' if owner else ''
        lines.append(f'- {entry_id}: {str(info.get("title") or entry_id).strip()}{owner_suffix}')
    return '\n'.join(lines)


def render_entry(entry_id: str, *, config_path: Path | None = None, extension_id: str | None = None) -> str:
    info = entry_info(entry_id, config_path=config_path, extension_id=extension_id)
    lines = [
        f'id: {entry_id}',
        f'extension: {str(info.get("extensionId") or "<base>").strip()}',
        f'title: {str(info.get("title") or entry_id).strip()}',
        f'purpose: {str(info.get("purpose") or "").strip()}',
        f'entry_command: {str(info.get("entry_command") or "").strip()}',
        'steps:',
    ]
    for step in _string_list(info.get('steps')):
        lines.append(f'  - {step}')
    prerequisites = _string_list(info.get('prerequisites'))
    if prerequisites:
        lines.append('prerequisites:')
        for item in prerequisites:
            lines.append(f'  - {item}')
    examples = _example_rows(info.get('example_commands'))
    if examples:
        lines.append('example_commands:')
        for item in examples:
            lines.append(f'  - {item["title"]}: {item["command"]}')
    result_checks = _string_list(info.get('result_checks'))
    if result_checks:
        lines.append('result_checks:')
        for item in result_checks:
            lines.append(f'  - {item}')
    common_branches = _string_list(info.get('common_branches'))
    if common_branches:
        lines.append('common_branches:')
        for item in common_branches:
            lines.append(f'  - {item}')
    refs = _string_list(info.get('references'))
    if refs:
        lines.append('references:')
        for item in refs:
            lines.append(f'  - {item}')
    notes = _string_list(info.get('notes'))
    if notes:
        lines.append('notes:')
        for item in notes:
            lines.append(f'  - {item}')
    return '\n'.join(lines)


def render_commands(entry_id: str, *, config_path: Path | None = None, extension_id: str | None = None) -> str:
    info = entry_info(entry_id, config_path=config_path, extension_id=extension_id)
    return '\n'.join(item for item in _string_list(info.get('steps')))


def render_decision_map(*, config_path: Path | None = None, extension_id: str | None = None) -> str:
    payload = load_config(config_path=config_path)
    rows = payload.get('decision_map') or []
    if not isinstance(rows, list):
        fail('decision_map 必须为数组')
    filtered = filter_rows_by_extension([row for row in rows if isinstance(row, dict)], extension_id)
    lines = ['goal | action | network | latest_default | reference | extension', '--- | --- | --- | --- | --- | ---']
    for row in filtered:
        lines.append(
            f"{str(row.get('goal') or '').strip()} | {str(row.get('action') or '').strip()} | {str(row.get('network') or '').strip()} | {str(row.get('latest_default') or '').strip()} | {str(row.get('reference') or '').strip()} | {str(row.get('extensionId') or '<base>').strip()}"
        )
    return '\n'.join(lines)


def render_logs(*, config_path: Path | None = None, extension_id: str | None = None) -> str:
    family = get_family('recovery_logs', ROOT_DIR, config_path=config_path, extension_id=extension_id)
    lines = []
    for item in family.get('entries') or []:
        logical = str(item.get('id') or '').strip()
        host_view = str(item.get('resolved_path') or '').strip()
        usage = str(item.get('usage') or '').strip()
        lines.append(f'- {logical}: {host_view} -> {usage}')
    return '\n'.join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        fail('缺少子命令；当前支持 show-index / show-entry / commands / decision-map / logs')
    command = args.pop(0)
    opts = parse_args(args)
    if opts.get('help'):
        sys.stdout.write(usage_block(
            canonical_cli_command('control-plane', 'recovery', 'show-index') + ' [--extension <id>] [--config-path <service.json>]',
            canonical_cli_command('control-plane', 'recovery', 'show-entry') + ' --entry <id> [--extension <id>] [--config-path <service.json>]',
            canonical_cli_command('control-plane', 'recovery', 'commands') + ' --entry <id> [--extension <id>] [--config-path <service.json>]',
            canonical_cli_command('control-plane', 'recovery', 'decision-map') + ' [--extension <id>] [--config-path <service.json>]',
            canonical_cli_command('control-plane', 'recovery', 'logs') + ' [--extension <id>] [--config-path <service.json>]',
            title='用法：',
        ))
        return 0
    extension_id = str(opts.get('extension') or '').strip() or None
    config_path = Path(str(opts.get('config_path') or '')).resolve() if str(opts.get('config_path') or '').strip() else None
    if command == 'show-index':
        sys.stdout.write(render_index(config_path=config_path, extension_id=extension_id) + '\n')
        return 0
    if command == 'show-entry':
        if not opts['entry']:
            fail('show-entry 缺少 --entry')
        sys.stdout.write(render_entry(opts['entry'], config_path=config_path, extension_id=extension_id) + '\n')
        return 0
    if command == 'commands':
        if not opts['entry']:
            fail('commands 缺少 --entry')
        sys.stdout.write(render_commands(opts['entry'], config_path=config_path, extension_id=extension_id) + '\n')
        return 0
    if command == 'decision-map':
        sys.stdout.write(render_decision_map(config_path=config_path, extension_id=extension_id) + '\n')
        return 0
    if command == 'logs':
        sys.stdout.write(render_logs(config_path=config_path, extension_id=extension_id) + '\n')
        return 0
    fail(f'未知子命令：{command}')


if __name__ == '__main__':
    raise SystemExit(main())
