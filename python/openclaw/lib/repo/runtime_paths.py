#!/usr/bin/env python3
"""Runtime-path helpers backed by the repo root resolver."""
from __future__ import annotations

from pathlib import Path

from .repo_root import RUNTIME_PATHS_REL_PATH, resolve_repo_root


def resolve_runtime_paths_manifest_path(start_path: Path | None = None) -> Path:
    return (resolve_repo_root(start_path) / RUNTIME_PATHS_REL_PATH).resolve()
