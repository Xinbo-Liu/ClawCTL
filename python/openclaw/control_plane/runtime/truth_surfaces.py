#!/usr/bin/env python3
"""Load the extension-aware runtime truth surfaces."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.control_plane.extensions.fragment_descriptors import (
    RUNTIME_CONTRACT_DESCRIPTOR,
    RUNTIME_SOURCE_STRATEGY_DESCRIPTOR,
    load_fragment_payload,
)
from openclaw.lib.repo.layout import resolve_repo_root


ROOT_DIR = resolve_repo_root(Path(__file__))
RUNTIME_CONTRACT_PATH = RUNTIME_CONTRACT_DESCRIPTOR.base_path
RUNTIME_SOURCE_STRATEGY_PATH = RUNTIME_SOURCE_STRATEGY_DESCRIPTOR.base_path


def load_runtime_contract(path: Path | None = None, *, config_path: Path | None = None) -> dict[str, Any]:
    return load_fragment_payload(RUNTIME_CONTRACT_DESCRIPTOR, path=path, config_path=config_path)


def load_runtime_source_strategy(path: Path | None = None, *, config_path: Path | None = None) -> dict[str, Any]:
    return load_fragment_payload(RUNTIME_SOURCE_STRATEGY_DESCRIPTOR, path=path, config_path=config_path)
