#!/usr/bin/env python3
"""internal API 令牌校验。"""
from __future__ import annotations

import hmac
import os

AUTH_HEADER = "Authorization"


def expected_bearer_token() -> str:
    return os.environ.get("OPENCLAW_INTERNAL_API_TOKEN", "").strip()


def is_authorized(header_value: str | None) -> bool:
    expected = expected_bearer_token()
    if not expected:
        return False
    if not header_value:
        return False
    prefix = "Bearer "
    if not header_value.startswith(prefix):
        return False
    actual = header_value[len(prefix):].strip()
    return hmac.compare_digest(actual, expected)
