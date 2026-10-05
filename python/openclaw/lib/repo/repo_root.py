#!/usr/bin/env python3
"""提供OpenClaw lib子系统的生产实现。"""
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
    """仓库根目录解析失败。"""


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


def relative_path_within_root(path: Path, root_dir: Path) -> Path:
    """返回可跨文件系统路径别名工作的根目录相对路径。

    参数：
        path（Path）：应位于根目录内的文件或目录；目标末端允许尚未创建。
        root_dir（Path）：已经存在的边界根目录；Windows 上可使用 8.3 短路径或长路径。

    返回：
        Path：从 ``root_dir`` 到 ``path`` 的相对路径。

    异常：
        ValueError：当目标越过根目录，或无法通过文件身份确认路径别名属于同一根目录时抛出。
    """
    candidate = Path(path)
    root = Path(root_dir)
    try:
        return candidate.relative_to(root)
    except ValueError as lexical_error:
        suffix: list[str] = []
        current = candidate
        while True:
            try:
                if current.samefile(root):
                    return Path(*reversed(suffix))
            except OSError:
                pass
            parent = current.parent
            if parent == current:
                raise lexical_error
            suffix.append(current.name)
            current = parent
