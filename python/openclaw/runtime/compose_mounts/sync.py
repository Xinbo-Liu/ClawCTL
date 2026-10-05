#!/usr/bin/env python3
"""提供OpenClaw 运行态子系统的生产实现。"""
from __future__ import annotations

import os
import re
import subprocess
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


def _default_route_interface() -> str:
    try:
        result = subprocess.run(
            ['ip', 'route', 'get', '1.1.1.1'],
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return ''
    if result.returncode != 0:
        return ''
    tokens = result.stdout.split()
    for index, token in enumerate(tokens):
        if token == 'dev' and index + 1 < len(tokens):
            return tokens[index + 1]
    return ''


def default_route_mtu() -> int | None:
    """读取宿主默认路由 MTU；读取失败时返回 None。"""

    try:
        result = subprocess.run(
            ['ip', 'route', 'get', '1.1.1.1'],
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0:
        tokens = result.stdout.split()
        for index, token in enumerate(tokens):
            if token == 'mtu' and index + 1 < len(tokens):
                try:
                    return int(tokens[index + 1])
                except ValueError:
                    return None
    interface = _default_route_interface()
    if not interface:
        return None
    mtu_path = Path('/sys/class/net') / interface / 'mtu'
    try:
        value = int(mtu_path.read_text(encoding='utf-8').strip())
    except (OSError, ValueError):
        return None
    return value


def requested_docker_network_mtu(*, fail: Callable[[str, int], None]) -> int | None:
    """解析 OPENCLAW_DOCKER_NETWORK_MTU；auto 只在宿主 MTU 低于 1500 时写入。"""

    raw = str(os.environ.get('OPENCLAW_DOCKER_NETWORK_MTU') or 'auto').strip().lower()
    if raw in {'', 'auto'}:
        mtu = default_route_mtu()
        if mtu is not None and mtu < 1500:
            return mtu
        return None
    if not raw.isdigit():
        fail('OPENCLAW_DOCKER_NETWORK_MTU 仅支持 auto 或整数', 2)
    mtu = int(raw)
    if mtu < 576 or mtu > 9000:
        fail('OPENCLAW_DOCKER_NETWORK_MTU 必须位于 576..9000', 2)
    return mtu


def _driver_opts_block(indent: str, mtu: int) -> list[str]:
    return [
        f'{indent}driver_opts:',
        f'{indent}  com.docker.network.driver.mtu: "{mtu}"',
    ]


def _driver_opts_mtu_line(indent: str, mtu: int) -> str:
    """渲染 Docker bridge 网络 driver_opts 中的 MTU 配置行。

    参数：
        indent（str）：driver_opts 所在 YAML 缩进，用于派生 MTU 子键缩进。
        mtu（int）：写入 com.docker.network.driver.mtu 的数值。

    返回：
        返回 str，表示可直接写入 compose networks 块的 MTU 配置行。
    """
    return f'{indent}  com.docker.network.driver.mtu: "{mtu}"'


def _network_block_has_bridge(block: list[str]) -> bool:
    return any(re.match(r'^[ \t]+driver:[ \t]*bridge[ \t]*$', line) for line in block)


def _is_network_entry_line(line: str) -> bool:
    """判断 compose networks 块中的一行是否为二级网络条目。

    参数：
        line（str）：待检查的 compose YAML 原始行。

    返回：
        返回 bool，True 表示该行形如 ``  network_name:``，可作为网络块边界。
    """
    return bool(re.match(r'^[ \t]{2}[^ \t:#][^:]*:[ \t]*$', line))


def _sync_network_block_mtu(block: list[str], mtu: int) -> list[str]:
    if not _network_block_has_bridge(block):
        return block
    output: list[str] = []
    index = 0
    inserted = False
    has_driver_opts = any(re.match(r'^[ \t]+driver_opts:[ \t]*$', line) for line in block)
    while index < len(block):
        line = block[index]
        if re.match(r'^[ \t]+driver_opts:[ \t]*$', line):
            indent = line[: len(line) - len(line.lstrip())]
            child_indent = f'{indent}  '
            mtu_line = _driver_opts_mtu_line(indent, mtu)
            child_lines: list[str] = []
            mtu_seen = False
            output.append(line)
            index += 1
            while index < len(block):
                child = block[index]
                if child.startswith(f'{indent}  ') or not child.strip():
                    if child.startswith(child_indent) and re.match(
                        rf'^{re.escape(child_indent)}com\.docker\.network\.driver\.mtu:[ \t]*',
                        child,
                    ):
                        if not mtu_seen:
                            child_lines.append(mtu_line)
                            mtu_seen = True
                        index += 1
                        continue
                    child_lines.append(child)
                    index += 1
                    continue
                break
            if not mtu_seen:
                output.append(mtu_line)
            output.extend(child_lines)
            inserted = True
            continue
        output.append(line)
        if not inserted and not has_driver_opts and re.match(r'^[ \t]+driver:[ \t]*bridge[ \t]*$', line):
            indent = line[: len(line) - len(line.lstrip())]
            output.extend(_driver_opts_block(indent, mtu))
            inserted = True
        index += 1
    return output


def sync_bridge_network_mtu(content: str, mtu: int | None) -> str:
    """把 Docker bridge network MTU 写入 compose networks 块。"""

    if mtu is None:
        return content
    lines = content.splitlines()
    try:
        networks_index = next(index for index, line in enumerate(lines) if line.strip() == 'networks:' and not line.startswith((' ', '\t')))
    except StopIteration:
        return content
    result = lines[: networks_index + 1]
    index = networks_index + 1
    while index < len(lines):
        line = lines[index]
        if line and not line.startswith((' ', '\t')):
            result.extend(lines[index:])
            break
        if _is_network_entry_line(line):
            block = [line]
            index += 1
            while index < len(lines):
                current = lines[index]
                if current and not current.startswith((' ', '\t')):
                    break
                if _is_network_entry_line(current):
                    break
                block.append(current)
                index += 1
            result.extend(_sync_network_block_mtu(block, mtu))
            continue
        result.append(line)
        index += 1
    else:
        return '\n'.join(result) + ('\n' if content.endswith('\n') else '')
    return '\n'.join(result) + ('\n' if content.endswith('\n') else '')


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
    content = sync_bridge_network_mtu(content, requested_docker_network_mtu(fail=fail))
    return content
