#!/usr/bin/env python3
"""轻量 JSON HTTP 客户端。"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

DEFAULT_MAX_RESPONSE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class JsonHttpResult:
    ok: bool
    status_code: int
    payload: dict[str, Any]
    raw_text: str
    error: str | None


def _as_headers(headers: dict[str, str] | None = None) -> dict[str, str]:
    normalized = {"Accept": "application/json"}
    if headers:
        normalized.update({str(key): str(value) for key, value in headers.items()})
    return normalized


def _parse_json_response(response_text: str) -> tuple[dict[str, Any], str | None]:
    clean = str(response_text or "")
    if not clean.strip():
        return {}, None
    try:
        parsed_payload = json.loads(clean)
    except json.JSONDecodeError:
        return {"raw": clean}, "invalid json response"
    return parsed_payload if isinstance(parsed_payload, dict) else {"raw": parsed_payload}, None


def _read_limited_text(response: Any, *, max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES) -> tuple[str, str | None]:
    limit = max(1, int(max_response_bytes))
    body = response.read(limit + 1)
    if len(body) > limit:
        return "", f"response too large: exceeds {limit} bytes"
    return body.decode("utf-8", errors="replace"), None


def http_post_json(
    url: str,
    payload: dict[str, Any],
    *,
    timeout_sec: float = 10.0,
    headers: dict[str, str] | None = None,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> JsonHttpResult:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers=_as_headers({"content-type": "application/json", **(headers or {})}),
    )
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=max(1.0, timeout_sec), context=context) as response:
            status = int(getattr(response, "status", 200) or 200)
            text, size_error = _read_limited_text(response, max_response_bytes=max_response_bytes)
            if size_error:
                return JsonHttpResult(ok=False, status_code=status, payload={}, raw_text="", error=size_error)
            parsed, parse_error = _parse_json_response(text)
            return JsonHttpResult(ok=True, status_code=status, payload=parsed, raw_text=text, error=parse_error)
    except urllib.error.HTTPError as exc:
        text, size_error = _read_limited_text(exc, max_response_bytes=max_response_bytes) if exc.fp else ("", None)
        if size_error:
            return JsonHttpResult(ok=False, status_code=int(exc.code or 500), payload={}, raw_text="", error=size_error)
        parsed, parse_error = _parse_json_response(text)
        return JsonHttpResult(ok=False, status_code=int(exc.code or 500), payload=parsed, raw_text=text, error=parse_error)
    except urllib.error.URLError as exc:
        return JsonHttpResult(ok=False, status_code=0, payload={}, raw_text="", error=f"transport error: {exc.reason}")
    except Exception as exc:  # pragma: no cover
        return JsonHttpResult(ok=False, status_code=0, payload={}, raw_text="", error=f"transport error: {exc}")


def http_get_text(
    url: str,
    *,
    timeout_sec: float = 10.0,
    headers: dict[str, str] | None = None,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> str:
    request = urllib.request.Request(url, method="GET", headers=_as_headers(headers))
    with urllib.request.urlopen(request, timeout=max(1.0, timeout_sec), context=ssl.create_default_context()) as response:
        text, size_error = _read_limited_text(response, max_response_bytes=max_response_bytes)
        if size_error:
            raise ValueError(size_error)
        return text
