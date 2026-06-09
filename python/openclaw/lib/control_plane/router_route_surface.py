#!/usr/bin/env python3
"""Router route reference surface with extension-aware lookup."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, NoReturn

from openclaw.control_plane.extensions.ownership import filter_rows_by_extension, resolve_owned_row
from openclaw.control_plane.governance_surfaces import load_router_route_surface
from openclaw.lib.cli.examples import canonical_cli_command, usage_block


def fail(message: str, code: int = 2) -> NoReturn:
    sys.stderr.write(f'[router_route_surface][FAIL] {message}\n')
    raise SystemExit(code)


def load_config(*, config_path: Path | None = None) -> dict[str, Any]:
    payload = load_router_route_surface(config_path=config_path)
    if not isinstance(payload, dict):
        fail('router_route_surface.json 顶层必须为对象')
    return payload


def parse_args(argv: list[str]) -> dict[str, str]:
    opts = {'route': '', 'target': '', 'group': '', 'extension': '', 'config_path': ''}
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
        if arg == '--route':
            opts['route'] = value
        elif arg == '--target':
            opts['target'] = value
        elif arg == '--group':
            opts['group'] = value
        elif arg == '--extension':
            opts['extension'] = value
        elif arg == '--config-path':
            opts['config_path'] = value
        else:
            fail(f'未知参数：{arg}')
    return opts


def explicit_routes(*, config_path: Path | None = None, extension_id: str | None = None) -> list[dict[str, Any]]:
    rows = load_config(config_path=config_path).get('explicitRoutes') or []
    return filter_rows_by_extension([row for row in rows if isinstance(row, dict)], extension_id)


def automatic_routes(*, config_path: Path | None = None, extension_id: str | None = None) -> list[dict[str, Any]]:
    rows = load_config(config_path=config_path).get('automaticRoutes') or []
    return filter_rows_by_extension([row for row in rows if isinstance(row, dict)], extension_id)


def health_rule_rows(*, config_path: Path | None = None, extension_id: str | None = None) -> list[dict[str, Any]]:
    rows = load_config(config_path=config_path).get('healthAwareRuleRows') or []
    return filter_rows_by_extension([row for row in rows if isinstance(row, dict)], extension_id)


def health_rules(*, config_path: Path | None = None, extension_id: str | None = None) -> list[str]:
    return [str(item.get('text') or '').strip() for item in health_rule_rows(config_path=config_path, extension_id=extension_id) if str(item.get('text') or '').strip()]


def render_index(*, config_path: Path | None = None, extension_id: str | None = None) -> str:
    cfg = load_config(config_path=config_path)
    lines = ['router 路由参考入口', '']
    lines.extend([
        f"heading: {str(cfg.get('heading') or '').strip()}",
        f"description: {str(cfg.get('description') or '').strip()}",
        '',
        'explicitRoutes:',
    ])
    for row in explicit_routes(config_path=config_path, extension_id=extension_id):
        owner = str(row.get('extensionId') or '').strip()
        owner_suffix = f' [{owner}]' if owner else ''
        lines.append(f"- {row.get('route')}: {row.get('target')}{owner_suffix}")
    lines.append('')
    lines.append('automaticTargets:')
    for row in automatic_routes(config_path=config_path, extension_id=extension_id):
        owner = str(row.get('extensionId') or '').strip()
        owner_suffix = f' [{owner}]' if owner else ''
        lines.append(f"- {row.get('target')}{owner_suffix}")
    lines.append('')
    lines.append('groups: explicit / automatic / health')
    return '\n'.join(lines)


def render_route(route: str, *, config_path: Path | None = None, extension_id: str | None = None) -> str:
    wanted = str(route).strip()
    try:
        row = resolve_owned_row(
            [item for item in load_config(config_path=config_path).get('explicitRoutes') or [] if isinstance(item, dict)],
            wanted,
            extension_id=extension_id,
            id_key='route',
            label='router route',
        )
    except KeyError as exc:
        fail(str(exc))
    except ValueError as exc:
        fail(str(exc))
    lines = [
        f"route: {row.get('route')}",
        f"extension: {str(row.get('extensionId') or '<base>').strip()}",
        f"target: {row.get('target')}",
        f"summary: {str(row.get('summary') or '').strip()}",
        'notes:',
    ]
    for note in list(row.get('notes') or []):
        lines.append(f'  - {str(note).strip()}')
    return '\n'.join(lines)


def render_target(target: str, *, config_path: Path | None = None, extension_id: str | None = None) -> str:
    wanted = str(target).strip()
    try:
        row = resolve_owned_row(
            [item for item in load_config(config_path=config_path).get('automaticRoutes') or [] if isinstance(item, dict)],
            wanted,
            extension_id=extension_id,
            id_key='target',
            label='router automatic target',
        )
    except KeyError as exc:
        fail(str(exc))
    except ValueError as exc:
        fail(str(exc))
    return '\n'.join([
        f"target: {row.get('target')}",
        f"extension: {str(row.get('extensionId') or '<base>').strip()}",
        f"when: {str(row.get('when') or '').strip()}",
        f"action: {str(row.get('action') or '').strip()}",
    ])


def render_group(group: str, *, config_path: Path | None = None, extension_id: str | None = None) -> str:
    key = str(group).strip()
    if key == 'explicit':
        return '\n'.join(str(row.get('route') or '').strip() for row in explicit_routes(config_path=config_path, extension_id=extension_id))
    if key == 'automatic':
        return '\n'.join(str(row.get('target') or '').strip() for row in automatic_routes(config_path=config_path, extension_id=extension_id))
    if key == 'health':
        return '\n'.join(health_rules(config_path=config_path, extension_id=extension_id))
    fail(f'未知 group：{key}')


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        fail('缺少子命令；当前支持 show-index / show-route / show-target / show-group')
    command = args.pop(0)
    opts = parse_args(args)
    if opts.get('help'):
        sys.stdout.write(usage_block(
            canonical_cli_command('control-plane', 'routes', 'show-index') + ' [--extension <id>] [--config-path <service.json>]',
            canonical_cli_command('control-plane', 'routes', 'show-route') + ' --route <ROUTE:...> [--extension <id>] [--config-path <service.json>]',
            canonical_cli_command('control-plane', 'routes', 'show-target') + ' --target <agent_id> [--extension <id>] [--config-path <service.json>]',
            canonical_cli_command('control-plane', 'routes', 'show-group') + ' --group <explicit|automatic|health> [--extension <id>] [--config-path <service.json>]',
            title='用法：',
        ))
        return 0
    extension_id = str(opts.get('extension') or '').strip() or None
    config_path = Path(str(opts.get('config_path') or '')).resolve() if str(opts.get('config_path') or '').strip() else None
    if command == 'show-index':
        sys.stdout.write(render_index(config_path=config_path, extension_id=extension_id) + '\n')
        return 0
    if command == 'show-route':
        if not opts['route']:
            fail('show-route 缺少 --route')
        sys.stdout.write(render_route(opts['route'], config_path=config_path, extension_id=extension_id) + '\n')
        return 0
    if command == 'show-target':
        if not opts['target']:
            fail('show-target 缺少 --target')
        sys.stdout.write(render_target(opts['target'], config_path=config_path, extension_id=extension_id) + '\n')
        return 0
    if command == 'show-group':
        if not opts['group']:
            fail('show-group 缺少 --group')
        sys.stdout.write(render_group(opts['group'], config_path=config_path, extension_id=extension_id) + '\n')
        return 0
    fail(f'未知子命令：{command}')


if __name__ == '__main__':
    raise SystemExit(main())
