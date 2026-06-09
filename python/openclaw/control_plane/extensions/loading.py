#!/usr/bin/env python3
"""扩展 manifest 加载辅助。"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from openclaw.control_plane.config_loader import ControlPlaneConfigError, load_control_plane_service_payload
from openclaw.control_plane.manifest_fields import (
    DISPATCH_PROVIDER_REGISTRY_PATHS_KEY,
    DISPATCH_TARGET_REGISTRY_PATHS_KEY,
    GOVERNANCE_SURFACES_FIELD,
    RUNTIME_ADAPTER_REGISTRY_PATHS_KEY,
    SURFACE_FRAGMENTS_FIELD,
)
from openclaw.lib.repo.layout import resolve_control_plane_service_config_path
from openclaw.lib.repo.repo_root import RepoRootResolutionError, resolve_repo_root

from openclaw.control_plane.extensions.normalization import (
    ExtensionError,
    _normalize_manifest,
    _optional_text,
    _resolve_optional_dir,
)

_NORMALIZED_MANIFEST_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}
_PLATFORM_EXTENSION_ID = 'agent_platform'
_PLATFORM_MANIFEST_REL_PATH = Path('config/control_plane/extensions.d/agent_platform.json')
_EXTENSION_ID_RE = re.compile(r'^[a-z0-9_]+$')
_REGISTRY_COLLECTION_DIR_KEYS = (
    'jobsDirs',
    'modelsDirs',
    'targetsDirs',
    'agentGroupsDirs',
    'agentModulesDirs',
)
_REGISTRY_FILE_KEYS = (
    RUNTIME_ADAPTER_REGISTRY_PATHS_KEY,
    DISPATCH_TARGET_REGISTRY_PATHS_KEY,
    DISPATCH_PROVIDER_REGISTRY_PATHS_KEY,
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError as exc:
        raise ExtensionError(f'扩展 manifest 不存在：{path}') from exc
    except Exception as exc:  # pragma: no cover
        # JSON 读入阶段可能同时失败于编码、权限或解析；统一折叠为扩展加载错误。
        raise ExtensionError(f'扩展 manifest 解析失败：{path} ({exc})') from exc
    if not isinstance(payload, dict):
        raise ExtensionError(f'扩展 manifest 顶层必须为对象：{path}')
    return payload


def _normalize_manifest_cached(path: Path, raw_payload: dict[str, Any]) -> dict[str, Any]:
    resolved = Path(path).resolve()
    try:
        stat = resolved.stat()
    except FileNotFoundError:
        return _normalize_manifest(resolved, raw_payload)
    cache_key = (str(resolved), stat.st_mtime_ns, stat.st_size)
    cached = _NORMALIZED_MANIFEST_CACHE.get(cache_key)
    if cached is None:
        if len(_NORMALIZED_MANIFEST_CACHE) >= 256:
            _NORMALIZED_MANIFEST_CACHE.clear()
        cached = _normalize_manifest(resolved, raw_payload)
        _NORMALIZED_MANIFEST_CACHE[cache_key] = cached
    return deepcopy(cached)


def _path_is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _service_repo_root(service_base_dir: Path) -> Path:
    try:
        repo_root = resolve_repo_root(service_base_dir)
    except RepoRootResolutionError as exc:
        raise ExtensionError(
            'control plane service configs that load extensions must stay inside '
            'the repository root and use repository contract paths'
        ) from exc
    if not _path_is_relative_to(service_base_dir, repo_root):
        raise ExtensionError(
            'control plane service configs that load extensions must stay inside '
            'the repository root and use repository contract paths'
        )
    return repo_root


def _expected_manifest_path(repo_root: Path, extension_id: str) -> Path:
    if extension_id == _PLATFORM_EXTENSION_ID:
        return (repo_root / _PLATFORM_MANIFEST_REL_PATH).resolve()
    return (
        repo_root
        / 'agent'
        / 'extensions'
        / extension_id
        / 'config'
        / 'control_plane'
        / 'extensions.d'
        / f'{extension_id}.json'
    ).resolve()


def _extension_root(repo_root: Path, extension_id: str) -> Path:
    return (repo_root / 'agent' / 'extensions' / extension_id).resolve()


def _validate_manifest_dir_contract(
    *,
    path: Path,
    repo_root: Path,
    label: str,
) -> None:
    resolved = path.resolve()
    platform_dir = (repo_root / 'config' / 'control_plane' / 'extensions.d').resolve()
    if resolved == platform_dir:
        return
    extensions_root = (repo_root / 'agent' / 'extensions').resolve()
    try:
        relative = resolved.relative_to(extensions_root)
    except ValueError as exc:
        raise ExtensionError(f'{label} must use repository contract manifest dirs: {resolved}') from exc
    parts = relative.parts
    if len(parts) != 4 or parts[1:] != ('config', 'control_plane', 'extensions.d') or not parts[0]:
        raise ExtensionError(f'{label} must use repository contract manifest dirs: {resolved}')
    if not _EXTENSION_ID_RE.match(parts[0]):
        raise ExtensionError(f'{label} must use repository contract manifest dirs: {resolved}')


def _validate_extension_path(
    *,
    extension_id: str,
    label: str,
    path: Path,
    extension_root: Path,
) -> None:
    if not _path_is_relative_to(path, extension_root):
        raise ExtensionError(f'extension {extension_id} {label} escapes extension root: {path}')


def _validate_repo_path(
    *,
    extension_id: str,
    label: str,
    path: Path,
    repo_root: Path,
) -> None:
    if not _path_is_relative_to(path, repo_root):
        raise ExtensionError(f'extension {extension_id} {label} escapes repository root: {path}')


def _extension_python_package_prefixes(extension_root: Path) -> tuple[str, ...]:
    python_root = (extension_root / 'python').resolve()
    if not python_root.is_dir():
        return ()
    return tuple(
        path.name
        for path in sorted(python_root.iterdir(), key=lambda item: item.name)
        if path.is_dir() and (path / '__init__.py').is_file()
    )


def _validate_extension_module_ref(
    *,
    extension_id: str,
    label: str,
    module_name: str,
    package_prefixes: tuple[str, ...],
) -> None:
    normalized = str(module_name or '').strip()
    if not normalized:
        return
    if normalized in package_prefixes or any(normalized.startswith(f'{prefix}.') for prefix in package_prefixes):
        return
    joined = ', '.join(package_prefixes) or '<missing python package>'
    raise ExtensionError(
        f'extension {extension_id} {label} must use an own extension python package '
        f'({joined}): {normalized}'
    )


def _callable_module_name(callable_ref: str) -> str:
    text = str(callable_ref or '').strip()
    if not text:
        return ''
    if ':' in text:
        return text.split(':', 1)[0].strip()
    module_name, _sep, _attr_name = text.rpartition('.')
    return module_name.strip()


def _validate_extension_callable_boundaries(
    manifest: dict[str, Any],
    *,
    extension_id: str,
    extension_root: Path,
) -> None:
    if extension_id == _PLATFORM_EXTENSION_ID:
        return
    package_prefixes = _extension_python_package_prefixes(extension_root)
    for idx, row in enumerate(manifest.get('jobRunners') or []):
        if isinstance(row, dict):
            _validate_extension_module_ref(
                extension_id=extension_id,
                label=f'jobRunners[{idx}].module',
                module_name=str(row.get('module') or ''),
                package_prefixes=package_prefixes,
            )
    for idx, row in enumerate(manifest.get('cliCommands') or []):
        if isinstance(row, dict):
            _validate_extension_module_ref(
                extension_id=extension_id,
                label=f'cliCommands[{idx}].module',
                module_name=str(row.get('module') or ''),
                package_prefixes=package_prefixes,
            )
    for idx, row in enumerate(manifest.get('internalApiRoutes') or []):
        if isinstance(row, dict):
            _validate_extension_module_ref(
                extension_id=extension_id,
                label=f'internalApiRoutes[{idx}].module',
                module_name=str(row.get('module') or ''),
                package_prefixes=package_prefixes,
            )
    for idx, row in enumerate(manifest.get('readyChecks') or []):
        if isinstance(row, dict):
            _validate_extension_module_ref(
                extension_id=extension_id,
                label=f'readyChecks[{idx}].module',
                module_name=str(row.get('module') or ''),
                package_prefixes=package_prefixes,
            )
    for idx, row in enumerate(manifest.get('migrations') or []):
        if isinstance(row, dict):
            _validate_extension_module_ref(
                extension_id=extension_id,
                label=f'migrations[{idx}].callable',
                module_name=_callable_module_name(str(row.get('callable') or '')),
                package_prefixes=package_prefixes,
            )


def _validate_manifest_boundaries(
    manifest: dict[str, Any],
    *,
    repo_root: Path,
    extension_id: str,
) -> None:
    extension_root = _extension_root(repo_root, extension_id)
    registry = manifest.get('registry') if isinstance(manifest.get('registry'), dict) else {}
    for key in (*_REGISTRY_COLLECTION_DIR_KEYS, *_REGISTRY_FILE_KEYS):
        for idx, path in enumerate(registry.get(key) or []):
            if isinstance(path, Path):
                label = f'registry.{key}[{idx}]'
                _validate_repo_path(extension_id=extension_id, label=label, path=path, repo_root=repo_root)
                if extension_id != _PLATFORM_EXTENSION_ID:
                    _validate_extension_path(
                        extension_id=extension_id,
                        label=label,
                        path=path,
                        extension_root=extension_root,
                    )
    _validate_extension_callable_boundaries(
        manifest,
        extension_id=extension_id,
        extension_root=extension_root,
    )
    schemas = manifest.get('schemas') if isinstance(manifest.get('schemas'), dict) else {}
    for key, path in schemas.items():
        if isinstance(path, Path):
            _validate_repo_path(extension_id=extension_id, label=f'schemas.{key}', path=path, repo_root=repo_root)
    for group_key in (SURFACE_FRAGMENTS_FIELD, GOVERNANCE_SURFACES_FIELD):
        group = manifest.get(group_key) if isinstance(manifest.get(group_key), dict) else {}
        for key, path in group.items():
            if isinstance(path, Path):
                label = f'{group_key}.{key}'
                _validate_repo_path(extension_id=extension_id, label=label, path=path, repo_root=repo_root)
                if extension_id != _PLATFORM_EXTENSION_ID:
                    _validate_extension_path(
                        extension_id=extension_id,
                        label=label,
                        path=path,
                        extension_root=extension_root,
                    )


def _validate_loaded_manifest_contract(
    manifest: dict[str, Any],
    *,
    path: Path,
    repo_root: Path,
) -> None:
    extension_id = str(manifest.get('id') or '').strip()
    expected = _expected_manifest_path(repo_root, extension_id)
    resolved = path.resolve()
    if resolved != expected:
        rel_expected = expected.relative_to(repo_root).as_posix()
        raise ExtensionError(
            f'extension {extension_id} manifest must be loaded from repository contract path: {rel_expected}'
        )
    _validate_manifest_boundaries(manifest, repo_root=repo_root, extension_id=extension_id)


def _read_service_payload(config_path: Path | None = None) -> tuple[Path, dict[str, Any]]:
    """读取控制面 service 配置并返回配置路径与已合并 payload。"""
    path = resolve_control_plane_service_config_path(Path(__file__)) if config_path is None else Path(config_path).resolve()
    try:
        _, payload = load_control_plane_service_payload(path)
    except ControlPlaneConfigError as exc:
        raise ExtensionError(str(exc)) from exc
    return path, payload


def _configured_manifest_dirs(
    service_payload: dict[str, Any],
    *,
    service_base_dir: Path,
    repo_root: Path,
) -> list[Path]:
    """解析 extensions.manifestsDirs，保留配置顺序并去重。"""
    extensions = service_payload.get('extensions') if isinstance(service_payload.get('extensions'), dict) else {}
    manifests_dirs: list[Path] = []
    raw_dirs = extensions.get('manifestsDirs')
    if raw_dirs not in (None, '') and not isinstance(raw_dirs, list):
        raise ExtensionError('control plane extensions.manifestsDirs must be a list')
    for idx, item in enumerate(raw_dirs if isinstance(raw_dirs, list) else []):
        resolved = _resolve_optional_dir(service_base_dir, item, label=f'control plane extensions.manifestsDirs[{idx}]')
        if resolved is not None and resolved not in manifests_dirs:
            _validate_manifest_dir_contract(
                path=resolved,
                repo_root=repo_root,
                label=f'control plane extensions.manifestsDirs[{idx}]',
            )
            manifests_dirs.append(resolved)
    return manifests_dirs


def _manifest_paths(service_payload: dict[str, Any], *, service_base_dir: Path, repo_root: Path) -> list[Path]:
    """列出所有 manifest 文件，跨目录按绝对路径去重。"""
    manifest_paths: list[Path] = []
    for manifests_dir in _configured_manifest_dirs(
        service_payload,
        service_base_dir=service_base_dir,
        repo_root=repo_root,
    ):
        manifest_paths.extend(sorted(manifests_dir.glob('*.json')))
    deduped: list[Path] = []
    seen_paths: set[str] = set()
    for path in manifest_paths:
        resolved = path.resolve()
        key = str(resolved)
        if key in seen_paths:
            continue
        seen_paths.add(key)
        deduped.append(resolved)
    return deduped


def _load_manifest_rows(
    service_payload: dict[str, Any],
    *,
    service_base_dir: Path,
    selected_ids: set[str] | None = None,
    duplicate_label: str = 'Duplicate extension id',
    ignore_read_errors: bool = False,
) -> dict[str, dict[str, Any]]:
    """加载并标准化 manifest 行，可按 selected_ids 过滤启用扩展。"""
    manifests_by_id: dict[str, dict[str, Any]] = {}
    repo_root = _service_repo_root(service_base_dir)
    for path in _manifest_paths(service_payload, service_base_dir=service_base_dir, repo_root=repo_root):
        try:
            raw_payload = _read_json(path)
        except ExtensionError:
            if ignore_read_errors:
                continue
            raise
        extension_id = _optional_text(raw_payload.get('id'))
        if not extension_id:
            continue
        manifest = _normalize_manifest_cached(path, raw_payload)
        _validate_loaded_manifest_contract(manifest, path=path, repo_root=repo_root)
        if selected_ids is not None and extension_id not in selected_ids:
            continue
        if extension_id in manifests_by_id:
            raise ExtensionError(f'{duplicate_label}: {extension_id}')
        manifests_by_id[extension_id] = manifest
    return manifests_by_id
