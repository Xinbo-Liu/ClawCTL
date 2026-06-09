#!/usr/bin/env python3
"""统一解析 OpenClaw 运行路径视角。

说明：
- base kernel 默认规范视角为 `host / gateway / scheduler`；
- extension 追加的运行视角必须来自 active profile 合并后的 runtime_paths truth。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from openclaw.lib.repo.layout import resolve_default_runtime_control_plane_service_config_path, resolve_repo_root
from openclaw.lib.runtime.resolver_loader import build_path_resolver

DEFAULT_VALID_VIEWS = ("host", "gateway", "scheduler")
def _repo_root() -> Path:
    return resolve_repo_root(Path(__file__))


@lru_cache(maxsize=1)
def _active_view_contract() -> tuple[set[str], dict[str, str]]:
    valid_views = set(DEFAULT_VALID_VIEWS)
    state_env_names = {
        'host': 'HOST_STATE_DIR',
        'gateway': 'OPENCLAW_STATE_DIR',
        'scheduler': 'OPENCLAW_STATE_DIR',
    }
    try:
        repo_root = _repo_root()
        resolver = build_path_resolver(
            repo_root=repo_root,
            config_path=resolve_default_runtime_control_plane_service_config_path(repo_root),
        )
        valid_views.update(resolver.internal_views)
        state_root = resolver.resolve_entry('state_root')
        env_names = state_root.get('env_names') if isinstance(state_root.get('env_names'), dict) else {}
        for view, env_name in env_names.items():
            view_name = str(view or '').strip()
            env_token = str(env_name or '').strip()
            if view_name and env_token:
                state_env_names[view_name] = env_token
    except (KeyError, OSError, TypeError, ValueError):
        return valid_views, state_env_names
    return valid_views, state_env_names


# 模块级缓存，供规范化辅助与直接导入场景复用。
VALID_VIEWS, _STATE_ENV_NAMES = _active_view_contract()


def normalize_runtime_path_view(value: object, *, fallback: str = "host") -> str:
    text = str(value or "").strip().lower()
    if text in VALID_VIEWS:
        return text
    fallback_text = str(fallback or '').strip().lower()
    return fallback_text if fallback_text in VALID_VIEWS else "host"


def detect_runtime_path_view() -> str:
    explicit = normalize_runtime_path_view(os.environ.get("OPENCLAW_RUNTIME_PATH_VIEW"), fallback="")
    if explicit:
        return explicit

    # 对额外 extension 视角，优先按 state_root env 名称识别，避免在基座里硬编码具体业务运行视角。
    for view, env_name in _STATE_ENV_NAMES.items():
        if view in {'host', 'gateway', 'scheduler'}:
            continue
        if env_name and os.environ.get(env_name):
            return view
    return "host"
