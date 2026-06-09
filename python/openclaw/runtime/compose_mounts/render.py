#!/usr/bin/env python3
"""Mount rendering helpers for runtime compose mounts."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from openclaw.lib.runtime.resolver_loader import PathResolverInstance


def runtime_entry_host_template(
    resolver: PathResolverInstance,
    entry_id: str,
    *,
    env_host_state_root: str,
    fail: Callable[[str, int], None],
) -> str:
    try:
        rendered = resolver.resolve_path(entry_id, view='host')
    except KeyError as exc:
        fail(str(exc), 2)
    host_state_root = resolver.roots.get('host_state_root') or '<current-host-state-root>'
    rendered = str(rendered).replace(str(host_state_root), env_host_state_root).replace('<current-host-state-root>', env_host_state_root)
    unresolved = re.search(r'(?<!\$)\{[^{}]+\}', rendered)
    if unresolved:
        fail(f'runtime_paths entry 仍包含未解析占位符：{entry_id} -> {rendered}', 2)
    path = Path(rendered)
    if path.is_absolute():
        return rendered
    return f'../{rendered}'


def runtime_entry_container_path(
    resolver: PathResolverInstance,
    entry_id: str,
    service_view: str,
    *,
    fail: Callable[[str, int], None],
) -> str:
    try:
        return resolver.resolve_path(entry_id, view=service_view)
    except KeyError as exc:
        fail(str(exc), 2)


def render_source_path(
    resolver: PathResolverInstance,
    mount: dict[str, object],
    *,
    env_host_state_root: str,
    fail: Callable[[str, int], None],
) -> str:
    source_type = str(mount.get('source_type') or '').strip()
    if source_type == 'runtime_path_entry':
        return runtime_entry_host_template(
            resolver,
            str(mount.get('entry') or '').strip(),
            env_host_state_root=env_host_state_root,
            fail=fail,
        )
    if source_type == 'repo_path':
        rel = str(mount.get('relative_path') or '').strip()
        if not rel:
            fail('repo_path 缺少 relative_path', 2)
        return f'../{rel}'
    if source_type == 'compose_relpath':
        rel = str(mount.get('relative_path') or '').strip()
        if not rel:
            fail('compose_relpath 缺少 relative_path', 2)
        return f'./{rel}'
    fail(f'未知 mount source_type：{source_type}', 2)
    raise AssertionError('unreachable')


def render_container_path(
    resolver: PathResolverInstance,
    mount: dict[str, object],
    *,
    fail: Callable[[str, int], None],
) -> str:
    explicit = str(mount.get('container_path') or '').strip()
    if explicit:
        return explicit
    source_type = str(mount.get('source_type') or '').strip()
    if source_type != 'runtime_path_entry':
        fail(f'{source_type} 必须显式提供 container_path', 2)
    entry_id = str(mount.get('entry') or '').strip()
    service_view = str(mount.get('service_view') or '').strip() or 'gateway'
    return runtime_entry_container_path(resolver, entry_id, service_view, fail=fail)


def render_mount_suffix(mount: dict[str, object], *, fail: Callable[[str, int], None]) -> str:
    mode = str(mount.get('mode') or 'rw').strip().lower()
    selinux = str(mount.get('selinux') or '').strip()
    items: list[str] = []
    if mode == 'ro':
        items.append('ro')
    elif mode != 'rw':
        fail(f'未知 mount mode：{mode}', 2)
    if selinux:
        items.append(selinux)
    return ':' + ','.join(items) if items else ''


def render_mount_line(
    resolver: PathResolverInstance,
    mount: dict[str, object],
    indent: str,
    *,
    env_host_state_root: str,
    fail: Callable[[str, int], None],
) -> str:
    source = render_source_path(resolver, mount, env_host_state_root=env_host_state_root, fail=fail)
    target = render_container_path(resolver, mount, fail=fail)
    suffix = render_mount_suffix(mount, fail=fail)
    description = str(mount.get('description') or '').strip()
    comment = f'  # {description}' if description else ''
    return f'{indent}- {source}:{target}{suffix}{comment}'
