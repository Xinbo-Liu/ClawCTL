#!/usr/bin/env python3
"""统一渠道发送层。"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

from openclaw.lib.channels.provider_registry import payload_builder, resolve_channel_provider_adapter, response_evaluator
from openclaw.lib.http.json_client import http_post_json


@dataclass(frozen=True)
class ChannelDeliveryRequest:
    provider: str
    transport: str
    endpoint_url: str
    title: str
    markdown: str
    message_format: str
    at_all: bool = False
    timeout_ms: int = 10000
    dry_run: bool = False
    request_payload: dict[str, Any] | None = None
    secret: str = ''


@dataclass(frozen=True)
class ChannelDeliveryResult:
    ok: bool
    response_status: int
    response_payload: dict[str, Any] | None
    error: str | None
    request_payload: dict[str, Any]


def send_channel_message(request: ChannelDeliveryRequest) -> ChannelDeliveryResult:
    adapter = resolve_channel_provider_adapter(request.provider, request.transport)
    if adapter is None:
        return ChannelDeliveryResult(
            ok=False,
            response_status=0,
            response_payload=None,
            error=f"unsupported provider adapter: provider={request.provider}, transport={request.transport}",
            request_payload=request.request_payload or {},
        )
    builder = payload_builder(adapter)
    evaluator = response_evaluator(adapter)
    payload = request.request_payload if isinstance(request.request_payload, dict) and request.request_payload else builder(
        title=request.title,
        markdown=request.markdown,
        msg_format=request.message_format,
        at_all=request.at_all,
    )
    secret = str(request.secret or '').strip()
    if secret:
        signer = getattr(importlib.import_module(adapter.module), 'sign_message_payload', None)
        if callable(signer):
            payload = signer(payload, secret=secret)
    if request.dry_run:
        return ChannelDeliveryResult(
            ok=True,
            response_status=0,
            response_payload={"dry_run": True, "payload": payload},
            error=None,
            request_payload=payload,
        )
    result = http_post_json(
        request.endpoint_url,
        payload,
        timeout_sec=max(1.0, float(request.timeout_ms) / 1000.0),
    )
    if not result.ok and result.status_code == 0:
        return ChannelDeliveryResult(
            ok=False,
            response_status=0,
            response_payload=None,
            error=result.error or "transport error",
            request_payload=payload,
        )
    ok, status, response_payload, error = evaluator(
        status=result.status_code,
        response_payload=result.payload,
        parse_error=result.error,
    )
    return ChannelDeliveryResult(
        ok=bool(ok),
        response_status=int(status or 0),
        response_payload=response_payload if isinstance(response_payload, dict) else None,
        error=error,
        request_payload=payload,
    )
