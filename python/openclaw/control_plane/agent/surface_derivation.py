#!/usr/bin/env python3
"""agent module 到 agent / implementation 虚拟视图的共享派生逻辑。"""
from __future__ import annotations

from typing import Any

from openclaw.control_plane.registry.owners import qualified_registry_id, row_owner_id
from openclaw.control_plane.registry.support import (
    _ensure_unique_text_list,
    _implementation_payload,
)
from openclaw.lib.cli.common import CliError
from openclaw.lib.io.json_access import json_object


def normalize_agent_capabilities(value: Any, *, label: str) -> dict[str, Any]:
    """规范化 agent capabilities 载荷。"""
    if not isinstance(value, dict):
        raise CliError(f'{label} 必须为对象', 2)
    filesystem_write = _ensure_unique_text_list(value.get('filesystemWrite') or [], label=f'{label}.filesystemWrite')
    return {
        'network': bool(value.get('network', False)),
        'filesystemWrite': filesystem_write,
        'modelRequired': bool(value.get('modelRequired', False)),
        'externalDispatch': bool(value.get('externalDispatch', False)),
    }


def build_expected_agent_control_plane_registry(
    modules: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """构建期望的 agent / implementation 虚拟 registry。"""
    expected: dict[str, dict[str, dict[str, Any]]] = {
        'agents': {},
        'implementations': {},
    }
    for module in modules:
        module_id = str(module.get('id') or '').strip()
        agent_ref = str(module.get('agentRef') or '').strip()
        if not agent_ref:
            raise CliError(f'agent module {module_id} agentRef 不能为空', 2)
        runtime = json_object(module.get('runtime'))
        entrypoint_kinds = _ensure_unique_text_list(runtime.get('entrypointKinds') or [], label=f'agent module {module_id} runtime.entrypointKinds')
        runtime_adapter_refs = _ensure_unique_text_list(runtime.get('runtimeAdapterRefs') or [], label=f'agent module {module_id} runtime.runtimeAdapterRefs')
        logic = json_object(module.get('logic'))
        implementation_ref = str(logic.get('implementationRef') or '').strip()
        if not implementation_ref:
            raise CliError(f'agent module {module_id} logic.implementationRef 不能为空', 2)
        control_plane = json_object(module.get('controlPlane'))
        agent_cfg = json_object(control_plane.get('agent'))
        if not agent_cfg:
            raise CliError(f'agent module {module_id} controlPlane.agent 不能为空', 2)
        agent_title = str(agent_cfg.get('title') or '').strip()
        if not agent_title:
            raise CliError(f'agent module {module_id} controlPlane.agent.title 不能为空', 2)
        entrypoint_kind = str(agent_cfg.get('entrypointKind') or '').strip()
        if not entrypoint_kind:
            raise CliError(f'agent module {module_id} controlPlane.agent.entrypointKind 不能为空', 2)
        if entrypoint_kind not in entrypoint_kinds:
            raise CliError(f'agent module {module_id} controlPlane.agent.entrypointKind={entrypoint_kind} 必须属于 runtime.entrypointKinds', 2)
        description = str(agent_cfg.get('description') or '').strip()
        if not description:
            raise CliError(f'agent module {module_id} controlPlane.agent.description 不能为空', 2)
        capabilities = normalize_agent_capabilities(agent_cfg.get('capabilities'), label=f'agent module {module_id} controlPlane.agent.capabilities')
        agent_payload: dict[str, Any] = {
            'schemaVersion': 1,
            'id': agent_ref,
            'title': agent_title,
            'entrypoint': {
                'kind': entrypoint_kind,
            },
            'description': description,
            'capabilities': capabilities,
            'allowedExecutorKinds': entrypoint_kinds,
            'governance': {
                'moduleRef': module_id,
            },
        }
        default_model_profile_ref = str(agent_cfg.get('defaultModelProfileRef') or '').strip()
        if default_model_profile_ref:
            agent_payload['defaultModelProfileRef'] = default_model_profile_ref
        implementation_cfg = json_object(control_plane.get('implementation'))
        if not implementation_cfg:
            raise CliError(f'agent module {module_id} controlPlane.implementation 不能为空', 2)
        implementation_title = str(implementation_cfg.get('title') or '').strip()
        if not implementation_title:
            raise CliError(f'agent module {module_id} controlPlane.implementation.title 不能为空', 2)
        implementation_runtime = _implementation_payload({'runtime': implementation_cfg.get('runtime')}, label=f'agent module {module_id} controlPlane.implementation')
        implementation_adapter_ref = str(implementation_runtime.get('adapterRef') or '').strip()
        if implementation_adapter_ref not in runtime_adapter_refs:
            raise CliError(
                f'agent module {module_id} controlPlane.implementation.runtime.adapterRef={implementation_adapter_ref} 必须属于 runtime.runtimeAdapterRefs',
                2,
            )
        implementation_payload: dict[str, Any] = {
            'schemaVersion': 1,
            'id': implementation_ref,
            'title': implementation_title,
            'runtime': implementation_runtime,
        }
        module_owner_id = row_owner_id(module)
        agent_key = qualified_registry_id(module_owner_id, agent_ref)
        implementation_key = qualified_registry_id(module_owner_id, implementation_ref)
        if agent_key in expected['agents']:
            owner = expected['agents'][agent_key].get('governance', {}).get('moduleRef')
            raise CliError(f'agent 派生 id 冲突：{agent_key} 同时来自 {owner} 与 {module_id}', 2)
        if implementation_key in expected['implementations']:
            owner = expected['implementations'][implementation_key].get('id')
            raise CliError(f'implementation 派生 id 冲突：{implementation_key} 同时来自 {owner} 与 {module_id}', 2)
        expected['agents'][agent_key] = agent_payload
        expected['implementations'][implementation_key] = implementation_payload
    return expected
