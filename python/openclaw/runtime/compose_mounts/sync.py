#!/usr/bin/env python3
"""Compose synchronization helpers for runtime compose mounts."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from openclaw.control_plane.extensions.fragments import iter_surface_fragment_paths
from openclaw.lib.runtime.resolver_loader import require_path_resolver

from openclaw.runtime.compose_mounts import manifest as mount_manifest
from openclaw.runtime.compose_mounts import render as mount_render


def indent_block(text: str, indent: str) -> list[str]:
    lines = text.strip('\n').splitlines()
    rendered: list[str] = []
    for line in lines:
        if not line.strip():
            rendered.append('')
        else:
            rendered.append(f'{indent}{line}')
    return rendered


def compose_service_fragment_text(
    *,
    root_dir: Path,
    config_path: Path | None,
) -> str:
    blocks: list[str] = []
    for _, fragment_path in iter_surface_fragment_paths(
        config_path=config_path,
        key='composeServicesPath',
    ):
        text = fragment_path.read_text(encoding='utf-8').strip('\n')
        if text:
            blocks.append(text)
    return '\n\n'.join(blocks)


def sync_extension_service_blocks(
    content: str,
    *,
    extension_services_begin: str,
    extension_services_end: str,
    root_dir: Path,
    config_path: Path,
    fail: Callable[[str, int], None],
) -> str:
    pattern = re.compile(rf'(?P<indent>[ \t]*){re.escape(extension_services_begin)}\n.*?(?P=indent){re.escape(extension_services_end)}', re.S)
    match = pattern.search(content)
    if not match:
        fail('compose 中缺少 extension services 块标记', 2)
    indent = match.group('indent')
    block_lines = [f'{indent}{extension_services_begin}']
    fragment_text = compose_service_fragment_text(root_dir=root_dir, config_path=config_path)
    if fragment_text:
        block_lines.extend(indent_block(fragment_text, indent))
    block_lines.append(f'{indent}{extension_services_end}')
    replacement = '\n'.join(block_lines)
    return content[:match.start()] + replacement + content[match.end():]


def sync_compose(
    content: str,
    *,
    root_dir: Path,
    manifest_path: Path,
    env_host_state_root: str,
    extension_services_begin: str,
    extension_services_end: str,
    config_path: Path | None,
    fail: Callable[[str, int], None],
) -> str:
    resolved_config_path = mount_manifest.resolve_config_path(root_dir, config_path)
    content = sync_extension_service_blocks(
        content,
        extension_services_begin=extension_services_begin,
        extension_services_end=extension_services_end,
        root_dir=root_dir,
        config_path=resolved_config_path,
        fail=fail,
    )
    resolver = require_path_resolver(repo_root=root_dir, config_path=resolved_config_path)
    enabled_ids = mount_manifest.enabled_extension_ids_for(root_dir, config_path=resolved_config_path)
    prefix = mount_manifest.marker_prefix(
        root_dir=root_dir,
        manifest_path=manifest_path,
        fail=fail,
        config_path=resolved_config_path,
    )
    services = mount_manifest.services(
        root_dir=root_dir,
        manifest_path=manifest_path,
        fail=fail,
        config_path=resolved_config_path,
    )
    service_index = {str(row.get('service') or '').strip(): row for row in services if str(row.get('service') or '').strip()}
    for service_name, payload in service_index.items():
        service_enabled = mount_manifest.service_is_enabled(payload, enabled_ids, fail=fail)
        begin = f'# {prefix}_BEGIN {service_name}'
        end = f'# {prefix}_END {service_name}'
        pattern = re.compile(rf'(?P<indent>[ \t]*){re.escape(begin)}\n.*?(?P=indent){re.escape(end)}', re.S)
        match = pattern.search(content)
        if not match:
            if service_enabled:
                fail(f'compose 中缺少挂载块标记：{service_name}', 2)
            continue
        indent = match.group('indent')
        block_lines = [f'{indent}{begin}']
        for mount in list(payload.get('mounts') or []):
            if not isinstance(mount, dict):
                continue
            if not mount_manifest.mount_is_enabled(mount, enabled_ids, fail=fail):
                continue
            block_lines.append(
                mount_render.render_mount_line(
                    resolver,
                    mount,
                    indent=indent,
                    env_host_state_root=env_host_state_root,
                    fail=fail,
                )
            )
        block_lines.append(f'{indent}{end}')
        replacement = '\n'.join(block_lines)
        content = content[:match.start()] + replacement + content[match.end():]
    return content
