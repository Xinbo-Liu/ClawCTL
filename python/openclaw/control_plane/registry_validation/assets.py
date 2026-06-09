#!/usr/bin/env python3
"""Asset and assembly validation helpers for control-plane registry validation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.control_plane.registry.owners import qualified_registry_id, row_owner_id
from openclaw.control_plane.registry.support import (
    CliError,
    _ensure_unique_text_list,
    _implementation_payload,
    _module_asset_path,
    _normalize_contract,
    _normalize_executor_contract,
    _parse_skill_markdown,
    _validate_permission_asset,
    _validate_toolset_asset,
)
from openclaw.control_plane.runtime.adapter_registry import RuntimeAdapterSpec, config_validator as runtime_config_validator
from openclaw.lib.io.json_access import json_object
from openclaw.lib.repo.path_contracts import resolve_extension_root, resolve_path_contract


def _validate_implementation_rows(
    implementations: list[dict[str, Any]],
    runtime_adapters_by_id: dict[str, dict[str, Any]],
    runtime_adapter_specs_by_id: dict[str, RuntimeAdapterSpec],
) -> None:
    for implementation in implementations:
        impl_id = str(implementation.get('id') or '')
        resolved_runtime = _implementation_payload(implementation, label=f'implementation {impl_id}')
        adapter_ref = str(resolved_runtime.get('adapterRef') or '').strip()
        adapter = runtime_adapters_by_id.get(adapter_ref)
        if not isinstance(adapter, dict):
            raise CliError('control-plane validation error', 2)
        spec = runtime_adapter_specs_by_id.get(adapter_ref)
        if spec is None:
            raise CliError('control-plane validation error', 2)
        validator = runtime_config_validator(spec)
        resolved_runtime['config'] = validator(resolved_runtime.get('config'), label=f'implementation {impl_id} runtime.config')
        implementation['resolvedRuntime'] = resolved_runtime
        implementation['resolvedRuntimeAdapterRef'] = adapter_ref
        implementation['resolvedRuntimeAdapter'] = dict(adapter)


def _module_validation_error(module_id: str, detail: str) -> None:
    raise CliError(f'control-plane validation error: agent module {module_id}: {detail}', 2)


def _path_is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _extension_root_for_module(module: dict[str, Any]) -> Path | None:
    source_path = Path(str(module.get('sourcePath') or '')).resolve()
    owner_id = row_owner_id(module)
    if not owner_id:
        return None
    try:
        extension_root = resolve_extension_root(source_path)
    except ValueError:
        return None
    return extension_root if extension_root.name == owner_id else None


def _resolve_module_local_file(module_id: str, module_dir: Path, rel: str, *, label: str) -> Path:
    if Path(rel).is_absolute():
        _module_validation_error(module_id, f'{label} 必须使用相对路径：{rel}')
    target = (module_dir / rel).resolve()
    if not _path_is_relative_to(target, module_dir):
        _module_validation_error(module_id, f'{label} 必须留在模块目录内：{target}')
    if not target.exists() or not target.is_file():
        _module_validation_error(module_id, f'{label} 指向的文件不存在：{target}')
    return target


def _resolve_extension_source_path(
    module_id: str,
    module_dir: Path,
    extension_root: Path | None,
    rel: str,
    *,
    label: str,
) -> Path:
    if Path(rel).is_absolute():
        _module_validation_error(module_id, f'{label} 必须使用相对路径：{rel}')
    if extension_root is None:
        _module_validation_error(module_id, f'{label} 无法解析所属 extension 根目录')
    try:
        target = resolve_path_contract(rel, base_dir=module_dir, start_path=module_dir)
    except ValueError as exc:
        _module_validation_error(module_id, f'{label} 路径合同无效：{exc}')
    if target is None:
        _module_validation_error(module_id, f'{label} 不能为空')
    if not _path_is_relative_to(target, extension_root):
        _module_validation_error(module_id, f'{label} 必须留在所属 extension 根目录内：{target}')
    if not target.exists():
        _module_validation_error(module_id, f'{label} 指向的文件或目录不存在：{target}')
    return target


def _lookup_scoped(
    index: dict[str, dict[str, Any]],
    ref: str,
    *,
    owner_id: str,
) -> dict[str, Any] | None:
    normalized_ref = str(ref or '').strip()
    if not normalized_ref:
        return None
    scoped_ref = normalized_ref if ':' in normalized_ref else qualified_registry_id(owner_id, normalized_ref)
    row = index.get(scoped_ref) or index.get(normalized_ref)
    return row if isinstance(row, dict) else None


def _resolve_agent_module_row(
    module: dict[str, Any],
    *,
    agents_by_id: dict[str, dict[str, Any]],
    implementations_by_id: dict[str, dict[str, Any]],
    groups_by_id: dict[str, dict[str, Any]],
    runtime_adapters_by_id: dict[str, dict[str, Any]],
    jobs_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    module_id = str(module.get('id') or '').strip()

    def fail(detail: str) -> None:
        _module_validation_error(module_id or '<empty>', detail)

    module_owner_id = row_owner_id(module)
    agent_ref = str(module.get('agentRef') or '').strip()
    if not agent_ref:
        fail('agentRef 不能为空')
    agent = _lookup_scoped(agents_by_id, agent_ref, owner_id=module_owner_id)
    if not isinstance(agent, dict):
        fail(f'agentRef 未注册：{agent_ref}')
    logic = json_object(module.get('logic'))
    implementation_ref = str(logic.get('implementationRef') or '').strip()
    implementation = _lookup_scoped(implementations_by_id, implementation_ref, owner_id=module_owner_id)
    if not isinstance(implementation, dict):
        fail(f'implementationRef 未注册：{implementation_ref or "<empty>"}')
    governance = json_object(module.get('governance'))
    if 'groupRefs' in governance:
        fail('governance.groupRefs 不允许直接声明，必须由 group 派生')
    assets = json_object(module.get('assets'))
    source_path = Path(str(module.get('sourcePath') or '')).resolve()
    module_dir = source_path.parent
    extension_root = _extension_root_for_module(module)
    for key in ('readmePath', 'binPath', 'skillsPath', 'permissionsPath', 'toolsPath'):
        rel = str(assets.get(key) or '').strip()
        if not rel:
            fail(f'assets.{key} 不能为空')
        _resolve_module_local_file(module_id, module_dir, rel, label=f'assets.{key}')
    for key in ('agentsMdPath',):
        rel = str(assets.get(key) or '').strip()
        if not rel:
            continue
        _resolve_module_local_file(module_id, module_dir, rel, label=f'assets.{key}')
    for rel in _ensure_unique_text_list(logic.get('sourcePaths') or [], label=f'agent module {module_id} logic.sourcePaths'):
        _resolve_extension_source_path(
            module_id,
            module_dir,
            extension_root,
            rel,
            label='logic.sourcePaths',
        )
    runtime = json_object(module.get('runtime'))
    runtime_adapter_refs = _ensure_unique_text_list(runtime.get('runtimeAdapterRefs') or [], label=f'agent module {module_id} runtime.runtimeAdapterRefs')
    entrypoint_kinds = _ensure_unique_text_list(runtime.get('entrypointKinds') or [], label=f'agent module {module_id} runtime.entrypointKinds')
    implementation_runtime = json_object(implementation.get('resolvedRuntime'))
    implementation_adapter_ref = str(implementation_runtime.get('adapterRef') or '').strip()
    if implementation_adapter_ref not in runtime_adapter_refs:
        fail(f'implementation.runtime.adapterRef 未出现在 runtime.runtimeAdapterRefs 中：{implementation_adapter_ref or "<empty>"}')
    for adapter_ref in runtime_adapter_refs:
        if adapter_ref not in runtime_adapters_by_id:
            fail(f'未注册的 runtime adapter：{adapter_ref}')
    assembly = json_object(module.get('assembly'))
    for key in ('skillSetRef', 'permissionPolicyRef', 'toolsetRef'):
        ref = str(assembly.get(key) or '').strip()
        if not ref:
            fail(f'assembly.{key} 不能为空')
    agent_governance = json_object(agent.get('governance'))
    if str(agent_governance.get('moduleRef') or '').strip() != module_id:
        fail(f'agent.governance.moduleRef 未绑定当前模块：{agent_ref}')
    resolved_group_refs = [
        group_id
        for group_id, group in groups_by_id.items()
        if isinstance(group, dict) and (
            agent_ref in set(group.get('resolvedMembers') or [])
            or qualified_registry_id(module_owner_id, agent_ref) in set(group.get('resolvedMembers') or [])
        )
    ]
    allowed_executor_kinds = set(_ensure_unique_text_list(agent.get('allowedExecutorKinds'), label=f'agent {agent_ref} allowedExecutorKinds'))
    normalized_contract = _normalize_contract(module.get('contract'), label=f'agent module {module_id} contract')
    operations = json_object(module.get('operations'))
    if not operations:
        fail('operations 不能为空')
    resolved_operations: dict[str, dict[str, Any]] = {}
    for op_name, op_payload in operations.items():
        op_id = str(op_name or '').strip()
        if not op_id:
            fail('operations.* 的 key 不能为空')
        if op_id in resolved_operations:
            fail(f'重复 operation：{op_id}')
        if not isinstance(op_payload, dict):
            fail(f'operations.{op_id} 必须是对象')
        summary = str(op_payload.get('summary') or '').strip()
        if not summary:
            fail(f'operations.{op_id}.summary 不能为空')
        normalized_executor = _normalize_executor_contract(op_payload.get('executor'), label=f'agent module {module_id} operations.{op_id}.executor')
        executor_kind = str(normalized_executor.get('kind') or '').strip()
        if executor_kind not in allowed_executor_kinds:
            fail(f'operations.{op_id}.executor.kind 未被 agent 允许：{executor_kind or "<empty>"}')
        if executor_kind not in entrypoint_kinds:
            fail(f'operations.{op_id}.executor.kind 未出现在 runtime.entrypointKinds 中：{executor_kind or "<empty>"}')
        declared_job_refs = _ensure_unique_text_list(op_payload.get('jobRefs') or [], label=f'agent module {module_id} operations.{op_id}.jobRefs') if op_payload.get('jobRefs') is not None else []
        job_bindings_payload = json_object(op_payload.get('jobBindings'))
        job_refs = [str(job_ref or '').strip() for job_ref in job_bindings_payload.keys()] or declared_job_refs
        if declared_job_refs and declared_job_refs != job_refs:
            fail(f'operations.{op_id}.jobRefs 与 jobBindings key 不一致')
        for job_ref in job_refs:
            job = _lookup_scoped(jobs_by_id, job_ref, owner_id=module_owner_id)
            if not isinstance(job, dict):
                fail(f'operations.{op_id} 绑定了未注册 job：{job_ref}')
            declared_agent_ref = str(job.get('agentRef') or '').strip()
            if declared_agent_ref and declared_agent_ref != agent_ref:
                fail(f'job {job_ref} 的 agentRef 与模块不一致：{declared_agent_ref}')
            declared_operation_ref = str(job.get('operationRef') or '').strip()
            if declared_operation_ref and declared_operation_ref != op_id:
                fail(f'job {job_ref} 的 operationRef 与模块 operation 不一致：{declared_operation_ref}')
        resolved_operations[op_id] = {
            'summary': summary,
            'executor': normalized_executor,
            'jobRefs': job_refs,
        }
    return {
        'agent': agent,
        'resolvedAgentRef': str(agent.get('qualifiedId') or qualified_registry_id(module_owner_id, agent_ref)),
        'resolvedGroupRefs': list(resolved_group_refs),
        'resolvedRuntimeAdapterRefs': runtime_adapter_refs,
        'resolvedContract': normalized_contract,
        'resolvedOperations': resolved_operations,
    }


def _validate_agent_module_rows(
    modules: list[dict[str, Any]],
    agents_by_id: dict[str, dict[str, Any]],
    implementations_by_id: dict[str, dict[str, Any]],
    groups_by_id: dict[str, dict[str, Any]],
    runtime_adapters_by_id: dict[str, dict[str, Any]],
    jobs_by_id: dict[str, dict[str, Any]],
) -> None:
    for module in modules:
        resolved = _resolve_agent_module_row(
            module,
            agents_by_id=agents_by_id,
            implementations_by_id=implementations_by_id,
            groups_by_id=groups_by_id,
            runtime_adapters_by_id=runtime_adapters_by_id,
            jobs_by_id=jobs_by_id,
        )
        agent = resolved['agent']
        normalized_contract = resolved['resolvedContract']
        module['resolvedAgentRef'] = resolved['resolvedAgentRef']
        module['resolvedGroupRefs'] = resolved['resolvedGroupRefs']
        module['resolvedRuntimeAdapterRefs'] = resolved['resolvedRuntimeAdapterRefs']
        module['resolvedContract'] = normalized_contract
        module['resolvedOperations'] = resolved['resolvedOperations']
        agent['resolvedContract'] = normalized_contract
        agent['resolvedInputs'] = dict(normalized_contract.get('inputs') or {})
        agent['resolvedOutputs'] = dict(normalized_contract.get('outputs') or {})


def _validate_skill_set_rows(
    skill_sets: list[dict[str, Any]],
    modules_by_id: dict[str, dict[str, Any]],
) -> None:
    for skill_set in skill_sets:
        item_id = str(skill_set.get('id') or '')
        module_ref = str(skill_set.get('moduleRef') or '').strip()
        module = _lookup_scoped(modules_by_id, module_ref, owner_id=row_owner_id(skill_set))
        if not isinstance(module, dict):
            raise CliError('control-plane validation error', 2)
        assembly = json_object(module.get('assembly'))
        if str(assembly.get('skillSetRef') or '').strip() != item_id:
            raise CliError('control-plane validation error', 2)
        source = json_object(skill_set.get('source'))
        source_path = Path(str(skill_set.get('sourcePath') or '')).parent / str(source.get('path') or '')
        resolved_path = source_path.resolve()
        expected_path = _module_asset_path(module, 'skillsPath')
        if resolved_path != expected_path:
            raise CliError('control-plane validation error', 2)
        derivation = json_object(skill_set.get('derivation'))
        if str(derivation.get('mode') or '').strip() != 'agent_internal':
            raise CliError('control-plane validation error', 2)
        if str(derivation.get('assetKey') or '').strip() != 'skillsPath':
            raise CliError('control-plane validation error', 2)
        module_manifest = Path(str(skill_set.get('sourcePath') or '')).parent / str(derivation.get('moduleManifestPath') or '')
        if module_manifest.resolve() != Path(str(module.get('sourcePath') or '')).resolve():
            raise CliError('control-plane validation error', 2)
        skill_set['resolvedSourcePath'] = str(resolved_path)
        skill_set['resolvedModuleRef'] = str(module.get('qualifiedId') or qualified_registry_id(row_owner_id(skill_set), module_ref))
        skill_set['resolvedSkills'] = _parse_skill_markdown(resolved_path, label=f'skill set {item_id}')


def _validate_permission_policy_rows(
    permission_policies: list[dict[str, Any]],
    modules_by_id: dict[str, dict[str, Any]],
) -> None:
    for policy in permission_policies:
        item_id = str(policy.get('id') or '')
        module_ref = str(policy.get('moduleRef') or '').strip()
        module = _lookup_scoped(modules_by_id, module_ref, owner_id=row_owner_id(policy))
        if not isinstance(module, dict):
            raise CliError('control-plane validation error', 2)
        assembly = json_object(module.get('assembly'))
        if str(assembly.get('permissionPolicyRef') or '').strip() != item_id:
            raise CliError('control-plane validation error', 2)
        source = json_object(policy.get('source'))
        source_path = Path(str(policy.get('sourcePath') or '')).parent / str(source.get('path') or '')
        resolved_path = source_path.resolve()
        expected_path = _module_asset_path(module, 'permissionsPath')
        if resolved_path != expected_path:
            raise CliError('control-plane validation error', 2)
        derivation = json_object(policy.get('derivation'))
        if str(derivation.get('mode') or '').strip() != 'agent_internal':
            raise CliError('control-plane validation error', 2)
        if str(derivation.get('assetKey') or '').strip() != 'permissionsPath':
            raise CliError('control-plane validation error', 2)
        module_manifest = Path(str(policy.get('sourcePath') or '')).parent / str(derivation.get('moduleManifestPath') or '')
        if module_manifest.resolve() != Path(str(module.get('sourcePath') or '')).resolve():
            raise CliError('control-plane validation error', 2)
        policy['resolvedSourcePath'] = str(resolved_path)
        policy['resolvedModuleRef'] = str(module.get('qualifiedId') or qualified_registry_id(row_owner_id(policy), module_ref))
        policy['resolvedPolicy'] = _validate_permission_asset(resolved_path, module_ref=module_ref, label=f'permission policy {item_id}')


def _validate_toolset_rows(
    toolsets: list[dict[str, Any]],
    modules_by_id: dict[str, dict[str, Any]],
) -> None:
    for toolset in toolsets:
        item_id = str(toolset.get('id') or '')
        module_ref = str(toolset.get('moduleRef') or '').strip()
        module = _lookup_scoped(modules_by_id, module_ref, owner_id=row_owner_id(toolset))
        if not isinstance(module, dict):
            raise CliError('control-plane validation error', 2)
        assembly = json_object(module.get('assembly'))
        if str(assembly.get('toolsetRef') or '').strip() != item_id:
            raise CliError('control-plane validation error', 2)
        source = json_object(toolset.get('source'))
        source_path = Path(str(toolset.get('sourcePath') or '')).parent / str(source.get('path') or '')
        resolved_path = source_path.resolve()
        expected_path = _module_asset_path(module, 'toolsPath')
        if resolved_path != expected_path:
            raise CliError('control-plane validation error', 2)
        derivation = json_object(toolset.get('derivation'))
        if str(derivation.get('mode') or '').strip() != 'agent_internal':
            raise CliError('control-plane validation error', 2)
        if str(derivation.get('assetKey') or '').strip() != 'toolsPath':
            raise CliError('control-plane validation error', 2)
        module_manifest = Path(str(toolset.get('sourcePath') or '')).parent / str(derivation.get('moduleManifestPath') or '')
        if module_manifest.resolve() != Path(str(module.get('sourcePath') or '')).resolve():
            raise CliError('control-plane validation error', 2)
        toolset['resolvedSourcePath'] = str(resolved_path)
        toolset['resolvedModuleRef'] = str(module.get('qualifiedId') or qualified_registry_id(row_owner_id(toolset), module_ref))
        toolset['resolvedToolset'] = _validate_toolset_asset(resolved_path, module_ref=module_ref, label=f'toolset {item_id}')
