#!/usr/bin/env python3
"""运行态状态根目录解析。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from openclaw.lib.runtime.resolver_loader import require_path_resolver
from openclaw.lib.repo.static_truth import ROOT_DIR


def resolve_state_root(*, view: str = 'scheduler', env: Mapping[str, str] | None = None) -> Path:
    env_map = dict(os.environ if env is None else env)
    resolver = require_path_resolver(repo_root=ROOT_DIR)
    return Path(resolver.resolve_path('state_root', view, env=env_map)).resolve()
