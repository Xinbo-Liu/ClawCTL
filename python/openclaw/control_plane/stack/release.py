#!/usr/bin/env python3
"""Stack release（基座发布组合）锁文件生成、校验与物化入口。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openclaw.control_plane.config_loader import load_control_plane_service_payload
from openclaw.control_plane.extensions.api import _version_satisfies, load_enabled_extensions
from openclaw.control_plane.extensions.lifecycle import (
    content_hash,
    extension_lock_path,
    repo_rel,
    write_lock,
)
from openclaw.lib.io.state import write_json_atomic
from openclaw.lib.repo.layout import control_plane_profile_status_rows, resolve_repo_root
from openclaw.lib.repo.managed_extensions import (
    MANAGED_EXPLICIT_EXTENSION_STATUS,
    ManagedExtensionRow,
    load_managed_extensions_index,
)
from openclaw.lib.repo.profiles import control_plane_repo_combination_profile_rows


STACK_LOCK_REL_PATH = 'openclaw-stack.lock.json'
PLATFORM_VERSION_REL_PATH = 'config/control_plane/platform_version.json'
PROFILE_REGISTRY_REL_PATH = 'config/control_plane/profile_registry.tsv'
EXTENSIONS_INDEX_REL_PATH = 'agent/extensions/index.json'
EXTENSIONS_PROVENANCE_REL_PATH = 'agent/extensions/provenance.json'
PLATFORM_EXTENSION_ID = 'agent_platform'
STACK_SCHEMA_VERSION = 1
FULL_GIT_SHA_RE = re.compile(r'^[0-9a-f]{40}$', re.IGNORECASE)
SHA256_RE = re.compile(r'^[0-9a-f]{64}$', re.IGNORECASE)
BASE_RELEASE_EXCLUDED_EXACT_PATHS = {
    STACK_LOCK_REL_PATH,
    PROFILE_REGISTRY_REL_PATH,
    'deploy/.env',
    'deploy/site.env',
}
BASE_RELEASE_EXCLUDED_ROOT_DIRS = {
    '.git',
    'agent/extensions',
    'artifacts',
    'certs',
    'deploy/certs',
    'deploy/nginx/certs',
    'deploy/secrets',
    'deploy/targets.d',
    'logs',
    'release',
    'state',
    'tmp',
}
BASE_RELEASE_EXCLUDED_ANY_PARTS = {'__pycache__', '.pytest_cache', '.mypy_cache'}


class StackReleaseError(RuntimeError):
    """基座发布组合操作失败，错误文本面向命令行调用者展示。"""


def _repo_root(start_path: str | Path | None = None) -> Path:
    return resolve_repo_root(Path(__file__) if start_path is None else Path(start_path))


def _read_json_object(path: Path, *, default: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return dict(default or {})
    except Exception as exc:
        raise StackReleaseError(f'JSON 读取失败：{path} ({exc})') from exc
    if not isinstance(payload, dict):
        raise StackReleaseError(f'JSON 顶层必须是对象：{path}')
    return payload


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_hash(path: Path) -> str:
    return _sha256_bytes(path.read_bytes()) if path.is_file() else ''


def _hash_payload(payload: Any) -> str:
    return _sha256_bytes(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8'))


def _combined_hash(rows: list[tuple[str, str]]) -> str:
    hasher = hashlib.sha256()
    for label, digest in sorted(rows):
        if not digest:
            continue
        hasher.update(label.encode('utf-8'))
        hasher.update(b'\0')
        hasher.update(digest.encode('utf-8'))
        hasher.update(b'\0')
    return hasher.hexdigest() if rows else ''


def _git_value(repo_root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ['git', *args],
            cwd=repo_root,
            text=True,
            capture_output=True,
            encoding='utf-8',
            errors='replace',
            check=False,
        )
    except OSError:
        return ''
    if completed.returncode != 0:
        return ''
    return completed.stdout.strip()


def _git_remote(repo_root: Path) -> str:
    return _git_value(repo_root, 'config', '--get', 'remote.origin.url')


def _git_commit(repo_root: Path) -> str:
    return _git_value(repo_root, 'rev-parse', 'HEAD')


def _is_full_git_sha(value: object) -> bool:
    return bool(FULL_GIT_SHA_RE.fullmatch(str(value or '').strip()))


def _git_dirty_paths(repo_root: Path) -> list[str]:
    try:
        completed = subprocess.run(
            ['git', 'status', '--porcelain=v1', '--untracked-files=normal'],
            cwd=repo_root,
            text=True,
            capture_output=True,
            encoding='utf-8',
            errors='replace',
            check=False,
        )
    except OSError:
        return []
    if completed.returncode != 0:
        return []
    result: list[str] = []
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        raw_path = line[3:].strip() or line.strip()
        rel_path = raw_path.split(' -> ', 1)[-1].strip()
        if rel_path and _should_hash_base_file(Path(rel_path)):
            result.append(raw_path)
    return result


def _git_exact_tag(repo_root: Path) -> str:
    return _git_value(repo_root, 'describe', '--tags', '--exact-match')


def _git_toplevel(path: Path) -> Path | None:
    text = _git_value(path, 'rev-parse', '--show-toplevel')
    return Path(text).resolve() if text else None


def _project_version(repo_root: Path) -> str:
    path = repo_root / 'pyproject.toml'
    if not path.is_file():
        return '1.0.0'
    payload = tomllib.loads(path.read_text(encoding='utf-8'))
    return str(payload.get('project', {}).get('version') or '1.0.0')


def platform_version_payload(repo_root: Path) -> dict[str, str]:
    """读取平台版本真源，返回 stack lock 使用的 control plane/schema/runtime 版本。"""
    path = repo_root / PLATFORM_VERSION_REL_PATH
    fallback_version = _project_version(repo_root)
    payload = _read_json_object(path, default={})
    return {
        'controlPlaneVersion': str(payload.get('controlPlaneVersion') or fallback_version),
        'schemaVersion': str(payload.get('schemaVersion') or fallback_version),
        'runtimeContractVersion': str(payload.get('runtimeContractVersion') or fallback_version),
    }


def _should_hash_base_file(rel_path: Path) -> bool:
    parts = rel_path.parts
    if not parts:
        return False
    rel_posix = rel_path.as_posix()
    if rel_posix in BASE_RELEASE_EXCLUDED_EXACT_PATHS:
        return False
    if any(part in BASE_RELEASE_EXCLUDED_ANY_PARTS for part in parts):
        return False
    for excluded_dir in BASE_RELEASE_EXCLUDED_ROOT_DIRS:
        if rel_posix == excluded_dir or rel_posix.startswith(f'{excluded_dir}/'):
            return False
    if rel_path.suffix in {'.pyc', '.pyo'}:
        return False
    return True


def _base_release_git_pathspecs() -> list[str]:
    pathspecs = ['.']
    pathspecs.extend(f':(exclude){rel_path}' for rel_path in sorted(BASE_RELEASE_EXCLUDED_EXACT_PATHS))
    pathspecs.extend(f':(exclude){rel_dir}/**' for rel_dir in sorted(BASE_RELEASE_EXCLUDED_ROOT_DIRS))
    pathspecs.extend([
        ':(glob,exclude)**/__pycache__/**',
        ':(glob,exclude)**/.pytest_cache/**',
        ':(glob,exclude)**/.mypy_cache/**',
        ':(glob,exclude)**/*.pyc',
        ':(glob,exclude)**/*.pyo',
    ])
    return pathspecs


def base_release_bundle_hash(repo_root: Path) -> str:
    """计算平台基座层源码 hash，排除扩展包、运行态 state、证书和临时产物。"""
    hasher = hashlib.sha256()
    paths = [
        path
        for path in repo_root.rglob('*')
        if path.is_file()
        and _should_hash_base_file(path.resolve().relative_to(repo_root.resolve()))
    ]
    for path in sorted(paths, key=lambda item: item.resolve().relative_to(repo_root.resolve()).as_posix()):
        rel_path = path.resolve().relative_to(repo_root.resolve()).as_posix()
        hasher.update(rel_path.encode('utf-8'))
        hasher.update(b'\0')
        hasher.update(path.read_bytes())
        hasher.update(b'\0')
    return hasher.hexdigest()


def _base_commit_matches_materialized_tree(repo_root: Path, commit: str) -> bool | None:
    if not _is_full_git_sha(commit):
        return None
    pathspecs = _base_release_git_pathspecs()
    try:
        completed = subprocess.run(
            ['git', 'diff', '--quiet', commit, '--', *pathspecs],
            cwd=repo_root,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    return None


def _source_metadata(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    return _read_json_object(Path(path).resolve(), default={})


def _stack_source_provenance_path(repo_root: Path) -> Path:
    return repo_root / EXTENSIONS_PROVENANCE_REL_PATH


def _stack_source_provenance_metadata(repo_root: Path) -> dict[str, Any]:
    path = _stack_source_provenance_path(repo_root)
    return _read_json_object(path, default={}) if path.is_file() else {}


def _clean_source_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    base = metadata.get('base') if isinstance(metadata.get('base'), dict) else {}
    base_metadata = {
        key: str(base.get(key) or '').strip()
        for key in ('repo', 'commit', 'tag', 'releaseBundleHash')
        if str(base.get(key) or '').strip()
    }
    if base_metadata:
        result['base'] = base_metadata
    extensions = metadata.get('extensions') if isinstance(metadata.get('extensions'), dict) else {}
    extension_metadata: dict[str, Any] = {}
    for extension_id, row in extensions.items():
        if not isinstance(row, dict):
            continue
        clean_row = {
            key: str(row.get(key) or '').strip()
            for key in ('repo', 'commit', 'tag', 'sourcePath')
            if str(row.get(key) or '').strip()
        }
        if clean_row:
            extension_metadata[str(extension_id)] = clean_row
    if extension_metadata:
        result['extensions'] = extension_metadata
    return result


def _source_metadata_base_issues(metadata: dict[str, Any]) -> list[str]:
    base = metadata.get('base') if isinstance(metadata.get('base'), dict) else {}
    commit = str(base.get('commit') or '').strip()
    release_bundle_hash = str(base.get('releaseBundleHash') or '').strip()
    issues: list[str] = []
    if commit and not _is_full_git_sha(commit):
        issues.append('source metadata base.commit must be a full 40-character git SHA')
    if release_bundle_hash and not SHA256_RE.fullmatch(release_bundle_hash):
        issues.append('source metadata base.releaseBundleHash must be a full 64-character SHA-256')
    return issues


def _write_stack_source_provenance(repo_root: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    payload = {'schemaVersion': STACK_SCHEMA_VERSION, **_clean_source_metadata(metadata)}
    write_json_atomic(_stack_source_provenance_path(repo_root), payload)
    return payload


def update_stack_source_provenance(
    repo_root: Path,
    *,
    source_metadata_path: str | Path | None = None,
    source_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把升级或物化得到的来源元数据写回 provenance 真源。"""
    repo_root = Path(repo_root).resolve()
    explicit_metadata = _explicit_source_metadata(source_metadata_path, source_metadata)
    metadata = _merge_source_metadata(_stack_source_provenance_metadata(repo_root), explicit_metadata)
    metadata = _with_bundled_extension_source_metadata(repo_root, metadata)
    base = _clean_source_metadata(metadata).get('base')
    if isinstance(base, dict):
        metadata = _metadata_with_bundled_extensions_from_base(
            repo_root,
            metadata,
            base,
            explicit_metadata=explicit_metadata,
        )
    issues = _source_metadata_base_issues(_clean_source_metadata(metadata))
    if issues:
        raise StackReleaseError('; '.join(issues))
    return _write_stack_source_provenance(repo_root, metadata)


def _merge_source_metadata(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(base, ensure_ascii=False))
    overlay_base = overlay.get('base') if isinstance(overlay.get('base'), dict) else {}
    if overlay_base:
        result_base = result.get('base') if isinstance(result.get('base'), dict) else {}
        result['base'] = {**result_base, **overlay_base}
    overlay_extensions = overlay.get('extensions') if isinstance(overlay.get('extensions'), dict) else {}
    if overlay_extensions:
        result_extensions = result.get('extensions') if isinstance(result.get('extensions'), dict) else {}
        merged_extensions: dict[str, Any] = dict(result_extensions)
        for extension_id, extension_meta in overlay_extensions.items():
            if not isinstance(extension_meta, dict):
                continue
            previous = merged_extensions.get(extension_id) if isinstance(merged_extensions.get(extension_id), dict) else {}
            merged_extensions[str(extension_id)] = {**previous, **extension_meta}
        result['extensions'] = merged_extensions
    return result


def _bundled_extension_source_metadata(repo_root: Path, base_metadata: dict[str, Any]) -> dict[str, Any]:
    base_repo = str(base_metadata.get('repo') or '').strip()
    base_commit = str(base_metadata.get('commit') or '').strip()
    base_tag = str(base_metadata.get('tag') or '').strip()
    if not base_repo and not base_commit and not base_tag:
        return {}
    extensions: dict[str, dict[str, str]] = {}
    for row in load_managed_extensions_index(repo_root):
        if row.status != MANAGED_EXPLICIT_EXTENSION_STATUS:
            continue
        extensions[row.id] = {
            key: value
            for key, value in {
                'repo': base_repo,
                'commit': base_commit,
                'tag': base_tag,
                'sourcePath': repo_rel(repo_root, row.root_dir),
            }.items()
            if value
        }
    return {'extensions': extensions} if extensions else {}


def _with_bundled_extension_source_metadata(repo_root: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    clean = _clean_source_metadata(metadata)
    base = clean.get('base') if isinstance(clean.get('base'), dict) else {}
    if not base:
        return metadata
    return _merge_source_metadata(_bundled_extension_source_metadata(repo_root, base), metadata)


def _explicit_source_metadata(source_metadata_path: str | Path | None, source_metadata: dict[str, Any] | None) -> dict[str, Any]:
    metadata = _source_metadata(source_metadata_path)
    return _merge_source_metadata(metadata, source_metadata or {})


def _metadata_without_stale_worktree_base(
    repo_root: Path,
    metadata: dict[str, Any],
    *,
    explicit_metadata: dict[str, Any],
) -> dict[str, Any]:
    if _git_toplevel(repo_root) is None:
        return metadata
    if 'base' in _clean_source_metadata(explicit_metadata):
        return metadata
    result = json.loads(json.dumps(metadata, ensure_ascii=False))
    result.pop('base', None)
    return result


def _metadata_with_bundled_extensions_from_base(
    repo_root: Path,
    metadata: dict[str, Any],
    base_entry: dict[str, Any],
    *,
    explicit_metadata: dict[str, Any],
) -> dict[str, Any]:
    explicit_extensions = _clean_source_metadata(explicit_metadata).get('extensions')
    explicit_extension_ids = set(explicit_extensions) if isinstance(explicit_extensions, dict) else set()
    bundled = _bundled_extension_source_metadata(repo_root, base_entry).get('extensions')
    if not isinstance(bundled, dict):
        return metadata
    result = json.loads(json.dumps(metadata, ensure_ascii=False))
    result_extensions = result.get('extensions') if isinstance(result.get('extensions'), dict) else {}
    base_repo = str(base_entry.get('repo') or '').strip()
    for extension_id, bundled_row in bundled.items():
        if extension_id in explicit_extension_ids:
            continue
        current = result_extensions.get(extension_id) if isinstance(result_extensions.get(extension_id), dict) else {}
        current_source = str(current.get('sourcePath') or '').strip()
        current_repo = str(current.get('repo') or '').strip()
        bundled_source = str(bundled_row.get('sourcePath') or '').strip()
        if current_source and current_source != bundled_source:
            continue
        if not current_source and current_repo and base_repo and current_repo != base_repo:
            continue
        result_extensions[extension_id] = {**current, **bundled_row}
    if result_extensions:
        result['extensions'] = result_extensions
    return result


def _current_worktree_source_metadata(repo_root: Path) -> dict[str, Any]:
    if _git_toplevel(repo_root) is None:
        return {}
    commit = _git_commit(repo_root)
    if not _is_full_git_sha(commit):
        return {}
    base = {
        'repo': _git_remote(repo_root),
        'commit': commit,
        'tag': _git_exact_tag(repo_root),
        'releaseBundleHash': base_release_bundle_hash(repo_root),
    }
    return {'base': {key: value for key, value in base.items() if value}}


def _composition_source_metadata(composition: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    base = composition.get('base') if isinstance(composition.get('base'), dict) else {}
    base_metadata = {
        key: str(base.get(key) or '').strip()
        for key in ('repo', 'commit', 'tag')
        if str(base.get(key) or '').strip()
    }
    if base_metadata:
        metadata['base'] = base_metadata
    extensions: dict[str, Any] = {}
    for item in composition.get('extensions') or []:
        if not isinstance(item, dict):
            continue
        extension_id = str(item.get('id') or '').strip()
        if not extension_id:
            continue
        extension_metadata = {
            key: str(item.get(key) or '').strip()
            for key in ('repo', 'commit', 'tag', 'sourcePath')
            if str(item.get(key) or '').strip()
        }
        if extension_metadata:
            extensions[extension_id] = extension_metadata
    if extensions:
        metadata['extensions'] = extensions
    return metadata


def _lock_base_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    base = payload.get('base') if isinstance(payload.get('base'), dict) else {}
    return {
        key: str(base.get(key) or '').strip()
        for key in ('repo', 'commit', 'tag')
        if str(base.get(key) or '').strip()
    }


def _source_git_metadata(source_root: Path, *, fallback: dict[str, Any]) -> dict[str, str]:
    top_level = _git_toplevel(source_root)
    commit = _git_commit(top_level) if top_level is not None else ''
    tag = _git_exact_tag(top_level) if top_level is not None else ''
    return {
        'repo': str(fallback.get('repo') or (_git_remote(top_level) if top_level is not None else '') or '').strip(),
        'commit': str(commit or fallback.get('commit') or '').strip(),
        'tag': str(fallback.get('tag') or tag or '').strip(),
        'sourcePath': str(fallback.get('sourcePath') or '').strip(),
    }


def _resolved_base_source_metadata(repo_root: Path, *, fallback: dict[str, Any]) -> dict[str, str]:
    top_level = _git_toplevel(repo_root)
    commit = _git_commit(top_level) if top_level is not None else ''
    tag = _git_exact_tag(top_level) if top_level is not None else ''
    requested_commit = str(fallback.get('commit') or '').strip()
    if requested_commit and not _is_full_git_sha(requested_commit) and not commit:
        raise StackReleaseError(f'base.commit 不能使用未解析的浮动引用：{requested_commit}')
    return {
        'repo': str(fallback.get('repo') or (_git_remote(top_level) if top_level is not None else '') or '').strip(),
        'commit': str(requested_commit if _is_full_git_sha(requested_commit) else (commit or '')).strip(),
        'tag': str(fallback.get('tag') or tag or '').strip(),
    }


def _metadata_for_extension(metadata: dict[str, Any], extension_id: str) -> dict[str, Any]:
    extensions = metadata.get('extensions') if isinstance(metadata.get('extensions'), dict) else {}
    row = extensions.get(extension_id) if isinstance(extensions.get(extension_id), dict) else {}
    return dict(row)


def _nested_git_metadata(repo_root: Path, row: ManagedExtensionRow) -> dict[str, str]:
    top_level = _git_toplevel(row.root_dir)
    if top_level is None or top_level.resolve() == repo_root.resolve():
        return {}
    return {
        'repo': _git_remote(top_level),
        'commit': _git_commit(top_level),
        'tag': _git_exact_tag(top_level),
    }


def _extension_entry(repo_root: Path, row: ManagedExtensionRow, *, metadata: dict[str, Any]) -> dict[str, Any]:
    manifest = _read_json_object(row.manifest_dir / f'{row.id}.json')
    override = _metadata_for_extension(metadata, row.id)
    nested = _nested_git_metadata(repo_root, row)
    requirements_lock = row.root_dir / 'requirements.lock'
    wheelhouse_manifest = row.root_dir / 'offline_wheelhouse' / 'manifest.json'
    compat = manifest.get('compat') if isinstance(manifest.get('compat'), dict) else {}
    return {
        'id': row.id,
        'repo': str(override.get('repo') or nested.get('repo') or ''),
        'commit': str(override.get('commit') or nested.get('commit') or ''),
        'tag': str(override.get('tag') or nested.get('tag') or ''),
        'sourcePath': str(override.get('sourcePath') or ''),
        'manifestVersion': str(manifest.get('version') or ''),
        'contentHash': content_hash(row.root_dir),
        'compat': {
            'controlPlane': str(compat.get('controlPlane') or ''),
        },
        'requirementsLockHash': _file_hash(requirements_lock),
        'wheelhouseManifestHash': _file_hash(wheelhouse_manifest),
        'rootDir': repo_rel(repo_root, row.root_dir),
    }


def _enabled_ids_from_payload(payload: dict[str, Any]) -> list[str]:
    extensions = payload.get('extensions') if isinstance(payload.get('extensions'), dict) else {}
    return [str(item).strip() for item in extensions.get('enabledExtensionIds') or [] if str(item).strip()]


def _deploy_env_schema_hashes(config_path: Path, payload: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for manifest in load_enabled_extensions(payload, service_base_dir=config_path.parent):
        extension_id = str(manifest.get('id') or '').strip()
        fragments = manifest.get('surfaceFragments') if isinstance(manifest.get('surfaceFragments'), dict) else {}
        path = fragments.get('deployEnvSchemaPath')
        if extension_id and isinstance(path, Path):
            result[extension_id] = _file_hash(path)
    return result


def _profile_entries(repo_root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for row in control_plane_profile_status_rows(repo_root):
        if row.get('status') != 'valid':
            entries.append({
                'id': str(row.get('id') or ''),
                'configPath': str(row.get('configPath') or row.get('path') or ''),
                'status': str(row.get('status') or ''),
                'issues': list(row.get('issues') or []),
            })
            continue
        config_path = (repo_root / str(row.get('configPath') or row.get('path') or '')).resolve()
        try:
            loaded_path, payload = load_control_plane_service_payload(config_path)
            enabled_ids = _enabled_ids_from_payload(payload)
            schema_hashes = _deploy_env_schema_hashes(loaded_path, payload)
        except Exception as exc:
            entries.append({
                'id': str(row.get('id') or ''),
                'configPath': str(row.get('configPath') or row.get('path') or ''),
                'status': 'invalid',
                'issues': [str(exc)],
            })
            continue
        entries.append({
            'id': str(row.get('id') or ''),
            'configPath': str(row.get('configPath') or row.get('path') or ''),
            'status': 'valid',
            'enabledExtensionIds': enabled_ids,
            'extensionIds': [item for item in enabled_ids if item != PLATFORM_EXTENSION_ID],
            'deployEnvSchemaHashes': schema_hashes,
            'deployEnvSchemaHash': _combined_hash(list(schema_hashes.items())),
        })
    return entries


def _generated_entry(repo_root: Path) -> dict[str, str]:
    return {
        'managedExtensionsIndexHash': _file_hash(repo_root / EXTENSIONS_INDEX_REL_PATH),
        'profileRegistryHash': _file_hash(repo_root / PROFILE_REGISTRY_REL_PATH),
        'extensionLockHash': _file_hash(extension_lock_path(repo_root)),
        'stackSourceProvenanceHash': _file_hash(repo_root / EXTENSIONS_PROVENANCE_REL_PATH),
    }


def build_stack_lock_payload(
    repo_root: Path,
    *,
    source_metadata_path: str | Path | None = None,
    source_metadata: dict[str, Any] | None = None,
    base_repo: str = '',
    base_commit: str = '',
    base_tag: str = '',
) -> dict[str, Any]:
    """构建当前物化 stack lock 载荷。

    参数说明：
    - repo_root：仓库根目录。
    - source_metadata_path/source_metadata：显式来源元数据，二者会与仓内 provenance 合并。
    - base_repo/base_commit/base_tag：手工指定基座来源，不能和 metadata.base 同时使用。

    返回用于写入 stack.lock 的字典；来源元数据冲突或缺失时抛出 StackReleaseError。
    """
    repo_root = Path(repo_root).resolve()
    explicit_metadata = _explicit_source_metadata(source_metadata_path, source_metadata)
    metadata = _merge_source_metadata(_stack_source_provenance_metadata(repo_root), explicit_metadata)
    metadata = _metadata_without_stale_worktree_base(repo_root, metadata, explicit_metadata=explicit_metadata)
    base_meta = metadata.get('base') if isinstance(metadata.get('base'), dict) else {}
    if base_meta and any(str(value or '').strip() for value in (base_repo, base_commit, base_tag)):
        raise StackReleaseError('base source metadata cannot be mixed with --base-repo/--base-commit/--base-tag')
    versions = platform_version_payload(repo_root)
    base_entry = {
        'repo': str(base_repo or base_meta.get('repo') or _git_remote(repo_root)),
        'commit': str(base_commit or base_meta.get('commit') or _git_commit(repo_root)),
        'tag': str(base_tag or base_meta.get('tag') or _git_exact_tag(repo_root)),
        **versions,
        'releaseBundleHash': base_release_bundle_hash(repo_root),
    }
    metadata = _merge_source_metadata(
        _bundled_extension_source_metadata(repo_root, base_entry),
        metadata,
    )
    metadata = _metadata_with_bundled_extensions_from_base(
        repo_root,
        metadata,
        base_entry,
        explicit_metadata=explicit_metadata,
    )
    rows = [
        row
        for row in load_managed_extensions_index(repo_root)
        if row.status == MANAGED_EXPLICIT_EXTENSION_STATUS
    ]
    payload = {
        'schemaVersion': STACK_SCHEMA_VERSION,
        'generatedAt': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'base': base_entry,
        'extensions': [
            _extension_entry(repo_root, row, metadata=metadata)
            for row in sorted(rows, key=lambda item: item.id)
        ],
        'profiles': _profile_entries(repo_root),
        'generated': _generated_entry(repo_root),
    }
    payload['compatibility'] = _compatibility_report(payload)
    return payload


def _strip_ephemeral(payload: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(payload, ensure_ascii=False))
    result.pop('generatedAt', None)
    return result


def _compatibility_report(payload: dict[str, Any]) -> dict[str, Any]:
    version = str((payload.get('base') or {}).get('controlPlaneVersion') or '')
    issues: list[str] = []
    rows: list[dict[str, str]] = []
    for extension in payload.get('extensions') or []:
        if not isinstance(extension, dict):
            continue
        extension_id = str(extension.get('id') or '')
        requirement = str((extension.get('compat') or {}).get('controlPlane') or '')
        ok = bool(version and requirement and _version_satisfies(version, requirement))
        if not ok:
            issues.append(f'{extension_id}: compat.controlPlane {requirement or "<missing>"} does not accept {version or "<missing>"}')
        rows.append({
            'id': extension_id,
            'controlPlane': version,
            'requirement': requirement,
            'status': 'ok' if ok else 'fail',
        })
    return {'status': 'ok' if not issues else 'fail', 'issues': issues, 'items': rows}


def _source_metadata_base_release_proof_ok(
    repo_root: Path,
    payload: dict[str, Any],
    source_metadata: dict[str, Any],
) -> bool:
    source_base = source_metadata.get('base') if isinstance(source_metadata.get('base'), dict) else {}
    if not source_base:
        return False
    source_commit = str(source_base.get('commit') or '').strip()
    source_release_hash = str(source_base.get('releaseBundleHash') or '').strip()
    source_repo = str(source_base.get('repo') or '').strip()
    actual_base = payload.get('base') if isinstance(payload.get('base'), dict) else {}
    actual_repo = str(actual_base.get('repo') or '').strip()
    actual_release_hash = str(actual_base.get('releaseBundleHash') or '').strip()
    if source_repo and actual_repo and source_repo != actual_repo:
        return False
    if not _is_full_git_sha(source_commit):
        return False
    if not SHA256_RE.fullmatch(source_release_hash):
        return False
    if not actual_release_hash or source_release_hash != actual_release_hash:
        return False
    return source_release_hash == base_release_bundle_hash(repo_root)


def _stack_source_provenance_matches_lock(repo_root: Path, payload: dict[str, Any]) -> bool:
    generated = payload.get('generated') if isinstance(payload.get('generated'), dict) else {}
    locked_hash = str(generated.get('stackSourceProvenanceHash') or '').strip()
    provenance_path = _stack_source_provenance_path(repo_root)
    return bool(locked_hash and provenance_path.is_file() and _file_hash(provenance_path) == locked_hash)


def _release_equivalent_base_commit_mismatch(
    repo_root: Path,
    *,
    expected_base: dict[str, Any],
    actual_payload: dict[str, Any],
    source_metadata: dict[str, Any],
) -> bool:
    """Return true when the checked lock and current HEAD describe the same base files."""
    actual_base = actual_payload.get('base') if isinstance(actual_payload.get('base'), dict) else {}
    expected_commit = str(expected_base.get('commit') or '').strip()
    actual_commit = str(actual_base.get('commit') or '').strip()
    if not expected_commit or not actual_commit or expected_commit == actual_commit:
        return False
    expected_release_hash = str(expected_base.get('releaseBundleHash') or '').strip()
    actual_release_hash = str(actual_base.get('releaseBundleHash') or '').strip()
    if (
        not SHA256_RE.fullmatch(expected_release_hash)
        or expected_release_hash != actual_release_hash
        or expected_release_hash != base_release_bundle_hash(repo_root)
    ):
        return False
    proof_candidates: list[dict[str, Any]] = []
    source_base = source_metadata.get('base') if isinstance(source_metadata.get('base'), dict) else {}
    if source_base:
        proof_candidates.append(source_base)
    if _stack_source_provenance_matches_lock(repo_root, actual_payload):
        provenance = _stack_source_provenance_metadata(repo_root)
        provenance_base = provenance.get('base') if isinstance(provenance.get('base'), dict) else {}
        if provenance_base:
            proof_candidates.append(provenance_base)
    actual_repo = str(actual_base.get('repo') or '').strip()
    expected_repo = str(expected_base.get('repo') or '').strip()
    for candidate in proof_candidates:
        proof_release_hash = str(candidate.get('releaseBundleHash') or '').strip()
        proof_commit = str(candidate.get('commit') or '').strip()
        proof_repo = str(candidate.get('repo') or '').strip()
        if not _is_full_git_sha(proof_commit):
            continue
        if proof_repo and actual_repo and proof_repo != actual_repo:
            continue
        if proof_repo and expected_repo and proof_repo != expected_repo:
            continue
        if proof_release_hash == expected_release_hash:
            return True
    return False


def _release_equivalent_extension_commit_mismatch(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    expected_commit = str(expected.get('commit') or '').strip()
    actual_commit = str(actual.get('commit') or '').strip()
    if not expected_commit or not actual_commit or expected_commit == actual_commit:
        return False
    if not _is_full_git_sha(expected_commit) or not _is_full_git_sha(actual_commit):
        return False
    expected_hash = str(expected.get('contentHash') or '').strip()
    actual_hash = str(actual.get('contentHash') or '').strip()
    if not expected_hash or expected_hash != actual_hash:
        return False
    expected_source = str(expected.get('sourcePath') or '').strip()
    actual_source = str(actual.get('sourcePath') or '').strip()
    if not expected_source or expected_source != actual_source:
        return False
    expected_repo = str(expected.get('repo') or '').strip()
    actual_repo = str(actual.get('repo') or '').strip()
    return not (expected_repo and actual_repo and expected_repo != actual_repo)


def _release_equivalent_expected_for_compare(
    expected: dict[str, Any],
    actual: dict[str, Any],
    *,
    base_commit_mismatch_is_release_equivalent: bool,
) -> dict[str, Any]:
    if not base_commit_mismatch_is_release_equivalent:
        return expected
    result = json.loads(json.dumps(expected, ensure_ascii=False))
    actual_base = actual.get('base') if isinstance(actual.get('base'), dict) else {}
    actual_base_commit = str(actual_base.get('commit') or '').strip()
    if actual_base_commit:
        result.setdefault('base', {})['commit'] = actual_base_commit
    actual_extensions = {
        str(row.get('id') or ''): row
        for row in actual.get('extensions') or []
        if isinstance(row, dict) and str(row.get('id') or '').strip()
    }
    for expected_extension in result.get('extensions') or []:
        if not isinstance(expected_extension, dict):
            continue
        extension_id = str(expected_extension.get('id') or '').strip()
        actual_extension = actual_extensions.get(extension_id)
        if not isinstance(actual_extension, dict):
            continue
        if _release_equivalent_extension_commit_mismatch(expected_extension, actual_extension):
            expected_extension['commit'] = str(actual_extension.get('commit') or '').strip()
    return result


def _strict_release_issues(
    payload: dict[str, Any],
    *,
    repo_root: Path | None = None,
    source_metadata: dict[str, Any] | None = None,
) -> list[str]:
    issues: list[str] = []
    clean_source_metadata = _clean_source_metadata(source_metadata or {})
    base = payload.get('base') if isinstance(payload.get('base'), dict) else {}
    release_proof_metadata = clean_source_metadata
    provenance: dict[str, Any] = {}
    provenance_base: dict[str, Any] = {}
    provenance_extensions: dict[str, Any] = {}
    if repo_root is not None:
        provenance = _stack_source_provenance_metadata(repo_root)
        provenance_base = provenance.get('base') if isinstance(provenance.get('base'), dict) else {}
        if 'base' not in clean_source_metadata and provenance_base:
            release_proof_metadata = _merge_source_metadata({'base': provenance_base}, clean_source_metadata)
    if not str(base.get('repo') or '').strip():
        issues.append('base.repo is required for strict release')
    base_commit = str(base.get('commit') or '').strip()
    if not base_commit:
        issues.append('base.commit is required for strict release')
    elif not _is_full_git_sha(base_commit):
        issues.append('base.commit must be a full 40-character git SHA for strict release')
    if repo_root is not None:
        if _is_full_git_sha(base_commit):
            base_matches = _base_commit_matches_materialized_tree(repo_root, base_commit)
            if base_matches is None:
                if not _source_metadata_base_release_proof_ok(repo_root, payload, release_proof_metadata):
                    issues.append(
                        'base.commit must be readable in local git history for strict release '
                        'or be backed by source metadata/provenance releaseBundleHash'
                    )
            elif not base_matches:
                issues.append('base.commit content must match base release bundle hash for strict release')
        dirty_paths = _git_dirty_paths(repo_root)
        if dirty_paths:
            issues.append(f'base working tree must be clean for strict release ({len(dirty_paths)} dirty paths)')
        generated = payload.get('generated') if isinstance(payload.get('generated'), dict) else {}
        provenance_path = _stack_source_provenance_path(repo_root)
        provenance_hash = str(generated.get('stackSourceProvenanceHash') or '').strip()
        if payload.get('extensions'):
            if not provenance_hash:
                issues.append('stack source provenance is required for strict release')
            elif not provenance_path.is_file():
                issues.append('stack source provenance file is required for strict release')
            else:
                current_hash = _file_hash(provenance_path)
                if current_hash != provenance_hash:
                    issues.append('stack source provenance hash drift for strict release')
        if payload.get('extensions'):
            if not provenance_base:
                issues.append('stack source provenance base is required for strict release')
            else:
                for key in ('repo', 'commit', 'releaseBundleHash'):
                    locked_value = str(base.get(key) or '').strip()
                    provenance_value = str(provenance_base.get(key) or '').strip()
                    if locked_value and locked_value != provenance_value:
                        issues.append(f'stack source provenance base.{key} does not match stack lock')
        provenance_extensions = provenance.get('extensions') if isinstance(provenance.get('extensions'), dict) else {}
    for extension in payload.get('extensions') or []:
        if not isinstance(extension, dict):
            continue
        extension_id = str(extension.get('id') or '<unknown>')
        if not str(extension.get('repo') or '').strip():
            issues.append(f'{extension_id}: repo is required for strict release')
        extension_commit = str(extension.get('commit') or '').strip()
        if not extension_commit:
            issues.append(f'{extension_id}: commit is required for strict release')
        elif not _is_full_git_sha(extension_commit):
            issues.append(f'{extension_id}: commit must be a full 40-character git SHA for strict release')
        if repo_root is not None:
            provenance_row = provenance_extensions.get(extension_id) if isinstance(provenance_extensions.get(extension_id), dict) else {}
            if not provenance_row:
                issues.append(f'{extension_id}: source provenance is required for strict release')
            else:
                for key in ('repo', 'commit', 'sourcePath'):
                    locked_value = str(extension.get(key) or '').strip()
                    provenance_value = str(provenance_row.get(key) or '').strip()
                    if locked_value and locked_value != provenance_value:
                        issues.append(f'{extension_id}: source provenance {key} does not match stack lock')
    return issues


def verify_stack_lock(
    repo_root: Path,
    *,
    lock_path: str | Path | None = None,
    source_metadata_path: str | Path | None = None,
    source_metadata: dict[str, Any] | None = None,
    strict_release: bool = False,
) -> dict[str, Any]:
    """校验已登记 stack lock 是否匹配当前物化树。

    参数说明：
    - repo_root：仓库根目录。
    - lock_path：可选 stack lock 路径，缺省读取仓库固定位置。
    - source_metadata_path/source_metadata：用于复现期望 lock 的来源元数据。
    - strict_release：为 True 时额外要求完整 release 来源证明。

    返回包含 ok、issues 和期望/实际摘要的字典；lock 文件不存在或 JSON 非对象时抛错。
    """
    repo_root = Path(repo_root).resolve()
    resolved_lock_path = repo_root / STACK_LOCK_REL_PATH if not str(lock_path or '').strip() else Path(lock_path).resolve()
    actual = _read_json_object(resolved_lock_path)
    expected_metadata = _source_metadata(source_metadata_path)
    expected_metadata = _merge_source_metadata(expected_metadata, source_metadata or {})
    clean_expected_metadata = _clean_source_metadata(expected_metadata)
    source_metadata_issues = _source_metadata_base_issues(clean_expected_metadata)
    if (
        not _stack_source_provenance_path(repo_root).is_file()
        and 'base' not in clean_expected_metadata
    ):
        expected_metadata = _merge_source_metadata({'base': _lock_base_metadata(actual)}, expected_metadata)
    expected = build_stack_lock_payload(
        repo_root,
        source_metadata=expected_metadata,
    )
    issues: list[str] = list(source_metadata_issues)
    expected_base = expected.get('base') if isinstance(expected.get('base'), dict) else {}
    actual_base = actual.get('base') if isinstance(actual.get('base'), dict) else {}
    expected_base_commit = str(expected_base.get('commit') or '').strip()
    actual_base_commit = str(actual_base.get('commit') or '').strip()
    expected_release_bundle_hash = str(expected_base.get('releaseBundleHash') or '').strip()
    source_base = clean_expected_metadata.get('base') if isinstance(clean_expected_metadata.get('base'), dict) else {}
    source_release_bundle_hash = str(source_base.get('releaseBundleHash') or '').strip()
    if (
        source_release_bundle_hash
        and expected_release_bundle_hash
        and source_release_bundle_hash != expected_release_bundle_hash
    ):
        issues.append(
            'source metadata base.releaseBundleHash does not match current base release files '
            f'({source_release_bundle_hash} != {expected_release_bundle_hash})'
        )
    commit_mismatch_is_release_equivalent = _release_equivalent_base_commit_mismatch(
        repo_root,
        expected_base=expected_base,
        actual_payload=actual,
        source_metadata=clean_expected_metadata,
    )
    if (
        expected_base_commit
        and actual_base_commit
        and expected_base_commit != actual_base_commit
        and not commit_mismatch_is_release_equivalent
    ):
        issues.append(
            'base.commit does not match expected source metadata '
            f'({actual_base_commit} != {expected_base_commit})'
        )
    expected_for_compare = _release_equivalent_expected_for_compare(
        expected,
        actual,
        base_commit_mismatch_is_release_equivalent=commit_mismatch_is_release_equivalent,
    )
    if _strip_ephemeral(actual) != _strip_ephemeral(expected_for_compare):
        issues.append(f'stack lock drift: {repo_rel(repo_root, resolved_lock_path)}')
    compatibility = _compatibility_report(actual)
    issues.extend(str(item) for item in compatibility.get('issues') or [])
    if strict_release:
        issues.extend(_strict_release_issues(actual, repo_root=repo_root, source_metadata=clean_expected_metadata))
    return {
        'status': 'ok' if not issues else 'fail',
        'lockPath': str(resolved_lock_path),
        'issues': issues,
        'compatibility': compatibility,
    }


def write_stack_lock(
    repo_root: Path,
    *,
    output_path: str | Path | None = None,
    source_metadata_path: str | Path | None = None,
    source_metadata: dict[str, Any] | None = None,
    base_repo: str = '',
    base_commit: str = '',
    base_tag: str = '',
) -> dict[str, Any]:
    """生成并写出 stack lock；output_path 为空时写入仓库默认锁文件。"""
    repo_root = Path(repo_root).resolve()
    resolved_output = repo_root / STACK_LOCK_REL_PATH if not str(output_path or '').strip() else Path(output_path).resolve()
    payload = build_stack_lock_payload(
        repo_root,
        source_metadata_path=source_metadata_path,
        source_metadata=source_metadata,
        base_repo=base_repo,
        base_commit=base_commit,
        base_tag=base_tag,
    )
    write_json_atomic(resolved_output, payload)
    return payload


def _manifest_path_for_extension(root: Path, extension_id: str) -> Path:
    return root / 'config' / 'control_plane' / 'extensions.d' / f'{extension_id}.json'


def _service_path_for_extension(root: Path, extension_id: str) -> Path:
    return root / 'config' / 'control_plane' / 'profiles' / f'{extension_id}.service.json'


def _extension_title(root: Path, extension_id: str) -> str:
    payload = _read_json_object(_manifest_path_for_extension(root, extension_id))
    title = str(payload.get('title') or extension_id).strip()
    return title or extension_id


def _extension_row_payload(repo_root: Path, extension_id: str, root: Path) -> dict[str, Any]:
    root = root.resolve()
    return {
        'id': extension_id,
        'title': _extension_title(root, extension_id),
        'rootDir': repo_rel(repo_root, root),
        'defaultServiceConfigPath': repo_rel(repo_root, _service_path_for_extension(root, extension_id)),
        'manifestDir': repo_rel(repo_root, root / 'config' / 'control_plane' / 'extensions.d'),
        'pythonRoots': [repo_rel(repo_root, root / 'python')],
        'status': MANAGED_EXPLICIT_EXTENSION_STATUS,
    }


def _materialized_extension_ids(repo_root: Path) -> list[str]:
    root = repo_root / 'agent' / 'extensions'
    if not root.is_dir():
        return []
    result: list[str] = []
    for child in sorted(path for path in root.iterdir() if path.is_dir()):
        extension_id = child.name
        if _manifest_path_for_extension(child, extension_id).is_file() and _service_path_for_extension(child, extension_id).is_file():
            result.append(extension_id)
    return result


def _write_index_from_materialized(repo_root: Path) -> dict[str, Any]:
    rows = [
        _extension_row_payload(repo_root, extension_id, repo_root / 'agent' / 'extensions' / extension_id)
        for extension_id in _materialized_extension_ids(repo_root)
    ]
    payload = {'extensions': rows}
    write_json_atomic(repo_root / EXTENSIONS_INDEX_REL_PATH, payload)
    return payload


def _write_profile_registry_from_materialized(repo_root: Path, extension_ids: list[str]) -> str:
    lines = [
        '# profile_id\tconfig_path',
        'base\tconfig/control_plane/service.json',
        'agent_platform\tconfig/control_plane/profiles/agent_platform.service.json',
    ]
    materialized_ids = set(extension_ids)
    for row in control_plane_repo_combination_profile_rows(repo_root):
        expected_ids = {
            str(item).strip()
            for item in row.get('enabledExtensionIds') or ()
            if str(item).strip() and str(item).strip() != PLATFORM_EXTENSION_ID
        }
        if expected_ids.issubset(materialized_ids):
            lines.append(f"{row['id']}\t{row['configPath']}")
    for extension_id in sorted(extension_ids):
        lines.append(
            f'{extension_id}\tagent/extensions/{extension_id}/config/control_plane/profiles/{extension_id}.service.json'
        )
    content = '\n'.join(lines) + '\n'
    path = repo_root / PROFILE_REGISTRY_REL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    return content


def _safe_extension_target(repo_root: Path, extension_id: str) -> Path:
    target = (repo_root / 'agent' / 'extensions' / extension_id).resolve()
    allowed_root = (repo_root / 'agent' / 'extensions').resolve()
    try:
        target.relative_to(allowed_root)
    except ValueError as exc:
        raise StackReleaseError(f'extension target 越界：{target}') from exc
    return target


def _validate_source_root(source_root: Path, extension_id: str) -> Path:
    source_root = Path(source_root).resolve()
    manifest_path = _manifest_path_for_extension(source_root, extension_id)
    service_path = _service_path_for_extension(source_root, extension_id)
    python_root = source_root / 'python'
    if not manifest_path.is_file():
        raise StackReleaseError(f'extension manifest 缺失：{manifest_path}')
    manifest = _read_json_object(manifest_path)
    if str(manifest.get('id') or '').strip() != extension_id:
        raise StackReleaseError(f'extension id 不匹配：{extension_id} != {manifest.get("id") or "<missing>"}')
    if not service_path.is_file():
        raise StackReleaseError(f'extension service profile 缺失：{service_path}')
    if not python_root.is_dir():
        raise StackReleaseError(f'extension python root 缺失：{python_root}')
    return source_root


def _checkout_repo_source(spec: dict[str, Any], extension_id: str) -> tempfile.TemporaryDirectory[str]:
    repo = str(spec.get('repo') or '').strip()
    commit = str(spec.get('commit') or '').strip()
    if not repo or not commit:
        raise StackReleaseError(f'{extension_id}: repo 与 commit 必须同时声明')
    tmp = tempfile.TemporaryDirectory(prefix=f'openclaw-stack-{extension_id}-')
    checkout_root = Path(tmp.name).resolve() / 'repo'
    for command, cwd in (
        (['git', 'clone', '--no-checkout', repo, str(checkout_root)], None),
        (['git', 'checkout', commit], checkout_root),
    ):
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            encoding='utf-8',
            errors='replace',
            check=False,
        )
        if completed.returncode != 0:
            detail = '\n'.join(
                part.strip()
                for part in (completed.stdout, completed.stderr)
                if part and part.strip()
            )
            tmp.cleanup()
            raise StackReleaseError(f'{extension_id}: git source checkout failed: {detail or command}')
    return tmp


def _source_root_for_spec(spec: dict[str, Any], extension_id: str, tmp_dirs: list[tempfile.TemporaryDirectory[str]]) -> Path:
    source_path = str(spec.get('sourcePath') or '').strip()
    subdir = str(spec.get('subdir') or '').strip()
    if source_path:
        source_root = Path(source_path).resolve()
    else:
        tmp = _checkout_repo_source(spec, extension_id)
        tmp_dirs.append(tmp)
        source_root = Path(tmp.name).resolve() / 'repo'
    if subdir:
        source_root = source_root / subdir
    return _validate_source_root(source_root, extension_id)


def _composition_extension_ids(specs: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for spec in specs:
        extension_id = str(spec.get('id') or '').strip()
        if not extension_id:
            raise StackReleaseError('composition extension.id 不能为空')
        if extension_id in seen:
            raise StackReleaseError(f'composition extension.id 重复：{extension_id}')
        seen.add(extension_id)
        result.append(extension_id)
    return result


def _guard_composition_extension_set(repo_root: Path, desired_ids: list[str]) -> None:
    existing = set(_materialized_extension_ids(repo_root))
    desired = set(desired_ids)
    extra = sorted(existing - desired)
    if extra:
        raise StackReleaseError(
            'composition 必须声明完整扩展集合；发现未声明的已物化扩展：' + ', '.join(extra)
        )


def materialize_stack(
    repo_root: Path,
    *,
    composition_path: str | Path | None = None,
    refresh_current: bool = False,
    replace: bool = False,
    dry_run: bool = False,
    output_path: str | Path | None = None,
    source_metadata_path: str | Path | None = None,
) -> dict[str, Any]:
    """按 composition 物化扩展源码，并刷新 extensions index、profile registry 与 stack lock。"""
    repo_root = Path(repo_root).resolve()
    if not refresh_current and not composition_path:
        raise StackReleaseError('materialize 需要 --composition 或 --refresh-current')
    tmp_dirs: list[tempfile.TemporaryDirectory[str]] = []
    actions: list[dict[str, Any]] = []
    try:
        specs = []
        composition_metadata: dict[str, Any] = {}
        if composition_path:
            composition = _read_json_object(Path(composition_path).resolve())
            composition_metadata = _composition_source_metadata(composition)
            if not dry_run:
                base_fallback = composition_metadata.get('base') if isinstance(composition_metadata.get('base'), dict) else {}
                resolved_base = {
                    key: value
                    for key, value in _resolved_base_source_metadata(repo_root, fallback=base_fallback).items()
                    if value
                }
                if resolved_base:
                    composition_metadata = _merge_source_metadata(composition_metadata, {'base': resolved_base})
            raw_specs = composition.get('extensions') or []
            if not isinstance(raw_specs, list):
                raise StackReleaseError('composition.extensions 必须是数组')
            if any(not isinstance(item, dict) for item in raw_specs):
                raise StackReleaseError('composition.extensions 的每一项都必须是对象')
            specs = [dict(item) for item in raw_specs]
            desired_extension_ids = _composition_extension_ids(specs)
            _guard_composition_extension_set(repo_root, desired_extension_ids)
        for spec in specs:
            extension_id = str(spec.get('id') or '').strip()
            if dry_run and not str(spec.get('sourcePath') or '').strip():
                repo = str(spec.get('repo') or '').strip()
                commit = str(spec.get('commit') or '').strip()
                if not repo or not commit:
                    raise StackReleaseError(f'{extension_id}: repo 与 commit 必须同时声明')
                target = _safe_extension_target(repo_root, extension_id)
                source_label = f'{repo}@{commit}'
                subdir = str(spec.get('subdir') or '').strip()
                if subdir:
                    source_label = f'{source_label}:{subdir}'
                actions.append({
                    'id': extension_id,
                    'source': source_label,
                    'target': str(target),
                    'replace': replace,
                })
                continue
            source_root = _source_root_for_spec(spec, extension_id, tmp_dirs)
            target = _safe_extension_target(repo_root, extension_id)
            source_metadata = _source_git_metadata(source_root, fallback=spec)
            composition_metadata = _merge_source_metadata(
                composition_metadata,
                {'extensions': {extension_id: {key: value for key, value in source_metadata.items() if value}}},
            )
            actions.append({
                'id': extension_id,
                'source': str(source_root),
                'target': str(target),
                'replace': replace,
            })
            if dry_run:
                continue
            if target.exists():
                if not replace:
                    raise StackReleaseError(f'extension target 已存在，需显式 --replace：{target}')
                shutil.rmtree(target)
            shutil.copytree(
                source_root,
                target,
                ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo', '.git'),
            )
        if dry_run:
            return {'status': 'ok', 'dryRun': True, 'actions': actions}
        index_payload = _write_index_from_materialized(repo_root)
        extension_ids = [str(row.get('id') or '') for row in index_payload.get('extensions') or [] if isinstance(row, dict)]
        _write_profile_registry_from_materialized(repo_root, extension_ids)
        extension_lock = write_lock(repo_root)
        lock_metadata = _merge_source_metadata(_source_metadata(source_metadata_path), composition_metadata)
        if composition_path or source_metadata_path:
            _write_stack_source_provenance(repo_root, lock_metadata)
        stack_lock = write_stack_lock(
            repo_root,
            output_path=output_path,
            source_metadata=lock_metadata,
        )
        return {
            'status': 'ok',
            'dryRun': False,
            'actions': actions,
            'extensionIds': extension_ids,
            'extensionLock': extension_lock,
            'stackLock': stack_lock,
        }
    finally:
        for tmp in tmp_dirs:
            tmp.cleanup()


def _print_json(payload: Any) -> int:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    return 0


def _emit_text(payload: dict[str, Any]) -> int:
    status = str(payload.get('status') or 'ok')
    print(f'stack release: {status}')
    for issue in payload.get('issues') or []:
        print(f'- {issue}')
    return 0 if status == 'ok' else 1


def cmd_show(args: argparse.Namespace) -> int:
    """处理 show 子命令，只计算并输出 stack lock payload，不写文件。"""
    if str(args.source_metadata or '').strip() and any(
        str(value or '').strip()
        for value in (args.base_repo, args.base_commit, args.base_tag)
    ):
        raise StackReleaseError('base source metadata cannot be mixed with --base-repo/--base-commit/--base-tag')
    payload = build_stack_lock_payload(
        _repo_root(args.repo_root),
        source_metadata_path=args.source_metadata,
        base_repo=args.base_repo,
        base_commit=args.base_commit,
        base_tag=args.base_tag,
    )
    return _print_json(payload)


def cmd_lock(args: argparse.Namespace) -> int:
    """处理 lock 子命令；dry-run 只输出 payload，正式模式会写 stack lock。"""
    repo_root = _repo_root(args.repo_root)
    has_source_metadata = bool(str(args.source_metadata or '').strip())
    has_base_overrides = any(
        str(value or '').strip()
        for value in (args.base_repo, args.base_commit, args.base_tag)
    )
    if str(args.source_metadata or '').strip() and any(
        str(value or '').strip()
        for value in (args.base_repo, args.base_commit, args.base_tag)
    ):
        raise StackReleaseError('base source metadata cannot be mixed with --base-repo/--base-commit/--base-tag')
    if bool(args.update_source_provenance) and not str(args.source_metadata or '').strip():
        raise StackReleaseError('--update-source-provenance 需要同时提供 --source-metadata')
    if bool(args.update_source_provenance) and not bool(args.dry_run):
        update_stack_source_provenance(repo_root, source_metadata_path=args.source_metadata)
    elif not bool(args.dry_run) and not has_source_metadata and not has_base_overrides:
        worktree_metadata = _current_worktree_source_metadata(repo_root)
        if worktree_metadata:
            update_stack_source_provenance(repo_root, source_metadata=worktree_metadata)
    payload = build_stack_lock_payload(
        repo_root,
        source_metadata_path=args.source_metadata,
        base_repo=args.base_repo,
        base_commit=args.base_commit,
        base_tag=args.base_tag,
    ) if bool(args.dry_run) else write_stack_lock(
        repo_root,
        output_path=args.output,
        source_metadata_path=args.source_metadata,
        base_repo=args.base_repo,
        base_commit=args.base_commit,
        base_tag=args.base_tag,
    )
    return _print_json({'status': 'ok', 'dryRun': bool(args.dry_run), 'stackLock': payload})


def cmd_verify(args: argparse.Namespace) -> int:
    """处理 verify 子命令，比较当前仓库事实与已提交 stack lock 是否一致。"""
    payload = verify_stack_lock(
        _repo_root(args.repo_root),
        lock_path=args.lock_path,
        source_metadata_path=args.source_metadata,
        strict_release=bool(args.strict_release),
    )
    return _print_json(payload) if bool(args.json) else _emit_text(payload)


def cmd_materialize(args: argparse.Namespace) -> int:
    """处理 materialize 子命令，按组合清单同步扩展源码并刷新派生真源。"""
    payload = materialize_stack(
        _repo_root(args.repo_root),
        composition_path=args.composition,
        refresh_current=bool(args.refresh_current),
        replace=bool(args.replace),
        dry_run=bool(args.dry_run),
        output_path=args.output,
        source_metadata_path=args.source_metadata,
    )
    return _print_json(payload)


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--repo-root', default='')
    parser.add_argument('--source-metadata', default='')


def _add_base_overrides(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--base-repo', default='')
    parser.add_argument('--base-commit', default='')
    parser.add_argument('--base-tag', default='')


def build_parser() -> argparse.ArgumentParser:
    """构造 stack release CLI 参数解析器，注册 show/lock/verify/materialize 子命令。"""
    parser = argparse.ArgumentParser(prog='python -m openclaw.cli control-plane stack')
    subparsers = parser.add_subparsers(dest='command', required=True)

    show_parser = subparsers.add_parser('show')
    _add_common(show_parser)
    _add_base_overrides(show_parser)
    show_parser.set_defaults(func=cmd_show)

    lock_parser = subparsers.add_parser('lock')
    _add_common(lock_parser)
    _add_base_overrides(lock_parser)
    lock_parser.add_argument('--output', default='')
    lock_parser.add_argument('--dry-run', action='store_true')
    lock_parser.add_argument('--update-source-provenance', action='store_true')
    lock_parser.set_defaults(func=cmd_lock)

    verify_parser = subparsers.add_parser('verify')
    _add_common(verify_parser)
    verify_parser.add_argument('--lock-path', default='')
    verify_parser.add_argument('--strict-release', action='store_true')
    verify_parser.add_argument('--json', action='store_true')
    verify_parser.set_defaults(func=cmd_verify)

    materialize_parser = subparsers.add_parser('materialize')
    _add_common(materialize_parser)
    materialize_parser.add_argument('--composition', default='')
    materialize_parser.add_argument('--refresh-current', action='store_true')
    materialize_parser.add_argument('--replace', action='store_true')
    materialize_parser.add_argument('--dry-run', action='store_true')
    materialize_parser.add_argument('--output', default='')
    materialize_parser.set_defaults(func=cmd_materialize)
    return parser


def main(argv: list[str] | None = None) -> int:
    """执行 stack release CLI，并把可预期异常转换为统一失败码。"""
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return int(args.func(args) or 0)
    except (StackReleaseError, subprocess.CalledProcessError, OSError, ValueError) as exc:
        sys.stderr.write(f'[stack_release][FAIL] {exc}\n')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
