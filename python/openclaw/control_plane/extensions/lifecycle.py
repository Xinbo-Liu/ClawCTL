#!/usr/bin/env python3
"""受管扩展生命周期 helper，负责安装、启用、禁用、迁移和 lock 漂移检查。"""
from __future__ import annotations

import hashlib
import importlib
import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

from openclaw.control_plane.config_loader import ControlPlaneConfigError, load_control_plane_service_payload
from openclaw.control_plane.extensions.api import _version_satisfies, known_extensions_from_config, load_enabled_extensions
from openclaw.control_plane.extensions.normalization import ExtensionError, _normalize_manifest
from openclaw.lib.io.state import write_json_atomic
from openclaw.lib.repo.layout import resolve_repo_root, resolve_selected_control_plane_config_path
from openclaw.lib.repo.managed_extensions import (
    MANAGED_EXPLICIT_EXTENSION_STATUS,
    ManagedExtensionRow,
    load_managed_extensions_index,
    managed_extension_manifest_path,
    managed_extensions_index_path,
)


EXTENSIONS_LOCK_REL_PATH = 'agent/extensions/lock.json'
MIGRATION_STATE_REL_PATH = 'state/openclaw/control_plane/extensions/migrations.json'


class ExtensionLifecycleError(RuntimeError):
    """受管扩展生命周期操作失败，错误文本面向 CLI 和 doctor 输出。"""


def repo_root_from(start_path: Path | None = None) -> Path:
    return resolve_repo_root(Path(__file__) if start_path is None else start_path)


def extension_lock_path(repo_root: Path) -> Path:
    return (repo_root / EXTENSIONS_LOCK_REL_PATH).resolve()


def migration_state_path(repo_root: Path) -> Path:
    return (repo_root / MIGRATION_STATE_REL_PATH).resolve()


def read_json_object(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return dict(default or {})
    except Exception as exc:
        raise ExtensionLifecycleError(f'JSON 读取失败：{path} ({exc})') from exc
    if not isinstance(payload, dict):
        raise ExtensionLifecycleError(f'JSON 顶层必须是对象：{path}')
    return payload


def write_json_object(path: Path, payload: dict[str, Any]) -> None:
    write_json_atomic(path, payload)


def repo_rel(repo_root: Path, path: Path) -> str:
    resolved_repo = repo_root.resolve()
    resolved_path = path.resolve()
    try:
        return resolved_path.relative_to(resolved_repo).as_posix()
    except ValueError as exc:
        raise ExtensionLifecycleError(f'extension path must stay inside repository: {resolved_path}') from exc


def _manifest_path_for_extension_root(extension_root: Path, extension_id: str) -> Path:
    return (extension_root / 'config' / 'control_plane' / 'extensions.d' / f'{extension_id}.json').resolve()


def _extension_root_from_manifest_path(manifest_path: Path) -> Path | None:
    manifest_dir = manifest_path.resolve().parent
    if tuple(manifest_dir.parts[-3:]) != ('config', 'control_plane', 'extensions.d'):
        return None
    return Path(*manifest_dir.parts[:-3]).resolve()


def _service_path_for_extension_root(extension_root: Path, extension_id: str) -> Path:
    return (extension_root / 'config' / 'control_plane' / 'profiles' / f'{extension_id}.service.json').resolve()


def _python_roots_for_extension_root(extension_root: Path, *, source_root: Path | None = None) -> list[Path]:
    source_candidate = ((source_root or extension_root) / 'python').resolve()
    target_candidate = (extension_root / 'python').resolve()
    return [target_candidate] if source_candidate.is_dir() else []


def _managed_row_payload(
    repo_root: Path,
    *,
    extension_id: str,
    title: str,
    extension_root: Path,
    source_root: Path | None = None,
) -> dict[str, Any]:
    python_roots = _python_roots_for_extension_root(extension_root, source_root=source_root)
    return {
        'id': extension_id,
        'title': title,
        'rootDir': repo_rel(repo_root, extension_root),
        'defaultServiceConfigPath': repo_rel(repo_root, _service_path_for_extension_root(extension_root, extension_id)),
        'manifestDir': repo_rel(repo_root, (extension_root / 'config' / 'control_plane' / 'extensions.d').resolve()),
        'pythonRoots': [repo_rel(repo_root, item) for item in python_roots],
        'status': MANAGED_EXPLICIT_EXTENSION_STATUS,
    }


def normalize_manifest_from_path(path: Path) -> dict[str, Any]:
    payload = read_json_object(path)
    return _normalize_manifest(path.resolve(), payload)


def _content_hash_file_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    if b'\0' in data:
        return data
    return data.replace(b'\r\n', b'\n')


def content_hash(root: Path) -> str:
    hasher = hashlib.sha256()
    paths: list[tuple[Path, Path]] = []
    for path in root.rglob('*'):
        if not path.is_file():
            continue
        rel_path = path.relative_to(root)
        parts = set(rel_path.parts)
        if parts.intersection({'__pycache__', '.pytest_cache', '.ruff_cache', '.git', 'tests'}):
            continue
        if len(rel_path.parts) >= 2 and rel_path.parts[-2:] == ('deploy', 'extension.env'):
            continue
        if len(rel_path.parts) >= 2 and rel_path.parts[0] == 'offline_wheelhouse' and path.suffix == '.whl':
            continue
        paths.append((rel_path, path))
    for rel_path, path in sorted(paths, key=lambda item: item[0].as_posix()):
        hasher.update(rel_path.as_posix().encode('utf-8'))
        hasher.update(b'\0')
        hasher.update(_content_hash_file_bytes(path))
        hasher.update(b'\0')
    return hasher.hexdigest()


def managed_rows_by_id(repo_root: Path) -> dict[str, ManagedExtensionRow]:
    return {row.id: row for row in load_managed_extensions_index(repo_root)}


def _managed_row_from_payload(repo_root: Path, payload: dict[str, Any]) -> ManagedExtensionRow:
    def rel_path(key: str) -> Path:
        text = str(payload.get(key) or '').strip()
        if not text:
            raise ExtensionLifecycleError(f'managed extension row missing {key}')
        path = (repo_root / text).resolve()
        try:
            path.relative_to(repo_root.resolve())
        except ValueError as exc:
            raise ExtensionLifecycleError(f'managed extension row {key} escapes repo: {text}') from exc
        return path

    python_roots = tuple(
        (repo_root / str(item)).resolve()
        for item in (payload.get('pythonRoots') or [])
        if str(item or '').strip()
    )
    if not python_roots:
        raise ExtensionLifecycleError('managed extension row pythonRoots is empty')
    for path in python_roots:
        try:
            path.relative_to(repo_root.resolve())
        except ValueError as exc:
            raise ExtensionLifecycleError(f'managed extension row pythonRoots escapes repo: {path}') from exc
    return ManagedExtensionRow(
        id=str(payload.get('id') or '').strip(),
        title=str(payload.get('title') or '').strip(),
        root_dir=rel_path('rootDir'),
        default_service_config_path=rel_path('defaultServiceConfigPath'),
        manifest_dir=rel_path('manifestDir'),
        python_roots=python_roots,
        status=str(payload.get('status') or '').strip(),
    )


def manifest_for_row(row: ManagedExtensionRow) -> dict[str, Any]:
    return normalize_manifest_from_path(managed_extension_manifest_path(row))


def known_manifests_by_id(config_path: Path) -> dict[str, dict[str, Any]]:
    return {str(row.get('id') or '').strip(): row for row in known_extensions_from_config(config_path)}


def dependency_rows(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in manifest.get('dependencies') or [] if isinstance(item, dict)]


def migration_rows(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in manifest.get('migrations') or [] if isinstance(item, dict)]


def build_lock_payload(
    repo_root: Path,
    *,
    rows: list[ManagedExtensionRow] | None = None,
    manifests_by_id: dict[str, dict[str, Any]] | None = None,
    visible_manifests_by_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = [
        row
        for row in (list(rows) if rows is not None else list(load_managed_extensions_index(repo_root)))
        if row.status == MANAGED_EXPLICIT_EXTENSION_STATUS
    ]
    manifests: dict[str, dict[str, Any]] = dict(manifests_by_id or {})
    visible_manifests: dict[str, dict[str, Any]] = dict(visible_manifests_by_id or {})
    for row in rows:
        if row.id not in manifests:
            manifests[row.id] = manifest_for_row(row)
        if visible_manifests_by_id is None:
            try:
                visible_manifests.update(known_manifests_by_id(row.default_service_config_path))
            except Exception:
                pass
    lock_entries: dict[str, Any] = {}
    for row in rows:
        manifest = manifests[row.id]
        resolved_dependencies: list[dict[str, Any]] = []
        for dep in dependency_rows(manifest):
            dep_id = str(dep.get('id') or '').strip()
            dep_manifest = manifests.get(dep_id) or visible_manifests.get(dep_id)
            resolved_dependencies.append({
                'id': dep_id,
                'version': str(dep_manifest.get('version') or '') if isinstance(dep_manifest, dict) else '',
                'requirement': str(dep.get('version') or ''),
                'optional': bool(dep.get('optional', False)),
            })
        lock_entries[row.id] = {
            'installedVersion': str(manifest.get('version') or ''),
            'source': repo_rel(repo_root, row.root_dir),
            'contentHash': content_hash(row.root_dir),
            'dependencies': dependency_rows(manifest),
            'resolvedDependencies': resolved_dependencies,
            'appliedMigrations': [],
        }
    existing = read_json_object(extension_lock_path(repo_root), {'schemaVersion': 1, 'extensions': {}})
    existing_entries = existing.get('extensions') if isinstance(existing.get('extensions'), dict) else {}
    for extension_id, entry in lock_entries.items():
        previous = existing_entries.get(extension_id) if isinstance(existing_entries.get(extension_id), dict) else {}
        entry['appliedMigrations'] = list(previous.get('appliedMigrations') or [])
    return {'schemaVersion': 1, 'extensions': lock_entries}


def write_lock(repo_root: Path) -> dict[str, Any]:
    payload = build_lock_payload(repo_root)
    write_json_object(extension_lock_path(repo_root), payload)
    return payload


def lock_drift_issues(repo_root: Path, *, expected: dict[str, Any] | None = None) -> list[str]:
    expected = expected or build_lock_payload(repo_root)
    actual = read_json_object(extension_lock_path(repo_root), {})
    if not actual:
        return ['agent/extensions/lock.json missing']
    issues: list[str] = []
    expected_entries = expected.get('extensions') if isinstance(expected.get('extensions'), dict) else {}
    actual_entries = actual.get('extensions') if isinstance(actual.get('extensions'), dict) else {}
    for extension_id, expected_entry in expected_entries.items():
        actual_entry = actual_entries.get(extension_id) if isinstance(actual_entries.get(extension_id), dict) else {}
        for key in ('installedVersion', 'source', 'contentHash', 'dependencies', 'resolvedDependencies'):
            if actual_entry.get(key) != expected_entry.get(key):
                issues.append(f'{extension_id}: lock {key} drift')
    for extension_id in sorted(set(actual_entries) - set(expected_entries)):
        issues.append(f'{extension_id}: lock entry has no managed extension')
    return issues


def lifecycle_doctor_issues(repo_root: Path) -> list[str]:
    issues: list[str] = []
    rows = [row for row in load_managed_extensions_index(repo_root) if row.status == MANAGED_EXPLICIT_EXTENSION_STATUS]
    manifests: dict[str, dict[str, Any]] = {}
    visible_manifests: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            manifest = manifest_for_row(row)
            visible_manifests.update(known_manifests_by_id(row.default_service_config_path))
        except Exception as exc:
            issues.append(f'{row.id}: manifest invalid: {exc}')
            continue
        manifests[row.id] = manifest
        if not str(manifest.get('version') or '').strip():
            issues.append(f'{row.id}: manifest version missing')
        compat = manifest.get('compat') if isinstance(manifest.get('compat'), dict) else {}
        if not str(compat.get('controlPlane') or '').strip():
            issues.append(f'{row.id}: manifest compat.controlPlane missing')
    issues.extend(_dependency_cycle_issues(manifests))
    for extension_id, manifest in manifests.items():
        for dep in dependency_rows(manifest):
            dep_id = str(dep.get('id') or '').strip()
            if bool(dep.get('optional', False)):
                continue
            dep_manifest = manifests.get(dep_id) or visible_manifests.get(dep_id)
            if dep_manifest is None:
                issues.append(f'{extension_id}: dependency {dep_id} is not visible from default profile')
                continue
            requirement = str(dep.get('version') or '').strip()
            dep_version = str(dep_manifest.get('version') or '').strip()
            if requirement and not _version_satisfies(dep_version, requirement):
                issues.append(f'{extension_id}: dependency {dep_id} requires {requirement}, current {dep_version or "<missing>"}')
    expected_lock = build_lock_payload(
        repo_root,
        rows=rows,
        manifests_by_id=manifests,
        visible_manifests_by_id=visible_manifests,
    )
    issues.extend(lock_drift_issues(repo_root, expected=expected_lock))
    lock_payload = read_json_object(extension_lock_path(repo_root), {})
    lock_entries = lock_payload.get('extensions') if isinstance(lock_payload.get('extensions'), dict) else {}
    for extension_id, manifest in manifests.items():
        entry = lock_entries.get(extension_id) if isinstance(lock_entries.get(extension_id), dict) else {}
        applied = {str(item) for item in entry.get('appliedMigrations') or []}
        for migration in migration_rows(manifest):
            migration_id = str(migration.get('id') or '').strip()
            if migration_id and migration_id not in applied:
                issues.append(f'{extension_id}: migration not applied: {migration_id}')
    return issues


def _dependency_cycle_issues(manifests: dict[str, dict[str, Any]]) -> list[str]:
    graph: dict[str, list[str]] = {}
    for extension_id, manifest in manifests.items():
        graph[extension_id] = [
            str(dep.get('id') or '').strip()
            for dep in dependency_rows(manifest)
            if str(dep.get('id') or '').strip() in manifests and not bool(dep.get('optional', False))
        ]
    visiting: set[str] = set()
    visited: set[str] = set()
    reported: set[tuple[str, ...]] = set()
    issues: list[str] = []

    def visit(extension_id: str, stack: list[str]) -> None:
        if extension_id in visiting:
            cycle = [*stack[stack.index(extension_id):], extension_id] if extension_id in stack else [*stack, extension_id]
            marker = tuple(cycle)
            if marker not in reported:
                reported.add(marker)
                issues.append(f'extension dependency cycle: {" -> ".join(cycle)}')
            return
        if extension_id in visited:
            return
        visiting.add(extension_id)
        for dep_id in graph.get(extension_id, []):
            visit(dep_id, [*stack, extension_id])
        visiting.remove(extension_id)
        visited.add(extension_id)

    for extension_id in sorted(graph):
        visit(extension_id, [])
    return issues


def resolve_profile_path(repo_root: Path, profile: str) -> Path:
    text = str(profile or '').strip()
    if not text:
        raise ExtensionLifecycleError('--profile 不能为空')
    maybe_path = Path(text)
    if maybe_path.suffix == '.json' or '/' in text or '\\' in text:
        path = maybe_path if maybe_path.is_absolute() else repo_root / maybe_path
        return path.resolve()
    return resolve_selected_control_plane_config_path(
        None,
        control_plane_profile=text,
        start_path=repo_root,
        default_to_base=False,
    )


def profile_enabled_ids(path: Path) -> list[str]:
    payload = read_json_object(path)
    extensions = payload.setdefault('extensions', {})
    if not isinstance(extensions, dict):
        extensions = {}
        payload['extensions'] = extensions
    return [str(item).strip() for item in extensions.get('enabledExtensionIds') or [] if str(item).strip()]


def write_profile_enabled_ids(path: Path, enabled_ids: list[str]) -> dict[str, Any]:
    payload = read_json_object(path)
    extensions = payload.get('extensions') if isinstance(payload.get('extensions'), dict) else {}
    extensions['enabledExtensionIds'] = list(dict.fromkeys(enabled_ids))
    payload['extensions'] = extensions
    write_json_object(path, payload)
    return payload


def _profile_payload_with_enabled_ids(path: Path, enabled_ids: list[str]) -> dict[str, Any]:
    try:
        _, payload = load_control_plane_service_payload(path)
    except ControlPlaneConfigError as exc:
        raise ExtensionLifecycleError(str(exc)) from exc
    candidate = deepcopy(payload)
    extensions = candidate.get('extensions') if isinstance(candidate.get('extensions'), dict) else {}
    candidate['extensions'] = {**extensions, 'enabledExtensionIds': list(dict.fromkeys(enabled_ids))}
    return candidate


def validate_profile_enabled_ids(path: Path, enabled_ids: list[str]) -> None:
    payload = _profile_payload_with_enabled_ids(path, enabled_ids)
    try:
        load_enabled_extensions(payload, service_base_dir=path.parent)
    except ExtensionError as exc:
        raise ExtensionLifecycleError(str(exc)) from exc


def enable_extension(repo_root: Path, *, profile: str, extension_id: str, dry_run: bool = False) -> dict[str, Any]:
    profile_path = resolve_profile_path(repo_root, profile)
    enabled_ids = profile_enabled_ids(profile_path)
    if extension_id not in enabled_ids:
        enabled_ids.append(extension_id)
    if not dry_run:
        validate_profile_enabled_ids(profile_path, enabled_ids)
        write_profile_enabled_ids(profile_path, enabled_ids)
    return {'profilePath': str(profile_path), 'enabledExtensionIds': enabled_ids, 'dryRun': dry_run}


def reverse_dependencies(repo_root: Path, extension_id: str) -> list[str]:
    dependents: list[str] = []
    for row in load_managed_extensions_index(repo_root):
        if row.status != MANAGED_EXPLICIT_EXTENSION_STATUS or row.id == extension_id:
            continue
        manifest = manifest_for_row(row)
        for dep in dependency_rows(manifest):
            if str(dep.get('id') or '').strip() == extension_id and not bool(dep.get('optional', False)):
                dependents.append(row.id)
    return dependents


def disable_extension(
    repo_root: Path,
    *,
    profile: str,
    extension_id: str,
    cascade_disable: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    profile_path = resolve_profile_path(repo_root, profile)
    enabled_ids = profile_enabled_ids(profile_path)
    dependents = [item for item in reverse_dependencies(repo_root, extension_id) if item in enabled_ids]
    if dependents and not cascade_disable:
        raise ExtensionLifecycleError(f'extension {extension_id} 被依赖，需先禁用：{", ".join(dependents)}')
    remove_ids = {extension_id, *dependents} if cascade_disable else {extension_id}
    next_enabled = [item for item in enabled_ids if item not in remove_ids]
    if not dry_run:
        validate_profile_enabled_ids(profile_path, next_enabled)
        write_profile_enabled_ids(profile_path, next_enabled)
    return {'profilePath': str(profile_path), 'enabledExtensionIds': next_enabled, 'disabledIds': sorted(remove_ids), 'dryRun': dry_run}


def find_source_manifest(source: Path, extension_id: str = '') -> tuple[str, Path, Path, dict[str, Any]]:
    resolved = source.resolve()
    if resolved.is_file():
        manifest_path = resolved
        manifest = normalize_manifest_from_path(manifest_path)
        extension_root = _extension_root_from_manifest_path(manifest_path) or manifest_path.parent
    else:
        if extension_id:
            manifest_path = _manifest_path_for_extension_root(resolved, extension_id)
        else:
            candidates = sorted((resolved / 'config' / 'control_plane' / 'extensions.d').glob('*.json'))
            if len(candidates) != 1:
                raise ExtensionLifecycleError(f'无法唯一识别 extension manifest：{resolved}')
            manifest_path = candidates[0]
        manifest = normalize_manifest_from_path(manifest_path)
        extension_root = resolved
    actual_id = str(manifest.get('id') or '').strip()
    if extension_id and actual_id != extension_id:
        raise ExtensionLifecycleError(f'--id 与 manifest id 不一致：{extension_id} != {actual_id}')
    return actual_id, extension_root.resolve(), manifest_path.resolve(), manifest


def install_extension(
    repo_root: Path,
    *,
    source: Path,
    extension_id: str = '',
    mode: str = '',
    enable_profile: str = '',
    dry_run: bool = False,
) -> dict[str, Any]:
    actual_id, source_root, _manifest_path, manifest = find_source_manifest(source, extension_id)
    target_root = (repo_root / 'agent' / 'extensions' / actual_id).resolve()
    default_mode = 'in-place' if source_root == target_root else 'copy'
    selected_mode = str(mode or default_mode).strip()
    if selected_mode not in {'copy', 'in-place'}:
        raise ExtensionLifecycleError('--mode 必须是 copy 或 in-place')
    rows = managed_rows_by_id(repo_root)
    if actual_id in rows:
        raise ExtensionLifecycleError(f'extension 已安装：{actual_id}')
    if selected_mode == 'copy' and target_root.exists():
        raise ExtensionLifecycleError(f'目标 extension root 已存在：{target_root}')
    install_root = source_root if selected_mode == 'in-place' else target_root
    if selected_mode == 'in-place':
        try:
            install_root.relative_to((repo_root / 'agent' / 'extensions').resolve())
        except ValueError as exc:
            raise ExtensionLifecycleError(f'--mode in-place 只能登记 agent/extensions 下的扩展目录：{install_root}') from exc
    row_payload = _managed_row_payload(
        repo_root,
        extension_id=actual_id,
        title=str(manifest.get('title') or actual_id),
        extension_root=install_root,
        source_root=source_root,
    )
    index_path = managed_extensions_index_path(repo_root)
    index_payload = read_json_object(index_path, {'extensions': []})
    next_extensions = [item for item in index_payload.get('extensions') or [] if isinstance(item, dict)]
    next_extensions.append(row_payload)
    result = {'id': actual_id, 'mode': selected_mode, 'rootDir': row_payload['rootDir'], 'dryRun': dry_run}
    if dry_run:
        result['indexExtensions'] = next_extensions
        return result
    copied_target = False
    old_index_exists = index_path.exists()
    old_index_payload = read_json_object(index_path, {}) if old_index_exists else {}
    lock_path = extension_lock_path(repo_root)
    old_lock_exists = lock_path.exists()
    old_lock_payload = read_json_object(lock_path, {}) if old_lock_exists else {}
    profile_path: Path | None = resolve_profile_path(repo_root, enable_profile) if enable_profile else None
    old_profile_exists = bool(profile_path and profile_path.exists())
    old_profile_payload = read_json_object(profile_path, {}) if old_profile_exists and profile_path is not None else {}
    try:
        if selected_mode == 'copy':
            shutil.copytree(source_root, target_root)
            copied_target = True
        candidate_row = _managed_row_from_payload(repo_root, row_payload)
        candidate_rows = [*load_managed_extensions_index(repo_root), candidate_row]
        lock_payload = build_lock_payload(repo_root, rows=candidate_rows)
        if profile_path is not None:
            enabled_ids = profile_enabled_ids(profile_path)
            if actual_id not in enabled_ids:
                enabled_ids.append(actual_id)
            validate_profile_enabled_ids(profile_path, enabled_ids)
            result['enable'] = {'profilePath': str(profile_path), 'enabledExtensionIds': enabled_ids, 'dryRun': False}
        index_payload['extensions'] = next_extensions
        write_json_object(index_path, index_payload)
        write_json_object(lock_path, lock_payload)
        if profile_path is not None:
            write_profile_enabled_ids(profile_path, result['enable']['enabledExtensionIds'])
    except Exception:
        if old_index_exists:
            write_json_object(index_path, old_index_payload)
        elif index_path.exists():
            index_path.unlink()
        if old_lock_exists:
            write_json_object(lock_path, old_lock_payload)
        elif lock_path.exists():
            lock_path.unlink()
        if profile_path is not None:
            if old_profile_exists:
                write_json_object(profile_path, old_profile_payload)
            elif profile_path.exists():
                profile_path.unlink()
        if copied_target and target_root.exists():
            shutil.rmtree(target_root)
        raise
    return result


def uninstall_extension(
    repo_root: Path,
    *,
    extension_id: str,
    profile: str = '',
    remove_files: bool = False,
    cascade_disable: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    rows = managed_rows_by_id(repo_root)
    row = rows.get(extension_id)
    if row is None:
        raise ExtensionLifecycleError(f'extension 未安装：{extension_id}')
    dependents = reverse_dependencies(repo_root, extension_id)
    if dependents and not cascade_disable:
        raise ExtensionLifecycleError(f'extension {extension_id} 仍被依赖：{", ".join(dependents)}')
    index_path = managed_extensions_index_path(repo_root)
    index_payload = read_json_object(index_path, {'extensions': []})
    next_extensions = [
        item for item in index_payload.get('extensions') or []
        if isinstance(item, dict) and str(item.get('id') or '').strip() != extension_id
    ]
    profile_result: dict[str, Any] | None = None
    if profile:
        profile_result = disable_extension(
            repo_root,
            profile=profile,
            extension_id=extension_id,
            cascade_disable=cascade_disable,
            dry_run=dry_run,
        )
    if remove_files:
        try:
            row.root_dir.resolve().relative_to((repo_root / 'agent' / 'extensions').resolve())
        except ValueError as exc:
            raise ExtensionLifecycleError(f'--remove-files 只能删除 agent/extensions 下的扩展目录：{row.root_dir}') from exc
    if not dry_run:
        index_payload['extensions'] = next_extensions
        write_json_object(index_path, index_payload)
        write_lock(repo_root)
        if remove_files and row.root_dir.exists():
            shutil.rmtree(row.root_dir)
    return {
        'id': extension_id,
        'removedFromIndex': True,
        'profile': profile_result,
        'removeFiles': remove_files,
        'dryRun': dry_run,
    }


def _call_migration(callable_ref: str, *, repo_root: Path, extension_id: str, dry_run: bool) -> dict[str, Any]:
    module_name, sep, attr_name = str(callable_ref or '').strip().partition(':')
    if not sep:
        module_name, sep, attr_name = str(callable_ref or '').strip().rpartition('.')
    if not module_name or not attr_name:
        raise ExtensionLifecycleError(f'migration callable 格式无效：{callable_ref}')
    module = importlib.import_module(module_name)
    func = getattr(module, attr_name)
    payload = func(repo_root=repo_root, extension_id=extension_id, dry_run=dry_run)
    return payload if isinstance(payload, dict) else {'result': payload}


def migrate_extension(repo_root: Path, *, extension_id: str, dry_run: bool = False) -> dict[str, Any]:
    rows = managed_rows_by_id(repo_root)
    row = rows.get(extension_id)
    if row is None:
        raise ExtensionLifecycleError(f'extension 未安装：{extension_id}')
    manifest = manifest_for_row(row)
    lock_payload = read_json_object(extension_lock_path(repo_root), {'schemaVersion': 1, 'extensions': {}})
    lock_entries = lock_payload.setdefault('extensions', {})
    lock_entry = lock_entries.setdefault(extension_id, {'appliedMigrations': []})
    applied = {str(item) for item in lock_entry.get('appliedMigrations') or []}
    executed: list[dict[str, Any]] = []
    for migration in sorted(migration_rows(manifest), key=lambda item: str(item.get('toVersion') or item.get('id') or '')):
        migration_id = str(migration.get('id') or '').strip()
        if not migration_id or migration_id in applied:
            continue
        callable_ref = str(migration.get('callable') or '').strip()
        result = {'id': migration_id, 'dryRun': dry_run}
        if callable_ref and not dry_run:
            result['callableResult'] = _call_migration(callable_ref, repo_root=repo_root, extension_id=extension_id, dry_run=False)
        executed.append(result)
        if not dry_run:
            applied.add(migration_id)
    if not dry_run:
        lock_entry['appliedMigrations'] = sorted(applied)
        write_json_object(extension_lock_path(repo_root), lock_payload)
        state_payload = read_json_object(migration_state_path(repo_root), {'schemaVersion': 1, 'extensions': {}})
        state_extensions = state_payload.setdefault('extensions', {})
        state_extensions[extension_id] = {'appliedMigrations': sorted(applied)}
        write_json_object(migration_state_path(repo_root), state_payload)
    return {'id': extension_id, 'executed': executed, 'dryRun': dry_run}
