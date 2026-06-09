#!/usr/bin/env python3
"""dispatch target 注册表解析、校验与摘要。"""
from __future__ import annotations

from ._target_registry_merge import load_dispatch_registry
from ._target_registry_render import (
    build_dispatch_compose_env_block,
    build_dispatch_default_exports,
    build_dispatch_registry_summary,
    dispatch_runtime_env_names,
)
from ._target_registry_shared import (
    DEFAULT_REGISTRY_PATH,
    DEFAULT_SCHEMA_PATH,
    DispatchRegistryValidationError,
    load_dispatch_registry_schema,
)
from ._target_registry_validation import validate_dispatch_registry_payload


__all__ = [
    'DEFAULT_REGISTRY_PATH',
    'DEFAULT_SCHEMA_PATH',
    'DispatchRegistryValidationError',
    'build_dispatch_compose_env_block',
    'build_dispatch_default_exports',
    'dispatch_runtime_env_names',
    'build_dispatch_registry_summary',
    'load_dispatch_registry',
    'load_dispatch_registry_schema',
    'validate_dispatch_registry_payload',
]
