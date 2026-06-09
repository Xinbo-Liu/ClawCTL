#!/usr/bin/env python3
"""Governance/docs fragment descriptors."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from openclaw.control_plane.extensions.ownership import annotate_rows, with_extension_owner
from openclaw.lib.io.json_access import json_array
from openclaw.lib.repo.contracts import repo_contract_path

from .core import (
    FragmentDescriptor,
    FragmentFieldDescriptor,
    _materialize_mapping_rows,
    _materialize_mapping_values,
    _materialize_rows,
)


def _router_rule_rows(rows: Any, extension_id: str | None) -> list[dict[str, Any]]:
    return [
        {'text': text, **({'extensionId': extension_id} if str(extension_id or '').strip() else {})}
        for item in rows if (text := str(item or '').strip())
    ]


def _prepare_router_surface(payload: dict[str, Any], extension_id: str | None) -> dict[str, Any]:
    materialized = deepcopy(payload)
    materialized['explicitRoutes'] = annotate_rows(
        materialized.get('explicitRoutes') if isinstance(materialized.get('explicitRoutes'), list) else [],
        extension_id,
    )
    materialized['automaticRoutes'] = annotate_rows(
        materialized.get('automaticRoutes') if isinstance(materialized.get('automaticRoutes'), list) else [],
        extension_id,
    )
    materialized['healthAwareRuleRows'] = _router_rule_rows(materialized.get('healthAwareRules'), extension_id)
    materialized['healthAwareRules'] = [
        str(row.get('text') or '').strip()
        for row in materialized['healthAwareRuleRows']
        if isinstance(row, dict) and str(row.get('text') or '').strip()
    ]
    return materialized


def _finalize_router_surface(payload: dict[str, Any]) -> dict[str, Any]:
    materialized = deepcopy(payload)
    materialized['healthAwareRules'] = [
        str(row.get('text') or '').strip()
        for row in materialized.get('healthAwareRuleRows') or []
        if isinstance(row, dict) and str(row.get('text') or '').strip()
    ]
    return materialized


def _annotate_setup_failures_payload(payload: dict[str, Any], extension_id: str | None) -> dict[str, Any]:
    materialized = deepcopy(payload)
    if extension_id is not None:
        defaults = dict(materialized.get('defaults') if isinstance(materialized.get('defaults'), dict) else {})
        emitted = [str(item).strip() for item in json_array(defaults.get('emittedBy')) if str(item).strip()]
        if extension_id not in emitted:
            emitted.append(extension_id)
        defaults['emittedBy'] = emitted
        materialized['defaults'] = defaults
    materialized['failures'] = [
        with_extension_owner(row, extension_id)
        for row in json_array(materialized.get('failures'))
        if isinstance(row, dict)
    ]
    return materialized


DISPATCH_OPERATIONS_DESCRIPTOR = FragmentDescriptor(
    group='governance',
    key='dispatchOperationsSurfacePath',
    base_path=repo_contract_path('governance.dispatch_operations_surface'),
    label='dispatch_operations',
    fields=(
        FragmentFieldDescriptor(
            path=('generated_artifacts',),
            label='dispatch_operations.generated_artifacts',
            merge_kind='unique_dict',
        ),
        FragmentFieldDescriptor(
            path=('entries',),
            label='dispatch_operations.entries',
            merge_kind='additive',
            materialize=_materialize_mapping_rows(label='dispatch_operations.entries', id_key='id'),
        ),
    ),
)


FULL_TEST_GROUP_REGISTRY_DESCRIPTOR = FragmentDescriptor(
    group='governance',
    key='fullTestGroupRegistryPath',
    base_path=repo_contract_path('governance.full_test_group_registry'),
    label='full_test_group_registry',
    fields=(
        FragmentFieldDescriptor(
            path=('generated_artifacts',),
            label='full_test_group_registry.generated_artifacts',
            merge_kind='unique_dict',
        ),
        FragmentFieldDescriptor(
            path=('dispatch_recovery_actions',),
            label='full_test_group_registry.dispatch_recovery_actions',
            merge_kind='unique_values',
        ),
        FragmentFieldDescriptor(
            path=('groups',),
            label='full_test_group_registry.groups',
            merge_kind='unique_dict',
            materialize=_materialize_mapping_values(label='full_test_group_registry.groups', id_key='id'),
        ),
    ),
)


DOCS_REGISTRY_DESCRIPTOR = FragmentDescriptor(
    group='governance',
    key='docsRegistryPath',
    base_path=repo_contract_path('governance.docs_registry'),
    label='docs_registry',
    fields=(
        FragmentFieldDescriptor(
            path=('checker',),
            label='docs_registry.checker',
            merge_kind='unique_dict',
        ),
        FragmentFieldDescriptor(
            path=('pages',),
            label='docs_registry.pages',
            merge_kind='additive_rows_by_key',
            key_name='path',
            materialize=_materialize_rows(label='docs_registry.pages'),
        ),
    ),
)


SETUP_FAILURES_DESCRIPTOR = FragmentDescriptor(
    group='governance',
    key='setupFailuresSurfacePath',
    base_path=repo_contract_path('governance.setup_failures'),
    label='setup_failures',
    prepare_payload=_annotate_setup_failures_payload,
    root_merge_kind='additive',
)


ROUTER_ROUTE_DESCRIPTOR = FragmentDescriptor(
    group='governance',
    key='routerRouteSurfacePath',
    base_path=repo_contract_path('governance.router_route_surface'),
    label='router_route_surface',
    prepare_payload=_prepare_router_surface,
    finalize_payload=_finalize_router_surface,
    root_merge_kind='additive',
)


PATH_ENTRYPOINTS_DESCRIPTOR = FragmentDescriptor(
    group='governance',
    key='pathEntrypointsSurfacePath',
    base_path=repo_contract_path('governance.path_entrypoints'),
    label='path_entrypoints',
    fields=(
        FragmentFieldDescriptor(
            path=('generated_artifacts',),
            label='path_entrypoints.generated_artifacts',
            merge_kind='unique_dict',
        ),
        FragmentFieldDescriptor(
            path=('entrypoints',),
            label='path_entrypoints.entrypoints',
            merge_kind='unique_dict',
            materialize=_materialize_mapping_values(label='path_entrypoints.entrypoints'),
        ),
        FragmentFieldDescriptor(
            path=('common_entries',),
            label='path_entrypoints.common_entries',
            merge_kind='unique_rows',
            key_name='entry_id',
            materialize=_materialize_rows(label='path_entrypoints.common_entries'),
        ),
    ),
)


RECOVERY_OPERATIONS_DESCRIPTOR = FragmentDescriptor(
    group='governance',
    key='recoveryOperationsSurfacePath',
    base_path=repo_contract_path('governance.recovery_operations_surface'),
    label='recovery_operations_surface',
    fields=(
        FragmentFieldDescriptor(
            path=('entries',),
            label='recovery_operations.entries',
            merge_kind='additive',
            materialize=_materialize_mapping_rows(label='recovery_operations.entries', id_key='id'),
        ),
        FragmentFieldDescriptor(
            path=('decision_map',),
            label='recovery_operations.decision_map',
            merge_kind='additive',
            materialize=_materialize_rows(label='recovery_operations.decision_map'),
        ),
    ),
    root_merge_kind='additive',
)


DOCUMENTATION_CLOSURE_RULES_DESCRIPTOR = FragmentDescriptor(
    group='governance',
    key='documentationClosureRulesPath',
    base_path=repo_contract_path('governance.documentation_closure_rules'),
    label='documentation_closure',
    fields=(
        FragmentFieldDescriptor(
            path=('checker',),
            label='documentation_closure.checker',
            merge_kind='unique_dict',
        ),
        FragmentFieldDescriptor(
            path=('entries',),
            label='documentation_closure.entries',
            merge_kind='additive_rows_by_key',
            key_name='path',
            materialize=_materialize_rows(label='documentation_closure.entries'),
        ),
    ),
)


__all__ = [
    'DISPATCH_OPERATIONS_DESCRIPTOR',
    'DOCS_REGISTRY_DESCRIPTOR',
    'DOCUMENTATION_CLOSURE_RULES_DESCRIPTOR',
    'FULL_TEST_GROUP_REGISTRY_DESCRIPTOR',
    'PATH_ENTRYPOINTS_DESCRIPTOR',
    'RECOVERY_OPERATIONS_DESCRIPTOR',
    'ROUTER_ROUTE_DESCRIPTOR',
    'SETUP_FAILURES_DESCRIPTOR',
]
