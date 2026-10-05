#!/usr/bin/env python3
"""提供OpenClaw lib子系统的生产实现。"""
from __future__ import annotations

from pathlib import Path

from .repo_root import RUNTIME_PATHS_REL_PATH, resolve_repo_root


def resolve_runtime_paths_manifest_path(start_path: Path | None = None) -> Path:
    return (resolve_repo_root(start_path) / RUNTIME_PATHS_REL_PATH).resolve()
