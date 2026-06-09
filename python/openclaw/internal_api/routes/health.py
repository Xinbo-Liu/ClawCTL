#!/usr/bin/env python3
"""健康检查路由。"""
from __future__ import annotations

import os
import threading
import time
from copy import deepcopy
from typing import Any, Callable

from openclaw.control_plane.api import render_control_plane_summary
from openclaw.control_plane.extensions.api import extension_ready_checks, import_extension_callable
from openclaw.lib.io.json_access import json_object

_READY_CACHE_LOCK = threading.Lock()
_READY_CACHE: dict[str, Any] = {"expires_at": 0.0, "payload": None}
_READY_REFRESH_LOCK = threading.Lock()
_READY_CHECK_INFLIGHT_LOCK = threading.Lock()
_READY_CHECK_INFLIGHT_RESERVED = object()
_READY_CHECK_INFLIGHT: dict[str, threading.Thread | object] = {}


def _float_env(name: str, default: float, *, minimum: float) -> float:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(minimum, value)


def _ready_cache_ttl_seconds() -> float:
    return _float_env("OPENCLAW_READY_CACHE_TTL_SECONDS", 3.0, minimum=0.0)


def _ready_check_timeout_seconds() -> float:
    return _float_env("OPENCLAW_READY_CHECK_TIMEOUT_SECONDS", 3.0, minimum=0.1)


def reset_ready_cache() -> None:
    with _READY_CACHE_LOCK:
        _READY_CACHE["expires_at"] = 0.0
        _READY_CACHE["payload"] = None
    with _READY_CHECK_INFLIGHT_LOCK:
        _READY_CHECK_INFLIGHT.clear()


def _cached_ready_payload(now: float) -> dict[str, Any] | None:
    with _READY_CACHE_LOCK:
        expires_at = float(_READY_CACHE.get("expires_at") or 0.0)
        payload = _READY_CACHE.get("payload")
        if now < expires_at and isinstance(payload, dict):
            return deepcopy(payload)
    return None


def _stale_ready_payload() -> dict[str, Any] | None:
    with _READY_CACHE_LOCK:
        payload = _READY_CACHE.get("payload")
        if isinstance(payload, dict):
            return deepcopy(payload)
    return None


def _store_ready_payload(payload: dict[str, Any], *, now: float, ttl_seconds: float) -> dict[str, Any]:
    materialized = deepcopy(payload)
    with _READY_CACHE_LOCK:
        _READY_CACHE["expires_at"] = now + ttl_seconds
        _READY_CACHE["payload"] = deepcopy(materialized)
    return materialized


class _CallState:
    def __init__(self) -> None:
        self.result: Any = None
        self.error: BaseException | None = None


def _call_with_timeout(func: Callable[[], Any], *, timeout_seconds: float, call_key: str = "") -> tuple[str, Any]:
    state = _CallState()
    normalized_key = str(call_key or '').strip()
    reserved_key = False
    if normalized_key:
        with _READY_CHECK_INFLIGHT_LOCK:
            existing = _READY_CHECK_INFLIGHT.get(normalized_key)
            if existing is _READY_CHECK_INFLIGHT_RESERVED:
                return "in_progress", None
            if isinstance(existing, threading.Thread):
                if existing.is_alive() or existing.ident is None:
                    return "in_progress", None
                _READY_CHECK_INFLIGHT.pop(normalized_key, None)
            _READY_CHECK_INFLIGHT[normalized_key] = _READY_CHECK_INFLIGHT_RESERVED
            reserved_key = True

    def _runner() -> None:
        try:
            state.result = func()
        except BaseException as exc:  # pragma: no cover - surfaced through structured payload
            state.error = exc
        finally:
            if normalized_key:
                with _READY_CHECK_INFLIGHT_LOCK:
                    if _READY_CHECK_INFLIGHT.get(normalized_key) is thread:
                        _READY_CHECK_INFLIGHT.pop(normalized_key, None)

    try:
        thread = threading.Thread(target=_runner, daemon=True)
    except BaseException:
        if reserved_key:
            with _READY_CHECK_INFLIGHT_LOCK:
                if _READY_CHECK_INFLIGHT.get(normalized_key) is _READY_CHECK_INFLIGHT_RESERVED:
                    _READY_CHECK_INFLIGHT.pop(normalized_key, None)
        raise
    if normalized_key:
        with _READY_CHECK_INFLIGHT_LOCK:
            if _READY_CHECK_INFLIGHT.get(normalized_key) is _READY_CHECK_INFLIGHT_RESERVED:
                _READY_CHECK_INFLIGHT[normalized_key] = thread
    try:
        thread.start()
    except BaseException:
        if normalized_key:
            with _READY_CHECK_INFLIGHT_LOCK:
                if _READY_CHECK_INFLIGHT.get(normalized_key) is thread:
                    _READY_CHECK_INFLIGHT.pop(normalized_key, None)
        raise
    thread.join(timeout_seconds)
    if thread.is_alive():
        return "timeout", None
    if state.error is not None:
        return "error", state.error
    return "ok", state.result


def render_health() -> dict[str, Any]:
    return {"status": "ok", "service": "openclaw-internal-api"}


def _conflict_payload(*, effective_id: str, manifest_id: str, extension_id: str, blocking: bool, reason: str) -> dict[str, Any]:
    return {
        "id": effective_id,
        "ok": False,
        "blocking": blocking,
        "error": "ready_check_conflict",
        "reason": reason,
        "manifestId": manifest_id,
        "extensionId": extension_id,
    }


def _execute_extension_ready_check(row: dict[str, Any], checks: dict[str, Any], *, timeout_seconds: float) -> tuple[str, dict[str, Any]]:
    manifest_id = str(row.get("id") or "").strip()
    extension_id = str(row.get("extensionId") or "").strip()
    blocking = bool(row.get("blocking", True))
    if not manifest_id:
        return f"invalid:{extension_id or 'extension'}", {
            "id": "",
            "ok": False,
            "blocking": blocking,
            "error": "invalid_ready_check",
            "message": "ready check id 为空",
            "extensionId": extension_id,
        }
    if manifest_id in checks:
        return f"conflict:{manifest_id}", _conflict_payload(
            effective_id=manifest_id,
            manifest_id=manifest_id,
            extension_id=extension_id,
            blocking=blocking,
            reason="manifest_ready_check_id_conflicts_with_existing_check",
        )

    def _materialize_callable() -> Any:
        check_callable = import_extension_callable(str(row.get("module") or "").strip(), str(row.get("callable") or "").strip())
        return check_callable()

    call_key = f"extension-ready:{extension_id}:{manifest_id}"
    status, value = _call_with_timeout(_materialize_callable, timeout_seconds=timeout_seconds, call_key=call_key)
    if status == "in_progress":
        return manifest_id, {
            "id": manifest_id,
            "ok": False,
            "blocking": blocking,
            "error": "previous_check_still_running",
            "timeoutSeconds": timeout_seconds,
            "extensionId": extension_id,
        }
    if status == "timeout":
        return manifest_id, {
            "id": manifest_id,
            "ok": False,
            "blocking": blocking,
            "error": "timeout",
            "timeoutSeconds": timeout_seconds,
            "extensionId": extension_id,
        }
    if status == "error":
        exc = value if isinstance(value, BaseException) else RuntimeError("unknown ready check failure")
        return manifest_id, {
            "id": manifest_id,
            "ok": False,
            "blocking": blocking,
            "error": "exception",
            "exceptionType": exc.__class__.__name__,
            "message": str(exc),
            "extensionId": extension_id,
        }
    if not isinstance(value, dict):
        return manifest_id, {
            "id": manifest_id,
            "ok": False,
            "blocking": blocking,
            "error": "invalid_payload",
            "message": "ready check callable 必须返回对象",
            "extensionId": extension_id,
        }
    payload = dict(value)
    payload.setdefault("blocking", blocking)
    payload.setdefault("extensionId", extension_id)
    effective_id = str(payload.get("id") or manifest_id).strip() or manifest_id
    payload["id"] = effective_id
    if effective_id in checks:
        return f"conflict:{effective_id}", _conflict_payload(
            effective_id=effective_id,
            manifest_id=manifest_id,
            extension_id=extension_id,
            blocking=blocking,
            reason="runtime_ready_check_id_conflicts_with_existing_check",
        )
    return effective_id, payload


def _render_ready_uncached() -> dict[str, Any]:
    control_plane = render_control_plane_summary()
    control_plane_counts = json_object(control_plane.get("counts"))
    scheduler = json_object(control_plane.get("scheduler"))
    job_count = int(control_plane_counts.get("jobs") or 0)
    extension_count = len(control_plane.get("extensions") or []) if isinstance(control_plane.get("extensions"), list) else 0
    timeout_seconds = _ready_check_timeout_seconds()
    checks: dict[str, Any] = {
        "controlPlaneRegistry": {
            "id": "controlPlaneRegistry",
            "ok": True,
            "blocking": True,
            "registryLoaded": True,
            "jobCount": job_count,
            "extensionCount": extension_count,
        },
        "schedulerHeartbeat": {
            "id": "schedulerHeartbeat",
            "ok": bool(scheduler.get("healthy")),
            "blocking": True,
            "heartbeatAgeSeconds": scheduler.get("heartbeatAgeSeconds"),
        },
    }
    for row in extension_ready_checks():
        if not isinstance(row, dict):
            continue
        check_key, payload = _execute_extension_ready_check(row, checks, timeout_seconds=timeout_seconds)
        checks[check_key] = payload
    ready = all(bool(item.get("ok")) or not bool(item.get("blocking")) for item in checks.values() if isinstance(item, dict))
    return {
        "status": "ready" if ready else "degraded",
        "service": "openclaw-internal-api",
        "checks": checks,
    }


def render_ready() -> dict[str, Any]:
    now = time.time()
    cached = _cached_ready_payload(now)
    if cached is not None:
        return cached
    if not _READY_REFRESH_LOCK.acquire(blocking=False):
        stale = _stale_ready_payload()
        if stale is not None:
            stale["readyRefresh"] = {"inProgress": True}
            return stale
        return {
            "status": "degraded",
            "service": "openclaw-internal-api",
            "checks": {
                "readyRefresh": {
                    "id": "readyRefresh",
                    "ok": False,
                    "blocking": True,
                    "error": "refresh_in_progress",
                }
            },
        }
    try:
        refreshed_now = time.time()
        cached = _cached_ready_payload(refreshed_now)
        if cached is not None:
            return cached
        payload = _render_ready_uncached()
        ttl_seconds = _ready_cache_ttl_seconds()
        if ttl_seconds <= 0:
            return payload
        return _store_ready_payload(payload, now=refreshed_now, ttl_seconds=ttl_seconds)
    finally:
        _READY_REFRESH_LOCK.release()
