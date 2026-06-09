#!/usr/bin/env python3
"""控制平面 registry loader 对外入口。"""
from __future__ import annotations

from pathlib import Path

from openclaw.control_plane.registry_loader.collections import _load_registry_collections
from openclaw.control_plane.registry_loader.config import load_registry_service_context
from openclaw.control_plane.registry_loader.payload import (
    _attach_registry_collections,
    _build_extension_rows,
    _build_registry_metadata,
)
from openclaw.control_plane.registry_loader.runtime import (
    _derive_registry_runtime_state,
    _validate_registry_collections,
)
from openclaw.control_plane.registry_loader.virtual_surfaces import (
    _ensure_agent_control_plane_registry,
    _ensure_agent_internal_assembly_registry,
)


def ensure_agent_internal_assembly_registry(config_path: Path, *, sync: bool = False) -> dict[str, object]:
    """确保 agent internal assembly registry 与派生结果一致。"""
    return _ensure_agent_internal_assembly_registry(config_path, sync=sync)


def ensure_agent_control_plane_registry(config_path: Path, *, sync: bool = False) -> dict[str, object]:
    """确保 agent control-plane registry 与派生结果一致。"""
    return _ensure_agent_control_plane_registry(config_path, sync=sync)


def load_registry_from_context(context: dict[str, object]) -> dict[str, object]:
    """从已解析的 service context 装配完整 registry。"""
    collections = _load_registry_collections(context)
    runtime_state = _derive_registry_runtime_state(context, collections)
    _validate_registry_collections(context, collections, runtime_state)
    payload = _build_registry_metadata(
        context,
        _build_extension_rows(context['extensions']),
    )
    return _attach_registry_collections(payload, collections, runtime_state)


def load_registry_from_path(config_path: Path) -> dict[str, object]:
    """从 service 配置路径装配完整 registry。"""
    return load_registry_from_context(load_registry_service_context(config_path))
