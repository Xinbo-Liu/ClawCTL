#!/usr/bin/env python3
"""Repository-root helpers shared by repo layout surfaces."""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Iterable


REPO_ROOT_ENV_VARS = ('OPENCLAW_REPO_ROOT', 'OPENCLAW_TOOLS_ROOT')
CONTROL_PLANE_SERVICE_CONFIG_REL_PATH = 'config/control_plane/service.json'
RUNTIME_PATHS_REL_PATH = '/'.join(('config', 'runtime', 'paths.json'))
CONTROL_PLANE_CONTAINER_REPO_ROOT = PurePosixPath('/opt/openclaw-tools')
REPO_MARKERS = ('python/openclaw', RUNTIME_PATHS_REL_PATH, CONTROL_PLANE_SERVICE_CONFIG_REL_PATH)


class RepoRootResolutionError(ValueError):
    """Raised when a start path cannot be resolved back to the repository root."""


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for item in paths:
        normalized = str(item.resolve())
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(Path(normalized))
    return result


def _default_repo_root() -> Path:
    self_path = Path(__file__).resolve()
    for candidate in self_path.parents:
        if all((candidate / marker).exists() for marker in REPO_MARKERS):
            return candidate
    return self_path.parent


ROOT_DIR = _default_repo_root()


def looks_like_repo_root(candidate: Path) -> bool:
    return all((candidate / marker).exists() for marker in REPO_MARKERS)


def candidate_repo_roots(start_path: Path | None = None) -> list[Path]:
    candidates: list[Path] = []
    if start_path is not None:
        resolved = Path(start_path).resolve()
        if resolved.is_file():
            candidates.extend(resolved.parents)
        else:
            candidates.append(resolved)
            candidates.extend(resolved.parents)
    for env_name in REPO_ROOT_ENV_VARS:
        raw = str(os.environ.get(env_name) or '').strip()
        if raw:
            candidates.append(Path(raw).resolve())
    if start_path is None:
        candidates.append(ROOT_DIR.resolve())
    return _dedupe_paths(candidates)


def resolve_repo_root(start_path: Path | None = None) -> Path:
    for candidate in candidate_repo_roots(start_path):
        if looks_like_repo_root(candidate):
            return candidate
    normalized_start = ROOT_DIR.resolve() if start_path is None else Path(start_path).resolve()
    env_context = ', '.join(
        f'{name}={str(os.environ.get(name) or "").strip()}'
        for name in REPO_ROOT_ENV_VARS
        if str(os.environ.get(name) or '').strip()
    ) or '<unset>'
    raise RepoRootResolutionError(
        f'cannot resolve repo root from {normalized_start}; required markers: {", ".join(REPO_MARKERS)}; '
        f'env: {env_context}'
    )


def resolve_repo_file(relative_path: str, start_path: Path | None = None) -> Path:
    return (resolve_repo_root(start_path) / relative_path).resolve()
