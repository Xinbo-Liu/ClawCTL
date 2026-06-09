#!/usr/bin/env python3
"""Repository and extension anchored path contract helpers."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.repo_root import RepoRootResolutionError


REPO_ANCHORED_PATH_PREFIX = '@repo/'
EXTENSION_ANCHORED_PATH_PREFIX = '@extension/'


def _path_is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def repo_anchored_path(relative_path: str) -> str:
    text = str(relative_path or '').strip().replace('\\', '/').lstrip('/')
    if not text:
        raise ValueError('relative_path must be non-empty')
    return f'{REPO_ANCHORED_PATH_PREFIX}{text}'


def extension_anchored_path(relative_path: str) -> str:
    text = str(relative_path or '').strip().replace('\\', '/').lstrip('/')
    if not text:
        raise ValueError('relative_path must be non-empty')
    return f'{EXTENSION_ANCHORED_PATH_PREFIX}{text}'


def is_repo_anchored_path(value: Any) -> bool:
    return str(value or '').strip().startswith(REPO_ANCHORED_PATH_PREFIX)


def is_extension_anchored_path(value: Any) -> bool:
    return str(value or '').strip().startswith(EXTENSION_ANCHORED_PATH_PREFIX)


def resolve_extension_root(start_path: Path) -> Path:
    resolved = Path(start_path).resolve()
    candidates = (resolved, *resolved.parents)
    try:
        managed_extensions_root = (resolve_repo_root(resolved) / 'agent' / 'extensions').resolve()
    except RepoRootResolutionError as exc:
        raise ValueError(f'cannot resolve extension root from {resolved}') from exc
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate.parent.resolve() == managed_extensions_root and candidate.name:
            return candidate
    raise ValueError(f'cannot resolve extension root from {resolved}')


def resolve_path_contract(
    value: Any,
    *,
    base_dir: Path,
    start_path: Path | None = None,
    repo_root: Path | None = None,
) -> Path | None:
    text = str(value or '').strip()
    if not text:
        return None
    if is_repo_anchored_path(text):
        resolved_repo_root = Path(repo_root).resolve() if repo_root is not None else resolve_repo_root(base_dir if start_path is None else start_path)
        relative = text[len(REPO_ANCHORED_PATH_PREFIX):].strip().replace('\\', '/').lstrip('/')
        if not relative:
            raise ValueError('repo-anchored path must include a repository-relative suffix')
        resolved = (resolved_repo_root / Path(relative)).resolve()
        try:
            resolved.relative_to(resolved_repo_root)
        except ValueError as exc:
            raise ValueError(f'repo-anchored path must stay inside the repository: {text}') from exc
        return resolved
    if is_extension_anchored_path(text):
        extension_root = resolve_extension_root(base_dir if start_path is None else start_path)
        relative = text[len(EXTENSION_ANCHORED_PATH_PREFIX):].strip().replace('\\', '/').lstrip('/')
        if not relative:
            raise ValueError('extension-anchored path must include an extension-relative suffix')
        resolved = (extension_root / Path(relative)).resolve()
        if not _path_is_relative_to(resolved, extension_root):
            raise ValueError(f'extension-anchored path must stay inside the extension root: {text}')
        return resolved
    return (base_dir / text).resolve()
