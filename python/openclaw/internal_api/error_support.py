#!/usr/bin/env python3
"""internal-api error helpers."""
from __future__ import annotations

import os
import sys
from typing import Any


def _env_flag_enabled(name: str) -> bool:
    return str(os.environ.get(name) or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def _internal_error_payload(*, path: str, exc: Exception) -> dict[str, Any]:
    payload: dict[str, Any] = {
        'error': 'internal_error',
        'message': 'internal server error',
        'path': path or '/',
    }
    if _env_flag_enabled('OPENCLAW_INTERNAL_API_DEBUG_ERRORS'):
        payload['detail'] = str(exc)
    return payload


def _log_internal_error(*, path: str, exc: Exception) -> None:
    detail = f' detail={exc}' if _env_flag_enabled('OPENCLAW_INTERNAL_API_DEBUG_ERRORS') else ''
    sys.stderr.write(
        f'[openclaw_internal_api][WARN] unhandled error path={path or "/"} exceptionType={exc.__class__.__name__}{detail}\n'
    )
