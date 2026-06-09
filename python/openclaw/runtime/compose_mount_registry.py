#!/usr/bin/env python3
"""runtime compose 挂载与 extension 服务块控制面。"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, NoReturn

from openclaw.lib.cli import CliError, FlagSpec, parse_typed_flag_args
from openclaw.lib.cli.examples import canonical_cli_command
from openclaw.lib.repo.layout import (
    DEFAULT_RUNTIME_CONTROL_PLANE_SERVICE_CONFIG_REL_PATH,
    resolve_repo_root,
)
from openclaw.lib.runtime.resolver_loader import PathResolverInstance
from openclaw.runtime.compose_mounts import manifest as mount_manifest
from openclaw.runtime.compose_mounts import render as mount_render
from openclaw.runtime.compose_mounts import sync as mount_sync

ROOT_DIR = resolve_repo_root(Path(__file__))
MANIFEST_PATH = ROOT_DIR / 'config' / 'services' / 'runtime_mounts.json'
ENV_HOST_STATE_ROOT = '${HOST_STATE_ROOT:?HOST_STATE_ROOT_required}'
EXTENSION_SERVICES_BEGIN = '# RUNTIME_EXTENSION_SERVICES_BEGIN'
EXTENSION_SERVICES_END = '# RUNTIME_EXTENSION_SERVICES_END'
RUNTIME_MOUNTS_SYNC_COMPOSE_CMD = canonical_cli_command('runtime', 'mounts', 'sync-compose')


def fail(message: str, code: int = 2) -> NoReturn:
    sys.stderr.write(f'[compose_mount_registry][FAIL] {message}\n')
    raise SystemExit(code)


def _read_json(path: Path) -> dict[str, Any]:
    return mount_manifest.read_json(path, root_dir=ROOT_DIR, fail=fail)


def _merge_unique_dict_entries(base: dict[str, Any], incoming: dict[str, Any], *, label: str) -> dict[str, Any]:
    return mount_manifest.merge_unique_dict_entries(base, incoming, label=label, fail=fail)


def _merge_mount_rows(base_rows: list[dict[str, Any]], incoming_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return mount_manifest.merge_mount_rows(base_rows, incoming_rows)


def _merge_service_rows(base_rows: list[dict[str, Any]], incoming_rows: list[dict[str, Any]], *, label: str) -> list[dict[str, Any]]:
    return mount_manifest.merge_service_rows(base_rows, incoming_rows, label=label, fail=fail)


def _resolve_config_path(config_path: Path | None = None) -> Path:
    return mount_manifest.resolve_config_path(ROOT_DIR, config_path)


def _enabled_extension_ids(config_path: Path | None = None) -> set[str]:
    return mount_manifest.enabled_extension_ids_for(ROOT_DIR, config_path=config_path)


def _required_extension_ids(payload: dict[str, Any], *, label: str) -> set[str]:
    return mount_manifest.required_extension_ids(payload, label=label, fail=fail)


def _service_is_enabled(payload: dict[str, Any], enabled_extension_ids: set[str]) -> bool:
    return mount_manifest.service_is_enabled(payload, enabled_extension_ids, fail=fail)


def _mount_is_enabled(mount: dict[str, Any], enabled_extension_ids: set[str]) -> bool:
    return mount_manifest.mount_is_enabled(mount, enabled_extension_ids, fail=fail)


def load_manifest(path: Path | None = None, *, config_path: Path | None = None) -> dict[str, Any]:
    return mount_manifest.load_manifest(
        root_dir=ROOT_DIR,
        manifest_path=MANIFEST_PATH,
        fail=fail,
        path=path,
        config_path=config_path,
    )


def services(*, config_path: Path | None = None) -> list[dict[str, Any]]:
    return mount_manifest.services(
        root_dir=ROOT_DIR,
        manifest_path=MANIFEST_PATH,
        fail=fail,
        config_path=config_path,
    )


def compose_file_path(*, config_path: Path | None = None) -> Path:
    return mount_manifest.compose_file_path(
        root_dir=ROOT_DIR,
        manifest_path=MANIFEST_PATH,
        fail=fail,
        config_path=config_path,
    )


def marker_prefix(*, config_path: Path | None = None) -> str:
    return mount_manifest.marker_prefix(
        root_dir=ROOT_DIR,
        manifest_path=MANIFEST_PATH,
        fail=fail,
        config_path=config_path,
    )


def _runtime_entry_host_template(resolver: PathResolverInstance, entry_id: str) -> str:
    return mount_render.runtime_entry_host_template(
        resolver,
        entry_id,
        env_host_state_root=ENV_HOST_STATE_ROOT,
        fail=fail,
    )


def _runtime_entry_container_path(resolver: PathResolverInstance, entry_id: str, service_view: str) -> str:
    return mount_render.runtime_entry_container_path(resolver, entry_id, service_view, fail=fail)


def render_source_path(resolver: PathResolverInstance, mount: dict[str, Any]) -> str:
    return mount_render.render_source_path(
        resolver,
        mount,
        env_host_state_root=ENV_HOST_STATE_ROOT,
        fail=fail,
    )


def render_container_path(resolver: PathResolverInstance, mount: dict[str, Any]) -> str:
    return mount_render.render_container_path(resolver, mount, fail=fail)


def render_mount_suffix(mount: dict[str, Any]) -> str:
    return mount_render.render_mount_suffix(mount, fail=fail)


def render_mount_line(resolver: PathResolverInstance, mount: dict[str, Any], indent: str) -> str:
    return mount_render.render_mount_line(
        resolver,
        mount,
        indent=indent,
        env_host_state_root=ENV_HOST_STATE_ROOT,
        fail=fail,
    )


def _service_index(*, config_path: Path | None = None) -> dict[str, dict[str, Any]]:
    return {str(row.get('service') or '').strip(): row for row in services(config_path=config_path) if str(row.get('service') or '').strip()}


def _indent_block(text: str, indent: str) -> list[str]:
    return mount_sync.indent_block(text, indent)


def _compose_service_fragment_text(config_path: Path | None = None) -> str:
    return mount_sync.compose_service_fragment_text(
        root_dir=ROOT_DIR,
        config_path=_resolve_config_path(config_path),
    )


def _sync_extension_service_blocks(content: str, *, config_path: Path | None = None) -> str:
    return mount_sync.sync_extension_service_blocks(
        content,
        extension_services_begin=EXTENSION_SERVICES_BEGIN,
        extension_services_end=EXTENSION_SERVICES_END,
        root_dir=ROOT_DIR,
        config_path=_resolve_config_path(config_path),
        fail=fail,
    )


def sync_compose(content: str, config_path: Path | None = None) -> str:
    return mount_sync.sync_compose(
        content,
        root_dir=ROOT_DIR,
        manifest_path=MANIFEST_PATH,
        env_host_state_root=ENV_HOST_STATE_ROOT,
        extension_services_begin=EXTENSION_SERVICES_BEGIN,
        extension_services_end=EXTENSION_SERVICES_END,
        config_path=config_path,
        fail=fail,
    )


def sync_compose_entry(argv: list[str]) -> int:
    if any(arg in {'-h', '--help'} for arg in argv):
        sys.stdout.write(
            '用法：\n'
            f'  {RUNTIME_MOUNTS_SYNC_COMPOSE_CMD}\n'
            f'  {RUNTIME_MOUNTS_SYNC_COMPOSE_CMD} --check\n'
            f'  {RUNTIME_MOUNTS_SYNC_COMPOSE_CMD} --stdout\n'
            f'  {RUNTIME_MOUNTS_SYNC_COMPOSE_CMD} --output <current-host-state-root>/control_plane/setup/docker-compose.effective.yml\n'
            f'  {RUNTIME_MOUNTS_SYNC_COMPOSE_CMD} --config-path {DEFAULT_RUNTIME_CONTROL_PLANE_SERVICE_CONFIG_REL_PATH}\n'
        )
        return 0
    try:
        values, _ = parse_typed_flag_args(
            argv,
            specs={
                'compose-file': FlagSpec(kind='path', dest='compose_path', default=compose_file_path()),
                'config-path': FlagSpec(kind='path', dest='config_path', default=None),
                'output': FlagSpec(kind='path', dest='output_path', default=None),
                'check': FlagSpec(kind='bool', dest='check_only', default=False),
                'stdout': FlagSpec(kind='bool', dest='mode_stdout', default=False),
            },
            allow_positionals=False,
        )
    except CliError as exc:
        fail(str(exc), exc.exit_code)
    compose_path = values['compose_path']
    output_path = values['output_path']
    check_only = bool(values['check_only'])
    mode_stdout = bool(values['mode_stdout'])
    config_path = values['config_path']
    if output_path is not None and (check_only or mode_stdout):
        fail('--output 不能与 --check 或 --stdout 同时使用')
    resolved_config_path = _resolve_config_path(config_path)
    if config_path is not None:
        compose_path = compose_path if compose_path != compose_file_path() else compose_file_path(config_path=resolved_config_path)
    content = compose_path.read_text(encoding='utf-8')
    rendered = sync_compose(content, config_path=resolved_config_path)
    if mode_stdout:
        sys.stdout.write(rendered)
        return 0 if rendered == content else 1
    if check_only:
        if rendered == content:
            sys.stdout.write('[compose_mount_registry] docker-compose 挂载块已同步\n')
            return 0
        sys.stderr.write('[compose_mount_registry] docker-compose 挂载块已漂移\n')
        return 2
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding='utf-8')
        try:
            label = str(output_path.relative_to(ROOT_DIR))
        except ValueError:
            label = str(output_path)
        sys.stdout.write(f'[compose_mount_registry] 已生成 effective compose：{label}\n')
        return 0
    compose_path.write_text(rendered, encoding='utf-8')
    sys.stdout.write(f'[compose_mount_registry] 已同步 compose 挂载块：{compose_path.relative_to(ROOT_DIR)}\n')
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        fail('缺少子命令；当前仅支持 sync-compose')
    command = args.pop(0)
    if command == 'sync-compose':
        return sync_compose_entry(args)
    fail(f'未知子命令：{command}')


if __name__ == '__main__':
    raise SystemExit(main())
