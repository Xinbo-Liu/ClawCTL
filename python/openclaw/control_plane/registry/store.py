#!/usr/bin/env python3
"""控制平面 registry 的文件存取与运行态路径辅助。"""
from __future__ import annotations

import json
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openclaw.lib.io.state import append_jsonl as write_jsonl_row
from openclaw.lib.io.state import write_text_atomic
from openclaw.lib.io.json_access import json_object


@dataclass(frozen=True)
class RuntimeFiles:
    """调度器运行态使用的关键文件路径集合。"""
    state_dir: Path
    status_path: Path
    heartbeat_path: Path
    history_path: Path
    agent_access_log_path: Path
    runs_dir: Path
    locks_dir: Path


@dataclass(frozen=True)
class _FileSignature:
    """文件存在性、mtime 与大小组成的缓存签名。"""
    exists: bool
    mtime_ns: int | None
    size: int | None


@dataclass(frozen=True)
class _JsonCacheEntry:
    """JSON 读取缓存条目。"""
    signature: _FileSignature
    payload: Any


@dataclass(frozen=True)
class _HistoryCacheEntry:
    """历史记录尾读缓存条目。"""
    signature: _FileSignature
    rows: tuple[dict[str, Any], ...]


_JSON_CACHE_LOCK = threading.RLock()
_JSON_CACHE_MAX_ENTRIES = 256
_JSON_CACHE: OrderedDict[str, _JsonCacheEntry] = OrderedDict()
_HISTORY_CACHE_LOCK = threading.RLock()
_HISTORY_CACHE_MAX_ENTRIES = 64
_HISTORY_CACHE: OrderedDict[tuple[str, int], _HistoryCacheEntry] = OrderedDict()
_JSON_CACHE_MISSING = object()
_JSON_CACHE_INVALID = object()


def _prune_lru_cache(cache: OrderedDict[Any, Any], max_entries: int) -> None:
    """把缓存裁剪到允许的最大条目数。"""
    while len(cache) > max_entries:
        cache.popitem(last=False)


def _path_key(path: Path) -> str:
    """为路径生成缓存键。"""
    try:
        return str(path.resolve())
    except (OSError, RuntimeError):
        return str(path)


def _file_signature(path: Path) -> _FileSignature:
    """生成文件签名，用于缓存命中判断。"""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return _FileSignature(False, None, None)
    return _FileSignature(True, int(stat.st_mtime_ns), int(stat.st_size))


def _invalidate_json_cache(path: Path) -> None:
    """失效指定路径的 JSON 缓存。"""
    key = _path_key(path)
    with _JSON_CACHE_LOCK:
        _JSON_CACHE.pop(key, None)


def _update_json_cache(path: Path, payload: Any) -> None:
    """更新指定路径的 JSON 缓存。"""
    key = _path_key(path)
    signature = _file_signature(path)
    with _JSON_CACHE_LOCK:
        _JSON_CACHE.pop(key, None)
        _JSON_CACHE[key] = _JsonCacheEntry(signature=signature, payload=payload)
        _prune_lru_cache(_JSON_CACHE, _JSON_CACHE_MAX_ENTRIES)


def _invalidate_history_cache(path: Path) -> None:
    """失效指定路径的历史尾读缓存。"""
    key = _path_key(path)
    with _HISTORY_CACHE_LOCK:
        stale_keys = [item_key for item_key in _HISTORY_CACHE if item_key[0] == key]
        for item_key in stale_keys:
            _HISTORY_CACHE.pop(item_key, None)


def read_json(path: Path, default: Any) -> Any:
    """读取 JSON，并在失败时返回默认值。"""
    key = _path_key(path)
    signature = _file_signature(path)
    with _JSON_CACHE_LOCK:
        entry = _JSON_CACHE.get(key)
        if entry is not None and entry.signature == signature:
            _JSON_CACHE.move_to_end(key)
            if entry.payload is _JSON_CACHE_MISSING or entry.payload is _JSON_CACHE_INVALID:
                return default
            return entry.payload
    if not signature.exists:
        with _JSON_CACHE_LOCK:
            _JSON_CACHE.pop(key, None)
            _JSON_CACHE[key] = _JsonCacheEntry(signature=signature, payload=_JSON_CACHE_MISSING)
            _prune_lru_cache(_JSON_CACHE, _JSON_CACHE_MAX_ENTRIES)
        return default
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        with _JSON_CACHE_LOCK:
            _JSON_CACHE.pop(key, None)
            _JSON_CACHE[key] = _JsonCacheEntry(signature=signature, payload=_JSON_CACHE_INVALID)
            _prune_lru_cache(_JSON_CACHE, _JSON_CACHE_MAX_ENTRIES)
        return default
    with _JSON_CACHE_LOCK:
        _JSON_CACHE.pop(key, None)
        _JSON_CACHE[key] = _JsonCacheEntry(signature=signature, payload=payload)
        _prune_lru_cache(_JSON_CACHE, _JSON_CACHE_MAX_ENTRIES)
    return payload


def write_json(path: Path, payload: Any) -> None:
    """原子写入 JSON，并刷新相关缓存。"""
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    write_text_atomic(path, serialized)
    _update_json_cache(path, payload)
    _invalidate_history_cache(path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    """向 JSONL 文件追加一行并刷新缓存。"""
    write_jsonl_row(path, payload)
    _invalidate_json_cache(path)
    _invalidate_history_cache(path)


def _tail_jsonl_dict_rows(path: Path, limit: int, *, block_size: int = 65536) -> list[dict[str, Any]]:
    """从 JSONL 文件尾部读取若干字典行。"""
    rows: list[dict[str, Any]] = []
    with path.open('rb') as fh:
        fh.seek(0, os.SEEK_END)
        cursor = fh.tell()
        pending = b''
        while cursor > 0 and len(rows) < limit:
            read_size = min(block_size, cursor)
            cursor -= read_size
            fh.seek(cursor)
            chunk = fh.read(read_size)
            pending = chunk + pending
            segments = pending.split(b'\n')
            pending = segments[0]
            for raw_line in reversed(segments[1:]):
                if len(rows) >= limit:
                    break
                line_bytes = raw_line.rstrip(b'\r')
                if not line_bytes:
                    continue
                try:
                    payload = json.loads(line_bytes.decode('utf-8'))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
        if len(rows) < limit:
            line_bytes = pending.rstrip(b'\r')
            if line_bytes:
                try:
                    payload = json.loads(line_bytes.decode('utf-8'))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    payload = None
                if isinstance(payload, dict):
                    rows.append(payload)
    rows.reverse()
    return rows


def tail_history(path: Path, limit: int = 20) -> list[dict[str, Any]]:
    """读取 history JSONL 的尾部记录。"""
    if limit <= 0:
        return []
    key = (_path_key(path), int(limit))
    signature = _file_signature(path)
    with _HISTORY_CACHE_LOCK:
        entry = _HISTORY_CACHE.get(key)
        if entry is not None and entry.signature == signature:
            _HISTORY_CACHE.move_to_end(key)
            return [dict(row) for row in entry.rows]
    if not signature.exists:
        with _HISTORY_CACHE_LOCK:
            _HISTORY_CACHE.pop(key, None)
            _HISTORY_CACHE[key] = _HistoryCacheEntry(signature=signature, rows=())
            _prune_lru_cache(_HISTORY_CACHE, _HISTORY_CACHE_MAX_ENTRIES)
        return []
    try:
        rows = _tail_jsonl_dict_rows(path, int(limit))
    except (OSError, UnicodeDecodeError):
        with _HISTORY_CACHE_LOCK:
            _HISTORY_CACHE.pop(key, None)
            _HISTORY_CACHE[key] = _HistoryCacheEntry(signature=signature, rows=())
            _prune_lru_cache(_HISTORY_CACHE, _HISTORY_CACHE_MAX_ENTRIES)
        return []
    cached_rows = tuple(dict(row) for row in rows)
    with _HISTORY_CACHE_LOCK:
        _HISTORY_CACHE.pop(key, None)
        _HISTORY_CACHE[key] = _HistoryCacheEntry(signature=signature, rows=cached_rows)
        _prune_lru_cache(_HISTORY_CACHE, _HISTORY_CACHE_MAX_ENTRIES)
    return [dict(row) for row in cached_rows]


def runtime_files(state_root: Path, config: dict[str, Any]) -> RuntimeFiles:
    """根据 state 根目录与 service 配置推导运行态文件路径。"""
    service = json_object(config.get('service'))
    state_dir_name = str(service.get('stateDirName') or 'control_plane_scheduler').strip()
    heartbeat_name = str(service.get('heartbeatFile') or 'control_plane_scheduler_heartbeat.json').strip()
    status_name = str(service.get('statusFile') or 'control_plane_scheduler_status.json').strip()
    history_name = str(service.get('historyFile') or 'control_plane_scheduler_history.jsonl').strip()
    agent_access_log_name = str(service.get('agentAccessLogFile') or 'control_plane_agent_access_log.jsonl').strip()
    state_dir = state_root / state_dir_name
    return RuntimeFiles(
        state_dir=state_dir,
        status_path=state_root / status_name,
        heartbeat_path=state_root / heartbeat_name,
        history_path=state_root / history_name,
        agent_access_log_path=state_root / agent_access_log_name,
        runs_dir=state_dir / 'runs',
        locks_dir=state_dir / 'locks',
    )
