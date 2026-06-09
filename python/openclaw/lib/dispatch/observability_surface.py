#!/usr/bin/env python3
"""Dispatch observability surface with extension-aware lookup."""
from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NoReturn

from openclaw.control_plane.extensions.ownership import filter_rows_by_extension, resolve_owned_row
from openclaw.control_plane.governance_surfaces import load_dispatch_observability_surface
from openclaw.lib.cli.examples import canonical_cli_command, usage_block
from openclaw.lib.control_plane.object_families import get_family
from openclaw.lib.repo.config_selection import (
    CONTROL_PLANE_CONFIG_ENV,
    CONTROL_PLANE_PROFILE_ENV,
    resolve_selected_control_plane_config_path,
)
from openclaw.lib.repo.layout import (
    control_plane_profile_id_for_config_path,
    resolve_control_plane_profile_service_config_path,
    resolve_repo_root,
)
from openclaw.lib.repo.static_truth import parse_env_file
from openclaw.control_plane.dispatch.dispatch_runtime_audit import (
    _exit_code_from_status,
    batch_acceptance_payload,
    health_overview_payload,
    maybe_write_batch_acceptance_audit,
    maybe_write_target_acceptance_audit,
    render_text,
    target_acceptance_payload,
)

ROOT_DIR = resolve_repo_root(Path(__file__))


def fail(message: str, code: int = 2) -> NoReturn:
    sys.stderr.write(f'[dispatch_observability_surface][FAIL] {message}\n')
    raise SystemExit(code)


def load_config(*, config_path: Path | None = None) -> dict[str, Any]:
    payload = load_dispatch_observability_surface(config_path=config_path)
    if not isinstance(payload, dict):
        fail('dispatch_observability_surface.json top-level payload must be an object')
    return payload


def entries(*, config_path: Path | None = None, extension_id: str | None = None) -> list[dict[str, Any]]:
    rows = load_config(config_path=config_path).get('entries') or []
    if not isinstance(rows, list):
        fail('entries must be a list')
    return filter_rows_by_extension([row for row in rows if isinstance(row, dict)], extension_id)


def entry_info(entry_id: str, *, config_path: Path | None = None, extension_id: str | None = None) -> dict[str, Any]:
    try:
        return resolve_owned_row(
            [row for row in load_config(config_path=config_path).get('entries') or [] if isinstance(row, dict)],
            entry_id,
            extension_id=extension_id,
            id_key='id',
            label='dispatch observability entry',
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
    opts: dict[str, Any] = {
        'entry': '',
        'extension': '',
        'config_path': '',
        'control_plane_profile': '',
        'gate_env_file': '',
        'target': '',
        'batch': '',
        'targets': '',
        'audit_dir': '',
        'json': False,
        'write_audit': False,
        'fail_on_fail': False,
        'fail_on_warn': False,
    }
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in {'-h', '--help'}:
            opts['help'] = True
            index += 1
            continue
        if arg in {'--json', '--write-audit', '--fail-on-fail', '--fail-on-warn'}:
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
        if arg == '--entry':
            opts['entry'] = value
        elif arg == '--extension':
            opts['extension'] = value
        elif arg == '--config-path':
            opts['config_path'] = value
        elif arg == '--control-plane-profile':
            opts['control_plane_profile'] = value
        elif arg == '--gate-env-file':
            opts['gate_env_file'] = value
        elif arg == '--target':
            opts['target'] = value
        elif arg == '--batch':
            opts['batch'] = value
        elif arg == '--targets':
            opts['targets'] = value
        elif arg == '--audit-dir':
            opts['audit_dir'] = value
        else:
            fail(f'unknown arg: {arg}')
    return opts


def render_index(*, config_path: Path | None = None, extension_id: str | None = None) -> str:
    lines = ['dispatch observability entries', '']
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


def render_steps(entry_id: str, *, config_path: Path | None = None, extension_id: str | None = None) -> str:
    info = entry_info(entry_id, config_path=config_path, extension_id=extension_id)
    return '\n'.join(item for item in _string_list(info.get('steps')))


def render_objects(*, config_path: Path | None = None, extension_id: str | None = None) -> str:
    family = get_family('dispatch_runtime_state', ROOT_DIR, config_path=config_path, extension_id=extension_id)
    lines = []
    for item in family.get('entries') or []:
        logical = str(item.get('id') or '').strip()
        host_view = str(item.get('resolved_path') or '').strip()
        usage = str(item.get('usage') or '').strip()
        lines.append(f'- {logical}: {host_view} -> {usage}')
    return '\n'.join(lines)


def _config_path(opts: dict[str, Any]) -> Path | None:
    value = str(opts.get('config_path') or '').strip()
    profile_id = str(opts.get('control_plane_profile') or '').strip()
    if value and profile_id:
        fail('--config-path 与 --control-plane-profile 不能同时使用')
    if value:
        try:
            return resolve_selected_control_plane_config_path(value, start_path=ROOT_DIR)
        except ValueError as exc:
            fail(str(exc))
    if profile_id:
        try:
            return resolve_control_plane_profile_service_config_path(profile_id, start_path=ROOT_DIR)
        except ValueError as exc:
            fail(str(exc))

    gate_env_file = str(opts.get('gate_env_file') or '').strip()
    if not gate_env_file:
        return None
    env_path = Path(gate_env_file).resolve()
    if not env_path.exists():
        fail(f'--gate-env-file 不存在：{env_path}')
    env_values = parse_env_file(env_path)
    env_config_path = str(env_values.get(CONTROL_PLANE_CONFIG_ENV) or '').strip()
    env_profile = str(env_values.get(CONTROL_PLANE_PROFILE_ENV) or '').strip()
    if env_config_path:
        try:
            resolved_path = resolve_selected_control_plane_config_path(env_config_path, start_path=ROOT_DIR)
        except ValueError as exc:
            fail(str(exc))
        if env_profile:
            resolved_profile = control_plane_profile_id_for_config_path(resolved_path, start_path=ROOT_DIR)
            if resolved_profile != env_profile:
                fail(
                    f'{env_path} 中的 {CONTROL_PLANE_CONFIG_ENV} 与 {CONTROL_PLANE_PROFILE_ENV} 不一致：'
                    f'{CONTROL_PLANE_CONFIG_ENV} -> {resolved_profile}, {CONTROL_PLANE_PROFILE_ENV}={env_profile}'
                )
        return resolved_path
    if env_profile:
        try:
            return resolve_control_plane_profile_service_config_path(env_profile, start_path=ROOT_DIR)
        except ValueError as exc:
            fail(str(exc))
    return None


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


def _target_acceptance(opts: dict[str, Any]) -> tuple[dict[str, Any], int]:
    target_id = str(opts.get('target') or '').strip()
    if not target_id:
        fail('show-target-acceptance requires --target')
    config_path = _config_path(opts)
    with _gate_env_context(opts):
        payload = target_acceptance_payload(target_id, config_path=config_path)
    if opts.get('write_audit'):
        audit_path = maybe_write_target_acceptance_audit(
            payload,
            config_path=Path(str(payload.get('config_path') or '')).resolve(),
            audit_dir=str(opts.get('audit_dir') or ''),
        )
        payload['audit_path'] = str(audit_path)
    return payload, _exit_code_from_status(
        str(payload.get('status') or ''),
        fail_on_warn=bool(opts.get('fail_on_warn')),
        fail_on_fail=bool(opts.get('fail_on_fail')),
    )


def _batch_acceptance(opts: dict[str, Any]) -> tuple[dict[str, Any], int]:
    config_path = _config_path(opts)
    with _gate_env_context(opts):
        payload = batch_acceptance_payload(
            config_path=config_path,
            batch_id=str(opts.get('batch') or ''),
            targets_csv=str(opts.get('targets') or ''),
        )
    if opts.get('write_audit'):
        audit_path = maybe_write_batch_acceptance_audit(
            payload,
            config_path=Path(str(payload.get('config_path') or '')).resolve(),
            audit_dir=str(opts.get('audit_dir') or ''),
        )
        payload['audit_path'] = str(audit_path)
    return payload, _exit_code_from_status(
        str(payload.get('overall_status') or ''),
        fail_on_warn=bool(opts.get('fail_on_warn')),
        fail_on_fail=bool(opts.get('fail_on_fail')),
    )


def _health_overview(opts: dict[str, Any]) -> tuple[dict[str, Any], int]:
    config_path = _config_path(opts)
    with _gate_env_context(opts):
        payload = health_overview_payload(config_path=config_path)
    return payload, _exit_code_from_status(
        str(payload.get('overall_status') or ''),
        fail_on_warn=False,
        fail_on_fail=bool(opts.get('fail_on_fail')),
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        fail('missing subcommand; supported: show-index / show-entry / steps / objects / show-target-acceptance / show-batch-acceptance / show-health-overview')
    command = args.pop(0)
    opts = parse_args(args)
    if opts.get('help'):
        sys.stdout.write(usage_block(
            canonical_cli_command('dispatch', 'observability', 'show-index') + ' [--extension <id>] [--control-plane-profile <profile>|--config-path <service.json>|--gate-env-file <deploy/.env>]',
            canonical_cli_command('dispatch', 'observability', 'show-entry') + ' --entry <id> [--extension <id>] [--control-plane-profile <profile>|--config-path <service.json>|--gate-env-file <deploy/.env>]',
            canonical_cli_command('dispatch', 'observability', 'steps') + ' --entry <id> [--extension <id>] [--control-plane-profile <profile>|--config-path <service.json>|--gate-env-file <deploy/.env>]',
            canonical_cli_command('dispatch', 'observability', 'objects') + ' [--extension <id>] [--control-plane-profile <profile>|--config-path <service.json>|--gate-env-file <deploy/.env>]',
            canonical_cli_command('dispatch', 'observability', 'show-target-acceptance') + ' --target <target_id> [--control-plane-profile <profile>|--config-path <service.json>|--gate-env-file <deploy/.env>] [--json] [--fail-on-fail] [--write-audit]',
            canonical_cli_command('dispatch', 'observability', 'show-batch-acceptance') + ' [--batch <batch_id>|--targets <csv>] [--control-plane-profile <profile>|--config-path <service.json>|--gate-env-file <deploy/.env>] [--json] [--fail-on-fail] [--fail-on-warn] [--write-audit]',
            canonical_cli_command('dispatch', 'observability', 'show-health-overview') + ' [--control-plane-profile <profile>|--config-path <service.json>|--gate-env-file <deploy/.env>] [--json] [--fail-on-fail]',
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
    if command == 'steps':
        if not opts['entry']:
            fail('steps requires --entry')
        sys.stdout.write(render_steps(opts['entry'], config_path=config_path, extension_id=extension_id) + '\n')
        return 0
    if command == 'objects':
        if opts.get('entry'):
            fail('objects does not accept --entry')
        sys.stdout.write(render_objects(config_path=config_path, extension_id=extension_id) + '\n')
        return 0
    if command == 'show-target-acceptance':
        payload, exit_code = _target_acceptance(opts)
        sys.stdout.write((json.dumps(payload, ensure_ascii=False, indent=2) if opts.get('json') else render_text(payload)) + '\n')
        return exit_code
    if command == 'show-batch-acceptance':
        payload, exit_code = _batch_acceptance(opts)
        sys.stdout.write((json.dumps(payload, ensure_ascii=False, indent=2) if opts.get('json') else render_text(payload)) + '\n')
        return exit_code
    if command == 'show-health-overview':
        payload, exit_code = _health_overview(opts)
        sys.stdout.write((json.dumps(payload, ensure_ascii=False, indent=2) if opts.get('json') else render_text(payload)) + '\n')
        return exit_code
    fail(f'unknown subcommand: {command}')


if __name__ == '__main__':
    raise SystemExit(main())
