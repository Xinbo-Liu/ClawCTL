#!/usr/bin/env python3
"""CLI for managed control-plane extension lifecycle operations."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from openclaw.control_plane.extensions.lifecycle import (
    ExtensionLifecycleError,
    build_lock_payload,
    disable_extension,
    enable_extension,
    install_extension,
    lifecycle_doctor_issues,
    managed_rows_by_id,
    manifest_for_row,
    migrate_extension,
    repo_root_from,
    uninstall_extension,
    write_lock,
)


def _print_json(payload: Any) -> int:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    return 0


def _print_text(payload: dict[str, Any]) -> int:
    status = str(payload.get('status') or 'ok')
    print(f'extensions lifecycle: {status}')
    for item in payload.get('items') or []:
        if isinstance(item, dict):
            print(f"- {item.get('id')}: {item.get('version') or '<missing>'} [{item.get('status')}]")
    for issue in payload.get('issues') or []:
        print(f'- {issue}')
    return 0 if status == 'ok' else 1


def _emit(payload: dict[str, Any], *, json_output: bool) -> int:
    return _print_json(payload) if json_output else _print_text(payload)


def _repo_root(args: argparse.Namespace) -> Path:
    start = Path(str(getattr(args, 'repo_root', '') or '')).resolve() if str(getattr(args, 'repo_root', '') or '').strip() else None
    return repo_root_from(start)


def cmd_list(args: argparse.Namespace) -> int:
    repo_root = _repo_root(args)
    rows = managed_rows_by_id(repo_root)
    items = []
    for row in sorted(rows.values(), key=lambda item: item.id):
        manifest = manifest_for_row(row)
        items.append({
            'id': row.id,
            'title': row.title,
            'version': str(manifest.get('version') or ''),
            'status': row.status,
            'rootDir': str(row.root_dir),
        })
    return _emit({'status': 'ok', 'items': items}, json_output=bool(args.json))


def cmd_show(args: argparse.Namespace) -> int:
    repo_root = _repo_root(args)
    row = managed_rows_by_id(repo_root).get(args.id)
    if row is None:
        raise ExtensionLifecycleError(f'extension 未安装：{args.id}')
    manifest = manifest_for_row(row)
    return _print_json({
        'id': row.id,
        'row': {
            'title': row.title,
            'status': row.status,
            'rootDir': str(row.root_dir),
            'defaultServiceConfigPath': str(row.default_service_config_path),
            'manifestDir': str(row.manifest_dir),
            'pythonRoots': [str(item) for item in row.python_roots],
        },
        'manifest': manifest,
    })


def cmd_doctor(args: argparse.Namespace) -> int:
    repo_root = _repo_root(args)
    issues = lifecycle_doctor_issues(repo_root)
    payload = {'status': 'ok' if not issues else 'fail', 'issues': issues}
    return _emit(payload, json_output=bool(args.json))


def cmd_lock(args: argparse.Namespace) -> int:
    repo_root = _repo_root(args)
    payload = build_lock_payload(repo_root) if bool(args.dry_run) else write_lock(repo_root)
    return _print_json({'status': 'ok', 'dryRun': bool(args.dry_run), 'lock': payload})


def cmd_install(args: argparse.Namespace) -> int:
    repo_root = _repo_root(args)
    payload = install_extension(
        repo_root,
        source=Path(args.source),
        extension_id=str(args.id or ''),
        mode=str(args.mode or ''),
        enable_profile=str(args.enable_profile or ''),
        dry_run=bool(args.dry_run),
    )
    return _print_json({'status': 'ok', **payload})


def cmd_uninstall(args: argparse.Namespace) -> int:
    repo_root = _repo_root(args)
    payload = uninstall_extension(
        repo_root,
        extension_id=args.id,
        profile=str(args.profile or ''),
        remove_files=bool(args.remove_files),
        cascade_disable=bool(args.cascade_disable),
        dry_run=bool(args.dry_run),
    )
    return _print_json({'status': 'ok', **payload})


def cmd_enable(args: argparse.Namespace) -> int:
    repo_root = _repo_root(args)
    payload = enable_extension(repo_root, profile=args.profile, extension_id=args.id, dry_run=bool(args.dry_run))
    return _print_json({'status': 'ok', **payload})


def cmd_disable(args: argparse.Namespace) -> int:
    repo_root = _repo_root(args)
    payload = disable_extension(
        repo_root,
        profile=args.profile,
        extension_id=args.id,
        cascade_disable=bool(args.cascade_disable),
        dry_run=bool(args.dry_run),
    )
    return _print_json({'status': 'ok', **payload})


def cmd_migrate(args: argparse.Namespace) -> int:
    repo_root = _repo_root(args)
    payload = migrate_extension(repo_root, extension_id=args.id, dry_run=bool(args.dry_run))
    return _print_json({'status': 'ok', **payload})


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--repo-root', default='')
    parser.add_argument('--json', action='store_true')


def _add_dry_run(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--dry-run', action='store_true')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='python -m openclaw.cli control-plane extensions')
    subparsers = parser.add_subparsers(dest='command', required=True)

    list_parser = subparsers.add_parser('list')
    _add_common(list_parser)
    list_parser.set_defaults(func=cmd_list)

    show_parser = subparsers.add_parser('show')
    _add_common(show_parser)
    show_parser.add_argument('--id', required=True)
    show_parser.set_defaults(func=cmd_show)

    doctor_parser = subparsers.add_parser('doctor')
    _add_common(doctor_parser)
    doctor_parser.set_defaults(func=cmd_doctor)

    lock_parser = subparsers.add_parser('lock')
    _add_common(lock_parser)
    _add_dry_run(lock_parser)
    lock_parser.set_defaults(func=cmd_lock)

    install_parser = subparsers.add_parser('install')
    _add_common(install_parser)
    _add_dry_run(install_parser)
    install_parser.add_argument('--source', required=True)
    install_parser.add_argument('--id', default='')
    install_parser.add_argument('--mode', choices=['copy', 'in-place'], default='')
    install_parser.add_argument('--enable-profile', default='')
    install_parser.set_defaults(func=cmd_install)

    uninstall_parser = subparsers.add_parser('uninstall')
    _add_common(uninstall_parser)
    _add_dry_run(uninstall_parser)
    uninstall_parser.add_argument('--id', required=True)
    uninstall_parser.add_argument('--profile', default='')
    uninstall_parser.add_argument('--remove-files', action='store_true')
    uninstall_parser.add_argument('--cascade-disable', action='store_true')
    uninstall_parser.set_defaults(func=cmd_uninstall)

    enable_parser = subparsers.add_parser('enable')
    _add_common(enable_parser)
    _add_dry_run(enable_parser)
    enable_parser.add_argument('--id', required=True)
    enable_parser.add_argument('--profile', required=True)
    enable_parser.set_defaults(func=cmd_enable)

    disable_parser = subparsers.add_parser('disable')
    _add_common(disable_parser)
    _add_dry_run(disable_parser)
    disable_parser.add_argument('--id', required=True)
    disable_parser.add_argument('--profile', required=True)
    disable_parser.add_argument('--cascade-disable', action='store_true')
    disable_parser.set_defaults(func=cmd_disable)

    migrate_parser = subparsers.add_parser('migrate')
    _add_common(migrate_parser)
    _add_dry_run(migrate_parser)
    migrate_parser.add_argument('--id', required=True)
    migrate_parser.set_defaults(func=cmd_migrate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return int(args.func(args) or 0)
    except ExtensionLifecycleError as exc:
        sys.stderr.write(f'[extension_lifecycle][FAIL] {exc}\n')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
