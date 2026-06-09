#!/usr/bin/env python3
"""Registry and diagnostic fragment descriptors."""
from __future__ import annotations

from typing import Any

from openclaw.control_plane.extensions.ownership import annotate_rows, mapping_to_owned_rows, normalize_extension_id, with_extension_owner
from openclaw.lib.io.json_access import json_array
from openclaw.lib.repo.contracts import repo_contract_path

from .core import (
    FragmentDescriptor,
    FragmentFieldDescriptor,
    _materialize_mapping_values,
    _materialize_rows,
)


def _materialize_family_rows(mapping: dict[str, Any], *, extension_id: str | None, label: str) -> list[dict[str, Any]]:
    rows = mapping_to_owned_rows(mapping, extension_id=extension_id, id_key='id', label=label)
    materialized: list[dict[str, Any]] = []
    for row in rows:
        owner = normalize_extension_id(row.get('extensionId')) or None
        payload = with_extension_owner(row, owner)
        payload['references'] = [
            str(item).strip()
            for item in json_array(payload.get('references'))
            if str(item).strip()
        ]
        payload['entries'] = annotate_rows(
            [item for item in json_array(payload.get('entries')) if isinstance(item, dict)],
            owner,
        )
        materialized.append(payload)
    return materialized


def _prepare_object_families_payload(payload: dict[str, Any], extension_id: str | None) -> dict[str, Any]:
    return {
        'generated_artifacts': dict(payload.get('generated_artifacts') if isinstance(payload.get('generated_artifacts'), dict) else {}),
        'families': _materialize_family_rows(
            payload.get('families') if isinstance(payload.get('families'), dict) else {},
            extension_id=extension_id,
            label='object_families.families' if extension_id is None else f'extension {extension_id} object_families.families',
        ),
    }


AGENT_CLI_SURFACE_DESCRIPTOR = FragmentDescriptor(
    group='surface',
    key='agentCliSurfacePath',
    base_path=repo_contract_path('control_plane.agent_cli_surface'),
    label='agent_cli_surface',
    fields=(
        FragmentFieldDescriptor(
            path=('generated_artifacts',),
            label='agent_cli_surface.generated_artifacts',
            merge_kind='unique_dict',
        ),
        FragmentFieldDescriptor(
            path=('agents',),
            label='agent_cli_surface.agents',
            merge_kind='unique_dict',
            materialize=_materialize_mapping_values(label='agent_cli_surface.agents', id_key='id'),
        ),
    ),
)


RUNTIME_CONTRACT_DESCRIPTOR = FragmentDescriptor(
    group='surface',
    key='runtimeContractPath',
    base_path=repo_contract_path('runtime.runtime_contract'),
    label='runtime_contract',
    root_merge_kind='additive',
)


RUNTIME_SOURCE_STRATEGY_DESCRIPTOR = FragmentDescriptor(
    group='surface',
    key='runtimeSourceStrategyPath',
    base_path=repo_contract_path('runtime.source_strategy'),
    label='runtime_source_strategy',
    root_merge_kind='additive',
)


OBJECT_FAMILIES_DESCRIPTOR = FragmentDescriptor(
    group='surface',
    key='objectFamiliesPath',
    base_path=repo_contract_path('control_plane.object_families'),
    label='object_families',
    prepare_payload=_prepare_object_families_payload,
    fields=(
        FragmentFieldDescriptor(
            path=('generated_artifacts',),
            label='object_families.generated_artifacts',
            merge_kind='unique_dict',
        ),
        FragmentFieldDescriptor(
            path=('families',),
            label='object_families.families',
            merge_kind='extend',
        ),
    ),
)


DIAGNOSTIC_SURFACE_DESCRIPTOR = FragmentDescriptor(
    group='governance',
    key='diagnosticSurfacePath',
    base_path=repo_contract_path('governance.diagnostic_surface'),
    label='diagnostic_surface',
    fields=(
        FragmentFieldDescriptor(
            path=('diagnostics', 'blockingGroups'),
            label='diagnostic_surface.diagnostics.blockingGroups',
            merge_kind='additive',
            materialize=_materialize_rows(label='diagnostic_surface.diagnostics.blockingGroups'),
        ),
        FragmentFieldDescriptor(
            path=('diagnostics', 'sourceDiagnosisGroups'),
            label='diagnostic_surface.diagnostics.sourceDiagnosisGroups',
            merge_kind='additive',
            materialize=_materialize_rows(label='diagnostic_surface.diagnostics.sourceDiagnosisGroups'),
        ),
        FragmentFieldDescriptor(
            path=('actions', 'actions'),
            label='diagnostic_surface.actions.actions',
            merge_kind='additive',
            materialize=_materialize_rows(label='diagnostic_surface.actions.actions'),
        ),
        FragmentFieldDescriptor(
            path=('reasons', 'routeHintReasons'),
            label='diagnostic_surface.reasons.routeHintReasons',
            merge_kind='additive',
            materialize=_materialize_rows(label='diagnostic_surface.reasons.routeHintReasons'),
        ),
        FragmentFieldDescriptor(
            path=('reasons', 'manualVerifyTaskReasons'),
            label='diagnostic_surface.reasons.manualVerifyTaskReasons',
            merge_kind='additive',
            materialize=_materialize_rows(label='diagnostic_surface.reasons.manualVerifyTaskReasons'),
        ),
        FragmentFieldDescriptor(
            path=('reasons', 'manualVerifyResultReasons'),
            label='diagnostic_surface.reasons.manualVerifyResultReasons',
            merge_kind='additive',
            materialize=_materialize_rows(label='diagnostic_surface.reasons.manualVerifyResultReasons'),
        ),
        FragmentFieldDescriptor(
            path=('reasons', 'manualVerifyBlockingReasons'),
            label='diagnostic_surface.reasons.manualVerifyBlockingReasons',
            merge_kind='additive',
            materialize=_materialize_rows(label='diagnostic_surface.reasons.manualVerifyBlockingReasons'),
        ),
    ),
    root_merge_kind='additive',
)


__all__ = [
    'AGENT_CLI_SURFACE_DESCRIPTOR',
    'DIAGNOSTIC_SURFACE_DESCRIPTOR',
    'OBJECT_FAMILIES_DESCRIPTOR',
    'RUNTIME_CONTRACT_DESCRIPTOR',
    'RUNTIME_SOURCE_STRATEGY_DESCRIPTOR',
]
