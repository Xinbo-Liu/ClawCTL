#!/usr/bin/env python3
"""Control-plane config selection surface."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import NoReturn

from openclaw.lib.repo.layout import (
    CONTROL_PLANE_CONFIG_ENV,
    CONTROL_PLANE_CONTAINER_REPO_ROOT,
    CONTROL_PLANE_PROFILE_ENV,
    DEFAULT_RUNTIME_CONTROL_PLANE_PROFILE_ID,
    control_plane_profile_status_rows,
    resolve_repo_root,
    resolve_selected_control_plane_config_path,
    resolve_selected_control_plane_container_config_path,
    resolve_selected_control_plane_profile_id,
)
from openclaw.lib.repo.managed_extensions import (
    ManagedExtensionError,
    managed_extension_default_service_config_path_for_agent_ref,
)


CONTAINER_CONFIG_ENV = 'CONTROL_PLANE_CONTAINER_CONFIG_PATH'
CUSTOM_PROFILE_ID = 'custom'


def fail(message: str, exit_code: int = 2) -> NoReturn:
    sys.stderr.write(f'[control_plane_config][FAIL] {message}\n')
    raise SystemExit(exit_code)


def _add_common_selection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--config-path', default=None)
    parser.add_argument('--control-plane-profile', default=None)
    parser.add_argument('--repo-root', default=None)


def _selection_kwargs(args: argparse.Namespace) -> dict[str, object]:
    repo_root = Path(args.repo_root).resolve() if args.repo_root else resolve_repo_root(Path(__file__))
    return {
        'config_path': args.config_path,
        'control_plane_profile': args.control_plane_profile,
        'start_path': repo_root,
        'default_profile': DEFAULT_RUNTIME_CONTROL_PLANE_PROFILE_ID,
    }


def _has_explicit_public_selection(args: argparse.Namespace) -> bool:
    return bool(str(args.config_path or '').strip() or str(args.control_plane_profile or '').strip())


def cmd_host_path(args: argparse.Namespace) -> int:
    resolved = resolve_selected_control_plane_config_path(**_selection_kwargs(args))
    print(resolved)
    return 0


def cmd_container_path(args: argparse.Namespace) -> int:
    override = str(os.environ.get(CONTAINER_CONFIG_ENV) or '').strip()
    if override and not _has_explicit_public_selection(args):
        print(override.replace('\\', '/'))
        return 0
    resolved = resolve_selected_control_plane_container_config_path(
        **_selection_kwargs(args),
        repo_mount_root=args.repo_mount_root,
    )
    print(resolved.as_posix())
    return 0


def cmd_profile_id(args: argparse.Namespace) -> int:
    profile_id = resolve_selected_control_plane_profile_id(**_selection_kwargs(args))
    print(profile_id or CUSTOM_PROFILE_ID)
    return 0


def _profile_status_counts(rows: tuple[dict[str, object], ...]) -> dict[str, int]:
    return {
        'total': len(rows),
        'valid': sum(1 for row in rows if row.get('status') == 'valid'),
        'invalid': sum(1 for row in rows if row.get('status') == 'invalid'),
        'registry': sum(1 for row in rows if row.get('source') == 'registry'),
        'discovered': sum(1 for row in rows if row.get('source') == 'discovered'),
    }


def cmd_profiles(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve() if args.repo_root else resolve_repo_root(Path(__file__))
    rows = control_plane_profile_status_rows(repo_root)
    if args.format == 'json':
        print(json.dumps({'profiles': rows, 'counts': _profile_status_counts(rows)}, ensure_ascii=False, indent=2))
        return 0
    for row in rows:
        path = str(row.get('path') or row.get('configPath') or '')
        issues = '; '.join(str(item) for item in row.get('issues') or [])
        fields = [str(row.get('id') or ''), str(row.get('source') or ''), str(row.get('status') or ''), path]
        if issues:
            fields.append(issues)
        print('\t'.join(fields))
    return 0


def cmd_agent_host_path(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve() if args.repo_root else resolve_repo_root(Path(__file__))
    explicit = str(os.environ.get(CONTROL_PLANE_CONFIG_ENV) or '').strip() or str(os.environ.get(CONTROL_PLANE_PROFILE_ENV) or '').strip()
    if explicit:
        print(resolve_selected_control_plane_config_path(start_path=repo_root, default_profile=DEFAULT_RUNTIME_CONTROL_PLANE_PROFILE_ID))
        return 0
    managed_config_path = managed_extension_default_service_config_path_for_agent_ref(
        args.agent_ref,
        start_path=repo_root,
    )
    if managed_config_path is not None:
        print(managed_config_path)
        return 0
    resolved = resolve_selected_control_plane_config_path(
        start_path=repo_root,
        default_profile=DEFAULT_RUNTIME_CONTROL_PLANE_PROFILE_ID,
    )
    print(resolved)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='control-plane config')
    subparsers = parser.add_subparsers(dest='subcommand', required=True)

    host_path_parser = subparsers.add_parser('host-path')
    _add_common_selection_args(host_path_parser)
    host_path_parser.set_defaults(handler=cmd_host_path)

    container_path_parser = subparsers.add_parser('container-path')
    _add_common_selection_args(container_path_parser)
    container_path_parser.add_argument('--repo-mount-root', default=str(CONTROL_PLANE_CONTAINER_REPO_ROOT))
    container_path_parser.set_defaults(handler=cmd_container_path)

    profile_id_parser = subparsers.add_parser('profile-id')
    _add_common_selection_args(profile_id_parser)
    profile_id_parser.set_defaults(handler=cmd_profile_id)

    profiles_parser = subparsers.add_parser('profiles')
    profiles_parser.add_argument('--repo-root', default=None)
    profiles_parser.add_argument('--format', choices=('text', 'json'), default='text')
    profiles_parser.set_defaults(handler=cmd_profiles)

    agent_host_path_parser = subparsers.add_parser('agent-host-path')
    agent_host_path_parser.add_argument('--agent-ref', required=True)
    agent_host_path_parser.add_argument('--repo-root', default=None)
    agent_host_path_parser.set_defaults(handler=cmd_agent_host_path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args) or 0)
    except (ManagedExtensionError, ValueError) as exc:
        message = str(exc)
        exit_code = 97 if 'profile registry' in message or 'duplicate control-plane profile' in message else 2
        fail(message, exit_code)


if __name__ == '__main__':
    raise SystemExit(main())
