#!/usr/bin/env python3
"""Filesystem collection and cleanup helpers for lifecycle operations."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.control_plane.modules.change_set import relative_to_repo
from openclaw.control_plane.modules.lifecycle_references import module_owned_logic_source_paths


def cleanup_empty_dirs(repo_root: Path, directories: set[Path]) -> list[str]:
    removed: list[str] = []
    for path in sorted({item.resolve() for item in directories}, key=lambda item: len(item.parts), reverse=True):
        if path == repo_root.resolve() or not path.exists() or not path.is_dir():
            continue
        try:
            path.rmdir()
            removed.append(relative_to_repo(repo_root, path))
        except OSError:
            continue
    return removed


def collect_drop_files(
    repo_root: Path,
    module_payload: dict[str, Any],
    *,
    registry: dict[str, Any] | None = None,
) -> tuple[list[Path], set[Path]]:
    source_path = Path(str(module_payload.get('sourcePath') or '')).resolve()
    module_dir = source_path.parent
    files: set[Path] = set()
    cleanup_dirs: set[Path] = set()

    for path in module_dir.rglob('*'):
        if path.is_file():
            files.add(path.resolve())
            cleanup_dirs.add(path.resolve().parent)
    cleanup_dirs.add(module_dir.resolve())
    _ = repo_root
    for target in module_owned_logic_source_paths(module_payload, registry=registry):
        if target.is_dir():
            for path in target.rglob('*'):
                if path.is_file():
                    files.add(path.resolve())
                    cleanup_dirs.add(path.resolve().parent)
            cleanup_dirs.add(target.resolve())
            cleanup_dirs.add(target.resolve().parent)
            continue
        if target.exists() and target.is_file():
            files.add(target.resolve())
            cleanup_dirs.add(target.resolve().parent)

    return sorted(files), cleanup_dirs
