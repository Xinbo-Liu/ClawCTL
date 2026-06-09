#!/usr/bin/env python3
"""控制平面只读视图。"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from openclaw.control_plane.api.summary_builders import (
    _render_agent_access_log_summary_uncached,
    _render_agent_group_acceptance_bindings_summary_uncached,
    _render_agent_group_access_summary_uncached,
    _render_agent_group_release_gates_summary_uncached,
    _render_agent_groups_summary_uncached,
    _render_agent_modules_summary_uncached,
    _render_control_plane_summary_uncached,
    _render_job_detail_uncached,
    _render_jobs_summary_uncached,
    _render_permission_policies_summary_uncached,
    _render_run_ledger_summary_uncached,
    _render_runtime_adapters_summary_uncached,
    _render_skill_sets_summary_uncached,
    _render_toolsets_summary_uncached,
)
from openclaw.control_plane.registry import control_plane_config_path, load_registry
from openclaw.control_plane.registry.store import runtime_files
from openclaw.control_plane.state_paths import resolve_control_plane_state_root

_REGISTRY_CACHE_TTL_SECONDS = 1.0
_SUMMARY_CACHE_TTL_SECONDS = 2.0
_JOBS_CACHE_TTL_SECONDS = 2.0
_RUN_LEDGER_CACHE_TTL_SECONDS = 5.0
_JOB_DETAIL_CACHE_TTL_SECONDS = 1.0
_ROUTE_CACHE_MAX_ENTRIES = 32


@dataclass(frozen=True)
class _TimedCacheEntry:
    expires_at: float
    dependency_token: tuple[Any, ...]
    payload: dict[str, Any]


_REGISTRY_CACHE_LOCK = threading.RLock()
_REGISTRY_CACHE: _TimedCacheEntry | None = None
_ROUTE_CACHE_LOCK = threading.RLock()
_ROUTE_CACHE: OrderedDict[tuple[Any, ...], _TimedCacheEntry] = OrderedDict()


def _prune_route_cache() -> None:
    while len(_ROUTE_CACHE) > _ROUTE_CACHE_MAX_ENTRIES:
        _ROUTE_CACHE.popitem(last=False)


def _monotonic() -> float:
    return time.monotonic()


def _state_root() -> Path:
    return resolve_control_plane_state_root()


def _path_signature(path: Path) -> tuple[bool, int | None, int | None]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return False, None, None
    return True, int(stat.st_mtime_ns), int(stat.st_size)


def _registry_dependency_token() -> tuple[Any, ...]:
    config_path = control_plane_config_path()
    return 'registry', str(config_path), *_path_signature(config_path)


def _safe_registry() -> dict[str, Any]:
    global _REGISTRY_CACHE
    dependency_token = _registry_dependency_token()
    now = _monotonic()
    with _REGISTRY_CACHE_LOCK:
        entry = _REGISTRY_CACHE
        if entry is not None and entry.expires_at > now and entry.dependency_token == dependency_token:
            return entry.payload
    payload = load_registry(control_plane_config_path())
    cached = _TimedCacheEntry(
        expires_at=now + _REGISTRY_CACHE_TTL_SECONDS,
        dependency_token=dependency_token,
        payload=payload,
    )
    with _REGISTRY_CACHE_LOCK:
        _REGISTRY_CACHE = cached
    return payload


def _state_dependency_token(registry: dict[str, Any]) -> tuple[Any, ...]:
    files = runtime_files(_state_root(), registry)
    return (
        'state',
        *_registry_dependency_token(),
        str(files.state_dir / 'state.json'),
        *_path_signature(files.state_dir / 'state.json'),
        str(files.status_path),
        *_path_signature(files.status_path),
        str(files.heartbeat_path),
        *_path_signature(files.heartbeat_path),
        str(files.history_path),
        *_path_signature(files.history_path),
        str(files.agent_access_log_path),
        *_path_signature(files.agent_access_log_path),
    )


def _response_cache_get_or_build(
    *,
    cache_key: tuple[Any, ...],
    dependency_token: tuple[Any, ...],
    ttl_seconds: float,
    builder: Callable[[], dict[str, Any]],
    should_cache: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    now = _monotonic()
    with _ROUTE_CACHE_LOCK:
        entry = _ROUTE_CACHE.get(cache_key)
        if entry is not None and entry.expires_at > now and entry.dependency_token == dependency_token:
            _ROUTE_CACHE.move_to_end(cache_key)
            return entry.payload
    payload = builder()
    if should_cache is not None and not should_cache(payload):
        return payload
    with _ROUTE_CACHE_LOCK:
        _ROUTE_CACHE.pop(cache_key, None)
        _ROUTE_CACHE[cache_key] = _TimedCacheEntry(
            expires_at=now + ttl_seconds,
            dependency_token=dependency_token,
            payload=payload,
        )
        _prune_route_cache()
    return payload


def render_control_plane_summary() -> dict[str, Any]:
    registry = _safe_registry()
    dependency_token = ('control_plane_summary', *_state_dependency_token(registry))
    return _response_cache_get_or_build(
        cache_key=('control_plane_summary',),
        dependency_token=dependency_token,
        ttl_seconds=_SUMMARY_CACHE_TTL_SECONDS,
        builder=lambda: _render_control_plane_summary_uncached(registry),
    )


def render_jobs_summary() -> dict[str, Any]:
    registry = _safe_registry()
    dependency_token = ('control_plane_jobs', *_state_dependency_token(registry))
    return _response_cache_get_or_build(
        cache_key=('control_plane_jobs',),
        dependency_token=dependency_token,
        ttl_seconds=_JOBS_CACHE_TTL_SECONDS,
        builder=lambda: _render_jobs_summary_uncached(registry),
    )


def render_run_ledger_summary() -> dict[str, Any]:
    registry = _safe_registry()
    dependency_token = ('control_plane_run_ledger', *_state_dependency_token(registry))
    return _response_cache_get_or_build(
        cache_key=('control_plane_run_ledger',),
        dependency_token=dependency_token,
        ttl_seconds=_RUN_LEDGER_CACHE_TTL_SECONDS,
        builder=lambda: _render_run_ledger_summary_uncached(registry),
    )


def render_job_detail(job_id: str) -> dict[str, Any]:
    registry = _safe_registry()
    normalized_job_id = str(job_id or '')
    dependency_token = ('control_plane_job_detail', normalized_job_id, *_state_dependency_token(registry))
    return _response_cache_get_or_build(
        cache_key=('control_plane_job_detail', normalized_job_id),
        dependency_token=dependency_token,
        ttl_seconds=_JOB_DETAIL_CACHE_TTL_SECONDS,
        builder=lambda: _render_job_detail_uncached(registry, normalized_job_id),
        should_cache=lambda payload: str(payload.get('error') or '') != 'job_not_found',
    )


def render_agents_summary() -> dict[str, Any]:
    registry = _safe_registry()
    return {'items': registry.get('agents', [])}


def render_agent_groups_summary() -> dict[str, Any]:
    registry = _safe_registry()
    dependency_token = ('control_plane_agent_groups', *_state_dependency_token(registry))
    return _response_cache_get_or_build(
        cache_key=('control_plane_agent_groups',),
        dependency_token=dependency_token,
        ttl_seconds=_JOBS_CACHE_TTL_SECONDS,
        builder=lambda: _render_agent_groups_summary_uncached(registry),
    )


def render_agent_group_acceptance_bindings_summary(*, group_ref: str = '') -> dict[str, Any]:
    registry = _safe_registry()
    dependency_token = ('control_plane_agent_group_acceptance_bindings', str(group_ref or ''), *_state_dependency_token(registry))
    return _response_cache_get_or_build(
        cache_key=('control_plane_agent_group_acceptance_bindings', str(group_ref or '')),
        dependency_token=dependency_token,
        ttl_seconds=_SUMMARY_CACHE_TTL_SECONDS,
        builder=lambda: _render_agent_group_acceptance_bindings_summary_uncached(registry, group_ref=group_ref),
    )


def render_agent_group_release_gates_summary(*, group_ref: str = '') -> dict[str, Any]:
    registry = _safe_registry()
    normalized_group_ref = str(group_ref or '')
    dependency_token = ('control_plane_agent_group_release_gates', normalized_group_ref, *_state_dependency_token(registry))
    return _response_cache_get_or_build(
        cache_key=('control_plane_agent_group_release_gates', normalized_group_ref),
        dependency_token=dependency_token,
        ttl_seconds=_JOBS_CACHE_TTL_SECONDS,
        builder=lambda: _render_agent_group_release_gates_summary_uncached(registry, group_ref=normalized_group_ref),
    )


def render_agent_modules_summary() -> dict[str, Any]:
    registry = _safe_registry()
    dependency_token = ('control_plane_agent_modules', *_registry_dependency_token())
    return _response_cache_get_or_build(
        cache_key=('control_plane_agent_modules',),
        dependency_token=dependency_token,
        ttl_seconds=_REGISTRY_CACHE_TTL_SECONDS,
        builder=lambda: _render_agent_modules_summary_uncached(registry),
    )


def render_skill_sets_summary() -> dict[str, Any]:
    registry = _safe_registry()
    dependency_token = ('control_plane_skill_sets', *_registry_dependency_token())
    return _response_cache_get_or_build(
        cache_key=('control_plane_skill_sets',),
        dependency_token=dependency_token,
        ttl_seconds=_REGISTRY_CACHE_TTL_SECONDS,
        builder=lambda: _render_skill_sets_summary_uncached(registry),
    )


def render_permission_policies_summary() -> dict[str, Any]:
    registry = _safe_registry()
    dependency_token = ('control_plane_permission_policies', *_registry_dependency_token())
    return _response_cache_get_or_build(
        cache_key=('control_plane_permission_policies',),
        dependency_token=dependency_token,
        ttl_seconds=_REGISTRY_CACHE_TTL_SECONDS,
        builder=lambda: _render_permission_policies_summary_uncached(registry),
    )


def render_toolsets_summary() -> dict[str, Any]:
    registry = _safe_registry()
    dependency_token = ('control_plane_toolsets', *_registry_dependency_token())
    return _response_cache_get_or_build(
        cache_key=('control_plane_toolsets',),
        dependency_token=dependency_token,
        ttl_seconds=_REGISTRY_CACHE_TTL_SECONDS,
        builder=lambda: _render_toolsets_summary_uncached(registry),
    )


def render_runtime_adapters_summary() -> dict[str, Any]:
    registry = _safe_registry()
    dependency_token = ('control_plane_runtime_adapters', *_registry_dependency_token())
    return _response_cache_get_or_build(
        cache_key=('control_plane_runtime_adapters',),
        dependency_token=dependency_token,
        ttl_seconds=_REGISTRY_CACHE_TTL_SECONDS,
        builder=lambda: _render_runtime_adapters_summary_uncached(registry),
    )


def render_implementations_summary() -> dict[str, Any]:
    registry = _safe_registry()
    return {'items': registry.get('implementations', [])}


def render_models_summary() -> dict[str, Any]:
    registry = _safe_registry()
    return {'items': registry.get('models', [])}


def render_targets_summary() -> dict[str, Any]:
    registry = _safe_registry()
    return {'items': registry.get('targets', [])}


def render_agent_group_access_summary(*, limit: int = 200, timeline_limit: int = 20, group_ref: str = '', status: str = '', source: str = '') -> dict[str, Any]:
    registry = _safe_registry()
    normalized_limit = max(0, int(limit))
    normalized_timeline_limit = max(0, int(timeline_limit))
    normalized_group_ref = str(group_ref or '').strip()
    normalized_status = str(status or '').strip()
    normalized_source = str(source or '').strip()
    dependency_token = ('control_plane_agent_group_access', normalized_limit, normalized_timeline_limit, normalized_group_ref, normalized_status, normalized_source, *_state_dependency_token(registry))
    return _response_cache_get_or_build(
        cache_key=('control_plane_agent_group_access', normalized_limit, normalized_timeline_limit, normalized_group_ref, normalized_status, normalized_source),
        dependency_token=dependency_token,
        ttl_seconds=_JOBS_CACHE_TTL_SECONDS,
        builder=lambda: _render_agent_group_access_summary_uncached(
            registry,
            limit=normalized_limit,
            timeline_limit=normalized_timeline_limit,
            group_ref=normalized_group_ref,
            status=normalized_status,
            source=normalized_source,
        ),
    )


def render_agent_access_log_summary(*, limit: int = 50, agent_ref: str = '', group_ref: str = '', job_id: str = '', status: str = '', source: str = '') -> dict[str, Any]:
    registry = _safe_registry()
    normalized_limit = max(0, int(limit))
    normalized_agent_ref = str(agent_ref or '').strip()
    normalized_group_ref = str(group_ref or '').strip()
    normalized_job_id = str(job_id or '').strip()
    normalized_status = str(status or '').strip()
    normalized_source = str(source or '').strip()
    dependency_token = ('control_plane_agent_access_log', normalized_limit, normalized_agent_ref, normalized_group_ref, normalized_job_id, normalized_status, normalized_source, *_state_dependency_token(registry))
    return _response_cache_get_or_build(
        cache_key=('control_plane_agent_access_log', normalized_limit, normalized_agent_ref, normalized_group_ref, normalized_job_id, normalized_status, normalized_source),
        dependency_token=dependency_token,
        ttl_seconds=_JOBS_CACHE_TTL_SECONDS,
        builder=lambda: _render_agent_access_log_summary_uncached(
            registry,
            limit=normalized_limit,
            agent_ref=normalized_agent_ref,
            group_ref=normalized_group_ref,
            job_id=normalized_job_id,
            status=normalized_status,
            source=normalized_source,
        ),
    )
