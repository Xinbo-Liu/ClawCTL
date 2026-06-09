#!/usr/bin/env python3
"""Runtime and config fragment descriptors."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from openclaw.lib.repo.contracts import repo_contract_path
from .core import (
    FragmentDescriptor,
    FragmentFieldDescriptor,
    _materialize_mapping_values,
    _materialize_rows,
    _merge_gateway_readonly_entries,
)
from openclaw.control_plane.extensions.merge import merge_unique_rows
from openclaw.lib.repo.profiles import control_plane_repo_combination_shared_deploy_env_owner_sets


_SHARED_MODEL_GROUP_ID = 'model_providers'
_SHARED_MODEL_ENV_KEYS = {
    'OLLAMA_BASE_URL': {
        'doc_summary': '共享 Ollama 模型 HTTP base URL',
        'site_env_examples': 'OLLAMA_BASE_URL=http://<ollama-host>:11434',
        'site_env_annotations': [
            '组合 profile 中声明为共享字段的扩展默认共用该 Ollama provider base URL。',
            '该值写入 deploy/site.env，不应包含 /api/chat、prompt 或业务参数。',
        ],
        'doc_details': {
            'step2_role': '启用组合 profile 时必须人工填写。',
            'meaning': 'Ollama HTTP 模型服务 base URL；模型客户端会按 profile.channel.api 调用 /api/chat。',
            'how_to_get': '填写目标服务器可访问的 Ollama 服务正式 HTTP 入口。',
            'format': '必须是 http/https URL。',
            'verify': '执行 one_click_config 后运行 setup env validate，并执行模型 smoke test。',
        },
    },
    'OLLAMA_MODEL_REF': {
        'doc_summary': '共享 Ollama 模型名',
        'site_env_examples': 'OLLAMA_MODEL_REF=<ollama-model-name>',
        'site_env_annotations': [
            '组合 profile 中声明为共享字段的扩展默认共用该 Ollama model ref。',
            '该值应填写 Ollama 已部署模型名，不写入仓库中的 model profile。',
        ],
        'doc_details': {
            'step2_role': '启用组合 profile 时必须人工填写。',
            'meaning': 'Ollama /api/chat 请求中的 model 名称。',
            'how_to_get': '从目标 Ollama 服务模型清单选择已部署模型。',
            'format': '非空模型名；可填写 provider 前缀形式，也可只填写 Ollama 原始模型名。',
            'verify': '执行模型 smoke test，确认 generate_text 能返回非空内容。',
        },
    },
}


def _as_list(value: object) -> list[Any]:
    if value in (None, ''):
        return []
    if isinstance(value, list):
        return list(value)
    return [value]


def _extension_owner(row: dict[str, Any]) -> str:
    return str(row.get('extensionId') or '').strip()


def _shared_owner_sets_for_item(item_id: str) -> tuple[frozenset[str], ...]:
    owner_sets_by_key = control_plane_repo_combination_shared_deploy_env_owner_sets()
    if item_id == _SHARED_MODEL_GROUP_ID:
        unique_sets: dict[tuple[str, ...], frozenset[str]] = {}
        for owner_sets in owner_sets_by_key.values():
            for owner_set in owner_sets:
                unique_sets[tuple(sorted(owner_set))] = owner_set
        return tuple(unique_sets.values())
    return owner_sets_by_key.get(item_id, ())


def _assert_allowed_shared_model_owners(existing: dict[str, Any], incoming: dict[str, Any], *, label: str, item_id: str) -> None:
    owners = {_extension_owner(existing), _extension_owner(incoming)}
    for allowed_owners in _shared_owner_sets_for_item(item_id):
        if owners == set(allowed_owners):
            return
    owner_text = ', '.join(sorted(owner or '<base>' for owner in owners))
    raise ValueError(f'{label} shared model owner conflict: {item_id} ({owner_text})')


def _merge_shared_model_group(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(existing)
    merged['id'] = _SHARED_MODEL_GROUP_ID
    merged['title'] = '模型 provider 与共享模型通道'
    orders = [
        int(row.get('doc_order'))
        for row in (existing, incoming)
        if isinstance(row.get('doc_order'), int)
    ]
    if orders:
        merged['doc_order'] = min(orders)
    merged.pop('extensionId', None)
    return merged


def _merge_deploy_env_groups(base_value: Any, incoming_value: Any, label: str) -> list[dict[str, Any]]:
    merged = [dict(row) for row in base_value if isinstance(row, dict)] if isinstance(base_value, list) else []
    index = {str(row.get('id') or '').strip(): row for row in merged if str(row.get('id') or '').strip()}
    for row in incoming_value if isinstance(incoming_value, list) else []:
        if not isinstance(row, dict):
            continue
        group_id = str(row.get('id') or '').strip()
        if group_id != _SHARED_MODEL_GROUP_ID:
            merged = merge_unique_rows(merged, [row], key_name='id', label=label)
            index = {str(item.get('id') or '').strip(): item for item in merged if str(item.get('id') or '').strip()}
            continue
        existing = index.get(group_id)
        if existing is None:
            merged.append(dict(row))
            index[group_id] = merged[-1]
            continue
        _assert_allowed_shared_model_owners(existing, row, label=label, item_id=group_id)
        merged_group = _merge_shared_model_group(existing, row)
        existing.clear()
        existing.update(merged_group)
    return merged


def _merge_shared_model_field(existing: dict[str, Any], incoming: dict[str, Any], *, key: str) -> dict[str, Any]:
    _assert_allowed_shared_model_owners(existing, incoming, label='deploy_env.fields', item_id=key)
    if existing.get('validator') != incoming.get('validator'):
        raise ValueError(f'deploy_env.fields shared model validator conflict: {key}')
    merged = deepcopy(existing)
    shared_doc = _SHARED_MODEL_ENV_KEYS[key]
    merged['key'] = key
    merged['group'] = _SHARED_MODEL_GROUP_ID
    merged['required'] = bool(existing.get('required')) or bool(incoming.get('required'))
    merged['manual_required'] = bool(existing.get('manual_required')) or bool(incoming.get('manual_required'))
    merged['default_kind'] = 'placeholder'
    merged['placeholder'] = '__REQUIRED__'
    merged.pop('default', None)
    merged['doc_location'] = 'deploy/site.env'
    merged['doc_summary'] = shared_doc['doc_summary']
    merged['site_env_examples'] = shared_doc['site_env_examples']
    annotations: list[Any] = []
    seen_annotations: set[str] = set()
    for item in [*list(shared_doc['site_env_annotations']), *_as_list(existing.get('site_env_annotations')), *_as_list(incoming.get('site_env_annotations'))]:
        marker = str(item)
        if not marker or marker in seen_annotations:
            continue
        seen_annotations.add(marker)
        annotations.append(item)
    merged['site_env_annotations'] = annotations
    merged['doc_details'] = shared_doc['doc_details']
    if bool(existing.get('secret')) or bool(incoming.get('secret')):
        merged['secret'] = True
    merged.pop('extensionId', None)
    return merged


def _merge_deploy_env_fields(base_value: Any, incoming_value: Any, label: str) -> list[dict[str, Any]]:
    merged = [dict(row) for row in base_value if isinstance(row, dict)] if isinstance(base_value, list) else []
    index = {str(row.get('key') or '').strip(): row for row in merged if str(row.get('key') or '').strip()}
    for row in incoming_value if isinstance(incoming_value, list) else []:
        if not isinstance(row, dict):
            continue
        key = str(row.get('key') or '').strip()
        if key not in _SHARED_MODEL_ENV_KEYS:
            merged = merge_unique_rows(merged, [row], key_name='key', label=label)
            index = {str(item.get('key') or '').strip(): item for item in merged if str(item.get('key') or '').strip()}
            continue
        existing = index.get(key)
        if existing is None:
            merged.append(dict(row))
            index[key] = merged[-1]
            continue
        merged_field = _merge_shared_model_field(existing, row, key=key)
        existing.clear()
        existing.update(merged_field)
    return merged


RUNTIME_PATHS_DESCRIPTOR = FragmentDescriptor(
    group='surface',
    key='runtimePathsPath',
    base_path=repo_contract_path('runtime.paths'),
    label='runtime_paths',
    fields=(
        FragmentFieldDescriptor(
            path=('roots',),
            label='runtime_paths.roots',
            merge_kind='additive',
        ),
        FragmentFieldDescriptor(
            path=('entries',),
            label='runtime_paths.entries',
            merge_kind='additive',
            materialize=_materialize_mapping_values(label='runtime_paths.entries'),
        ),
        FragmentFieldDescriptor(
            path=('logical_groups',),
            label='runtime_paths.logical_groups',
            merge_kind='additive',
            materialize=_materialize_mapping_values(label='runtime_paths.logical_groups'),
        ),
        FragmentFieldDescriptor(
            path=('view_contract',),
            label='runtime_paths.view_contract',
            merge_kind='additive',
        ),
        FragmentFieldDescriptor(
            path=('generated_artifacts',),
            label='runtime_paths.generated_artifacts',
            merge_kind='additive',
        ),
    ),
)


DEPLOY_ENV_SCHEMA_DESCRIPTOR = FragmentDescriptor(
    group='surface',
    key='deployEnvSchemaPath',
    base_path=repo_contract_path('deploy_env.schema'),
    label='deploy_env',
    fields=(
        FragmentFieldDescriptor(
            path=('groups',),
            label='deploy_env.groups',
            merge_kind='custom',
            key_name='id',
            materialize=_materialize_rows(label='deploy_env.groups'),
            merge_value=_merge_deploy_env_groups,
        ),
        FragmentFieldDescriptor(
            path=('fields',),
            label='deploy_env.fields',
            merge_kind='custom',
            key_name='key',
            materialize=_materialize_rows(label='deploy_env.fields'),
            merge_value=_merge_deploy_env_fields,
        ),
    ),
)


TESTING_MANIFEST_DESCRIPTOR = FragmentDescriptor(
    group='surface',
    key='testingManifestPath',
    base_path=repo_contract_path('runtime.testing_manifest'),
    label='testing_manifest',
    fields=(
        FragmentFieldDescriptor(
            path=('groups',),
            label='testing_manifest.groups',
            merge_kind='unique_rows',
            key_name='id',
            materialize=_materialize_rows(label='testing_manifest.groups'),
        ),
        FragmentFieldDescriptor(
            path=('checks',),
            label='testing_manifest.checks',
            merge_kind='unique_rows',
            key_name='id',
            materialize=_materialize_rows(label='testing_manifest.checks'),
        ),
        FragmentFieldDescriptor(
            path=('release_gate_checks',),
            label='testing_manifest.release_gate_checks',
            merge_kind='unique_rows',
            key_name='id',
            materialize=_materialize_rows(label='testing_manifest.release_gate_checks'),
        ),
        FragmentFieldDescriptor(
            path=('valid_groups',),
            label='testing_manifest.valid_groups',
            merge_kind='unique_values',
        ),
        FragmentFieldDescriptor(
            path=('execution_order',),
            label='testing_manifest.execution_order',
            merge_kind='unique_values',
        ),
        FragmentFieldDescriptor(
            path=('acceptance_reference', 'title'),
            label='testing_manifest.acceptance_reference.title',
            merge_kind='last_nonempty',
        ),
        FragmentFieldDescriptor(
            path=('acceptance_reference', 'generated_doc'),
            label='testing_manifest.acceptance_reference.generated_doc',
            merge_kind='last_nonempty',
        ),
        FragmentFieldDescriptor(
            path=('acceptance_reference', 'entrypoints'),
            label='testing_manifest.acceptance_reference.entrypoints',
            merge_kind='unique_rows',
            key_name='title',
            materialize=_materialize_rows(label='testing_manifest.acceptance_reference.entrypoints'),
        ),
        FragmentFieldDescriptor(
            path=('acceptance_reference', 'artifacts'),
            label='testing_manifest.acceptance_reference.artifacts',
            merge_kind='unique_rows',
            key_name='path',
            materialize=_materialize_rows(label='testing_manifest.acceptance_reference.artifacts'),
        ),
        FragmentFieldDescriptor(
            path=('acceptance_reference', 'scenarios'),
            label='testing_manifest.acceptance_reference.scenarios',
            merge_kind='unique_rows',
            key_name='title',
            materialize=_materialize_rows(label='testing_manifest.acceptance_reference.scenarios'),
        ),
        FragmentFieldDescriptor(
            path=('acceptance_reference', 'required_checks'),
            label='testing_manifest.acceptance_reference.required_checks',
            merge_kind='unique_values',
        ),
        FragmentFieldDescriptor(
            path=('acceptance_reference', 'required_run_ledger_jobs'),
            label='testing_manifest.acceptance_reference.required_run_ledger_jobs',
            merge_kind='unique_values',
        ),
        FragmentFieldDescriptor(
            path=('acceptance_contract',),
            label='testing_manifest.acceptance_contract',
            merge_kind='overlay_dict',
        ),
    ),
)


RUNTIME_SERVICE_REGISTRY_DESCRIPTOR = FragmentDescriptor(
    group='surface',
    key='runtimeServiceRegistryPath',
    base_path=repo_contract_path('runtime.service_registry'),
    label='runtime_service_registry',
    fields=(
        FragmentFieldDescriptor(
            path=('targets',),
            label='runtime_service_registry.targets',
            merge_kind='unique_rows',
            key_name='target',
            materialize=_materialize_rows(label='runtime_service_registry.targets'),
        ),
    ),
)


GATEWAY_READONLY_MANIFEST_DESCRIPTOR = FragmentDescriptor(
    group='surface',
    key='gatewayReadonlyManifestPath',
    base_path=repo_contract_path('gateway.readonly_manifest'),
    label='gateway_readonly',
    fields=(
        FragmentFieldDescriptor(
            path=('entries',),
            label='gateway_readonly.entries',
            merge_kind='custom',
            materialize=_materialize_rows(label='gateway_readonly.entries'),
            merge_value=_merge_gateway_readonly_entries,
        ),
    ),
)


GATEWAY_EXEC_APPROVALS_DESCRIPTOR = FragmentDescriptor(
    group='surface',
    key='gatewayExecApprovalsPath',
    base_path=repo_contract_path('gateway.exec_approvals'),
    label='gateway_exec_approvals',
    fields=(
        FragmentFieldDescriptor(
            path=('agents',),
            label='gateway_exec_approvals.agents',
            merge_kind='additive',
            materialize=_materialize_mapping_values(label='gateway_exec_approvals.agents'),
        ),
    ),
    root_merge_kind='additive',
)


__all__ = [
    'DEPLOY_ENV_SCHEMA_DESCRIPTOR',
    'GATEWAY_EXEC_APPROVALS_DESCRIPTOR',
    'GATEWAY_READONLY_MANIFEST_DESCRIPTOR',
    'RUNTIME_PATHS_DESCRIPTOR',
    'RUNTIME_SERVICE_REGISTRY_DESCRIPTOR',
    'TESTING_MANIFEST_DESCRIPTOR',
]
