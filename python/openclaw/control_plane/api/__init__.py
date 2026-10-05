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
    """保存只读 API 缓存 payload 及其依赖 token。"""
    expires_at: float
    dependency_token: tuple[Any, ...]
    payload: dict[str, Any]


_REGISTRY_CACHE_LOCK = threading.RLock()
_REGISTRY_CACHE: _TimedCacheEntry | None = None
_ROUTE_CACHE_LOCK = threading.RLock()
_ROUTE_CACHE: OrderedDict[tuple[Any, ...], _TimedCacheEntry] = OrderedDict()


def _prune_route_cache() -> None:
    """按 LRU 顺序裁剪只读路由缓存。"""
    while len(_ROUTE_CACHE) > _ROUTE_CACHE_MAX_ENTRIES:
        _ROUTE_CACHE.popitem(last=False)


def _monotonic() -> float:
    """读取单调时钟，供短 TTL 缓存计算过期时间。

    返回：
        返回 float，单位为秒。
    """
    return time.monotonic()


def _state_root() -> Path:
    """解析 control-plane 运行态状态根目录。

    返回：
        返回 Path，指向 scheduler state、heartbeat、history 等状态文件所在根目录。
    """
    return resolve_control_plane_state_root()


def _path_signature(path: Path) -> tuple[bool, int | None, int | None]:
    """计算文件存在性、mtime 与 size 组成的依赖签名。

    参数：
        path（Path）：要参与缓存失效判断的文件路径。

    返回：
        返回 tuple[bool, int | None, int | None]，依次表示是否存在、mtime_ns 和 size。
    """
    try:
        stat = path.stat()
    except FileNotFoundError:
        return False, None, None
    return True, int(stat.st_mtime_ns), int(stat.st_size)


def _registry_dependency_token() -> tuple[Any, ...]:
    """构建 registry 缓存依赖 token。

    返回：
        返回 tuple[Any, ...]，包含当前 control-plane config 路径及其文件签名。
    """
    config_path = control_plane_config_path()
    return 'registry', str(config_path), *_path_signature(config_path)


def _safe_registry() -> dict[str, Any]:
    """读取 control-plane registry，并用短 TTL 降低高频只读接口开销。

    返回：
        返回 dict[str, Any]，表示当前 active control-plane registry。
    """
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
    """构建 runtime state 相关只读接口的缓存依赖 token。

    参数：
        registry（dict[str, Any]）：当前 active control-plane registry。

    返回：
        返回 tuple[Any, ...]，包含 registry token 以及 state/status/heartbeat/history/access-log 文件签名。
    """
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
    """读取或构建只读 API 响应缓存。

    参数：
        cache_key（tuple[Any, ...]）：缓存条目键。
        dependency_token（tuple[Any, ...]）：依赖签名；变化时缓存失效。
        ttl_seconds（float）：缓存有效秒数。
        builder（Callable[[], dict[str, Any]]）：缓存未命中时构建 payload 的回调。
        should_cache（Callable[[dict[str, Any]], bool] | None）：可选缓存准入回调，返回 False 时直接返回 payload。

    返回：
        返回 dict[str, Any]，表示缓存命中或 freshly built 的响应 payload。
    """
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
    """渲染 control-plane 聚合摘要。

    返回：
        返回 dict[str, Any]，包含 scheduler、registry、job、group 和 release gate 的整体状态。
    """
    registry = _safe_registry()
    dependency_token = ('control_plane_summary', *_state_dependency_token(registry))
    return _response_cache_get_or_build(
        cache_key=('control_plane_summary',),
        dependency_token=dependency_token,
        ttl_seconds=_SUMMARY_CACHE_TTL_SECONDS,
        builder=lambda: _render_control_plane_summary_uncached(registry),
    )


def render_jobs_summary() -> dict[str, Any]:
    """渲染 job 集合摘要。

    返回：
        返回 dict[str, Any]，包含每个 job 的运行状态、artifact 状态和最新 run 信息。
    """
    registry = _safe_registry()
    dependency_token = ('control_plane_jobs', *_state_dependency_token(registry))
    return _response_cache_get_or_build(
        cache_key=('control_plane_jobs',),
        dependency_token=dependency_token,
        ttl_seconds=_JOBS_CACHE_TTL_SECONDS,
        builder=lambda: _render_jobs_summary_uncached(registry),
    )


def render_run_ledger_summary() -> dict[str, Any]:
    """渲染 run ledger 摘要。

    返回：
        返回 dict[str, Any]，包含 job 执行、artifact 与最新访问覆盖后的有效状态。
    """
    registry = _safe_registry()
    dependency_token = ('control_plane_run_ledger', *_state_dependency_token(registry))
    return _response_cache_get_or_build(
        cache_key=('control_plane_run_ledger',),
        dependency_token=dependency_token,
        ttl_seconds=_RUN_LEDGER_CACHE_TTL_SECONDS,
        builder=lambda: _render_run_ledger_summary_uncached(registry),
    )


def render_job_detail(job_id: str) -> dict[str, Any]:
    """渲染单个 job 的详情。

    参数：
        job_id（str）：control-plane job 标识。

    返回：
        返回 dict[str, Any]，命中时为 job 详情；未命中时为错误 payload。
    """
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
    """返回 registry 中的 agent 列表。

    返回：
        返回 dict[str, Any]，包含 `items` 列表。
    """
    registry = _safe_registry()
    return {'items': registry.get('agents', [])}


def render_agent_groups_summary() -> dict[str, Any]:
    """渲染 agent group 摘要。

    返回：
        返回 dict[str, Any]，包含 group 成员、健康状态、访问记录和发布门禁摘要。
    """
    registry = _safe_registry()
    dependency_token = ('control_plane_agent_groups', *_state_dependency_token(registry))
    return _response_cache_get_or_build(
        cache_key=('control_plane_agent_groups',),
        dependency_token=dependency_token,
        ttl_seconds=_JOBS_CACHE_TTL_SECONDS,
        builder=lambda: _render_agent_groups_summary_uncached(registry),
    )


def render_agent_group_release_gates_summary(*, group_ref: str = '') -> dict[str, Any]:
    """渲染 agent group 发布门禁摘要。

    参数：
        group_ref（str）：按 group 标识过滤；空字符串表示返回全部 group。

    返回：
        返回 dict[str, Any]，包含门禁状态、失败检查、缺失证据和 rollback 建议。
    """
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
    """渲染 agent module 摘要。

    返回：
        返回 dict[str, Any]，包含 registry 当前 agent modules 列表。
    """
    registry = _safe_registry()
    dependency_token = ('control_plane_agent_modules', *_registry_dependency_token())
    return _response_cache_get_or_build(
        cache_key=('control_plane_agent_modules',),
        dependency_token=dependency_token,
        ttl_seconds=_REGISTRY_CACHE_TTL_SECONDS,
        builder=lambda: _render_agent_modules_summary_uncached(registry),
    )


def render_skill_sets_summary() -> dict[str, Any]:
    """渲染 skill set 摘要。

    返回：
        返回 dict[str, Any]，包含 registry 当前 skill sets 列表。
    """
    registry = _safe_registry()
    dependency_token = ('control_plane_skill_sets', *_registry_dependency_token())
    return _response_cache_get_or_build(
        cache_key=('control_plane_skill_sets',),
        dependency_token=dependency_token,
        ttl_seconds=_REGISTRY_CACHE_TTL_SECONDS,
        builder=lambda: _render_skill_sets_summary_uncached(registry),
    )


def render_permission_policies_summary() -> dict[str, Any]:
    """渲染 permission policy 摘要。

    返回：
        返回 dict[str, Any]，包含 registry 当前 permission policies 列表。
    """
    registry = _safe_registry()
    dependency_token = ('control_plane_permission_policies', *_registry_dependency_token())
    return _response_cache_get_or_build(
        cache_key=('control_plane_permission_policies',),
        dependency_token=dependency_token,
        ttl_seconds=_REGISTRY_CACHE_TTL_SECONDS,
        builder=lambda: _render_permission_policies_summary_uncached(registry),
    )


def render_toolsets_summary() -> dict[str, Any]:
    """渲染 toolset 摘要。

    返回：
        返回 dict[str, Any]，包含 registry 当前 toolsets 列表。
    """
    registry = _safe_registry()
    dependency_token = ('control_plane_toolsets', *_registry_dependency_token())
    return _response_cache_get_or_build(
        cache_key=('control_plane_toolsets',),
        dependency_token=dependency_token,
        ttl_seconds=_REGISTRY_CACHE_TTL_SECONDS,
        builder=lambda: _render_toolsets_summary_uncached(registry),
    )


def render_runtime_adapters_summary() -> dict[str, Any]:
    """渲染 runtime adapter 摘要。

    返回：
        返回 dict[str, Any]，包含 registry 当前 runtime adapters 列表。
    """
    registry = _safe_registry()
    dependency_token = ('control_plane_runtime_adapters', *_registry_dependency_token())
    return _response_cache_get_or_build(
        cache_key=('control_plane_runtime_adapters',),
        dependency_token=dependency_token,
        ttl_seconds=_REGISTRY_CACHE_TTL_SECONDS,
        builder=lambda: _render_runtime_adapters_summary_uncached(registry),
    )


def render_implementations_summary() -> dict[str, Any]:
    """返回 registry 中的 implementation 列表。

    返回：
        返回 dict[str, Any]，包含 `items` 列表。
    """
    registry = _safe_registry()
    return {'items': registry.get('implementations', [])}


def render_models_summary() -> dict[str, Any]:
    """返回 registry 中的模型声明列表。

    返回：
        返回 dict[str, Any]，包含 `items` 列表。
    """
    registry = _safe_registry()
    return {'items': registry.get('models', [])}


def render_targets_summary() -> dict[str, Any]:
    """返回 registry 中的 target 声明列表。

    返回：
        返回 dict[str, Any]，包含 `items` 列表。
    """
    registry = _safe_registry()
    return {'items': registry.get('targets', [])}


def render_agent_group_access_summary(*, limit: int = 200, timeline_limit: int = 20, group_ref: str = '', status: str = '', source: str = '') -> dict[str, Any]:
    """渲染 agent group 维度的访问聚合。

    参数：
        limit（int）：最多返回的 group 聚合条数。
        timeline_limit（int）：每个 group 最多保留的时间线记录数。
        group_ref（str）：按 group 标识过滤；空字符串表示不过滤。
        status（str）：按访问状态过滤；空字符串表示不过滤。
        source（str）：按访问来源过滤；空字符串表示不过滤。

    返回：
        返回 dict[str, Any]，包含 group 访问聚合、时间线和来源路径。
    """
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
    """渲染 agent access log 记录。

    参数：
        limit（int）：最多返回的访问记录数。
        agent_ref（str）：按 agent 标识过滤；空字符串表示不过滤。
        group_ref（str）：按 group 标识过滤；空字符串表示不过滤。
        job_id（str）：按 job 标识过滤；空字符串表示不过滤。
        status（str）：按访问状态过滤；空字符串表示不过滤。
        source（str）：按访问来源过滤；空字符串表示不过滤。

    返回：
        返回 dict[str, Any]，包含匹配访问记录、计数和来源路径。
    """
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
