#!/usr/bin/env python3
"""Dispatch operations surface with extension-aware lookup."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NoReturn

from openclaw.control_plane.manifest_fields import (
    DISPATCH_PROVIDER_REGISTRY_PATHS_KEY,
    DISPATCH_TARGET_REGISTRY_PATHS_KEY,
)
from openclaw.control_plane.dispatch.targets import load_targets_payload
from openclaw.control_plane.registry import (
    DIRECT_CONTROL_PLANE_EXEC,
    SCHEDULER_SERVICE_EXEC,
    load_registry,
    resolve_dispatch_target_operation_command,
)
from openclaw.lib.cli.examples import canonical_cli_command, usage_block
from openclaw.lib.dispatch.operations.executor import (
    collect_targets,
    run_target_operation,
    target_passthrough_args,
    verify_rotation_sequence,
    verify_target,
)
from openclaw.lib.dispatch.operations.render import render_commands, render_entry, render_index
from openclaw.lib.dispatch.operations.state import config_path_from_opts, entries, entry_info, fail, load_config
from openclaw.lib.dispatch.target_registry import load_dispatch_registry
from openclaw.lib.repo.static_truth import parse_env_file
from openclaw.control_plane.dispatch.dispatch_runtime_audit import (
    _exit_code_from_status,
    maybe_write_rotation_sequence_audit,
    render_text,
    rotation_sequence_payload,
    target_acceptance_payload,
)


def parse_args(argv: list[str]) -> dict[str, Any]:
    opts: dict[str, Any] = {
        'entry': '',
        'extension': '',
        'config_path': '',
        'control_plane_profile': '',
        'target': '',
        'batch': '',
        'targets': '',
        'audit_dir': '',
        'json': False,
        'write_audit': False,
        'fail_on_fail': False,
        'fail_on_warn': False,
        'real_send': False,
        'keep_going': False,
        'push_run_id': '',
        'skip_explain': False,
        'skip_acceptance_summary': False,
        'gate_env_file': '',
        'execution_surface': 'host',
    }
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in {'-h', '--help'}:
            opts['help'] = True
            index += 1
            continue
        if arg in {'--json', '--write-audit', '--fail-on-fail', '--fail-on-warn', '--real-send', '--keep-going', '--skip-explain', '--skip-acceptance-summary'}:
            opts[arg[2:].replace('-', '_')] = True
            index += 1
            continue
        if not arg.startswith('--'):
            fail(f'unknown arg: {arg}')
        index += 1
        if index >= len(argv):
            fail(f'{arg} requires a value')
        value = argv[index]
        index += 1
        match arg:
            case '--entry':
                opts['entry'] = value
            case '--extension':
                opts['extension'] = value
            case '--config-path':
                opts['config_path'] = value
            case '--control-plane-profile':
                opts['control_plane_profile'] = value
            case '--target':
                opts['target'] = value
            case '--batch':
                opts['batch'] = value
            case '--targets':
                opts['targets'] = value
            case '--audit-dir':
                opts['audit_dir'] = value
            case '--push-run-id':
                opts['push_run_id'] = value
            case '--gate-env-file':
                opts['gate_env_file'] = value
            case '--execution-surface':
                opts['execution_surface'] = value
            case _:
                fail(f'unknown arg: {arg}')
    return opts


def _config_path(opts: dict[str, Any]) -> Path | None:
    return config_path_from_opts(opts)


def _exec_mode(opts: dict[str, Any]) -> str:
    value = str(opts.get('execution_surface') or 'host').strip().lower()
    if value in {'host', 'direct', 'direct_control_plane_exec'}:
        return DIRECT_CONTROL_PLANE_EXEC
    if value in {'scheduler', 'scheduler_service', 'scheduler_service_exec'}:
        return SCHEDULER_SERVICE_EXEC
    fail(f'--execution-surface 取值非法：{value or "<empty>"}；允许 host / scheduler')


@contextmanager
def _gate_env_context(opts: dict[str, Any]):
    gate_env_file = str(opts.get('gate_env_file') or '').strip()
    if not gate_env_file:
        yield
        return
    env_path = Path(gate_env_file).resolve()
    if not env_path.exists():
        fail(f'--gate-env-file 不存在：{env_path}')
    env_values = parse_env_file(env_path)
    old_values: dict[str, str | None] = {key: os.environ.get(key) for key in env_values}
    try:
        for key, value in env_values.items():
            if str(key).strip():
                os.environ[str(key)] = str(value)
        yield
    finally:
        for key, old_value in old_values.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


def _target_passthrough_args(target_id: str, *, push_run_id: str = '', dry_run: bool = False) -> list[str]:
    return target_passthrough_args(target_id, push_run_id=push_run_id, dry_run=dry_run)


def _run_target_operation(
    registry: dict[str, Any],
    *,
    target_id: str,
    operation: str,
    extra_args: list[str],
    exec_mode: str = DIRECT_CONTROL_PLANE_EXEC,
) -> dict[str, Any]:
    return run_target_operation(
        registry,
        target_id=target_id,
        operation=operation,
        extra_args=extra_args,
        subprocess_run=subprocess.run,
        resolve_dispatch_target_operation_command=lambda registry, **kwargs: resolve_dispatch_target_operation_command(
            registry,
            exec_mode=exec_mode,
            **kwargs,
        ),
    )


def _verify_target(opts: dict[str, Any]) -> tuple[dict[str, Any], int]:
    exec_mode = _exec_mode(opts)
    with _gate_env_context(opts):
        return verify_target(
            opts,
            config_path_resolver=_config_path,
            registry_loader=load_registry,
            run_target_operation=lambda registry, **kwargs: _run_target_operation(registry, exec_mode=exec_mode, **kwargs),
            target_acceptance_payload=target_acceptance_payload,
            exit_code_from_status=_exit_code_from_status,
            fail=fail,
        )


def _collect_targets(opts: dict[str, Any]) -> str:
    return collect_targets(
        opts,
        config_path_resolver=_config_path,
        registry_loader=load_registry,
        env_loader=parse_env_file,
        dispatch_registry_loader=load_dispatch_registry,
        targets_payload_loader=load_targets_payload,
        fail=fail,
    )


def _verify_rotation_sequence(opts: dict[str, Any]) -> tuple[dict[str, Any], int]:
    with _gate_env_context(opts):
        return verify_rotation_sequence(
            opts,
            config_path_resolver=_config_path,
            rotation_sequence_payload=rotation_sequence_payload,
            verify_target=_verify_target,
            maybe_write_rotation_sequence_audit=maybe_write_rotation_sequence_audit,
            exit_code_from_status=_exit_code_from_status,
        )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        fail('missing subcommand; supported: show-index / show-entry / commands / collect-targets / verify-target / verify-rotation-sequence')
    command = args.pop(0)
    opts = parse_args(args)
    if opts.get('help'):
        sys.stdout.write(usage_block(
            canonical_cli_command('dispatch', 'ops', 'show-index') + ' [--extension <id>] [--control-plane-profile <profile>|--config-path <service.json>|--gate-env-file <deploy/.env>]',
            canonical_cli_command('dispatch', 'ops', 'show-entry') + ' --entry <id> [--extension <id>] [--control-plane-profile <profile>|--config-path <service.json>|--gate-env-file <deploy/.env>]',
            canonical_cli_command('dispatch', 'ops', 'commands') + ' --entry <id> [--extension <id>] [--control-plane-profile <profile>|--config-path <service.json>|--gate-env-file <deploy/.env>]',
            canonical_cli_command('dispatch', 'ops', 'collect-targets') + ' --gate-env-file <deploy/.env> [--batch <batch_id>] [--control-plane-profile <profile>|--config-path <service.json>]',
            canonical_cli_command('dispatch', 'ops', 'verify-target') + ' --target <target_id> [--gate-env-file <deploy/.env>] [--control-plane-profile <profile>|--config-path <service.json>] [--execution-surface <host|scheduler>] [--push-run-id <id>] [--json] [--fail-on-fail] [--fail-on-warn] [--real-send] [--skip-explain]',
            canonical_cli_command('dispatch', 'ops', 'verify-rotation-sequence') + ' [--batch <batch_id>|--targets <csv>] [--gate-env-file <deploy/.env>] [--control-plane-profile <profile>|--config-path <service.json>] [--execution-surface <host|scheduler>] [--push-run-id <id>] [--json] [--fail-on-fail] [--fail-on-warn] [--write-audit] [--real-send] [--keep-going]',
            title='Usage:',
        ))
        return 0
    extension_id = str(opts.get('extension') or '').strip() or None
    config_path = _config_path(opts)
    if command == 'show-index':
        sys.stdout.write(render_index(config_path=config_path, extension_id=extension_id) + '\n')
        return 0
    if command == 'show-entry':
        if not opts['entry']:
            fail('show-entry requires --entry')
        sys.stdout.write(render_entry(opts['entry'], config_path=config_path, extension_id=extension_id) + '\n')
        return 0
    if command == 'commands':
        if not opts['entry']:
            fail('commands requires --entry')
        sys.stdout.write(render_commands(opts['entry'], config_path=config_path, extension_id=extension_id) + '\n')
        return 0
    if command == 'collect-targets':
        sys.stdout.write(_collect_targets(opts))
        return 0
    if command == 'verify-target':
        payload, exit_code = _verify_target(opts)
        sys.stdout.write((json.dumps(payload, ensure_ascii=False, indent=2) if opts.get('json') else render_text(payload)) + '\n')
        return exit_code
    if command == 'verify-rotation-sequence':
        payload, exit_code = _verify_rotation_sequence(opts)
        sys.stdout.write((json.dumps(payload, ensure_ascii=False, indent=2) if opts.get('json') else render_text(payload)) + '\n')
        return exit_code
    fail(f'unknown subcommand: {command}')


if __name__ == '__main__':
    raise SystemExit(main())
