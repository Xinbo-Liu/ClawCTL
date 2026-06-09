#!/usr/bin/env python3
"""Repository-local extension discovery helpers."""
from __future__ import annotations

import json
import os
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
from openclaw.lib.repo.repo_root import RepoRootResolutionError, resolve_repo_root


DISCOVERED_EXTENSION_STATUS = 'managed_explicit_extension'
EXTENSIONS_REL_DIR = 'agent/extensions'
_PLATFORM_EXTENSION_ID = 'agent_platform'
_PLATFORM_MANIFEST_REL_DIR = 'config/control_plane/extensions.d'
_EXTENSION_ID_PATTERN = re.compile(r'^[a-z0-9_]+$')
_CONTROL_PLANE_PROFILE_REL_DIR = 'config/control_plane/profiles'
_CONTROL_PLANE_MANIFEST_REL_DIR = 'config/control_plane/extensions.d'
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
_SURFACE_PATH_GROUPS = (
    SURFACE_FRAGMENTS_FIELD,
    GOVERNANCE_SURFACES_FIELD,
)
_SCHEMA_PATH_GROUP = 'schemas'
_SIGNATURE_IGNORED_DIR_NAMES = frozenset(('.git', '__pycache__', '.mypy_cache', '.pytest_cache'))
_DISCOVERY_CACHE: dict[
    tuple[str, tuple[str, ...], tuple[tuple[object, ...], ...]],
    tuple['DiscoveredExtensionProfile', ...],
] = {}


def _discovery_repo_root(start_path: Path | None = None) -> Path:
    if start_path is None:
        return resolve_repo_root(None).resolve()
    resolved = Path(start_path).resolve()
    candidates = [resolved.parent] if resolved.is_file() else [resolved]
    candidates.extend(resolved.parents)
    extensions_rel = Path(EXTENSIONS_REL_DIR)
    for candidate in candidates:
        if (candidate / extensions_rel).is_dir():
            return candidate.resolve()
    return resolve_repo_root(resolved).resolve()


@dataclass(frozen=True)
class DiscoveredExtensionProfile:
    """A directory-scanned extension profile candidate."""

    id: str
    title: str
    root_dir: Path
    default_service_config_path: Path
    manifest_dir: Path
    manifest_path: Path
    python_roots: tuple[Path, ...]
    status: str
    issues: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.issues


def _repo_relpath(repo_root: Path, path: Path) -> str:
    return Path(os.path.relpath(path.resolve(), start=repo_root.resolve())).as_posix()


def _read_json_object(path: Path, *, label: str, issues: list[str]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        issues.append(f'{label} does not exist: {_repo_relpath(resolve_repo_root(path), path)}')
        return {}
    except Exception as exc:
        issues.append(f'{label} cannot be parsed: {path} ({exc})')
        return {}
    if not isinstance(payload, dict):
        issues.append(f'{label} root must be an object: {path}')
        return {}
    return payload


def _path_is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _append_extension_path_issue(
    *,
    issues: list[str],
    label: str,
    path: Path,
    extension_root: Path,
    expected_kind: str,
) -> None:
    resolved = Path(path).resolve()
    if expected_kind == 'dir' and not resolved.is_dir():
        issues.append(f'{label} must be an existing directory: {resolved}')
    if expected_kind == 'file' and not resolved.is_file():
        issues.append(f'{label} must be an existing file: {resolved}')
    if not _path_is_relative_to(resolved, extension_root):
        issues.append(f'{label} escapes extension root: {resolved}')


def _append_repo_path_issue(
    *,
    issues: list[str],
    label: str,
    path: Path,
    repo_root: Path,
) -> None:
    resolved = Path(path).resolve()
    if not _path_is_relative_to(resolved, repo_root):
        issues.append(f'{label} escapes repository root: {resolved}')


def _python_package_dirs(python_root: Path) -> tuple[Path, ...]:
    if not python_root.is_dir():
        return ()
    return tuple(
        path.resolve()
        for path in sorted(python_root.iterdir())
        if path.is_dir() and (path / '__init__.py').is_file()
    )


def _stat_signature(path: Path) -> tuple[bool, bool, int, int]:
    try:
        stat = path.stat()
    except OSError:
        return (False, False, 0, 0)
    return (True, path.is_dir(), int(stat.st_mtime_ns), int(stat.st_size))


def _discovery_signature(children: list[Path]) -> tuple[tuple[object, ...], ...]:
    rows: list[tuple[object, ...]] = []
    for child in children:
        rows.append((child.name, _extension_tree_signature(child)))
    return tuple(rows)


def _extension_tree_signature(extension_root: Path) -> tuple[tuple[object, ...], ...]:
    rows: list[tuple[object, ...]] = [('.', *_stat_signature(extension_root))]
    scan_roots = [
        extension_root / _CONTROL_PLANE_PROFILE_REL_DIR,
        extension_root / _CONTROL_PLANE_MANIFEST_REL_DIR,
        extension_root / 'python',
    ]
    paths: list[Path] = []
    for child in sorted(extension_root.iterdir() if extension_root.is_dir() else (), key=lambda path: path.name):
        if child.is_file():
            paths.append(child)
    for scan_root in scan_roots:
        if not scan_root.exists():
            paths.append(scan_root)
            continue
        paths.append(scan_root)
        if scan_root.name == 'python':
            for package_dir in sorted(scan_root.iterdir(), key=lambda path: path.name):
                if package_dir.is_dir():
                    paths.append(package_dir)
                    paths.append(package_dir / '__init__.py')
            continue
        try:
            paths.extend(sorted(scan_root.rglob('*'), key=lambda path: path.relative_to(extension_root).as_posix()))
        except OSError:
            continue
    for path in paths:
        try:
            rel_path = path.relative_to(extension_root)
        except ValueError:
            continue
        if any(part in _SIGNATURE_IGNORED_DIR_NAMES for part in rel_path.parts):
            continue
        rows.append((rel_path.as_posix(), *_stat_signature(path)))
    return tuple(rows)


def _validate_service_profile(
    service_path: Path,
    *,
    extension_id: str,
    manifest_path: Path,
    issues: list[str],
) -> None:
    if not service_path.is_file():
        issues.append(f'missing service profile: {service_path}')
        return
    try:
        from openclaw.control_plane.config_loader import (
            control_plane_service_schema_path,
            load_control_plane_service_payload,
        )
        from openclaw.control_plane.extensions.api import load_enabled_extensions
        from openclaw.control_plane.schema import load_schema, validate_payload_against_schema

        _, service_payload = load_control_plane_service_payload(service_path)
        validate_payload_against_schema(
            service_payload,
            load_schema(control_plane_service_schema_path(service_path)),
            label=f'discovered extension service {extension_id}',
            strict_dependency=True,
        )
        extensions_payload = service_payload.get('extensions') if isinstance(service_payload.get('extensions'), dict) else {}
        enabled_ids = [
            str(item).strip()
            for item in (extensions_payload.get('enabledExtensionIds') or [])
            if str(item).strip()
        ]
        _validate_discovered_service_activation(enabled_ids, extension_id=extension_id, issues=issues)
        _validate_discovered_manifest_dirs(
            extensions_payload.get('manifestsDirs'),
            service_path=service_path,
            manifest_path=manifest_path,
            issues=issues,
        )
        if _PLATFORM_EXTENSION_ID not in enabled_ids:
            issues.append(f'service profile must enable {_PLATFORM_EXTENSION_ID}')
        if extension_id not in enabled_ids:
            issues.append(f'service profile must enable extension id: {extension_id}')
        enabled_manifests = load_enabled_extensions(service_payload, service_base_dir=service_path.parent)
        for manifest in enabled_manifests:
            if str(manifest.get('id') or '').strip() != extension_id:
                continue
            source_path = Path(str(manifest.get('sourcePath') or '')).resolve()
            if source_path != manifest_path.resolve():
                issues.append(f'service profile must load own manifest from convention path: {manifest_path}')
            break
        else:
            issues.append(f'service profile did not load enabled extension manifest: {extension_id}')
    except Exception as exc:
        issues.append(f'service profile validation failed: {exc}')


def _validate_discovered_service_activation(
    enabled_ids: list[str],
    *,
    extension_id: str,
    issues: list[str],
) -> None:
    allowed_ids = {_PLATFORM_EXTENSION_ID, extension_id}
    extra_ids = [item for item in enabled_ids if item not in allowed_ids]
    if extra_ids:
        issues.append(
            'service profile may only enable '
            f'{_PLATFORM_EXTENSION_ID} and extension id {extension_id}: {", ".join(extra_ids)}'
        )


def _validate_discovered_manifest_dirs(
    manifest_dirs: Any,
    *,
    service_path: Path,
    manifest_path: Path,
    issues: list[str],
) -> bool:
    if not isinstance(manifest_dirs, list):
        issues.append('service profile extensions.manifestsDirs must be a list')
        return False

    from openclaw.lib.repo.path_contracts import resolve_path_contract

    repo_root = resolve_repo_root(service_path)
    expected_dirs = {
        (repo_root / _PLATFORM_MANIFEST_REL_DIR).resolve(),
        manifest_path.parent.resolve(),
    }
    resolved_dirs: list[Path] = []
    for idx, value in enumerate(manifest_dirs):
        text = str(value or '').strip()
        if not text:
            issues.append(f'service profile extensions.manifestsDirs[{idx}] cannot be empty')
            continue
        resolved = resolve_path_contract(text, base_dir=service_path.parent, start_path=service_path.parent, repo_root=repo_root)
        if resolved is None:
            issues.append(f'service profile extensions.manifestsDirs[{idx}] cannot be resolved')
            continue
        resolved_dirs.append(resolved.resolve())
    for expected in sorted(expected_dirs, key=str):
        if expected not in resolved_dirs:
            if expected == manifest_path.parent.resolve():
                issues.append(f'service profile must load own manifest from convention path: {manifest_path}')
            else:
                issues.append(f'service profile must load platform manifest dir: {expected}')
    extra_dirs = [path for path in resolved_dirs if path not in expected_dirs]
    if extra_dirs:
        issues.append(
            'service profile may only load platform and own manifest dirs: '
            + ', '.join(str(path) for path in extra_dirs)
        )
    return True


def _prevalidate_service_profile_shape(
    service_path: Path,
    *,
    extension_id: str,
    manifest_path: Path,
    issues: list[str],
) -> bool:
    if not service_path.is_file():
        issues.append(f'missing service profile: {service_path}')
        return False
    payload = _read_json_object(service_path, label='service profile', issues=issues)
    if not payload:
        return False
    extensions = payload.get('extensions') if isinstance(payload.get('extensions'), dict) else {}
    enabled_ids = [
        str(item).strip()
        for item in (extensions.get('enabledExtensionIds') or [])
        if str(item).strip()
    ]
    _validate_discovered_service_activation(enabled_ids, extension_id=extension_id, issues=issues)
    if _PLATFORM_EXTENSION_ID not in enabled_ids:
        issues.append(f'service profile must enable {_PLATFORM_EXTENSION_ID}')
    if extension_id not in enabled_ids:
        issues.append(f'service profile must enable extension id: {extension_id}')

    manifest_dirs = extensions.get('manifestsDirs') or []
    return _validate_discovered_manifest_dirs(
        manifest_dirs,
        service_path=service_path,
        manifest_path=manifest_path,
        issues=issues,
    )


def _validate_normalized_manifest_boundaries(
    normalized_manifest: dict[str, Any],
    *,
    repo_root: Path,
    extension_root: Path,
    issues: list[str],
) -> None:
    registry = normalized_manifest.get('registry') if isinstance(normalized_manifest.get('registry'), dict) else {}
    for key in _REGISTRY_FILE_KEYS:
        for idx, path in enumerate(registry.get(key) or []):
            if isinstance(path, Path):
                _append_repo_path_issue(
                    issues=issues,
                    label=f'manifest registry.{key}[{idx}]',
                    path=path,
                    repo_root=repo_root,
                )
                _append_extension_path_issue(
                    issues=issues,
                    label=f'manifest registry.{key}[{idx}]',
                    path=path,
                    extension_root=extension_root,
                    expected_kind='file',
                )
    schemas = normalized_manifest.get(_SCHEMA_PATH_GROUP) if isinstance(normalized_manifest.get(_SCHEMA_PATH_GROUP), dict) else {}
    for key, path in schemas.items():
        if isinstance(path, Path):
            _append_repo_path_issue(
                issues=issues,
                label=f'manifest schemas.{key}',
                path=path,
                repo_root=repo_root,
            )
    for group_key in _SURFACE_PATH_GROUPS:
        group = normalized_manifest.get(group_key) if isinstance(normalized_manifest.get(group_key), dict) else {}
        for key, path in group.items():
            if isinstance(path, Path):
                _append_repo_path_issue(
                    issues=issues,
                    label=f'manifest {group_key}.{key}',
                    path=path,
                    repo_root=repo_root,
                )
                _append_extension_path_issue(
                    issues=issues,
                    label=f'manifest {group_key}.{key}',
                    path=path,
                    extension_root=extension_root,
                    expected_kind='file',
                )


def _validate_manifest(
    manifest_path: Path,
    *,
    extension_id: str,
    extension_root: Path,
    issues: list[str],
) -> str:
    if not manifest_path.is_file():
        issues.append(f'missing extension manifest: {manifest_path}')
        return extension_id
    payload = _read_json_object(manifest_path, label='extension manifest', issues=issues)
    title = str(payload.get('title') or extension_id).strip() or extension_id
    manifest_id = str(payload.get('id') or '').strip()
    if manifest_id != extension_id:
        issues.append(f'manifest id must match extension directory: {manifest_id or "<empty>"} != {extension_id}')
    registry = payload.get('registry') if isinstance(payload.get('registry'), dict) else {}
    for key in _REGISTRY_COLLECTION_DIR_KEYS:
        values = registry.get(key)
        if values in (None, ''):
            continue
        if not isinstance(values, list):
            issues.append(f'manifest registry.{key} must be a list')
            continue
        for idx, value in enumerate(values):
            text = str(value or '').strip()
            if not text:
                issues.append(f'manifest registry.{key}[{idx}] cannot be empty')
                continue
            from openclaw.lib.repo.path_contracts import resolve_path_contract

            resolved = resolve_path_contract(text, base_dir=manifest_path.parent, start_path=manifest_path.parent)
            if resolved is None:
                issues.append(f'manifest registry.{key}[{idx}] cannot be resolved')
                continue
            _append_extension_path_issue(
                issues=issues,
                label=f'manifest registry.{key}[{idx}]',
                path=resolved,
                extension_root=extension_root,
                expected_kind='dir',
            )
    try:
        from openclaw.control_plane.extensions.normalization import _normalize_manifest

        normalized_manifest = _normalize_manifest(manifest_path, payload)
    except Exception as exc:
        issues.append(f'manifest validation failed: {exc}')
    else:
        _validate_normalized_manifest_boundaries(
            normalized_manifest,
            repo_root=resolve_repo_root(manifest_path),
            extension_root=extension_root,
            issues=issues,
        )
    return title


def _discover_one(repo_root: Path, extension_root: Path) -> DiscoveredExtensionProfile:
    extension_id = extension_root.name
    issues: list[str] = []
    if not _EXTENSION_ID_PATTERN.match(extension_id):
        issues.append(f'extension directory name is not a valid extension id: {extension_id}')

    service_path = (
        extension_root / _CONTROL_PLANE_PROFILE_REL_DIR / f'{extension_id}.service.json'
    ).resolve()
    manifest_dir = (extension_root / _CONTROL_PLANE_MANIFEST_REL_DIR).resolve()
    manifest_path = (manifest_dir / f'{extension_id}.json').resolve()
    python_root = (extension_root / 'python').resolve()

    title = _validate_manifest(
        manifest_path,
        extension_id=extension_id,
        extension_root=extension_root.resolve(),
        issues=issues,
    )
    precheck_issue_count = len(issues)
    precheck_ok = _prevalidate_service_profile_shape(
        service_path,
        extension_id=extension_id,
        manifest_path=manifest_path,
        issues=issues,
    )
    if precheck_ok and len(issues) == 0 and precheck_issue_count == 0:
        _validate_service_profile(
            service_path,
            extension_id=extension_id,
            manifest_path=manifest_path,
            issues=issues,
        )
    if not python_root.is_dir():
        issues.append(f'missing python root: {python_root}')
    elif not _python_package_dirs(python_root):
        issues.append(f'python root has no package directories: {python_root}')

    return DiscoveredExtensionProfile(
        id=extension_id,
        title=title,
        root_dir=extension_root.resolve(),
        default_service_config_path=service_path,
        manifest_dir=manifest_dir,
        manifest_path=manifest_path,
        python_roots=(python_root,),
        status=DISCOVERED_EXTENSION_STATUS,
        issues=tuple(issues),
    )


def discover_extension_profiles(
    start_path: Path | None = None,
    *,
    skip_ids: set[str] | frozenset[str] | tuple[str, ...] = (),
) -> tuple[DiscoveredExtensionProfile, ...]:
    """Scan repository-local extension directories without changing registry truth files."""
    try:
        repo_root = _discovery_repo_root(start_path)
    except RepoRootResolutionError:
        return ()
    extensions_root = repo_root / EXTENSIONS_REL_DIR
    if not extensions_root.is_dir():
        return ()
    skipped = {str(item).strip() for item in skip_ids if str(item).strip()}
    all_children = [child for child in sorted(extensions_root.iterdir(), key=lambda path: path.name) if child.is_dir()]
    effective_skipped = frozenset(child.name for child in all_children if child.name in skipped)
    children = [child for child in all_children if child.name not in effective_skipped]
    cache_key = (repo_root.as_posix(), tuple(sorted(effective_skipped)), _discovery_signature(children))
    cached = _DISCOVERY_CACHE.get(cache_key)
    if cached is not None:
        return cached
    rows = tuple(_discover_one(repo_root, child.resolve()) for child in children)
    _DISCOVERY_CACHE[cache_key] = rows
    return rows


def discovered_profile_rel_path(repo_root: Path, row: DiscoveredExtensionProfile) -> str:
    return _repo_relpath(repo_root, row.default_service_config_path)
