#!/usr/bin/env python3
"""Diagnostic surface with extension-aware lookup."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, NoReturn

from openclaw.control_plane.extensions.ownership import filter_rows_by_extension, resolve_owned_row
from openclaw.control_plane.governance_surfaces import load_diagnostic_surface
from openclaw.lib.cli.examples import canonical_cli_command, usage_block


def fail(message: str, code: int = 2) -> NoReturn:
    sys.stderr.write(f'[diagnostic_surface][FAIL] {message}\n')
    raise SystemExit(code)


def load_config(*, config_path: Path | None = None) -> dict[str, Any]:
    payload = load_diagnostic_surface(config_path=config_path)
    if not isinstance(payload, dict):
        fail('diagnostic_surface.json 顶层必须为对象')
    return payload


def diagnostics() -> dict[str, Any]:
    raw = load_config().get('diagnostics') or {}
    if not isinstance(raw, dict):
        fail('diagnostics 必须为对象')
    return raw


def actions() -> dict[str, Any]:
    raw = load_config().get('actions') or {}
    if not isinstance(raw, dict):
        fail('actions 必须为对象')
    return raw


def reasons() -> dict[str, Any]:
    raw = load_config().get('reasons') or {}
    if not isinstance(raw, dict):
        fail('reasons 必须为对象')
    return raw


def blocking_groups(*, config_path: Path | None = None, extension_id: str | None = None) -> list[dict[str, Any]]:
    payload = load_config(config_path=config_path)
    raw = payload.get('diagnostics') if isinstance(payload.get('diagnostics'), dict) else {}
    rows = raw.get('blockingGroups') if isinstance(raw, dict) else []
    return filter_rows_by_extension([row for row in list(rows or []) if isinstance(row, dict)], extension_id)


def source_groups(*, config_path: Path | None = None, extension_id: str | None = None) -> list[dict[str, Any]]:
    payload = load_config(config_path=config_path)
    raw = payload.get('diagnostics') if isinstance(payload.get('diagnostics'), dict) else {}
    rows = raw.get('sourceDiagnosisGroups') if isinstance(raw, dict) else []
    return filter_rows_by_extension([row for row in list(rows or []) if isinstance(row, dict)], extension_id)


def action_rows(*, config_path: Path | None = None, extension_id: str | None = None) -> list[dict[str, Any]]:
    payload = load_config(config_path=config_path)
    raw = payload.get('actions') if isinstance(payload.get('actions'), dict) else {}
    rows = raw.get('actions') if isinstance(raw, dict) else []
    return filter_rows_by_extension([row for row in list(rows or []) if isinstance(row, dict)], extension_id)


def reason_group(name: str, *, config_path: Path | None = None, extension_id: str | None = None) -> list[dict[str, Any]]:
    payload = load_config(config_path=config_path)
    raw = payload.get('reasons') if isinstance(payload.get('reasons'), dict) else {}
    rows = raw.get(name) if isinstance(raw, dict) else []
    if not isinstance(rows, list):
        fail(f'reasons.{name} 必须为数组')
    return filter_rows_by_extension([row for row in rows if isinstance(row, dict)], extension_id)


def parse_args(argv: list[str]) -> dict[str, Any]:
    opts: dict[str, Any] = {'code': '', 'action': '', 'group': '', 'extension': '', 'config_path': ''}
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
        if arg == '--code':
            opts['code'] = value
        elif arg == '--action':
            opts['action'] = value
        elif arg == '--group':
            opts['group'] = value
        elif arg == '--extension':
            opts['extension'] = value
        elif arg == '--config-path':
            opts['config_path'] = value
        else:
            fail(f'未知参数：{arg}')
    return opts


def render_index(*, config_path: Path | None = None, extension_id: str | None = None) -> str:
    lines = ['诊断参考入口', '', 'blocking_issue.code groups:']
    for row in blocking_groups(config_path=config_path, extension_id=extension_id):
        title = str(row.get('title') or '').strip()
        owner = str(row.get('extensionId') or '').strip()
        owner_suffix = f' [{owner}]' if owner else ''
        lines.append(f'- {title}{owner_suffix}')
    lines.extend(['', 'source diagnosis code:'])
    for row in source_groups(config_path=config_path, extension_id=extension_id):
        code = str(row.get('code') or '').strip()
        owner = str(row.get('extensionId') or '').strip()
        owner_suffix = f' [{owner}]' if owner else ''
        lines.append(f'- {code}{owner_suffix}')
    lines.extend(['', 'recommended_action:'])
    for row in action_rows(config_path=config_path, extension_id=extension_id):
        action = str(row.get('action') or '').strip()
        owner = str(row.get('extensionId') or '').strip()
        owner_suffix = f' [{owner}]' if owner else ''
        lines.append(f'- {action}{owner_suffix}')
    lines.extend(['', 'reason groups:', '- routeHintReasons', '- manualVerifyTaskReasons', '- manualVerifyResultReasons', '- manualVerifyBlockingReasons'])
    return '\n'.join(lines)


def render_blocking(code: str, *, config_path: Path | None = None, extension_id: str | None = None) -> str:
    wanted = str(code).strip()
    matches = []
    for row in blocking_groups(config_path=config_path, extension_id=extension_id):
        codes = [str(item).strip() for item in list(row.get('blockingCodes') or [])]
        if wanted in codes:
            matches.append(row)
    if not matches:
        fail(f'未知 blocking code：{wanted}')
    if len(matches) > 1:
        owners = sorted({str(row.get('extensionId') or '<base>').strip() for row in matches})
        fail(f'ambiguous blocking code: {wanted} ({", ".join(owners)})')
    row = matches[0]
    lines = [
        f'blocking_code: {wanted}',
        f'extension: {str(row.get("extensionId") or "<base>").strip()}',
        f'title: {str(row.get("title") or "").strip()}',
        f'recommended_agent: {str(row.get("recommendedAgent") or "").strip()}',
        f'recommended_action: {str(row.get("recommendedAction") or "").strip()}',
        'runbook:',
    ]
    for item in list(row.get('runbook') or []):
        lines.append(f'  - {str(item).rstrip()}')
    return '\n'.join(lines)


def render_action(action: str, *, config_path: Path | None = None, extension_id: str | None = None) -> str:
    wanted = str(action).strip()
    try:
        row = resolve_owned_row(
            action_rows(config_path=config_path, extension_id=extension_id),
            wanted,
            extension_id=extension_id,
            id_key='action',
            label='diagnostic action',
        )
    except KeyError as exc:
        fail(str(exc))
    except ValueError as exc:
        fail(str(exc))
    lines = [
        f'action: {wanted}',
        f'extension: {str(row.get("extensionId") or "<base>").strip()}',
        f'title: {str(row.get("title") or "").strip()}',
        f'meaning: {str(row.get("meaning") or "").strip()}',
        'typical_agents:',
    ]
    for item in list(row.get('typicalAgents') or []):
        lines.append(f'  - {str(item).rstrip()}')
    return '\n'.join(lines)


def render_reason_group(name: str, *, config_path: Path | None = None, extension_id: str | None = None) -> str:
    rows = reason_group(name, config_path=config_path, extension_id=extension_id)
    lines = [f'group: {name}', 'codes:']
    for row in rows:
        lines.append(f'  - {str(row.get("code") or "").strip()}: {str(row.get("title") or "").strip()} [{str(row.get("extensionId") or "<base>").strip()}]')
    return '\n'.join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        fail('缺少子命令；当前支持 show-index / show-blocking / show-action / show-reasons')
    command = args.pop(0)
    opts = parse_args(args)
    if opts.get('help'):
        sys.stdout.write(usage_block(
            canonical_cli_command('control-plane', 'diagnostics', 'show-index') + ' [--extension <id>] [--config-path <service.json>]',
            canonical_cli_command('control-plane', 'diagnostics', 'show-blocking') + ' --code <blocking_code> [--extension <id>] [--config-path <service.json>]',
            canonical_cli_command('control-plane', 'diagnostics', 'show-action') + ' --action <recommended_action> [--extension <id>] [--config-path <service.json>]',
            canonical_cli_command('control-plane', 'diagnostics', 'show-reasons') + ' --group <routeHintReasons|manualVerifyTaskReasons|manualVerifyResultReasons|manualVerifyBlockingReasons> [--extension <id>] [--config-path <service.json>]',
            title='用法：',
        ))
        return 0
    extension_id = str(opts.get('extension') or '').strip() or None
    config_path = Path(str(opts.get('config_path') or '')).resolve() if str(opts.get('config_path') or '').strip() else None
    if command == 'show-index':
        sys.stdout.write(render_index(config_path=config_path, extension_id=extension_id) + '\n')
        return 0
    if command == 'show-blocking':
        if not opts['code']:
            fail('show-blocking 缺少 --code')
        sys.stdout.write(render_blocking(opts['code'], config_path=config_path, extension_id=extension_id) + '\n')
        return 0
    if command == 'show-action':
        if not opts['action']:
            fail('show-action 缺少 --action')
        sys.stdout.write(render_action(opts['action'], config_path=config_path, extension_id=extension_id) + '\n')
        return 0
    if command == 'show-reasons':
        if not opts['group']:
            fail('show-reasons 缺少 --group')
        sys.stdout.write(render_reason_group(opts['group'], config_path=config_path, extension_id=extension_id) + '\n')
        return 0
    fail(f'未知子命令：{command}')


if __name__ == '__main__':
    raise SystemExit(main())
