#!/usr/bin/env python3
"""控制平面 grouped CLI。"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from openclaw import cli_registry
from openclaw.control_plane.cli_support.handlers import CliError, fail
from openclaw.control_plane.cli_support.registration.group_evidence import register_group_evidence_commands
from openclaw.control_plane.cli_support.registration.module_lifecycle import register_module_lifecycle_commands
from openclaw.control_plane.cli_support.registration.readonly_summary import register_readonly_summary_commands
from openclaw.control_plane.cli_support.registration.registry_validation import register_registry_validation_commands
from openclaw.control_plane.cli_support.registration.runtime_execution import register_runtime_execution_commands
from openclaw.control_plane.registry import control_plane_config_path
from openclaw.lib.repo.layout import CONTROL_PLANE_CONFIG_ENV, CONTROL_PLANE_PROFILE_ENV, resolve_selected_control_plane_config_path
from openclaw.lib.runtime.execution import run_module_main

HELP_FLAGS = {'-h', '--help'}
SELECTION_VALUE_FLAGS = {'--config-path', '--control-plane-profile'}
SELECTION_PREFIX_FLAGS = ('--config-path=', '--control-plane-profile=')


PUBLIC_GROUP_COMMANDS: dict[str, dict[str, str]] = {
    'validate': {
        'registry': 'validate-registry',
        'agent-control-plane': 'check-agent-control-plane-registry',
        'agent-assembly': 'check-agent-assembly-registry',
    },
    'summary': {
        'overview': 'summary',
        'jobs': 'jobs',
        'job': 'job',
        'agents': 'agents',
        'agent-groups': 'agent-groups',
        'agent-modules': 'agent-modules',
        'agent-module-pluggability': 'agent-module-pluggability',
        'skill-sets': 'skill-sets',
        'permission-policies': 'permission-policies',
        'toolsets': 'toolsets',
        'runtime-adapters': 'runtime-adapters',
        'implementations': 'implementations',
        'models': 'models',
        'targets': 'targets',
    },
    'module': {
        'attach': 'agent-module-attach',
        'detach': 'agent-module-detach',
        'prune': 'agent-module-prune',
        'drop': 'agent-module-drop',
        'job-surface-prune': 'job-surface-prune',
        'scaffold': 'scaffold-agent-module',
    },
    'evidence': {
        'agent-access-log': 'agent-access-log',
        'agent-group-access': 'agent-group-access',
        'agent-group-acceptance-bindings': 'agent-group-acceptance-bindings',
        'agent-group-release-gates': 'agent-group-release-gates',
        'export-agent-group-evidence': 'export-agent-group-evidence',
    },
    'runtime': {
        'resolve-job-command': 'resolve-job-command',
        'resolve-job-plan': 'resolve-job-plan',
        'resolve-target-operation': 'resolve-target-operation',
        'run-target-operation': 'run-target-operation',
        'run-agent-runtime': 'run-agent-runtime',
        'run-ledger-summary': 'run-ledger-summary',
        'due-preview': 'due-preview',
        'extension-env': 'extension-env',
    },
}


GROUP_REGISTRARS: dict[str, Callable[[argparse._SubParsersAction[argparse.ArgumentParser], str], None]] = {
    'validate': register_registry_validation_commands,
    'summary': register_readonly_summary_commands,
    'module': register_module_lifecycle_commands,
    'evidence': register_group_evidence_commands,
    'runtime': register_runtime_execution_commands,
}


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
        raise SystemExit(fail(str(exc), 2))


def _profile_hint(argv: list[str]) -> str:
    for idx, token in enumerate(argv):
        text = str(token or '').strip()
        if text == '--':
            return ''
        if text.startswith('--control-plane-profile='):
            return text.split('=', 1)[1].strip()
        if text == '--control-plane-profile' and idx + 1 < len(argv):
            return str(argv[idx + 1] or '').strip()
    return ''


def _selection_path_hint(argv: list[str]) -> Path | None:
    config_path = _config_path_hint(argv)
    profile = _profile_hint(argv)
    if not config_path and not profile:
        return _config_path_from_env()
    try:
        return resolve_selected_control_plane_config_path(
            config_path or None,
            control_plane_profile=profile or None,
            start_path=Path(__file__),
        )
    except ValueError as exc:
        raise SystemExit(fail(str(exc), 2))


def _default_config() -> str:
    return str(control_plane_config_path())


def build_group_parser(group: str) -> argparse.ArgumentParser:
    if group not in GROUP_REGISTRARS:
        raise KeyError(group)
    parser = argparse.ArgumentParser(prog=f'python -m openclaw.cli control-plane {group}')
    subparsers = parser.add_subparsers(dest='command', required=True)
    GROUP_REGISTRARS[group](subparsers, default_config=_default_config())
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='python -m openclaw.cli control-plane')
    subparsers = parser.add_subparsers(dest='namespace', required=True)
    for namespace, commands in PUBLIC_GROUP_COMMANDS.items():
        group_parser = subparsers.add_parser(namespace)
        group_subparsers = group_parser.add_subparsers(dest='command', required=True)
        for public_name in sorted(commands.keys()):
            group_subparsers.add_parser(public_name)
    return parser


def _consume_leading_selection_args(argv: list[str]) -> tuple[list[str], list[str]]:
    selection_args: list[str] = []
    remaining = list(argv)
    while remaining:
        token = str(remaining[0] or '').strip()
        if token in SELECTION_VALUE_FLAGS:
            if len(remaining) < 2:
                raise SystemExit(fail(f'{token} 缺少参数', 2))
            selection_args.extend([remaining[0], remaining[1]])
            remaining = remaining[2:]
            continue
        if any(token.startswith(prefix) for prefix in SELECTION_PREFIX_FLAGS):
            selection_args.append(remaining[0])
            remaining = remaining[1:]
            continue
        break
    return selection_args, remaining


def _print_group_help(group: str) -> None:
    public_commands = PUBLIC_GROUP_COMMANDS[group]
    print(f'usage: python -m openclaw.cli control-plane {group} [--config-path PATH | --control-plane-profile PROFILE] <command> ...')
    print()
    print('commands:')
    for public_name in sorted(public_commands.keys()):
        print(f'  {public_name}')


def _contains_help_flag(argv: list[str]) -> bool:
    for token in argv:
        if token == '--':
            return False
        if token in HELP_FLAGS:
            return True
    return False


def _print_public_leaf_help(group: str, public_command: str, flat_command: str) -> int:
    parser = build_group_parser(group)
    subparser_action = next(
        (action for action in parser._actions if isinstance(action, argparse._SubParsersAction)),
        None,
    )
    leaf_parser = subparser_action.choices.get(flat_command) if subparser_action is not None else None
    if leaf_parser is None:
        return 2
    leaf_parser.prog = f'python -m openclaw.cli control-plane {group} {public_command}'
    leaf_parser.print_help()
    return 0


def _run_group(group: str, argv: list[str]) -> int:
    public_commands = PUBLIC_GROUP_COMMANDS[group]
    if not argv:
        supported = ' / '.join(sorted(public_commands.keys()))
        return fail(f'control-plane {group} 缺少子命令；当前支持 {supported}', 2)
    if argv[0] in HELP_FLAGS:
        _print_group_help(group)
        return 0
    selection_args, command_args = _consume_leading_selection_args(argv)
    if not command_args:
        supported = ' / '.join(sorted(public_commands.keys()))
        return fail(f'control-plane {group} 缺少子命令；当前支持 {supported}', 2)
    if command_args[0] in HELP_FLAGS:
        _print_group_help(group)
        return 0
    public_command = command_args[0]
    flat_command = public_commands.get(public_command)
    if flat_command is None:
        return fail(f'未知 control-plane {group} 子命令：{public_command}', 2)
    if _contains_help_flag(command_args[1:]):
        return _print_public_leaf_help(group, public_command, flat_command)
    parser = build_group_parser(group)
    args = parser.parse_args([flat_command, *selection_args, *command_args[1:]])
    try:
        return int(args.func(args) or 0)
    except CliError as exc:
        return fail(str(exc), exc.exit_code)


def validate_entry(argv: list[str] | None = None) -> int:
    return _run_group('validate', list(sys.argv[1:] if argv is None else argv))


def summary_entry(argv: list[str] | None = None) -> int:
    return _run_group('summary', list(sys.argv[1:] if argv is None else argv))


def module_entry(argv: list[str] | None = None) -> int:
    return _run_group('module', list(sys.argv[1:] if argv is None else argv))


def evidence_entry(argv: list[str] | None = None) -> int:
    return _run_group('evidence', list(sys.argv[1:] if argv is None else argv))


def runtime_entry(argv: list[str] | None = None) -> int:
    return _run_group('runtime', list(sys.argv[1:] if argv is None else argv))


def extension_entry(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    config_path = _selection_path_hint(args)
    commands = cli_registry.control_plane_extension_commands(config_path)
    if not args or args[0] in HELP_FLAGS:
        supported = ' / '.join(sorted(commands.keys())) if commands else '<none>'
        print('usage: python -m openclaw.cli control-plane extension <command> ...')
        print()
        print(f'commands: {supported}')
        return 0 if args and args[0] in HELP_FLAGS else fail(f'control-plane extension 缺少子命令；当前支持 {supported}', 2)
    command = args.pop(0)
    target = commands.get(command)
    if target is None:
        return fail(f'未知 control-plane extension 子命令：{command}', 2)
    return run_module_main(target, args, RuntimeError, target)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    parsed = parser.parse_args(args[:2] if len(args) >= 2 else args)
    namespace = str(parsed.namespace)
    return _run_group(namespace, args[1:])


if __name__ == '__main__':
    raise SystemExit(main())
