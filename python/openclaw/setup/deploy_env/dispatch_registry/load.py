#!/usr/bin/env python3
"""Load-side helpers for deploy-env dispatch registry operations."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.control_plane.manifest_fields import (
    DISPATCH_PROVIDER_REGISTRY_PATHS_KEY,
    DISPATCH_TARGET_REGISTRY_PATHS_KEY,
)
from openclaw.setup.deploy_env.dispatch_registry.common import (
    DISPATCH_TARGET_REGISTRY_SCHEMA_PATH,
    DispatchRegistryValidationError,
    _display_path,
    _fail,
    control_plane_config_path,
    load_dispatch_registry,
    load_registry,
    require_runtime_dependencies,
)


def resolve_dispatch_targets_paths(config_path: Path | None = None, *, required: bool = True) -> list[Path]:
    resolved_config, registry_paths = _registry_paths_payload(config_path)
    return _resolve_dispatch_targets_paths_from_registry_paths(resolved_config, registry_paths, required=required)


def resolve_dispatch_targets_path(config_path: Path | None = None, *, required: bool = True) -> Path | None:
    rows = resolve_dispatch_targets_paths(config_path, required=required)
    if not rows:
        return None
    return rows[0]


def resolve_dispatch_provider_paths(config_path: Path | None = None, *, required: bool = False) -> list[Path]:
    resolved_config, registry_paths = _registry_paths_payload(config_path)
    return _resolve_dispatch_provider_paths_from_registry_paths(resolved_config, registry_paths, required=required)


def resolve_dispatch_registry_paths(
    config_path: Path | None = None,
    *,
    target_required: bool = True,
    provider_required: bool = False,
) -> tuple[list[Path], list[Path]]:
    """一次读取 active profile registry，同时返回 target/provider registry 路径。"""
    resolved_config, registry_paths = _registry_paths_payload(config_path)
    target_paths = _resolve_dispatch_targets_paths_from_registry_paths(resolved_config, registry_paths, required=target_required)
    provider_paths = _resolve_dispatch_provider_paths_from_registry_paths(resolved_config, registry_paths, required=provider_required)
    return target_paths, provider_paths


def _registry_paths_payload(config_path: Path | None = None) -> tuple[Path, dict[str, Any]]:
    require_runtime_dependencies()
    resolved_config = Path(config_path).resolve() if config_path else Path(control_plane_config_path()).resolve()
    registry_payload = load_registry(resolved_config)
    registry_paths = registry_payload.get('registryPaths') if isinstance(registry_payload, dict) else {}
    return resolved_config, registry_paths if isinstance(registry_paths, dict) else {}


def _resolve_dispatch_targets_paths_from_registry_paths(
    resolved_config: Path,
    registry_paths: dict[str, Any],
    *,
    required: bool = True,
) -> list[Path]:
    rows = [Path(item).resolve() for item in list((registry_paths or {}).get(DISPATCH_TARGET_REGISTRY_PATHS_KEY) or []) if str(item).strip()]
    if rows:
        return rows
    if required:
        _fail(f'{_display_path(resolved_config)} 未启用 dispatch registry', 2)
    return []


def _resolve_dispatch_provider_paths_from_registry_paths(
    resolved_config: Path,
    registry_paths: dict[str, Any],
    *,
    required: bool = False,
) -> list[Path]:
    rows = [Path(item).resolve() for item in list((registry_paths or {}).get(DISPATCH_PROVIDER_REGISTRY_PATHS_KEY) or []) if str(item).strip()]
    if rows:
        return rows
    if required:
        _fail(f'{_display_path(resolved_config)} 未启用 dispatch provider registry', 2)
    return []


def load_dispatch_targets(config_path: Path | None = None, *, required: bool = True) -> dict[str, Any]:
    require_runtime_dependencies()
    registry_paths, provider_registry_paths = resolve_dispatch_registry_paths(config_path, target_required=required, provider_required=False)
    if not registry_paths:
        return {}
    try:
        return load_dispatch_registry(
            registry_paths,
            DISPATCH_TARGET_REGISTRY_SCHEMA_PATH,
            provider_registry_paths or None,
        )
    except DispatchRegistryValidationError as registry_error:
        _fail(str(registry_error), 2)


def dispatch_registry_disabled_summary() -> dict[str, Any]:
    return {
        'registry_version': 0,
        'registry_enabled': False,
        'target_count': 0,
        'target_ids': [],
        'enabled_default_target_ids': [],
        'release_policy_count': 0,
        'release_policy_ids': [],
        'lifecycle_state_count': 0,
        'lifecycle_state_ids': [],
        'verification_batch_count': 0,
        'verification_batch_ids': [],
        'default_rotation_batch_id': '',
        'owners': {},
    }
