#!/usr/bin/env python3
"""OpenClaw 仓库级 Python CLI 总入口。"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import NoReturn

from openclaw import cli_registry
from openclaw.lib.repo.bootstrap import bootstrap_sys_path
from openclaw.lib.repo.layout import CONTROL_PLANE_CONFIG_ENV, CONTROL_PLANE_PROFILE_ENV, resolve_selected_control_plane_config_path
from openclaw.lib.runtime.execution import import_callable, run_module_main

HELP_FLAGS = {'-h', '--help'}
SELECTION_VALUE_FLAGS = {'--config-path', '--control-plane-profile'}
SELECTION_PREFIX_FLAGS = ('--config-path=', '--control-plane-profile=')


def fail(prefix: str, message: str, exit_code: int = 2) -> NoReturn:
    sys.stderr.write(f'[{prefix}][FAIL] {message}\n')
    raise SystemExit(exit_code)


def run_target(target: str, argv: list[str]) -> int:
    if ':' in target:
        module_name, func_name = target.split(':', 1)
        func = import_callable(module_name, func_name, RuntimeError, f'{module_name}:{func_name}')
        return int(func(list(argv)) or 0)
    return run_module_main(target, argv, RuntimeError, target)


def _config_path_hint(argv: list[str]) -> str | None:
    for idx, token in enumerate(argv):
        text = str(token or '').strip()
        if text == '--':
            return None
        if text.startswith('--config-path='):
            value = text.split('=', 1)[1].strip()
            return value if value else None
        if text == '--config-path' and idx + 1 < len(argv):
            value = str(argv[idx + 1] or '').strip()
            return value if value else None
    return None


def _config_path_from_env() -> Path | None:
    if not (str(os.environ.get(CONTROL_PLANE_CONFIG_ENV) or '').strip() or str(os.environ.get(CONTROL_PLANE_PROFILE_ENV) or '').strip()):
        return None
    try:
        return resolve_selected_control_plane_config_path(start_path=Path(__file__))
    except ValueError as exc:
        fail('openclaw_cli', str(exc), 2)


def _selection_path_hint(argv: list[str]) -> Path | None:
    config_path = _config_path_hint(argv)
    profile = ''
    idx = 0
    while idx < len(argv):
        token = str(argv[idx] or '').strip()
        if token == '--':
            break
        if token.startswith('--control-plane-profile='):
            profile = token.split('=', 1)[1].strip()
            break
        if token == '--control-plane-profile' and idx + 1 < len(argv):
            profile = str(argv[idx + 1] or '').strip()
            break
        idx += 1
    if not config_path and not profile:
        return _config_path_from_env()
    try:
        return resolve_selected_control_plane_config_path(
            config_path or None,
            control_plane_profile=profile or None,
            start_path=Path(__file__),
        )
    except ValueError as exc:
        fail('openclaw_cli', str(exc), 2)


def _consume_selection_arg(args: list[str]) -> tuple[list[str], list[str]] | None:
    if not args:
        return None
    token = str(args[0] or '').strip()
    if token in SELECTION_VALUE_FLAGS:
        if len(args) < 2:
            fail('openclaw_cli', f'{token} 缺少参数', 2)
        return [args[0], args[1]], args[2:]
    if any(token.startswith(prefix) for prefix in SELECTION_PREFIX_FLAGS):
        return [args[0]], args[1:]
    return None


def _print_tree_help(command_path: list[str], tree: cli_registry.CommandNode) -> None:
    path_text = ' '.join(command_path)
    command_label = f' {path_text}' if path_text else ''
    sys.stdout.write(f'usage: python -m openclaw.cli{command_label} <command> ...\n\n')
    sys.stdout.write('commands:\n')
    for name in sorted(tree.keys()):
        sys.stdout.write(f'  {name}\n')


def _resolve_command_target(
    tree: cli_registry.CommandNode,
    args: list[str],
    *,
    prefix: list[str] | None = None,
    selection_args: list[str] | None = None,
) -> tuple[str, list[str], list[str]]:
    command_path = [] if prefix is None else list(prefix)
    carried_selection_args = [] if selection_args is None else list(selection_args)
    if not args:
        supported = ' / '.join(sorted(tree.keys()))
        label = ' '.join(command_path) if command_path else 'root'
        fail('openclaw_cli', f'{label} 缺少子命令；当前支持 {supported}', 2)
    if args[0] in HELP_FLAGS:
        _print_tree_help(command_path, tree)
        raise SystemExit(0)
    consumed_selection = _consume_selection_arg(args)
    if consumed_selection is not None:
        consumed, remaining = consumed_selection
        return _resolve_command_target(
            tree,
            remaining,
            prefix=command_path,
            selection_args=[*carried_selection_args, *consumed],
        )
    command = args.pop(0)
    node = tree.get(command)
    if node is None:
        fail('openclaw_cli', f'未知命令：{" ".join([*command_path, command])}', 2)
    command_path.append(command)
    if isinstance(node, str):
        return node, [*carried_selection_args, *args], command_path
    return _resolve_command_target(
        node,
        args,
        prefix=command_path,
        selection_args=carried_selection_args,
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    config_path = _selection_path_hint(args)
    bootstrap_sys_path(Path(__file__), config_path=config_path)
    tree = cli_registry.root_command_tree(config_path)
    if args and args[0] in HELP_FLAGS:
        _print_tree_help([], tree)
        return 0
    if not args:
        supported = ' / '.join(cli_registry.supported_root_commands(config_path))
        fail('openclaw_cli', f'缺少命令；当前支持 {supported}', 2)
    target, remaining, _ = _resolve_command_target(tree, args)
    return run_target(target, remaining)


if __name__ == '__main__':
    raise SystemExit(main())
