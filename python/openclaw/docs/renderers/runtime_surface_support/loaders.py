#!/usr/bin/env python3
"""Truth-manifest loaders for runtime_surface renderer."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from openclaw.control_plane.surfaces import load_testing_manifest as load_testing_surface_manifest
from openclaw.lib.repo.layout import resolve_default_runtime_control_plane_service_config_path
from openclaw.lib.repo.static_truth import (
    repo_contract_path,
    read_repo_contract_json,
    service_registry_targets,
)


def load_testing_manifest(*, config_path: Path | None = None) -> dict[str, Any]:
    return load_testing_surface_manifest(
        repo_contract_path('runtime.testing_manifest'),
        config_path=config_path,
    )


def load_runtime_surface_manifest() -> dict[str, Any]:
    payload = read_repo_contract_json('governance.runtime_entrypoints')
    if not isinstance(payload, dict):
        raise ValueError('runtime_entrypoints 顶层必须为对象')
    return payload


def load_json_object(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding='utf-8'))


def read_manifest(
    root_dir: Path,
    *,
    config_path: Path | None = None,
    resolve_config_path_fn: Callable[[Path], Path] = resolve_default_runtime_control_plane_service_config_path,
    load_runtime_surface_manifest_fn: Callable[[], dict[str, Any]] = load_runtime_surface_manifest,
    service_registry_targets_fn: Callable[[Path], list[dict[str, Any]]] = service_registry_targets,
    load_testing_manifest_fn: Callable[..., dict[str, Any]] = load_testing_manifest,
    read_repo_contract_json_fn: Callable[[str], dict[str, Any]] = read_repo_contract_json,
) -> dict[str, Any]:
    resolved_config_path = config_path or resolve_config_path_fn(root_dir)
    manifest = load_runtime_surface_manifest_fn()
    manifest['targets'] = service_registry_targets_fn(root_dir, config_path=resolved_config_path)
    testing_manifest = load_testing_manifest_fn(config_path=resolved_config_path)
    manifest['acceptance_reference'] = dict(testing_manifest.get('acceptance_reference') or {})
    manifest['runtime_contract'] = read_repo_contract_json_fn('runtime.runtime_contract')
    manifest['source_strategy'] = read_repo_contract_json_fn('runtime.source_strategy')
    return manifest
