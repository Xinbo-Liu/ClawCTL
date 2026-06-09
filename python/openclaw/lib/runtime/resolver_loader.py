#!/usr/bin/env python3
"""规范运行态路径解析器的共享加载器。"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Protocol

from openclaw.lib.repo.layout import resolve_default_runtime_control_plane_service_config_path, resolve_repo_root
from openclaw.lib.runtime.path_resolver import PathResolver


class PathResolverInstance(Protocol):
    internal_views: tuple[str, ...]
    roots: dict[str, str]
    entries: dict[str, dict[str, Any]]

    def resolve_entry(self, entry_id: str) -> dict[str, Any]:
        ...

    def resolve_path(self, entry_id: str, view: str = 'gateway', env: dict[str, str] | None = None) -> str:
        ...

    def normalize_view(self, view: str) -> str:
        ...

    def absolute_host_path(self, entry_id: str) -> Path:
        ...


class PathResolverFactory(Protocol):
    @staticmethod
    def from_repo_root(repo_root: Path, *, config_path: Path | None = None) -> PathResolverInstance:
        ...


_RESOLVER_CACHE_LOCK = threading.Lock()
_RESOLVER_CACHE: dict[tuple[str, str, int | None, int | None], PathResolverInstance] = {}


def _file_cache_identity(path: Path) -> tuple[int | None, int | None]:
    try:
        stat = path.stat()
    except OSError:
        return None, None
    return int(stat.st_mtime_ns), int(stat.st_size)


def clear_path_resolver_cache() -> None:
    with _RESOLVER_CACHE_LOCK:
        _RESOLVER_CACHE.clear()


def load_path_resolver_class(start_path: Path | None = None) -> PathResolverFactory:
    del start_path
    return PathResolver


def build_path_resolver(
    start_path: Path | None = None,
    *,
    repo_root: Path | None = None,
    config_path: Path | None = None,
) -> PathResolverInstance:
    base = Path(repo_root).resolve() if repo_root is not None else resolve_repo_root(start_path)
    factory = load_path_resolver_class(base if repo_root is not None else start_path)
    resolved_config_path = (
        Path(config_path).resolve()
        if config_path is not None
        else resolve_default_runtime_control_plane_service_config_path(base)
    )
    mtime_ns, size = _file_cache_identity(resolved_config_path)
    cache_key = (str(base), str(resolved_config_path), mtime_ns, size)
    with _RESOLVER_CACHE_LOCK:
        cached = _RESOLVER_CACHE.get(cache_key)
    if cached is not None:
        return cached
    resolver = factory.from_repo_root(base, config_path=resolved_config_path)
    with _RESOLVER_CACHE_LOCK:
        existing = _RESOLVER_CACHE.get(cache_key)
        if existing is not None:
            return existing
        _RESOLVER_CACHE[cache_key] = resolver
    return resolver


def require_path_resolver(
    start_path: Path | None = None,
    *,
    repo_root: Path | None = None,
    config_path: Path | None = None,
) -> PathResolverInstance:
    return build_path_resolver(start_path, repo_root=repo_root, config_path=config_path)
