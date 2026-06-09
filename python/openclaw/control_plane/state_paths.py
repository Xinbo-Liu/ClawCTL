#!/usr/bin/env python3
"""控制平面运行态状态根目录解析。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from openclaw.lib.repo.static_truth import ROOT_DIR
from openclaw.lib.runtime.resolver_loader import require_path_resolver
from openclaw.lib.runtime.state import resolve_state_root
from openclaw.runtime.path_view import normalize_runtime_path_view


def resolve_control_plane_state_root(*, env: Mapping[str, str] | None = None) -> Path:
    env_map = dict(os.environ if env is None else env)
    view = normalize_runtime_path_view(env_map.get('OPENCLAW_RUNTIME_PATH_VIEW'), fallback='host')
    if view == 'host':
        resolver = require_path_resolver(repo_root=ROOT_DIR)
        return Path(resolver.resolve_path('control_plane_host_state_dir', 'host', env=env_map)).resolve()
    return resolve_state_root(view=view, env=env_map)
