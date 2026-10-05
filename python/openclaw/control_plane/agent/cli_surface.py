#!/usr/bin/env python3
"""提供OpenClaw 控制平面子系统的生产实现。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.control_plane.extensions.fragment_descriptors import (
    AGENT_CLI_SURFACE_DESCRIPTOR,
    load_fragment_payload,
)

AGENT_CLI_SURFACE_PATH = AGENT_CLI_SURFACE_DESCRIPTOR.base_path


def load_agent_cli_surface(path: Path | None = None, *, config_path: Path | None = None) -> dict[str, Any]:
    return load_fragment_payload(AGENT_CLI_SURFACE_DESCRIPTOR, path=path, config_path=config_path)
