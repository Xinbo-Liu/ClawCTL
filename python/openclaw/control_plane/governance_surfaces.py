#!/usr/bin/env python3
"""Load extension-aware governance surfaces with extension ownership."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from openclaw.control_plane.extensions.fragment_descriptors import (
    DIAGNOSTIC_SURFACE_DESCRIPTOR,
    DISPATCH_OPERATIONS_DESCRIPTOR,
    DOCS_REGISTRY_DESCRIPTOR,
    DOCUMENTATION_CLOSURE_RULES_DESCRIPTOR,
    FULL_TEST_GROUP_REGISTRY_DESCRIPTOR,
    PATH_ENTRYPOINTS_DESCRIPTOR,
    RECOVERY_OPERATIONS_DESCRIPTOR,
    ROUTER_ROUTE_DESCRIPTOR,
    SETUP_FAILURES_DESCRIPTOR,
    load_fragment_payload,
)
from openclaw.control_plane.extensions.merge import read_json_object
from openclaw.control_plane.extensions.ownership import mapping_to_owned_rows
from openclaw.lib.repo.contracts import repo_contract_path

DISPATCH_OPERATIONS_SURFACE_PATH = DISPATCH_OPERATIONS_DESCRIPTOR.base_path
DISPATCH_OBSERVABILITY_SURFACE_PATH = repo_contract_path('governance.dispatch_observability_surface')
FULL_TEST_GROUP_REGISTRY_PATH = FULL_TEST_GROUP_REGISTRY_DESCRIPTOR.base_path
DOCS_REGISTRY_PATH = DOCS_REGISTRY_DESCRIPTOR.base_path
SETUP_FAILURES_SURFACE_PATH = SETUP_FAILURES_DESCRIPTOR.base_path
ROUTER_ROUTE_SURFACE_PATH = ROUTER_ROUTE_DESCRIPTOR.base_path
DIAGNOSTIC_SURFACE_PATH = DIAGNOSTIC_SURFACE_DESCRIPTOR.base_path
PATH_ENTRYPOINTS_SURFACE_PATH = PATH_ENTRYPOINTS_DESCRIPTOR.base_path
RECOVERY_OPERATIONS_SURFACE_PATH = RECOVERY_OPERATIONS_DESCRIPTOR.base_path
DOCUMENTATION_CLOSURE_RULES_PATH = DOCUMENTATION_CLOSURE_RULES_DESCRIPTOR.base_path


def _annotate_dispatch_observability(payload: dict[str, Any]) -> dict[str, Any]:
    materialized = deepcopy(payload)
    materialized['entries'] = mapping_to_owned_rows(
        materialized.get('entries') if isinstance(materialized.get('entries'), dict) else {},
        extension_id=None,
        id_key='id',
        label='dispatch_observability.entries',
    )
    return materialized


def load_dispatch_operations_surface(path: Path | None = None, *, config_path: Path | None = None) -> dict[str, Any]:
    return load_fragment_payload(DISPATCH_OPERATIONS_DESCRIPTOR, path=path, config_path=config_path)


def load_dispatch_observability_surface(path: Path | None = None, *, config_path: Path | None = None) -> dict[str, Any]:
    _ = config_path
    return _annotate_dispatch_observability(read_json_object(path or DISPATCH_OBSERVABILITY_SURFACE_PATH))


def load_full_test_group_registry(path: Path | None = None, *, config_path: Path | None = None) -> dict[str, Any]:
    return load_fragment_payload(FULL_TEST_GROUP_REGISTRY_DESCRIPTOR, path=path, config_path=config_path)


def load_docs_registry(path: Path | None = None, *, config_path: Path | None = None) -> dict[str, Any]:
    return load_fragment_payload(DOCS_REGISTRY_DESCRIPTOR, path=path, config_path=config_path)


def load_setup_failures_surface(path: Path | None = None, *, config_path: Path | None = None) -> dict[str, Any]:
    return load_fragment_payload(SETUP_FAILURES_DESCRIPTOR, path=path, config_path=config_path)


def load_router_route_surface(path: Path | None = None, *, config_path: Path | None = None) -> dict[str, Any]:
    return load_fragment_payload(ROUTER_ROUTE_DESCRIPTOR, path=path, config_path=config_path)


def load_diagnostic_surface(
    path: Path | None = None,
    *,
    config_path: Path | None = None,
    extensions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return load_fragment_payload(DIAGNOSTIC_SURFACE_DESCRIPTOR, path=path, config_path=config_path, extensions=extensions)


def load_path_entrypoints_surface(path: Path | None = None, *, config_path: Path | None = None) -> dict[str, Any]:
    return load_fragment_payload(PATH_ENTRYPOINTS_DESCRIPTOR, path=path, config_path=config_path)


def load_recovery_operations_surface(path: Path | None = None, *, config_path: Path | None = None) -> dict[str, Any]:
    return load_fragment_payload(RECOVERY_OPERATIONS_DESCRIPTOR, path=path, config_path=config_path)


def load_documentation_closure_rules(path: Path | None = None, *, config_path: Path | None = None) -> dict[str, Any]:
    return load_fragment_payload(DOCUMENTATION_CLOSURE_RULES_DESCRIPTOR, path=path, config_path=config_path)
