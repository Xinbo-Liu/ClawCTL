#!/usr/bin/env python3
"""扩展 manifest 的公共加载与查询辅助。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
import re

from openclaw.lib.repo.managed_extensions import managed_explicit_extensions
from openclaw.lib.runtime.execution import import_callable

from openclaw.control_plane.extensions.conflicts import _validate_enabled_manifest_conflicts
from openclaw.control_plane.extensions.loading import _load_manifest_rows, _read_service_payload
from openclaw.control_plane.extensions.normalization import ExtensionError, _normalize_text_list


ExtensionCallable = Callable[..., Any]


_PLATFORM_EXTENSION_ID = 'agent_platform'
_VERSION_CLAUSE_RE = re.compile(r'^(>=|<=|>|<|==|=)?\s*([0-9]+(?:\.[0-9]+){0,2}(?:[-+][0-9A-Za-z_.-]+)?)$')


def _version_tuple(value: object) -> tuple[int, int, int]:
    text = str(value or '').strip().split('-', 1)[0].split('+', 1)[0]
    parts = [int(part) for part in text.split('.') if part.isdigit()]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])  # type: ignore[return-value]


def _version_satisfies(version: str, requirement: str) -> bool:
    normalized_requirement = str(requirement or '').strip()
    if not normalized_requirement:
        return True
    current = _version_tuple(version)
    for clause in [part.strip() for part in normalized_requirement.split(',') if part.strip()]:
        match = _VERSION_CLAUSE_RE.match(clause)
        if not match:
            raise ExtensionError(f'unsupported extension dependency version constraint: {clause}')
        op = match.group(1) or '=='
        expected = _version_tuple(match.group(2))
        if op in {'=', '=='} and current != expected:
            return False
        if op == '>=' and current < expected:
            return False
        if op == '<=' and current > expected:
            return False
        if op == '>' and current <= expected:
            return False
        if op == '<' and current >= expected:
            return False
    return True


def _validate_enabled_extension_dependencies(ordered: list[dict[str, Any]]) -> None:
    manifests_by_id = {str(item.get('id') or '').strip(): item for item in ordered if str(item.get('id') or '').strip()}
    enabled_ids = [str(item.get('id') or '').strip() for item in ordered if str(item.get('id') or '').strip()]
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(extension_id: str, stack: list[str]) -> None:
        if extension_id in visiting:
            raise ExtensionError(f'extension dependency cycle: {" -> ".join([*stack, extension_id])}')
        if extension_id in visited:
            return
        visiting.add(extension_id)
        manifest = manifests_by_id[extension_id]
        for dep in manifest.get('dependencies') or []:
            if not isinstance(dep, dict):
                continue
            dep_id = str(dep.get('id') or '').strip()
            if not dep_id or bool(dep.get('optional', False)):
                continue
            dep_manifest = manifests_by_id.get(dep_id)
            if dep_manifest is None:
                raise ExtensionError(f'extension {extension_id} dependency not enabled: {dep_id}')
            requirement = str(dep.get('version') or '').strip()
            dep_version = str(dep_manifest.get('version') or '').strip()
            if requirement and not _version_satisfies(dep_version, requirement):
                raise ExtensionError(
                    f'extension {extension_id} dependency {dep_id} version mismatch: '
                    f'requires {requirement}, current {dep_version or "<missing>"}'
                )
            visit(dep_id, [*stack, extension_id])
        visiting.remove(extension_id)
        visited.add(extension_id)

    for extension_id in enabled_ids:
        visit(extension_id, [])

    position = {extension_id: index for index, extension_id in enumerate(enabled_ids)}
    for extension_id in enabled_ids:
        manifest = manifests_by_id[extension_id]
        for dep in manifest.get('dependencies') or []:
            if not isinstance(dep, dict) or bool(dep.get('optional', False)):
                continue
            dep_id = str(dep.get('id') or '').strip()
            if dep_id and position.get(dep_id, 10**9) > position.get(extension_id, -1):
                raise ExtensionError(f'extension {extension_id} dependency order invalid: {dep_id} must be enabled before {extension_id}')


def _validate_platform_baseline(enabled_ids: list[str]) -> None:
    business_ids = [extension_id for extension_id in enabled_ids if extension_id != _PLATFORM_EXTENSION_ID]
    if not business_ids:
        return
    if _PLATFORM_EXTENSION_ID not in enabled_ids:
        raise ExtensionError(
            f'non-platform extensions require {_PLATFORM_EXTENSION_ID} to be enabled first: '
            f'{", ".join(business_ids)}'
        )
    platform_index = enabled_ids.index(_PLATFORM_EXTENSION_ID)
    first_business_index = min(enabled_ids.index(extension_id) for extension_id in business_ids)
    if platform_index > first_business_index:
        raise ExtensionError(
            f'{_PLATFORM_EXTENSION_ID} must be enabled before non-platform extensions: '
            f'{", ".join(business_ids)}'
        )


def load_extension_manifests(service_payload: dict[str, Any], *, service_base_dir: Path) -> list[dict[str, Any]]:
    """加载配置可见的全部扩展 manifest，不过滤启用状态。"""
    return list(
        _load_manifest_rows(
            service_payload,
            service_base_dir=service_base_dir,
            duplicate_label='Duplicate extension id',
        ).values()
    )


def load_enabled_extensions(service_payload: dict[str, Any], *, service_base_dir: Path) -> list[dict[str, Any]]:
    """按 enabledExtensionIds 顺序加载已启用扩展，并校验扩展间冲突。"""
    extensions = service_payload.get('extensions') if isinstance(service_payload.get('extensions'), dict) else {}
    enabled_ids = _normalize_text_list(extensions.get('enabledExtensionIds'), label='control plane extensions.enabledExtensionIds')
    if not enabled_ids:
        return []
    _validate_platform_baseline(enabled_ids)
    enabled_set = set(enabled_ids)
    manifest_by_id = _load_manifest_rows(
        service_payload,
        service_base_dir=service_base_dir,
        selected_ids=enabled_set,
        duplicate_label='Duplicate enabled extension id',
        ignore_read_errors=True,
    )
    missing = [extension_id for extension_id in enabled_ids if extension_id not in manifest_by_id]
    if missing:
        raise ExtensionError(f'Enabled extension manifests not found or invalid: {", ".join(missing)}')
    ordered = [manifest_by_id[extension_id] for extension_id in enabled_ids]
    _validate_enabled_extension_dependencies(ordered)
    _validate_enabled_manifest_conflicts(ordered)
    return ordered


def enabled_extensions_from_config(config_path: Path | None = None) -> list[dict[str, Any]]:
    path, payload = _read_service_payload(config_path)
    return load_enabled_extensions(payload, service_base_dir=path.parent)


def known_extensions_from_config(config_path: Path | None = None) -> list[dict[str, Any]]:
    path, payload = _read_service_payload(config_path)
    return load_extension_manifests(payload, service_base_dir=path.parent)


def discover_known_extension_ids(start_path: Path | None = None) -> set[str]:
    """从显式索引与有效自动发现结果中发现已知扩展 id。"""
    return {
        row.id
        for row in managed_explicit_extensions(
            Path(__file__) if start_path is None else Path(start_path).resolve()
        )
    }


def import_extension_callable(module_name: str, attr_name: str) -> ExtensionCallable:
    """导入扩展暴露的 callable，并把缺失成员转换为扩展语义错误。"""
    try:
        return import_callable(module_name, attr_name, ExtensionError, '扩展目标')
    except ExtensionError as exc:
        if f'缺少可调用成员：{module_name}.{attr_name}' in str(exc):
            raise ExtensionError(f'扩展目标不是可调用成员：{module_name}.{attr_name}') from exc
        raise


def _cli_commands_from_manifests(manifests: list[dict[str, Any]]) -> dict[str, str]:
    commands: dict[str, str] = {}
    for manifest in manifests:
        extension_id = str(manifest.get('id') or '').strip() or '<unknown-extension>'
        for row in manifest.get('cliCommands') or []:
            command = str(row.get('command') or '').strip()
            module_name = str(row.get('module') or '').strip()
            if not command or not module_name:
                continue
            existing_module = commands.get(command)
            if existing_module is not None and existing_module != module_name:
                raise ExtensionError(f'extension CLI command conflict: {command} ({extension_id})')
            commands[command] = module_name
    return commands


def extension_cli_commands(config_path: Path | None = None) -> dict[str, str]:
    """返回启用扩展贡献的 CLI 命令映射。"""
    return _cli_commands_from_manifests(enabled_extensions_from_config(config_path))


def known_extension_cli_commands(config_path: Path | None = None) -> dict[str, str]:
    """返回当前配置可见扩展贡献的 CLI 命令映射。"""
    return _cli_commands_from_manifests(known_extensions_from_config(config_path))


def extension_internal_api_routes(config_path: Path | None = None) -> list[dict[str, Any]]:
    """返回启用扩展贡献的 internal-api route 定义。"""
    routes: list[dict[str, Any]] = []
    for manifest in enabled_extensions_from_config(config_path):
        for row in manifest.get('internalApiRoutes') or []:
            if isinstance(row, dict):
                routes.append(dict(row, extensionId=str(manifest.get('id') or '')))
    return routes


def extension_ready_checks(config_path: Path | None = None) -> list[dict[str, Any]]:
    """返回启用扩展贡献的 ready check 定义。"""
    checks: list[dict[str, Any]] = []
    for manifest in enabled_extensions_from_config(config_path):
        for row in manifest.get('readyChecks') or []:
            if isinstance(row, dict):
                checks.append(dict(row, extensionId=str(manifest.get('id') or '')))
    return checks
