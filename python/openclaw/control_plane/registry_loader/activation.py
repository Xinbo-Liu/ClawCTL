#!/usr/bin/env python3
"""Helpers for extension-owned shared object activation filtering."""
from __future__ import annotations

from typing import Any, Iterable

from openclaw.lib.cli.common import CliError
from openclaw.lib.io.json_access import json_object


def _normalize_extension_ids(value: Any, *, label: str) -> list[str]:
    if value in (None, ''):
        return []
    if not isinstance(value, list):
        raise CliError(f'{label} 必须是字符串数组', 2)
    normalized: list[str] = []
    for idx, item in enumerate(value):
        extension_id = str(item or '').strip()
        if not extension_id:
            raise CliError(f'{label}[{idx}] 不能为空', 2)
        if extension_id in normalized:
            raise CliError(f'{label} 不允许重复值：{extension_id}', 2)
        normalized.append(extension_id)
    return normalized


def resolve_object_activation(
    payload: dict[str, Any],
    *,
    label: str,
    enabled_extension_ids: Iterable[str],
    known_extension_ids: Iterable[str],
    require_activation: bool,
) -> dict[str, Any]:
    activation = json_object(payload.get('activation'))
    configured_extension_ids = _normalize_extension_ids(
        activation.get('enabledExtensionIds'),
        label=f'{label} activation.enabledExtensionIds',
    ) if activation else []
    if require_activation and not configured_extension_ids:
        raise CliError(f'{label} 必须声明 activation.enabledExtensionIds', 2)
    known_extension_id_set = {
        str(item or '').strip()
        for item in known_extension_ids
        if str(item or '').strip()
    }
    unknown_extension_ids = [
        extension_id
        for extension_id in configured_extension_ids
        if extension_id not in known_extension_id_set
    ]
    if unknown_extension_ids:
        raise CliError(
            f'{label} activation.enabledExtensionIds 引用了未知 extension：{", ".join(unknown_extension_ids)}',
            2,
        )
    enabled_extension_id_list = [
        str(item or '').strip()
        for item in enabled_extension_ids
        if str(item or '').strip()
    ]
    active_extension_ids = [
        extension_id
        for extension_id in enabled_extension_id_list
        if extension_id in configured_extension_ids
    ]
    return {
        'configuredExtensionIds': configured_extension_ids,
        'activeExtensionIds': active_extension_ids,
        'primaryActiveExtensionId': active_extension_ids[0] if active_extension_ids else '',
        'visible': bool(active_extension_ids) if configured_extension_ids else not require_activation,
    }
