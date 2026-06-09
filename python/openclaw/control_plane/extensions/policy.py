#!/usr/bin/env python3
"""Extension runtime policy helpers."""
from __future__ import annotations

import os


def _text_list_from_env(name: str) -> set[str]:
    raw = str(os.environ.get(name) or '').strip()
    if not raw:
        return set()
    return {item.strip() for item in raw.split(',') if item.strip()}


def _allowlisted_unauthenticated_route_ids() -> set[str]:
    return _text_list_from_env('OPENCLAW_INTERNAL_API_UNAUTH_EXTENSION_ROUTE_IDS')


def _env_flag_enabled(name: str) -> bool:
    return str(os.environ.get(name) or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def unauthenticated_extension_routes_enabled() -> bool:
    return _env_flag_enabled('OPENCLAW_INTERNAL_API_ENABLE_UNAUTH_EXTENSION_ROUTES')


def is_unauthenticated_extension_route_allowed(route_id: str) -> bool:
    normalized_route_id = str(route_id or '').strip()
    return bool(normalized_route_id) and unauthenticated_extension_routes_enabled() and normalized_route_id in _allowlisted_unauthenticated_route_ids()
