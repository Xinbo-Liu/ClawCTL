#!/usr/bin/env python3
"""Shared control-plane manifest field definitions."""
from __future__ import annotations

from pathlib import Path
from typing import Any


RUNTIME_ADAPTER_REGISTRY_PATHS_KEY = 'runtimeAdapterRegistryPaths'
DISPATCH_TARGET_REGISTRY_PATHS_KEY = 'dispatchTargetRegistryPaths'
DISPATCH_PROVIDER_REGISTRY_PATHS_KEY = 'dispatchProviderRegistryPaths'

REGISTRY_PATH_LIST_KEYS = (
    RUNTIME_ADAPTER_REGISTRY_PATHS_KEY,
    DISPATCH_TARGET_REGISTRY_PATHS_KEY,
    DISPATCH_PROVIDER_REGISTRY_PATHS_KEY,
)

SURFACE_FRAGMENTS_FIELD = 'surfaceFragments'
GOVERNANCE_SURFACES_FIELD = 'governanceSurfaces'

FRAGMENT_GROUP_FIELDS = {
    'surface': SURFACE_FRAGMENTS_FIELD,
    'governance': GOVERNANCE_SURFACES_FIELD,
}

SURFACE_FRAGMENT_KEYS = (
    'runtimePathsPath',
    'deployEnvSchemaPath',
    'testingManifestPath',
    'runtimeServiceRegistryPath',
    'gatewayReadonlyManifestPath',
    'gatewayExecApprovalsPath',
    'composeServicesPath',
    'workspaceTemplatesManifestPath',
    'agentCliSurfacePath',
    'runtimeMountsPath',
    'runtimeContractPath',
    'runtimeSourceStrategyPath',
    'objectFamiliesPath',
)

GOVERNANCE_SURFACE_KEYS = (
    'dispatchOperationsSurfacePath',
    'fullTestGroupRegistryPath',
    'docsRegistryPath',
    'setupFailuresSurfacePath',
    'routerRouteSurfacePath',
    'diagnosticSurfacePath',
    'pathEntrypointsSurfacePath',
    'recoveryOperationsSurfacePath',
    'documentationClosureRulesPath',
)

FRAGMENT_FIELD_KEYS = {
    SURFACE_FRAGMENTS_FIELD: SURFACE_FRAGMENT_KEYS,
    GOVERNANCE_SURFACES_FIELD: GOVERNANCE_SURFACE_KEYS,
}


def fragment_group_field(group: str) -> str:
    field = FRAGMENT_GROUP_FIELDS.get(group)
    if field is None:
        raise ValueError(f'unknown fragment group: {group}')
    return field


def filter_path_mapping(payload: Any, *, keys: tuple[str, ...]) -> dict[str, Path]:
    if not isinstance(payload, dict):
        return {}
    return {
        key: value
        for key in keys
        if isinstance((value := payload.get(key)), Path)
    }
