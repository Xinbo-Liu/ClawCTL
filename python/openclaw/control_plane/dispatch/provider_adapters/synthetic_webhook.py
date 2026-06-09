#!/usr/bin/env python3
"""Synthetic webhook provider adapter for base release fixtures."""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlparse


def validate_endpoint(url: str) -> dict[str, Any]:
    text = str(url or '').strip()
    if not text:
        return {'ok': False, 'reason': 'endpoint 为空'}
    parsed = urlparse(text)
    if parsed.scheme != 'https':
        return {'ok': False, 'reason': 'endpoint 必须使用 https'}
    if parsed.username or parsed.password:
        return {'ok': False, 'reason': 'endpoint 不允许包含用户名或密码'}
    if parsed.query or parsed.fragment:
        return {'ok': False, 'reason': 'endpoint 不允许携带 query 或 hash'}
    if not (parsed.hostname or '').strip():
        return {'ok': False, 'reason': 'endpoint host 不能为空'}
    if not (parsed.path or '').startswith('/webhook/'):
        return {'ok': False, 'reason': 'endpoint path 必须以 /webhook/ 开头'}
    return {'ok': True, 'reason': None}


def build_message_payload(*, title: str, markdown: str, msg_format: str, at_all: bool) -> dict[str, Any]:
    normalized_format = str(msg_format or 'card').strip().lower()
    clean_markdown = str(markdown or '').strip()
    if normalized_format == 'text':
        return {
            'type': 'text',
            'text': f'{title}\n\n{clean_markdown}'.strip(),
        }
    if normalized_format == 'post':
        return {
            'type': 'post',
            'title': title,
            'body': clean_markdown or title,
        }
    return {
        'type': 'card',
        'title': title,
        'body': clean_markdown or title,
        'notifyAll': bool(at_all),
    }


def sign_message_payload(payload: dict[str, Any], *, secret: str, timestamp: int | None = None) -> dict[str, Any]:
    clean_secret = str(secret or '').strip()
    if not clean_secret:
        return dict(payload)
    current_timestamp = int(time.time()) if timestamp is None else int(timestamp)
    string_to_sign = f'{current_timestamp}\n{clean_secret}'
    sign = base64.b64encode(
        hmac.new(string_to_sign.encode('utf-8'), digestmod=hashlib.sha256).digest()
    ).decode('utf-8')
    return {
        'timestamp': current_timestamp,
        'sign': sign,
        **dict(payload),
    }


def evaluate_delivery_response(
    *,
    status: int,
    response_payload: dict[str, Any] | None,
    parse_error: str | None,
) -> tuple[bool, int, dict[str, Any] | None, str | None]:
    if 200 <= status < 300:
        if parse_error:
            return False, status, response_payload, parse_error
        if isinstance(response_payload, dict) and response_payload.get('code') not in {None, 0, '0'}:
            message = (
                response_payload.get('msg')
                or response_payload.get('message')
                or response_payload.get('errmsg')
                or response_payload.get('error')
            )
            detail = f"delivery business error: code={response_payload.get('code')}"
            if message:
                detail = f'{detail}, msg={message}'
            return False, status, response_payload, detail
        return True, status, response_payload, None
    return False, status, response_payload, f'https error: status={status}'
