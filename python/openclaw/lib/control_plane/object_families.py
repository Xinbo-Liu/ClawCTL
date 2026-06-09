#!/usr/bin/env python3
"""Control-plane object family truth with extension-aware ownership."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, NoReturn

from openclaw.control_plane.state_paths import resolve_control_plane_state_root
from openclaw.control_plane.extensions.fragment_descriptors import (
    OBJECT_FAMILIES_DESCRIPTOR,
    load_fragment_payload,
)
from openclaw.control_plane.extensions.ownership import (
    filter_rows_by_extension,
    normalize_extension_id,
    resolve_owned_row,
)
from openclaw.lib.cli.examples import canonical_cli_command, usage_block
from openclaw.lib.repo.layout import resolve_control_plane_service_config_path, resolve_repo_root
from openclaw.lib.runtime.resolver_loader import require_path_resolver
from openclaw.lib.repo.static_truth import host_control_plane_file, host_state_file
from openclaw.runtime.path_view import normalize_runtime_path_view


ROOT_DIR = resolve_repo_root(Path(__file__))
CONFIG_PATH = OBJECT_FAMILIES_DESCRIPTOR.base_path


def _fail(message: str) -> NoReturn:
    raise SystemExit(f'[control_plane_object_families][FAIL] {message}')


def _resolved_config_path(config_path: Path | None = None) -> Path:
    return Path(config_path).resolve() if config_path is not None else resolve_control_plane_service_config_path(ROOT_DIR)


def load_contract(
    config_path: Path | None = None,
    *,
    extensions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = load_fragment_payload(
        OBJECT_FAMILIES_DESCRIPTOR,
        config_path=_resolved_config_path(config_path),
        extensions=extensions,
    )
    if not payload.get('families'):
        _fail('families cannot be empty')
    return payload


_PATH_KINDS = {'runtime_path', 'host_control_plane_file', 'host_state_file', 'repo_relative'}


def resolve_entry_path(entry: dict[str, Any], base_root: Path = ROOT_DIR, *, config_path: Path | None = None) -> str:
    path_kind = str(entry.get('path_kind') or '').strip()
    path_ref = str(entry.get('path_ref') or '').strip()
    if path_kind not in _PATH_KINDS:
        _fail(f'unsupported path_kind: {path_kind or "<empty>"}')
    if not path_ref:
        _fail('path_ref cannot be empty')
    if path_kind == 'runtime_path':
        return require_path_resolver(repo_root=base_root, config_path=_resolved_config_path(config_path)).resolve_path(path_ref, view='host')
    if path_kind == 'host_control_plane_file':
        if normalize_runtime_path_view(os.environ.get('OPENCLAW_RUNTIME_PATH_VIEW'), fallback='host') == 'scheduler':
            return str((resolve_control_plane_state_root() / path_ref).resolve())
        if config_path is not None:
            resolver = require_path_resolver(repo_root=base_root, config_path=_resolved_config_path(config_path))
            return str((resolver.absolute_host_path('control_plane_host_state_dir') / path_ref).resolve())
        return host_control_plane_file(path_ref, base_root)
    if path_kind == 'host_state_file':
        if config_path is not None:
            resolver = require_path_resolver(repo_root=base_root, config_path=_resolved_config_path(config_path))
            return str((resolver.absolute_host_path('state_root') / path_ref).resolve())
        return host_state_file(path_ref, base_root)
    return path_ref


def get_family(
    family_id: str,
    base_root: Path = ROOT_DIR,
    config_path: Path | None = None,
    *,
    extension_id: str | None = None,
    resolve_paths: bool = True,
    extensions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    contract = load_contract(config_path=config_path, extensions=extensions)
    family_rows = [row for row in contract.get('families') or [] if isinstance(row, dict)]
    try:
        family = resolve_owned_row(
            family_rows,
            family_id,
            extension_id=extension_id,
            id_key='id',
            label='family',
        )
    except KeyError as exc:
        _fail(str(exc))
    except ValueError as exc:
        _fail(str(exc))
    owner = normalize_extension_id(family.get('extensionId')) or None
    return _materialize_family(family, base_root, config_path=config_path, owner=owner, resolve_paths=resolve_paths)


def _materialize_family(
    family: dict[str, Any],
    base_root: Path,
    *,
    config_path: Path | None,
    owner: str | None,
    resolve_paths: bool = True,
) -> dict[str, Any]:
    """将已加载的 object family 行解析为运行展示行，避免重复读取合同真源。"""
    entries: list[dict[str, Any]] = []
    for item in family.get('entries') or []:
        if not isinstance(item, dict):
            _fail(f'{family.get("id")}.entries must be objects')
        resolved = dict(item)
        if resolve_paths:
            resolved['resolved_path'] = resolve_entry_path(resolved, base_root, config_path=config_path)
        entries.append(resolved)
    resolved_family = dict(family)
    resolved_family['entries'] = entries
    if owner:
        resolved_family['extensionId'] = owner
    return resolved_family


def get_entry(
    family_id: str,
    entry_id: str,
    base_root: Path = ROOT_DIR,
    config_path: Path | None = None,
    *,
    extension_id: str | None = None,
) -> dict[str, Any]:
    family = get_family(family_id, base_root, config_path=config_path, extension_id=extension_id)
    owner = normalize_extension_id(family.get('extensionId')) or None
    try:
        return resolve_owned_row(
            [row for row in family.get('entries') or [] if isinstance(row, dict)],
            entry_id,
            extension_id=owner,
            id_key='id',
            label=f'{family_id} entry',
        )
    except KeyError as exc:
        _fail(str(exc))
    except ValueError as exc:
        _fail(str(exc))


def all_families(
    base_root: Path = ROOT_DIR,
    config_path: Path | None = None,
    *,
    extension_id: str | None = None,
) -> list[dict[str, Any]]:
    contract = load_contract(config_path=config_path)
    rows = filter_rows_by_extension(
        [row for row in contract.get('families') or [] if isinstance(row, dict)],
        extension_id=extension_id,
    )
    return [
        _materialize_family(
            row,
            base_root,
            config_path=config_path,
            owner=normalize_extension_id(row.get('extensionId')) or None,
        )
        for row in rows
        if str(row.get('id') or '').strip()
    ]


def _usage() -> str:
    return usage_block(
        canonical_cli_command('control-plane', 'objects', 'json') + ' [--config-path <service.json>]',
        canonical_cli_command('control-plane', 'objects', 'family') + ' --id <family_id> [--extension <extension_id>] [--root <repo-root>] [--config-path <service.json>]',
        canonical_cli_command('control-plane', 'objects', 'entry-path') + ' --family <family_id> --entry <entry_id> [--extension <extension_id>] [--root <repo-root>] [--config-path <service.json>]',
        title='Usage:',
    )


def _parse_args(argv: list[str]) -> dict[str, str]:
    opts: dict[str, str] = {}
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in {'-h', '--help'}:
            opts[arg] = '1'
            index += 1
            continue
        if not arg.startswith('--'):
            _fail(f'unknown argument: {arg}')
        if index + 1 >= len(argv):
            _fail(f'missing value for {arg}')
        opts[arg] = argv[index + 1]
        index += 2
    return opts


def _base_root(opts: dict[str, str]) -> Path:
    return Path(opts.get('--root', str(ROOT_DIR))).resolve()


def _config_path(opts: dict[str, str]) -> Path | None:
    value = str(opts.get('--config-path') or '').strip()
    if not value:
        return None
    return Path(value).resolve()


def _extension_id(opts: dict[str, str]) -> str | None:
    value = str(opts.get('--extension') or '').strip()
    return value or None


def main(argv: list[str] | None = None) -> int:
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {'-h', '--help'}:
        sys.stdout.write(_usage())
        return 0
    command = args.pop(0)
    opts = _parse_args(args)
    if '-h' in opts or '--help' in opts:
        sys.stdout.write(_usage())
        return 0
    base_root = _base_root(opts)
    config_path = _config_path(opts)
    extension_id = _extension_id(opts)
    if command == 'json':
        payload = load_contract(config_path=config_path)
        if extension_id:
            payload = {
                'generated_artifacts': payload.get('generated_artifacts') or {},
                'families': filter_rows_by_extension(
                    [row for row in payload.get('families') or [] if isinstance(row, dict)],
                    extension_id=extension_id,
                ),
            }
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
        return 0
    if command == 'family':
        family_id = str(opts.get('--id') or '').strip()
        if not family_id:
            _fail('family requires --id')
        sys.stdout.write(
            json.dumps(
                get_family(family_id, base_root, config_path=config_path, extension_id=extension_id),
                ensure_ascii=False,
                indent=2,
            ) + '\n'
        )
        return 0
    if command == 'entry-path':
        family_id = str(opts.get('--family') or '').strip()
        entry_id = str(opts.get('--entry') or '').strip()
        if not family_id or not entry_id:
            _fail('entry-path requires both --family and --entry')
        sys.stdout.write(
            str(
                get_entry(
                    family_id,
                    entry_id,
                    base_root,
                    config_path=config_path,
                    extension_id=extension_id,
                ).get('resolved_path')
                or ''
            ).strip() + '\n'
        )
        return 0
    _fail(f'unknown command: {command}')


if __name__ == '__main__':
    raise SystemExit(main())
