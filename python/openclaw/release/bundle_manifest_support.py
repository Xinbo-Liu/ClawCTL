#!/usr/bin/env python3
"""交付包 manifest 与 allowlist 的共享治理辅助。"""
from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from openclaw.lib.repo.local_workspace_policy import bundle_shared_excludes as policy_bundle_shared_excludes
from openclaw.lib.repo.static_truth import repo_contract_path, repo_contract_root

ROOT_DIR = repo_contract_root()
MANIFEST_PATH = repo_contract_path('governance.bundle_manifest')
GLOB_CHARS = set('*?[]')


def read_json(path: Path) -> Any:
    """读取 UTF-8 JSON 文件。"""
    import json

    return json.loads(path.read_text(encoding='utf-8'))


def _merge_unique_patterns(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for raw in group:
            pattern = str(raw or '').strip()
            if not pattern or pattern in seen:
                continue
            seen.add(pattern)
            merged.append(pattern)
    return merged


def load_manifest(*, error_factory: type[Exception]) -> dict[str, Any]:
    """加载交付包治理 manifest，并合并本地残留排除真源。"""
    payload = read_json(MANIFEST_PATH)
    if not isinstance(payload, dict):
        raise error_factory(f'{MANIFEST_PATH.relative_to(ROOT_DIR)} top level must be an object')
    bundles = payload.get('bundles')
    if not isinstance(bundles, dict) or not bundles:
        raise error_factory('bundle_manifest.json -> bundles must be a non-empty object')
    for bundle_id, spec in bundles.items():
        if not isinstance(spec, dict):
            continue
        if 'forbidden' in spec:
            raise error_factory(f'bundle_manifest.json -> bundles.{bundle_id}.forbidden is not supported; use must_not_ship')
    raw_shared_excludes = payload.get('sharedExcludes') or []
    if not isinstance(raw_shared_excludes, list):
        raise error_factory('bundle_manifest.json -> sharedExcludes must be a list')
    payload['sharedExcludes'] = _merge_unique_patterns(
        default_shared_excludes(error_factory=error_factory),
        [str(item).strip() for item in raw_shared_excludes if str(item).strip()],
    )
    return payload


def contains_glob(pattern: str) -> bool:
    """判断 pattern 是否包含 glob 语法。"""
    return any(ch in pattern for ch in GLOB_CHARS)


def normalize_pattern(pattern: str) -> str:
    normalized = str(pattern or '').strip().replace('\\', '/')
    while normalized.startswith('./'):
        normalized = normalized[2:]
    return normalized


def iter_pattern_matches(pattern: str) -> list[str]:
    """把 manifest include pattern 展开为仓库内文件列表。"""
    rel = normalize_pattern(pattern)
    if not rel:
        return []
    target = ROOT_DIR / rel
    results: set[str] = set()
    if target.exists() and target.is_file() and not contains_glob(rel):
        return [rel]
    if target.exists() and target.is_dir() and not contains_glob(rel):
        for path in target.rglob('*'):
            if path.is_file():
                results.add(path.relative_to(ROOT_DIR).as_posix())
        return sorted(results)
    import glob

    for raw in glob.glob(str(ROOT_DIR / rel), recursive=True):
        path = Path(raw)
        if path.is_file():
            results.add(path.relative_to(ROOT_DIR).as_posix())
    return sorted(results)


def match_any(rel_path: str, patterns: list[str]) -> bool:
    """判断仓库相对路径是否命中任一 pattern。"""
    for raw in patterns:
        pattern = normalize_pattern(raw)
        if not pattern:
            continue
        if fnmatch.fnmatch(rel_path, pattern):
            return True
        if pattern.endswith('/') and rel_path.startswith(pattern):
            return True
        if not contains_glob(pattern):
            if rel_path == pattern or rel_path.startswith(pattern + '/'):
                return True
    return False


def default_shared_excludes(*, error_factory: type[Exception]) -> list[str]:
    """返回本地残留策略派生的共享排除项，并补充 bundle 专属排除项。"""
    return _merge_unique_patterns(
        policy_bundle_shared_excludes(error_factory=error_factory),
        [
            '.git/**',
            '*.zip',
            'deploy/.env',
            'deploy/site.env',
            'deploy/nginx/certs/**',
            '.local_mounts/**',
        ],
    )


def bundle_spec(bundle_id: str, manifest: dict[str, Any], *, error_factory: type[Exception]) -> dict[str, Any]:
    """从 manifest 中读取单个 bundle 定义。"""
    spec = (manifest.get('bundles') or {}).get(bundle_id)
    if not isinstance(spec, dict):
        raise error_factory(f'unknown bundle: {bundle_id}')
    if 'forbidden' in spec:
        raise error_factory(f'bundle_manifest.json -> bundles.{bundle_id}.forbidden is not supported; use must_not_ship')
    return spec


def resolve_bundle_files(bundle_id: str, manifest: dict[str, Any], *, error_factory: type[Exception]) -> list[str]:
    """解析 bundle 最终允许打包的文件集合。"""
    spec = bundle_spec(bundle_id, manifest, error_factory=error_factory)
    includes = [str(item).strip() for item in (spec.get('include') or []) if str(item).strip()]
    if not includes:
        raise error_factory(f'bundle {bundle_id} is missing include entries')
    shared_excludes = [str(item).strip() for item in (manifest.get('sharedExcludes') or []) if str(item).strip()]
    excludes = shared_excludes + [str(item).strip() for item in (spec.get('exclude') or []) if str(item).strip()]
    resolved: list[str] = []
    seen: set[str] = set()
    for pattern in includes:
        matches = iter_pattern_matches(pattern)
        if not matches:
            raise error_factory(f'bundle {bundle_id} include matched no files: {pattern}')
        for rel in matches:
            if match_any(rel, excludes):
                continue
            if rel not in seen:
                resolved.append(rel)
                seen.add(rel)
    resolved.sort()
    if not resolved:
        raise error_factory(f'bundle {bundle_id} resolved to no files; check include/exclude rules')
    return resolved


def must_not_ship_hits(bundle_id: str, file_list: list[str], manifest: dict[str, Any], *, error_factory: type[Exception]) -> list[str]:
    """返回已解析文件中命中 must_not_ship 规则的路径。"""
    spec = bundle_spec(bundle_id, manifest, error_factory=error_factory)
    raw_patterns = spec.get('must_not_ship')
    if raw_patterns is None:
        raw_patterns = []
    patterns = [str(item).strip() for item in raw_patterns if str(item).strip()] if isinstance(raw_patterns, list) else []
    return [path for path in file_list if match_any(path, patterns)]
