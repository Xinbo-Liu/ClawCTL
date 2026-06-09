#!/usr/bin/env python3
"""internal API 只读客户端。"""
from __future__ import annotations

import json
import os
import urllib.error
from typing import Any

from openclaw.lib.http.json_client import DEFAULT_MAX_RESPONSE_BYTES, http_get_text


DEFAULT_TIMEOUT_SEC = 5.0


def internal_api_base_url() -> str:
    return str(os.environ.get("OPENCLAW_INTERNAL_API_BASE_URL", "http://openclaw-internal-api:18081")).rstrip("/")


def internal_api_token() -> str:
    return str(os.environ.get("OPENCLAW_INTERNAL_API_TOKEN", "")).strip()


def _request_headers(*, require_auth: bool) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if require_auth:
        token = internal_api_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return headers


def get_json(
    path: str,
    *,
    require_auth: bool = True,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> dict[str, Any]:
    target = f"{internal_api_base_url()}{path}"
    payload = http_get_text(
        target,
        timeout_sec=timeout_sec,
        headers=_request_headers(require_auth=require_auth),
        max_response_bytes=max_response_bytes,
    )
    data = json.loads(payload or "{}")
    if not isinstance(data, dict):
        raise RuntimeError(f"internal API 返回非对象 JSON：{path}")
    return data


def probe_internal_api(*, timeout_sec: float = DEFAULT_TIMEOUT_SEC, include_summary: bool = True) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "base_url": internal_api_base_url(),
        "configured": bool(internal_api_token()),
        "reachable": False,
        "ready": False,
        "health": None,
        "readyz": None,
        "config_summary": None,
        "error": None,
    }
    try:
        health = get_json("/healthz", require_auth=False, timeout_sec=timeout_sec)
        snapshot["health"] = health
        snapshot["reachable"] = str(health.get("status") or "").lower() == "ok"
        readyz = get_json("/readyz", require_auth=False, timeout_sec=timeout_sec)
        snapshot["readyz"] = readyz
        status = str(readyz.get("status") or "").lower()
        snapshot["ready"] = status == "ready"
        if include_summary and snapshot["configured"]:
            snapshot["config_summary"] = get_json("/v1/config/summary", require_auth=True, timeout_sec=timeout_sec)
    except urllib.error.HTTPError as exc:
        snapshot["error"] = f"http_{exc.code}"
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        snapshot["error"] = f"unreachable: {reason}"
    except Exception as exc:  # pragma: no cover - 网络运行面保护
        snapshot["error"] = str(exc)
    return snapshot
