#!/usr/bin/env python3
"""控制平面运行态 manifest 的安全读取边界。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Mapping

from openclaw.control_plane.registry.store import read_json
from openclaw.lib.repo.layout import CONTROL_PLANE_CONFIG_ENV, resolve_repo_root
from openclaw.lib.runtime.resolver_loader import PathResolverInstance, require_path_resolver

ROOT_DIR = resolve_repo_root(Path(__file__))
ResolverFactory = Callable[[Path | None], PathResolverInstance]


def _path_is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _resolver(config_path: Path | None = None) -> PathResolverInstance:
    return require_path_resolver(repo_root=ROOT_DIR, config_path=config_path)


def _resolver_config_path(env: Mapping[str, str] | None) -> Path | None:
    value = str((env or {}).get(CONTROL_PLANE_CONFIG_ENV) or os.environ.get(CONTROL_PLANE_CONFIG_ENV) or '').strip()
    return Path(value).resolve() if value else None


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _runtime_roots(
    *,
    env: Mapping[str, str] | None = None,
    resolver_factory: ResolverFactory = _resolver,
) -> tuple[Path | None, Path | None, list[Path]]:
    env_map = dict(os.environ if env is None else env)
    scheduler_root: Path | None = None
    host_control_plane_root: Path | None = None
    roots: list[Path] = []
    try:
        resolver = resolver_factory(_resolver_config_path(env_map))
    except (OSError, RuntimeError, ValueError, KeyError):
        resolver = None
    if resolver is not None:
        try:
            scheduler_root = Path(resolver.resolve_path('state_root', 'scheduler', env=env_map)).resolve()
            roots.append(scheduler_root / 'control_plane_scheduler')
        except (OSError, RuntimeError, ValueError, KeyError):
            pass
        try:
            host_control_plane_root = Path(
                resolver.resolve_path('control_plane_host_state_dir', 'host', env=env_map)
            ).resolve()
            roots.append(host_control_plane_root / 'control_plane_scheduler')
        except (OSError, RuntimeError, ValueError, KeyError):
            pass
    explicit_state_root = str(env_map.get('OPENCLAW_STATE_DIR') or '').strip()
    if explicit_state_root:
        explicit_root = Path(explicit_state_root).resolve()
        roots.append(explicit_root / 'control_plane_scheduler')
        roots.append(explicit_root / 'control_plane' / 'control_plane_scheduler')
    return scheduler_root, host_control_plane_root, _dedupe_paths(roots)


def runtime_json_path_candidates(
    path_text: str,
    *,
    env: Mapping[str, str] | None = None,
    resolver_factory: ResolverFactory = _resolver,
) -> list[Path]:
    raw_path = str(path_text or '').strip()
    path = Path(raw_path)
    if not path.is_absolute() and not raw_path.startswith(('/', '\\')):
        return []
    try:
        resolved_path = path.resolve()
    except OSError:
        return []
    scheduler_root, host_control_plane_root, roots = _runtime_roots(env=env, resolver_factory=resolver_factory)
    candidates: list[Path] = []
    if any(_path_is_relative_to(resolved_path, root) for root in roots):
        candidates.append(resolved_path)
    if scheduler_root is not None and host_control_plane_root is not None:
        try:
            relative = resolved_path.relative_to(scheduler_root / 'control_plane_scheduler')
        except ValueError:
            relative = None
        if relative is not None:
            candidates.append((host_control_plane_root / 'control_plane_scheduler' / relative).resolve())
    return _dedupe_paths(candidates)


def read_runtime_manifest_json(
    path_value: object,
    *,
    manifest_memo: dict[str, dict[str, Any] | None] | None = None,
    env: Mapping[str, str] | None = None,
    resolver_factory: ResolverFactory = _resolver,
) -> dict[str, Any] | None:
    path_text = str(path_value or '').strip()
    if not path_text:
        return None
    if manifest_memo is not None and path_text in manifest_memo:
        return manifest_memo[path_text]
    payload: dict[str, Any] | None = None
    for candidate in runtime_json_path_candidates(path_text, env=env, resolver_factory=resolver_factory):
        payload_obj = read_json(candidate, None)
        if isinstance(payload_obj, dict):
            payload = payload_obj
            break
    if manifest_memo is not None:
        manifest_memo[path_text] = payload
    return payload
