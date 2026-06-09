#!/usr/bin/env python3
"""运行路径解析与安全路径约束。"""
from __future__ import annotations

import os
from pathlib import Path

from openclaw.lib.cli.common import fail
from openclaw.lib.runtime.resolver_loader import require_path_resolver
from openclaw.runtime.path_view import detect_runtime_path_view


RUNTIME_PATH_VIEW = detect_runtime_path_view()


def resolve_runtime_path(
    entry_id: str,
    view: str = RUNTIME_PATH_VIEW,
    env_name: str | None = None,
    start_path: Path | None = None,
) -> str:
    if env_name and os.environ.get(env_name):
        return os.environ[env_name]
    resolver = require_path_resolver(start_path)
    try:
        return resolver.resolve_path(entry_id, view, env=dict(os.environ))
    except KeyError:
        fail(f"无法解析运行路径：{entry_id}", 3)


def is_path_inside(base: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(base)
        return True
    except ValueError:
        return False


def _nearest_existing(path: Path) -> tuple[Path, Path]:
    current = path
    while True:
        try:
            current.lstat()
            return current, current.resolve()
        except (FileNotFoundError, NotADirectoryError):
            if current.parent == current:
                raise
            current = current.parent


def _project_realpath(path: Path) -> Path:
    existing, real = _nearest_existing(path)
    rel_suffix = path.relative_to(existing)
    return real / rel_suffix


def safe_resolve_under(base_dir: str | Path, rel_or_abs: str | Path, *, label: str = "openclaw") -> Path:
    base = Path(base_dir).resolve()
    raw = "" if rel_or_abs is None else str(rel_or_abs)
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    candidate = candidate.resolve(strict=False)
    if not is_path_inside(base, candidate):
        fail(f"[{label}] 非法路径（不在允许目录下）：{candidate}", 2)
    projected_base = _project_realpath(base)
    projected_candidate = _project_realpath(candidate)
    if not is_path_inside(projected_base, projected_candidate):
        fail(f"[{label}] 非法路径（真实路径越界）：{projected_candidate}", 2)
    return candidate
