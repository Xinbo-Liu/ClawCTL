#!/usr/bin/env python3
"""运行态路径治理入口。"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, NoReturn

from openclaw.control_plane.governance_surfaces import load_path_entrypoints_surface
from openclaw.lib.cli import CliError, FlagSpec, parse_typed_flag_args
from openclaw.lib.cli.examples import canonical_cli_command
from openclaw.lib.repo.contracts import repo_contract_path, repo_contract_relpath
from openclaw.lib.repo.layout import resolve_default_runtime_control_plane_service_config_path, resolve_repo_root
from openclaw.lib.runtime.resolver_loader import require_path_resolver
from openclaw.runtime import path_resolve_cli
from openclaw.runtime.generated_paths.rendering import check_generated_outputs, render_generated_outputs

ROOT_DIR = resolve_repo_root(Path(__file__))
CONFIG_PATH = repo_contract_path('governance.path_entrypoints')
RUNTIME_PATHS_COMMAND = canonical_cli_command('runtime', 'paths')


def fail(message: str, code: int = 2) -> NoReturn:
    sys.stderr.write(f'[runtime_path_surface][FAIL] {message}\n')
    raise SystemExit(code)


def read_config(*, config_path: Path | None = None) -> dict[str, Any]:
    payload = load_path_entrypoints_surface(CONFIG_PATH, config_path=config_path)
    if not isinstance(payload, dict):
        fail(f'{repo_contract_relpath("governance.path_entrypoints")} top-level must be an object')
    return payload


def usage() -> str:
    return (
        'Usage:\n'
        f'  {RUNTIME_PATHS_COMMAND} resolve <entry_id> [--view <view>] [--abs-host] [--env-file <path>] [--config-path <path>]\n'
        f'  {RUNTIME_PATHS_COMMAND} show-entry <entry_id> [--config-path <path>]\n'
        f'  {RUNTIME_PATHS_COMMAND} show-index [--config-path <path>]\n'
        f'  {RUNTIME_PATHS_COMMAND} check-generated [--repo-root <path>] [--config-path <path>]\n'
        f'  {RUNTIME_PATHS_COMMAND} render-generated [--repo-root <path>] [--config-path <path>]\n'
    )


def cmd_resolve(argv: list[str]) -> int:
    return int(path_resolve_cli.main(argv) or 0)


def parse_repo_root_arg(argv: list[str]) -> tuple[Path, Path]:
    if any(arg in {'-h', '--help'} for arg in argv):
        sys.stdout.write(usage())
        raise SystemExit(0)
    try:
        values, positionals = parse_typed_flag_args(
            argv,
            specs={
                'repo-root': FlagSpec(kind='path', dest='repo_root', default=ROOT_DIR),
                'config-path': FlagSpec(kind='path', dest='config_path', default=None),
            },
        )
    except CliError as exc:
        fail(str(exc))
    if positionals:
        fail(f'unknown argument: {positionals[0]}')
    repo_root = values['repo_root']
    config_path = values['config_path']
    return repo_root, (config_path or resolve_default_runtime_control_plane_service_config_path(repo_root))


def cmd_check_generated(argv: list[str]) -> int:
    repo_root, config_path = parse_repo_root_arg(argv)
    resolver = require_path_resolver(repo_root=repo_root, config_path=config_path)
    return int(check_generated_outputs(repo_root, resolver, config_path))


def cmd_render_generated(argv: list[str]) -> int:
    repo_root, config_path = parse_repo_root_arg(argv)
    resolver = require_path_resolver(repo_root=repo_root, config_path=config_path)
    render_generated_outputs(repo_root, resolver, config_path)
    sys.stdout.write(
        '[runtime_path_surface] rendered runtime path artifacts under '
        f"{resolver.absolute_host_path('gateway_host_state_dir')} / "
        f"{resolver.absolute_host_path('control_plane_host_state_dir')}\n"
    )
    return 0


def cmd_show_entry(argv: list[str]) -> int:
    if not argv:
        fail('show-entry missing entry_id')
    entry_id = argv.pop(0)
    try:
        values, positionals = parse_typed_flag_args(
            argv,
            specs={
                'config-path': FlagSpec(kind='path', dest='config_path', default=None),
            },
        )
    except CliError as exc:
        fail(str(exc))
    if positionals:
        fail(f'unknown argument: {positionals[0]}')
    config_path = values['config_path']
    resolver = require_path_resolver(
        repo_root=ROOT_DIR,
        config_path=config_path or resolve_default_runtime_control_plane_service_config_path(ROOT_DIR),
    )
    try:
        entry = resolver.resolve_entry(entry_id)
    except KeyError as exc:
        fail(str(exc))
    sys.stdout.write(json.dumps(entry, ensure_ascii=False, indent=2) + '\n')
    return 0


def cmd_show_index(argv: list[str]) -> int:
    try:
        values, positionals = parse_typed_flag_args(
            argv,
            specs={
                'config-path': FlagSpec(kind='path', dest='config_path', default=None),
            },
        )
    except CliError as exc:
        fail(str(exc))
    if positionals:
        fail(f'unknown argument: {positionals[0]}')
    config_path = values['config_path']
    payload = read_config(config_path=config_path)
    entries = payload.get('common_entries') or []
    resolver = require_path_resolver(
        repo_root=ROOT_DIR,
        config_path=config_path or resolve_default_runtime_control_plane_service_config_path(ROOT_DIR),
    )
    rows: list[dict[str, Any]] = []
    for item in entries:
        entry_id = str(item.get('entry_id') or '').strip()
        if not entry_id:
            continue
        try:
            entry = resolver.resolve_entry(entry_id)
        except KeyError as exc:
            fail(str(exc))
        row: dict[str, Any] = {
            'entry_id': entry_id,
            'title': item.get('title') or '',
            'description': item.get('description') or '',
        }
        for view in resolver.internal_views:
            row[view] = (entry.get('paths') or {}).get(view)
        rows.append(row)
    sys.stdout.write(json.dumps({'views': list(resolver.internal_views), 'entries': rows}, ensure_ascii=False, indent=2) + '\n')
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {'-h', '--help'}:
        sys.stdout.write(usage())
        return 0
    command = args.pop(0)
    if command == 'resolve':
        return cmd_resolve(args)
    if command == 'show-entry':
        return cmd_show_entry(args)
    if command == 'show-index':
        return cmd_show_index(args)
    if command == 'check-generated':
        return cmd_check_generated(args)
    if command == 'render-generated':
        return cmd_render_generated(args)
    fail(f'unknown command: {command}')


if __name__ == '__main__':
    raise SystemExit(main())
