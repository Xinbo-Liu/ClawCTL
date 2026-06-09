#!/usr/bin/env python3
"""Shared bootstrap helpers for repo and extension tests."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable

from openclaw.control_plane.modules.scaffold_support import managed_extension_profile_rel_path
from openclaw.lib.repo.managed_extensions import managed_explicit_extensions
from openclaw.lib.repo.bootstrap import bootstrap_sys_path, prepend_python_roots
from openclaw.lib.repo.layout import resolve_repo_root

sys.dont_write_bytecode = True


def prepend_sys_path_entries(entries: Iterable[Path]) -> tuple[Path, ...]:
    return prepend_python_roots(entries)


def ensure_repo_pythonpath(start_path: Path) -> Path:
    repo_root = resolve_repo_root(start_path)
    bootstrap_sys_path(start_path)
    return repo_root


def find_parent_with_markers(start_path: Path, markers: Iterable[str]) -> Path:
    resolved = Path(start_path).resolve()
    candidates = [resolved.parent] if resolved.is_file() else [resolved]
    candidates.extend(resolved.parents)
    required = tuple(str(item) for item in markers)
    for candidate in candidates:
        if all((candidate / marker).exists() for marker in required):
            return candidate
    joined = ', '.join(required) or '<none>'
    raise ValueError(f'cannot find parent for {resolved} with markers: {joined}')


def extension_test_context(start_path: Path, service_config_rel_path: str) -> tuple[Path, Path]:
    package_root = find_parent_with_markers(start_path, (service_config_rel_path, 'python'))
    prepend_sys_path_entries([package_root / 'python'])
    return package_root, (package_root / service_config_rel_path).resolve()


def prepend_managed_extension_python_roots(start_path: Path, extension_id: str) -> tuple[Path, ...]:
    repo_root = resolve_repo_root(start_path)
    normalized_extension_id = str(extension_id or '').strip()
    for row in managed_explicit_extensions(repo_root):
        if row.id == normalized_extension_id:
            return prepend_sys_path_entries(row.python_roots)
    raise ValueError(f'cannot resolve managed extension python roots for {normalized_extension_id or "<empty>"} under {repo_root}')


def managed_extension_test_context(start_path: Path, extension_id: str) -> tuple[Path, Path]:
    return extension_test_context(start_path, managed_extension_profile_rel_path(extension_id))
