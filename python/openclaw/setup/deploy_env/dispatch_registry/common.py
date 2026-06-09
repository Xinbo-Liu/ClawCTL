#!/usr/bin/env python3
"""Shared helpers for deploy-env dispatch registry operations."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import NoReturn

from openclaw.lib.repo.layout import resolve_repo_root

ROOT_DIR = resolve_repo_root(Path(__file__))
OPTIONAL_RUNTIME_DEPENDENCY_ERROR: ModuleNotFoundError | None = None
try:
    from openclaw.control_plane.registry import control_plane_config_path, load_registry
    from openclaw.lib.dispatch.target_registry import (
        DEFAULT_SCHEMA_PATH as DISPATCH_TARGET_REGISTRY_SCHEMA_PATH,
    )
    from openclaw.lib.dispatch.target_registry import (
        DispatchRegistryValidationError,
        build_dispatch_compose_env_block,
        build_dispatch_default_exports,
        build_dispatch_registry_summary,
        dispatch_runtime_env_names,
        load_dispatch_registry,
    )
except ModuleNotFoundError as exc:
    OPTIONAL_RUNTIME_DEPENDENCY_ERROR = exc
    control_plane_config_path = None
    load_registry = None
    DISPATCH_TARGET_REGISTRY_SCHEMA_PATH = None
    DispatchRegistryValidationError = None
    build_dispatch_compose_env_block = None
    build_dispatch_default_exports = None
    build_dispatch_registry_summary = None
    dispatch_runtime_env_names = None
    load_dispatch_registry = None


def _fail(message: str, exit_code: int = 2) -> NoReturn:
    sys.stderr.write(f'[deploy_env_control_plane][FAIL] {message}\n')
    raise SystemExit(exit_code)


def _note(message: str) -> None:
    sys.stdout.write(f'[deploy_env_control_plane] {message}\n')


def _read_text(path: Path) -> str:
    return path.read_text(encoding='utf-8')


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


def require_runtime_dependencies() -> None:
    if OPTIONAL_RUNTIME_DEPENDENCY_ERROR is None:
        return
    missing = OPTIONAL_RUNTIME_DEPENDENCY_ERROR.name or str(OPTIONAL_RUNTIME_DEPENDENCY_ERROR)
    _fail(f'当前命令依赖缺失模块：{missing}', 2)
