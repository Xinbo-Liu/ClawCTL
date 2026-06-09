#!/usr/bin/env python3
"""Managed explicit extension index and python-root helpers."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openclaw.control_plane.manifest_fields import (
    DISPATCH_PROVIDER_REGISTRY_PATHS_KEY,
    DISPATCH_TARGET_REGISTRY_PATHS_KEY,
    GOVERNANCE_SURFACES_FIELD,
    RUNTIME_ADAPTER_REGISTRY_PATHS_KEY,
    SURFACE_FRAGMENTS_FIELD,
)
from openclaw.lib.repo.path_contracts import resolve_path_contract

from .extension_discovery import discover_extension_profiles
from .repo_root import resolve_repo_root


MANAGED_EXTENSIONS_REL_DIR = 'agent/extensions'
MANAGED_EXTENSIONS_INDEX_REL_PATH = f'{MANAGED_EXTENSIONS_REL_DIR}/index.json'
MANAGED_EXPLICIT_EXTENSION_STATUS = 'managed_explicit_extension'
_INACTIVE_EXTENSION_STATUS = 'retired'
_PLATFORM_EXTENSION_ID = 'agent_platform'
_PLATFORM_MANIFEST_DIR_REL_PATH = 'config/control_plane/extensions.d'
_EXTENSION_ID_PATTERN = re.compile(r'^[a-z0-9_]+$')
_INDEX_ROOT_KEYS = ('extensions',)
_INDEX_EXTENSION_KEYS = (
    'id',
    'title',
    'rootDir',
    'defaultServiceConfigPath',
    'manifestDir',
    'pythonRoots',
    'status',
)
_INDEX_EXTENSION_STATUSES = (MANAGED_EXPLICIT_EXTENSION_STATUS, _INACTIVE_EXTENSION_STATUS)
_MANAGED_EXTENSION_REGISTRY_DIR_KEYS = (
    'jobsDirs',
    'modelsDirs',
    'targetsDirs',
    'agentGroupsDirs',
    'agentModulesDirs',
)
_MANAGED_EXTENSION_REGISTRY_FILE_KEYS = (
    RUNTIME_ADAPTER_REGISTRY_PATHS_KEY,
    DISPATCH_TARGET_REGISTRY_PATHS_KEY,
    DISPATCH_PROVIDER_REGISTRY_PATHS_KEY,
)


class ManagedExtensionError(RuntimeError):
    """Managed extension index is missing or malformed."""


@dataclass(frozen=True)
class ManagedExtensionRow:
    id: str
    title: str
    root_dir: Path
    default_service_config_path: Path
    manifest_dir: Path
    python_roots: tuple[Path, ...]
    status: str


@dataclass(frozen=True)
class ManagedExtensionLayout:
    row: ManagedExtensionRow
    module_root: Path
    python_root: Path
    python_package_dir: Path


def managed_extensions_index_path(start_path: Path | None = None) -> Path:
    return (_managed_extensions_root(start_path) / MANAGED_EXTENSIONS_INDEX_REL_PATH).resolve()


def _managed_extensions_root(start_path: Path | None = None) -> Path:
    if start_path is None:
        return resolve_repo_root(None)

    resolved = Path(start_path).resolve()
    candidates = [resolved.parent] if resolved.is_file() else [resolved]
    candidates.extend(resolved.parents)
    index_rel_path = Path(MANAGED_EXTENSIONS_INDEX_REL_PATH)
    for candidate in candidates:
        if (candidate / index_rel_path).is_file():
            return candidate

    if not resolved.is_file():
        return resolved
    return resolve_repo_root(resolved)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise ManagedExtensionError(f'managed extension index not found: {path}') from exc
    except Exception as exc:
        raise ManagedExtensionError(f'managed extension index is unreadable: {path} ({exc})') from exc
    if not isinstance(payload, dict):
        raise ManagedExtensionError(f'managed extension index root must be an object: {path}')
    return payload


def _require_text(payload: dict[str, Any], key: str, *, label: str) -> str:
    value = str(payload.get(key) or '').strip()
    if not value:
        raise ManagedExtensionError(f'{label}.{key} must be a non-empty string')
    return value


def _reject_unknown_keys(payload: dict[str, Any], *, allowed: tuple[str, ...], label: str) -> None:
    unknown = sorted(str(key) for key in payload if key not in allowed)
    if unknown:
        raise ManagedExtensionError(f'{label} contains unsupported field(s): {", ".join(unknown)}')


def _require_extension_id(payload: dict[str, Any], *, label: str) -> str:
    extension_id = _require_text(payload, 'id', label=label)
    if not _EXTENSION_ID_PATTERN.match(extension_id):
        raise ManagedExtensionError(f'{label}.id must match lowercase extension id pattern [a-z0-9_]+: {extension_id}')
    return extension_id


def _resolve_repo_relative_path(repo_root: Path, value: str, *, label: str) -> Path:
    path = (repo_root / value).resolve()
    try:
        path.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise ManagedExtensionError(f'{label} must stay inside the repository: {value}') from exc
    return path


def _require_contract_path(*, label: str, actual: Path, expected: Path) -> None:
    if actual.resolve() != expected.resolve():
        raise ManagedExtensionError(f'{label} must use extension contract path: {expected}')


def _validate_index_row_contract_paths(
    *,
    repo_root: Path,
    label: str,
    extension_id: str,
    root_dir: Path,
    default_service_config_path: Path,
    manifest_dir: Path,
    python_roots: tuple[Path, ...],
) -> None:
    expected_root_dir = (repo_root / MANAGED_EXTENSIONS_REL_DIR / extension_id).resolve()
    expected_service_path = (
        expected_root_dir / 'config' / 'control_plane' / 'profiles' / f'{extension_id}.service.json'
    ).resolve()
    expected_manifest_dir = (expected_root_dir / 'config' / 'control_plane' / 'extensions.d').resolve()
    expected_python_root = (expected_root_dir / 'python').resolve()
    _require_contract_path(label=f'{label}.rootDir', actual=root_dir, expected=expected_root_dir)
    _require_contract_path(
        label=f'{label}.defaultServiceConfigPath',
        actual=default_service_config_path,
        expected=expected_service_path,
    )
    _require_contract_path(label=f'{label}.manifestDir', actual=manifest_dir, expected=expected_manifest_dir)
    if python_roots != (expected_python_root,):
        raise ManagedExtensionError(f'{label}.pythonRoots must use extension contract path: {expected_python_root}')


def load_managed_extensions_index(start_path: Path | None = None) -> tuple[ManagedExtensionRow, ...]:
    repo_root = _managed_extensions_root(start_path)
    index_path = managed_extensions_index_path(repo_root)
    if not index_path.is_file():
        return ()
    payload = _read_json(index_path)
    _reject_unknown_keys(payload, allowed=_INDEX_ROOT_KEYS, label='managed_extensions')
    rows = payload.get('extensions') or []
    if not isinstance(rows, list):
        raise ManagedExtensionError('managed extension index .extensions must be a list')
    result: list[ManagedExtensionRow] = []
    seen_ids: set[str] = set()
    seen_roots: set[Path] = set()
    for idx, row in enumerate(rows):
        label = f'managed_extensions.extensions[{idx}]'
        if not isinstance(row, dict):
            raise ManagedExtensionError(f'{label} must be an object')
        _reject_unknown_keys(row, allowed=_INDEX_EXTENSION_KEYS, label=label)
        extension_id = _require_extension_id(row, label=label)
        if extension_id in seen_ids:
            raise ManagedExtensionError(f'duplicate managed extension id: {extension_id}')
        seen_ids.add(extension_id)
        root_dir = _resolve_repo_relative_path(repo_root, _require_text(row, 'rootDir', label=label), label=f'{label}.rootDir')
        if root_dir in seen_roots:
            raise ManagedExtensionError(f'duplicate managed extension rootDir: {root_dir}')
        seen_roots.add(root_dir)
        default_service_config_path = _resolve_repo_relative_path(
            repo_root,
            _require_text(row, 'defaultServiceConfigPath', label=label),
            label=f'{label}.defaultServiceConfigPath',
        )
        manifest_dir = _resolve_repo_relative_path(repo_root, _require_text(row, 'manifestDir', label=label), label=f'{label}.manifestDir')
        raw_python_roots = row.get('pythonRoots') or []
        if not isinstance(raw_python_roots, list) or not raw_python_roots:
            raise ManagedExtensionError(f'{label}.pythonRoots must be a non-empty list')
        python_roots: list[Path] = []
        for root_index, value in enumerate(raw_python_roots):
            text = str(value or '').strip()
            if not text:
                raise ManagedExtensionError(f'{label}.pythonRoots[{root_index}] must be a non-empty string')
            resolved = _resolve_repo_relative_path(repo_root, text, label=f'{label}.pythonRoots[{root_index}]')
            if resolved not in python_roots:
                python_roots.append(resolved)
        status = _require_text(row, 'status', label=label)
        if status not in _INDEX_EXTENSION_STATUSES:
            allowed = ', '.join(_INDEX_EXTENSION_STATUSES)
            raise ManagedExtensionError(f'{label}.status must be one of: {allowed}')
        python_roots_tuple = tuple(python_roots)
        _validate_index_row_contract_paths(
            repo_root=repo_root,
            label=label,
            extension_id=extension_id,
            root_dir=root_dir,
            default_service_config_path=default_service_config_path,
            manifest_dir=manifest_dir,
            python_roots=python_roots_tuple,
        )
        result.append(
            ManagedExtensionRow(
                id=extension_id,
                title=_require_text(row, 'title', label=label),
                root_dir=root_dir,
                default_service_config_path=default_service_config_path,
                manifest_dir=manifest_dir,
                python_roots=python_roots_tuple,
                status=status,
            )
        )
    return tuple(result)


def managed_explicit_extensions(start_path: Path | None = None) -> tuple[ManagedExtensionRow, ...]:
    all_indexed_rows = load_managed_extensions_index(start_path)
    indexed_rows = tuple(row for row in all_indexed_rows if row.status == MANAGED_EXPLICIT_EXTENSION_STATUS)
    indexed_ids = {row.id for row in all_indexed_rows}
    discovered_rows = tuple(
        ManagedExtensionRow(
            id=row.id,
            title=row.title,
            root_dir=row.root_dir,
            default_service_config_path=row.default_service_config_path,
            manifest_dir=row.manifest_dir,
            python_roots=row.python_roots,
            status=MANAGED_EXPLICIT_EXTENSION_STATUS,
        )
        for row in discover_extension_profiles(start_path, skip_ids=indexed_ids)
        if row.valid and row.id not in indexed_ids
    )
    return (*indexed_rows, *discovered_rows)


def _filter_managed_explicit_extensions(
    start_path: Path | None = None,
    *,
    extension_id: str | None = None,
) -> tuple[ManagedExtensionRow, ...]:
    normalized_id = str(extension_id or '').strip()
    rows = managed_explicit_extensions(start_path)
    if not normalized_id:
        return rows
    return tuple(row for row in rows if row.id == normalized_id)


def managed_extension_manifest_path(row: ManagedExtensionRow) -> Path:
    return (row.manifest_dir / f'{row.id}.json').resolve()


def managed_extension_module_roots(
    start_path: Path | None = None,
    *,
    extension_id: str | None = None,
) -> tuple[Path, ...]:
    roots: list[Path] = []
    for row in _filter_managed_explicit_extensions(start_path, extension_id=extension_id):
        candidate = (row.root_dir / 'agent' / 'modules').resolve()
        if candidate.is_dir() and candidate not in roots:
            roots.append(candidate)
    return tuple(roots)


def managed_extension_test_roots(
    start_path: Path | None = None,
    *,
    extension_id: str | None = None,
) -> tuple[Path, ...]:
    roots: list[Path] = []
    for row in _filter_managed_explicit_extensions(start_path, extension_id=extension_id):
        candidate = (row.root_dir / 'tests').resolve()
        if candidate.is_dir() and candidate not in roots:
            roots.append(candidate)
    return tuple(roots)


def managed_extension_for_config_path(
    config_path: str | Path | None,
    *,
    start_path: Path | None = None,
) -> ManagedExtensionRow | None:
    if config_path in (None, ''):
        return None
    resolved = Path(config_path).resolve()
    for row in managed_explicit_extensions(start_path):
        if resolved == row.default_service_config_path.resolve():
            return row
    return None


def managed_extension_for_agent_ref(
    agent_ref: str,
    *,
    start_path: Path | None = None,
) -> ManagedExtensionRow | None:
    normalized_agent_ref = str(agent_ref or '').strip()
    if not normalized_agent_ref:
        return None
    owner_id = ''
    local_agent_ref = normalized_agent_ref
    if ':' in normalized_agent_ref:
        owner_id, local_agent_ref = (part.strip() for part in normalized_agent_ref.split(':', 1))
        if not owner_id or not local_agent_ref:
            return None

    matches: list[ManagedExtensionRow] = []
    for row in managed_explicit_extensions(start_path):
        if owner_id and row.id != owner_id:
            continue
        module_root = (row.root_dir / 'agent' / 'modules').resolve()
        if not module_root.is_dir():
            continue
        if (module_root / local_agent_ref).is_dir():
            matches.append(row)

    if len(matches) > 1:
        joined = ', '.join(row.id for row in matches)
        raise ManagedExtensionError(
            f'agentRef {normalized_agent_ref} maps to multiple managed explicit extensions: {joined}'
        )
    return matches[0] if matches else None


def managed_extension_default_service_config_path_for_agent_ref(
    agent_ref: str,
    *,
    start_path: Path | None = None,
) -> Path | None:
    row = managed_extension_for_agent_ref(agent_ref, start_path=start_path)
    return None if row is None else row.default_service_config_path


def managed_extension_python_roots_for_config_path(
    config_path: str | Path | None,
    *,
    start_path: Path | None = None,
) -> tuple[Path, ...]:
    row = managed_extension_for_config_path(config_path, start_path=start_path)
    if row is None or row.status != MANAGED_EXPLICIT_EXTENSION_STATUS:
        return ()
    return row.python_roots


def _python_package_dirs(python_root: Path) -> tuple[Path, ...]:
    if not python_root.is_dir():
        raise ManagedExtensionError(f'managed extension python root does not exist: {python_root}')
    return tuple(
        path.resolve()
        for path in sorted(python_root.iterdir())
        if path.is_dir() and (path / '__init__.py').is_file()
    )


def _path_is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _normalized_path(value: Any) -> Path | None:
    return value if isinstance(value, Path) else None


def _append_if_path_escapes_extension(
    issues: list[str],
    row: ManagedExtensionRow,
    *,
    label: str,
    path: Path | None,
) -> None:
    if path is None:
        return
    if not _path_is_relative_to(path, row.root_dir):
        issues.append(f'{row.id}: {label} escapes extension root -> {path}')


def _append_if_path_escapes_repo(
    issues: list[str],
    row: ManagedExtensionRow,
    *,
    label: str,
    path: Path | None,
    repo_root: Path,
) -> None:
    if path is None:
        return
    if not _path_is_relative_to(path, repo_root):
        issues.append(f'{row.id}: {label} escapes repository root -> {path}')


def _validate_service_profile_contract(
    row: ManagedExtensionRow,
    *,
    repo_root: Path,
    payload: dict[str, Any],
    issues: list[str],
) -> None:
    extensions = payload.get('extensions') if isinstance(payload.get('extensions'), dict) else {}
    enabled_values = extensions.get('enabledExtensionIds') or []
    if not isinstance(enabled_values, list):
        issues.append(f'{row.id}: default service config extensions.enabledExtensionIds must be a list')
        enabled_ids: tuple[str, ...] = ()
    else:
        enabled_ids = tuple(
            str(item).strip()
            for item in enabled_values
            if str(item).strip()
        )
    if _PLATFORM_EXTENSION_ID not in enabled_ids:
        issues.append(
            f'{row.id}: default service config does not enable {_PLATFORM_EXTENSION_ID} -> {row.default_service_config_path}'
        )
    if row.id not in enabled_ids:
        issues.append(
            f'{row.id}: default service config does not enable extension id -> {row.default_service_config_path}'
        )
    extra_enabled_ids = [extension_id for extension_id in enabled_ids if extension_id not in {_PLATFORM_EXTENSION_ID, row.id}]
    if extra_enabled_ids:
        issues.append(
            f'{row.id}: default service config may only enable {_PLATFORM_EXTENSION_ID} and own extension id -> '
            f'{", ".join(extra_enabled_ids)}'
        )

    manifest_dirs = extensions.get('manifestsDirs') or []
    if not isinstance(manifest_dirs, list):
        issues.append(f'{row.id}: default service config extensions.manifestsDirs must be a list')
        return

    resolved_dirs: list[Path] = []
    for idx, value in enumerate(manifest_dirs):
        try:
            resolved = resolve_path_contract(
                value,
                base_dir=row.default_service_config_path.parent,
                start_path=row.default_service_config_path,
                repo_root=repo_root,
            )
        except Exception as exc:
            issues.append(f'{row.id}: default service config manifestsDirs[{idx}] cannot be resolved ({exc})')
            continue
        if resolved is not None and resolved not in resolved_dirs:
            resolved_dirs.append(resolved)

    expected_platform_manifest_dir = (repo_root / _PLATFORM_MANIFEST_DIR_REL_PATH).resolve()
    expected_own_manifest_dir = row.manifest_dir.resolve()
    if expected_platform_manifest_dir not in resolved_dirs:
        issues.append(
            f'{row.id}: default service config must load {_PLATFORM_EXTENSION_ID} manifest dir -> {expected_platform_manifest_dir}'
        )
    if expected_own_manifest_dir not in resolved_dirs:
        issues.append(
            f'{row.id}: default service config must load own manifest dir -> {expected_own_manifest_dir}'
        )
    extra_manifest_dirs = [
        path
        for path in resolved_dirs
        if path not in {expected_platform_manifest_dir, expected_own_manifest_dir}
    ]
    if extra_manifest_dirs:
        issues.append(
            f'{row.id}: default service config may only load platform and own manifest dirs -> '
            f'{", ".join(str(path) for path in extra_manifest_dirs)}'
        )


def _validate_manifest_boundaries(
    row: ManagedExtensionRow,
    *,
    repo_root: Path,
    manifest_path: Path,
    manifest_payload: dict[str, Any],
    issues: list[str],
) -> None:
    try:
        from openclaw.control_plane.extensions.normalization import _normalize_manifest

        manifest = _normalize_manifest(manifest_path, manifest_payload)
    except Exception as exc:
        issues.append(f'{row.id}: manifest validation failed -> {manifest_path} ({exc})')
        return

    registry = manifest.get('registry') if isinstance(manifest.get('registry'), dict) else {}
    for key in (*_MANAGED_EXTENSION_REGISTRY_DIR_KEYS, *_MANAGED_EXTENSION_REGISTRY_FILE_KEYS):
        for idx, path in enumerate(registry.get(key) or []):
            _append_if_path_escapes_repo(
                issues,
                row,
                label=f'manifest registry.{key}[{idx}]',
                path=_normalized_path(path),
                repo_root=repo_root,
            )
            _append_if_path_escapes_extension(
                issues,
                row,
                label=f'manifest registry.{key}[{idx}]',
                path=_normalized_path(path),
            )

    schemas = manifest.get('schemas') if isinstance(manifest.get('schemas'), dict) else {}
    for key, path in schemas.items():
        _append_if_path_escapes_repo(
            issues,
            row,
            label=f'manifest schemas.{key}',
            path=_normalized_path(path),
            repo_root=repo_root,
        )

    for group_key in (SURFACE_FRAGMENTS_FIELD, GOVERNANCE_SURFACES_FIELD):
        group = manifest.get(group_key) if isinstance(manifest.get(group_key), dict) else {}
        for key, path in group.items():
            _append_if_path_escapes_repo(
                issues,
                row,
                label=f'manifest {group_key}.{key}',
                path=_normalized_path(path),
                repo_root=repo_root,
            )
            _append_if_path_escapes_extension(
                issues,
                row,
                label=f'manifest {group_key}.{key}',
                path=_normalized_path(path),
            )


def _contains_python_sources(path: Path) -> bool:
    return any(
        item.is_file() and item.suffix == '.py' and '__pycache__' not in item.parts
        for item in path.rglob('*.py')
    )


def _has_bytecode_guard(marker_path: Path) -> bool:
    try:
        source = marker_path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        return False
    return (
        'sys.dont_write_bytecode = True' in source
        and '__pycache__' in source
        and 'shutil.rmtree' in source
        and 'atexit.register' in source
    )


def _validate_tests_package_marker(row: ManagedExtensionRow, marker_path: Path, *, issues: list[str]) -> None:
    if not marker_path.is_file():
        issues.append(f'{row.id}: tests package marker missing bytecode guard -> {marker_path}')
        return
    if not _has_bytecode_guard(marker_path):
        issues.append(f'{row.id}: tests package marker must disable and clean bytecode cache -> {marker_path}')


def _validate_python_package_marker(row: ManagedExtensionRow, marker_path: Path, *, issues: list[str]) -> None:
    if not marker_path.is_file():
        issues.append(f'{row.id}: python package marker missing bytecode guard -> {marker_path}')
        return
    if not _has_bytecode_guard(marker_path):
        issues.append(f'{row.id}: python package marker must disable and clean bytecode cache -> {marker_path}')


def _validate_tests_package_guard(row: ManagedExtensionRow, *, issues: list[str]) -> None:
    tests_dir = (row.root_dir / 'tests').resolve()
    if not tests_dir.exists():
        return
    if not tests_dir.is_dir():
        issues.append(f'{row.id}: tests path must be a directory -> {tests_dir}')
        return
    _validate_tests_package_marker(row, tests_dir / '__init__.py', issues=issues)
    for child in sorted(path for path in tests_dir.iterdir() if path.is_dir() and path.name != '__pycache__'):
        if _contains_python_sources(child) and not (child / '__init__.py').is_file():
            issues.append(f'{row.id}: tests package marker missing bytecode guard -> {child / "__init__.py"}')
        elif _contains_python_sources(child):
            _validate_tests_package_marker(row, child / '__init__.py', issues=issues)


def _validate_python_package_guard(row: ManagedExtensionRow, python_root: Path, *, issues: list[str]) -> None:
    try:
        package_dirs = _python_package_dirs(python_root)
    except ManagedExtensionError as exc:
        issues.append(f'{row.id}: {exc}')
        return
    for package_dir in package_dirs:
        _validate_python_package_marker(row, package_dir / '__init__.py', issues=issues)


def managed_extension_layout_for_config_path(
    config_path: str | Path | None,
    *,
    start_path: Path | None = None,
) -> ManagedExtensionLayout | None:
    row = managed_extension_for_config_path(config_path, start_path=start_path)
    if row is None or row.status != MANAGED_EXPLICIT_EXTENSION_STATUS:
        return None
    if len(row.python_roots) != 1:
        joined = ', '.join(str(item) for item in row.python_roots) or '<none>'
        raise ManagedExtensionError(
            f'managed extension layout requires exactly one python root for authoring: {row.id} ({joined})'
        )
    python_root = row.python_roots[0]
    package_dirs = _python_package_dirs(python_root)
    if len(package_dirs) != 1:
        joined = ', '.join(str(item) for item in package_dirs) or '<none>'
        raise ManagedExtensionError(
            f'managed extension python root must contain exactly one top-level package dir: {python_root} ({joined})'
        )
    return ManagedExtensionLayout(
        row=row,
        module_root=(row.root_dir / 'agent' / 'modules').resolve(),
        python_root=python_root.resolve(),
        python_package_dir=package_dirs[0],
    )


def validate_managed_explicit_extension_index(start_path: Path | None = None) -> tuple[str, ...]:
    issues: list[str] = []
    repo_root = _managed_extensions_root(start_path)
    for row in tuple(row for row in load_managed_extensions_index(start_path) if row.status == MANAGED_EXPLICIT_EXTENSION_STATUS):
        expected_root_dir = row.root_dir.resolve()
        expected_convention_root_dir = (repo_root / MANAGED_EXTENSIONS_REL_DIR / row.id).resolve()
        expected_service_path = (row.root_dir / 'config' / 'control_plane' / 'profiles' / f'{row.id}.service.json').resolve()
        expected_manifest_dir = (row.root_dir / 'config' / 'control_plane' / 'extensions.d').resolve()
        expected_module_root = (row.root_dir / 'agent' / 'modules').resolve()
        expected_python_root = (row.root_dir / 'python').resolve()
        manifest_path = managed_extension_manifest_path(row)

        if row.root_dir.resolve() != expected_convention_root_dir:
            issues.append(f'{row.id}: rootDir drift -> {row.root_dir} (expected {expected_convention_root_dir})')
        if not row.root_dir.is_dir():
            issues.append(f'{row.id}: rootDir does not exist -> {row.root_dir}')
            continue
        if row.default_service_config_path != expected_service_path:
            issues.append(
                f'{row.id}: defaultServiceConfigPath drift -> {row.default_service_config_path} (expected {expected_service_path})'
            )
        if row.manifest_dir != expected_manifest_dir:
            issues.append(f'{row.id}: manifestDir drift -> {row.manifest_dir} (expected {expected_manifest_dir})')
        if not expected_module_root.is_dir():
            issues.append(f'{row.id}: missing module root -> {expected_module_root}')
        if not row.default_service_config_path.is_file():
            issues.append(f'{row.id}: missing default service config -> {row.default_service_config_path}')
        else:
            payload = _read_json(row.default_service_config_path)
            _validate_service_profile_contract(row, repo_root=repo_root, payload=payload, issues=issues)
        if not row.manifest_dir.is_dir():
            issues.append(f'{row.id}: missing manifest dir -> {row.manifest_dir}')
        elif not manifest_path.is_file():
            issues.append(f'{row.id}: missing manifest file -> {manifest_path}')
        else:
            payload = _read_json(manifest_path)
            if str(payload.get('id') or '').strip() != row.id:
                issues.append(f'{row.id}: manifest id mismatch -> {manifest_path}')
            _validate_manifest_boundaries(
                row,
                repo_root=repo_root,
                manifest_path=manifest_path,
                manifest_payload=payload,
                issues=issues,
            )
        if not row.python_roots:
            issues.append(f'{row.id}: pythonRoots is empty')
        if row.python_roots != (expected_python_root,):
            joined_roots = ', '.join(str(path) for path in row.python_roots) or '<none>'
            issues.append(f'{row.id}: pythonRoots drift -> {joined_roots} (expected {expected_python_root})')
        for python_root in row.python_roots:
            try:
                python_root.resolve().relative_to(expected_root_dir)
            except ValueError:
                issues.append(f'{row.id}: python root escapes extension root -> {python_root}')
                continue
            if not python_root.is_dir():
                issues.append(f'{row.id}: missing python root -> {python_root}')
                continue
            try:
                package_dirs = _python_package_dirs(python_root)
            except ManagedExtensionError as exc:
                issues.append(f'{row.id}: {exc}')
                continue
            if not package_dirs:
                issues.append(f'{row.id}: python root has no package dirs -> {python_root}')
            _validate_python_package_guard(row, python_root, issues=issues)
        _validate_tests_package_guard(row, issues=issues)
    return tuple(issues)
