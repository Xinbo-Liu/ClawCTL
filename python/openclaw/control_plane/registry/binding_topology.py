#!/usr/bin/env python3
"""控制平面 registry 装配使用的绑定与拓扑辅助。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.control_plane.registry.owners import (
    qualified_registry_id,
    resolve_collection_ref,
    resolved_collection_ref,
    row_owner_id,
)
from openclaw.control_plane.registry.support import _ensure_unique_text_list, _normalize_dependency_specs
from openclaw.lib.cli.common import CliError
from openclaw.lib.io.json_access import json_object


def _path_is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False

def _extension_id_for_agent_module(module: dict[str, Any], extensions: list[dict[str, Any]]) -> str:
    extension_id = str(module.get('extensionId') or '').strip()
    if extension_id:
        return extension_id
    activation = json_object(module.get('activation'))
    configured_extension_ids = [
        str(item).strip()
        for item in (activation.get('enabledExtensionIds') or [])
        if str(item).strip()
    ]
    if configured_extension_ids:
        matches = [
            str(extension.get('id') or '').strip()
            for extension in extensions
            if str(extension.get('id') or '').strip() in configured_extension_ids
        ]
        module_id = str(module.get('id') or '').strip() or '<unknown-module>'
        if len(matches) > 1:
            raise CliError(f'agent module {module_id} 同时命中多个激活 extension：{", ".join(matches)}', 2)
        if matches:
            return matches[0]
    raw_source_path = str(module.get('sourcePath') or '').strip()
    if not raw_source_path:
        return ''
    source_path = Path(raw_source_path).resolve()
    module_id = str(module.get('id') or source_path.stem).strip()
    matches: list[str] = []
    for extension in extensions:
        extension_id = str(extension.get('id') or '').strip()
        if not extension_id:
            continue
        registry = extension.get('registry') if isinstance(extension.get('registry'), dict) else {}
        for modules_dir in registry.get('agentModulesDirs') or []:
            if isinstance(modules_dir, Path) and _path_is_relative_to(source_path, modules_dir):
                matches.append(extension_id)
                break
    if len(matches) > 1:
        raise CliError(f'agent module {module_id} 命中多个 extension.agentModulesDirs：{", ".join(matches)}', 2)
    return matches[0] if matches else ''


def _resolve_optional_binding_ref(
    collections: dict[str, Any] | None,
    collection_key: str,
    ref: str,
    *,
    owner_id: str,
    label: str,
) -> tuple[str, str]:
    if not ref:
        return '', ''
    if not isinstance(collections, dict):
        return ref, ref
    row = resolve_collection_ref(collections, collection_key, ref, owner_id=owner_id, label=label)
    return str(row.get('id') or '').strip(), str(row.get('qualifiedId') or qualified_registry_id(row_owner_id(row), row.get('id')))


def _resolve_dependency_specs(
    dependencies: list[dict[str, Any]],
    *,
    collections: dict[str, Any] | None,
    owner_id: str,
    label: str,
) -> list[dict[str, Any]]:
    if not isinstance(collections, dict):
        return dependencies
    resolved: list[dict[str, Any]] = []
    for dep in dependencies:
        dep_job = str(dep.get('jobId') or '').strip()
        job_row = resolve_collection_ref(collections, 'jobs', dep_job, owner_id=owner_id, label=f'{label}.jobId')
        resolved.append({
            **dep,
            'jobId': str(job_row.get('id') or '').strip(),
            'resolvedJobRef': str(job_row.get('qualifiedId') or qualified_registry_id(row_owner_id(job_row), job_row.get('id'))),
        })
    return resolved


def _derive_job_bindings_from_modules(
    modules: list[dict[str, Any]],
    *,
    extensions: list[dict[str, Any]] | None = None,
    collections: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    bindings: dict[str, dict[str, Any]] = {}
    extensions = list(extensions or [])
    for module in modules:
        module_id = str(module.get('id') or '').strip()
        module_owner_id = row_owner_id(module)
        module_qualified_ref = str(module.get('qualifiedId') or qualified_registry_id(module_owner_id, module_id))
        agent_ref = str(module.get('agentRef') or module_id).strip()
        agent_local_ref, agent_qualified_ref = _resolve_optional_binding_ref(
            collections,
            'agents',
            agent_ref,
            owner_id=module_owner_id,
            label=f'agent module {module_id} agentRef',
        )
        extension_id = str(module.get('extensionId') or '').strip()
        if not extension_id and extensions:
            extension_id = _extension_id_for_agent_module(module, extensions)
        operations = json_object(module.get('operations'))
        for operation_ref, operation in operations.items():
            op_ref = str(operation_ref or '').strip()
            if not op_ref:
                raise CliError(f'agent module {module_id} operations 包含空 operationRef', 2)
            if not isinstance(operation, dict):
                raise CliError(f'agent module {module_id} operations.{op_ref} 必须为对象', 2)
            declared_job_refs = _ensure_unique_text_list(
                operation.get('jobRefs') or [],
                label=f'agent module {module_id} operations.{op_ref}.jobRefs',
            ) if operation.get('jobRefs') is not None else []
            job_bindings = json_object(operation.get('jobBindings'))
            binding_job_refs = [str(job_ref or '').strip() for job_ref in job_bindings.keys()]
            if any(not job_ref for job_ref in binding_job_refs):
                raise CliError(f'agent module {module_id} operations.{op_ref}.jobBindings 包含空 jobRef', 2)
            if declared_job_refs and declared_job_refs != binding_job_refs:
                raise CliError(f'agent module {module_id} operations.{op_ref}.jobRefs 必须与 jobBindings 键顺序一致', 2)
            for job_ref in (binding_job_refs or declared_job_refs):
                job_row = resolve_collection_ref(
                    collections or {},
                    'jobs',
                    job_ref,
                    owner_id=module_owner_id,
                    label=f'agent module {module_id} operations.{op_ref}.jobRef',
                ) if isinstance(collections, dict) else {'id': job_ref, 'qualifiedId': job_ref}
                resolved_job_ref = str(job_row.get('qualifiedId') or qualified_registry_id(row_owner_id(job_row), job_row.get('id')))
                local_job_ref = str(job_row.get('id') or job_ref).strip()
                binding_payload = json_object(job_bindings.get(job_ref))
                existing = bindings.get(resolved_job_ref)
                if isinstance(existing, dict):
                    raise CliError(
                        f'job {resolved_job_ref} 只能由一个模块 operation 认领；当前冲突：'
                        f'{existing.get("moduleRef")}.{existing.get("operationRef")} 与 {module_id}.{op_ref}',
                        2,
                    )
                depends_on = _resolve_dependency_specs(
                    _normalize_dependency_specs(
                        binding_payload.get('dependsOn'),
                        label=f'agent module {module_id} operations.{op_ref}.jobBindings.{job_ref}.dependsOn',
                        source=f'module:{module_id}.{op_ref}',
                    ),
                    collections=collections,
                    owner_id=module_owner_id,
                    label=f'agent module {module_id} operations.{op_ref}.jobBindings.{job_ref}.dependsOn',
                )
                target_binding_ref = str(binding_payload.get('targetBindingRef') or '').strip()
                local_target_ref, resolved_target_ref = _resolve_optional_binding_ref(
                    collections,
                    'targets',
                    target_binding_ref,
                    owner_id=module_owner_id,
                    label=f'agent module {module_id} operations.{op_ref}.jobBindings.{job_ref}.targetBindingRef',
                )
                bindings[resolved_job_ref] = {
                    'jobRef': local_job_ref,
                    'resolvedJobRef': resolved_job_ref,
                    'moduleRef': module_id,
                    'resolvedModuleRef': module_qualified_ref,
                    'agentRef': agent_local_ref or agent_ref,
                    'resolvedAgentRef': agent_qualified_ref or agent_ref,
                    'operationRef': op_ref,
                    'extensionId': extension_id,
                    'ownerId': module_owner_id,
                    'source': f'module:{module_id}.{op_ref}',
                    'targetBindingRef': local_target_ref,
                    'resolvedTargetBindingRef': resolved_target_ref,
                    'dependsOn': depends_on,
                }
    return bindings


def _infer_binding_runner_ref(
    *,
    job_id: str,
    binding: dict[str, Any] | None,
    job_runners_by_id: dict[str, dict[str, Any]],
    binding_runner_ids: list[str],
) -> str:
    if not isinstance(binding, dict) or not binding_runner_ids:
        return ''
    extension_id = str(binding.get('extensionId') or '').strip()
    if extension_id:
        candidates = [
            runner_id
            for runner_id in binding_runner_ids
            if str((job_runners_by_id.get(runner_id) or {}).get('extensionId') or '').strip() == extension_id
        ]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise CliError(f'job {job_id} 绑定到 extension {extension_id}，但该 extension 存在多个 handlesAgentBindings runner；请显式声明 runnerRef', 2)
        if len(binding_runner_ids) == 1:
            return binding_runner_ids[0]
        raise CliError(f'job {job_id} 绑定到 extension {extension_id}，但该 extension 未声明 handlesAgentBindings runner', 2)
    if len(binding_runner_ids) == 1:
        return binding_runner_ids[0]
    raise CliError(f'job {job_id} 缺少 runnerRef；当前存在多个 handlesAgentBindings runner，且无法从 module 来源推导唯一执行器', 2)


def _derive_group_topologies_from_groups(
    groups: list[dict[str, Any]],
    jobs_by_id: dict[str, dict[str, Any]],
    job_bindings_by_job_id: dict[str, dict[str, Any]],
    *,
    collections: dict[str, Any] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    grouped_job_bindings: dict[str, dict[str, Any]] = {}
    resolved: dict[str, dict[str, Any]] = {}
    for group in groups:
        group_ref = str(group.get('id') or '').strip()
        group_owner_id = row_owner_id(group)
        group_qualified_ref = str(group.get('qualifiedId') or qualified_registry_id(group_owner_id, group_ref))
        if not group_ref:
            raise CliError('agent group 缺少 id', 2)
        schedule_policy = json_object(group.get('schedulePolicy'))
        dependency_policy = json_object(group.get('dependencyPolicy'))
        recovery_policy = json_object(group.get('recoveryPolicy'))
        declared_schedule_job_refs = _ensure_unique_text_list(schedule_policy.get('jobRefs') or [], label=f'agent group {group_ref} schedulePolicy.jobRefs')
        declared_ordered_job_refs = _ensure_unique_text_list(dependency_policy.get('orderedJobRefs') or [], label=f'agent group {group_ref} dependencyPolicy.orderedJobRefs')
        schedule_job_refs = [
            resolved_collection_ref(collections or {}, 'jobs', job_ref, owner_id=group_owner_id, label=f'agent group {group_ref} schedulePolicy.jobRefs')
            if isinstance(collections, dict) else job_ref
            for job_ref in declared_schedule_job_refs
        ]
        ordered_job_refs = [
            resolved_collection_ref(collections or {}, 'jobs', job_ref, owner_id=group_owner_id, label=f'agent group {group_ref} dependencyPolicy.orderedJobRefs')
            if isinstance(collections, dict) else job_ref
            for job_ref in declared_ordered_job_refs
        ]
        if not schedule_job_refs:
            raise CliError(f'agent group {group_ref} schedulePolicy.jobRefs 不能为空', 2)
        if not ordered_job_refs:
            raise CliError(f'agent group {group_ref} dependencyPolicy.orderedJobRefs 不能为空', 2)
        if not set(ordered_job_refs).issubset(set(schedule_job_refs)):
            raise CliError(f'agent group {group_ref} dependencyPolicy.orderedJobRefs 必须属于 schedulePolicy.jobRefs', 2)
        recovery_steps = [item for item in (recovery_policy.get('steps') or []) if isinstance(item, dict)]
        recovery_job_refs: list[str] = []
        normalized_recovery_steps: list[dict[str, Any]] = []
        seen_after_minutes: set[int] = set()
        for idx, item in enumerate(recovery_steps):
            job_ref = str(item.get('jobRef') or '').strip()
            if not job_ref:
                raise CliError(f'agent group {group_ref} recoveryPolicy.steps[{idx}].jobRef 不能为空', 2)
            resolved_job_ref = (
                resolved_collection_ref(collections or {}, 'jobs', job_ref, owner_id=group_owner_id, label=f'agent group {group_ref} recoveryPolicy.steps[{idx}].jobRef')
                if isinstance(collections, dict) else job_ref
            )
            if resolved_job_ref in recovery_job_refs:
                raise CliError(f'agent group {group_ref} recoveryPolicy.steps.jobRef 不允许重复：{resolved_job_ref}', 2)
            if resolved_job_ref not in schedule_job_refs:
                raise CliError(f'agent group {group_ref} recoveryPolicy.steps[{idx}].jobRef 必须属于 schedulePolicy.jobRefs：{job_ref}', 2)
            if resolved_job_ref in ordered_job_refs:
                raise CliError(f'agent group {group_ref} recoveryPolicy.steps[{idx}].jobRef 不得属于 dependencyPolicy.orderedJobRefs：{job_ref}', 2)
            if resolved_job_ref not in job_bindings_by_job_id:
                raise CliError(f'agent group {group_ref} recoveryPolicy.steps[{idx}].jobRef 缺少模块 operation 绑定：{resolved_job_ref}', 2)
            action_kind = str(item.get('actionKind') or '').strip()
            if action_kind not in {'retry', 'compensate', 'replay'}:
                raise CliError(f'agent group {group_ref} recoveryPolicy.steps[{idx}].actionKind 未注册：{action_kind}', 2)
            after_minutes = int(item.get('afterMinutes') or 0)
            if after_minutes < 1:
                raise CliError(f'agent group {group_ref} recoveryPolicy.steps[{idx}].afterMinutes 必须 >= 1', 2)
            if after_minutes in seen_after_minutes:
                raise CliError(f'agent group {group_ref} recoveryPolicy.steps.afterMinutes 不允许重复：{after_minutes}', 2)
            seen_after_minutes.add(after_minutes)
            recovery_job_refs.append(resolved_job_ref)
            normalized_recovery_steps.append({
                'jobRef': resolved_job_ref,
                'localJobRef': job_ref,
                'actionKind': action_kind,
                'afterMinutes': after_minutes,
                'operationRef': str(item.get('operationRef') or '').strip(),
                'notes': [str(note).strip() for note in (item.get('notes') or []) if str(note).strip()],
            })
        uncovered_job_refs = [job_ref for job_ref in schedule_job_refs if job_ref not in set(ordered_job_refs) | set(recovery_job_refs)]
        if uncovered_job_refs:
            raise CliError(f'agent group {group_ref} schedulePolicy.jobRefs 存在未归类 job：{", ".join(uncovered_job_refs)}', 2)
        for job_ref in schedule_job_refs:
            if job_ref not in job_bindings_by_job_id:
                raise CliError(f'agent group {group_ref} schedulePolicy.jobRefs 缺少模块 operation 绑定：{job_ref}', 2)
            if job_ref not in jobs_by_id and not (isinstance(collections, dict) and job_ref in (collections.get('jobsByQualifiedId') or {})):
                raise CliError(f'agent group {group_ref} schedulePolicy.jobRefs 未注册：{job_ref}', 2)
            binding = job_bindings_by_job_id.get(job_ref)
            if not isinstance(binding, dict):
                raise CliError(f'agent group {group_ref} schedulePolicy.jobRefs 缺少模块 operation 绑定：{job_ref}', 2)
            existing = grouped_job_bindings.get(job_ref)
            if isinstance(existing, dict) and str(existing.get('resolvedGroupRef') or existing.get('groupRef') or '').strip() != group_qualified_ref:
                raise CliError(f'job {job_ref} 只能归属一个 group；当前冲突：{existing.get("resolvedGroupRef") or existing.get("groupRef")} 与 {group_qualified_ref}', 2)
            grouped_job_bindings[job_ref] = {**binding, 'groupRef': group_ref, 'resolvedGroupRef': group_qualified_ref}
        for job_ref in ordered_job_refs:
            grouped_job_bindings[job_ref]['groupRef'] = group_ref
            grouped_job_bindings[job_ref]['resolvedGroupRef'] = group_qualified_ref
        for step in normalized_recovery_steps:
            grouped_job_bindings[step['jobRef']]['groupRef'] = group_ref
            grouped_job_bindings[step['jobRef']]['resolvedGroupRef'] = group_qualified_ref
        normalized_recovery_steps.sort(key=lambda item: (int(item.get('afterMinutes') or 0), str(item.get('jobRef') or '')))
        resolved[group_qualified_ref] = {
            'scheduleJobRefs': list(schedule_job_refs),
            'localScheduleJobRefs': list(declared_schedule_job_refs),
            'orderedJobRefs': list(ordered_job_refs),
            'localOrderedJobRefs': list(declared_ordered_job_refs),
            'recoverySteps': normalized_recovery_steps,
        }
    return grouped_job_bindings, resolved


def _derive_group_agent_refs_from_job_refs(
    job_refs: list[str],
    *,
    group_id: str,
    label: str,
    jobs_by_id: dict[str, dict[str, Any]],
    job_bindings_by_job_id: dict[str, dict[str, Any]],
) -> list[str]:
    derived: list[str] = []
    for idx, job_ref in enumerate(job_refs):
        if job_ref not in jobs_by_id:
            raise CliError(f'agent group {group_id} {label}[{idx}] 未注册 job：{job_ref}', 2)
        binding = job_bindings_by_job_id.get(job_ref)
        if not isinstance(binding, dict):
            raise CliError(f'agent group {group_id} {label}[{idx}] 缺少模块 operation 绑定：{job_ref}', 2)
        agent_ref = str(binding.get('resolvedAgentRef') or binding.get('agentRef') or '').strip()
        if not agent_ref:
            raise CliError(f'agent group {group_id} {label}[{idx}] 缺少派生 agentRef：{job_ref}', 2)
        if agent_ref not in derived:
            derived.append(agent_ref)
    return derived


def _derive_group_agent_views(
    *,
    group_id: str,
    schedule_job_refs: list[str],
    ordered_job_refs: list[str],
    jobs_by_id: dict[str, dict[str, Any]],
    job_bindings_by_job_id: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    member_agent_refs = _derive_group_agent_refs_from_job_refs(
        schedule_job_refs,
        group_id=group_id,
        label='schedulePolicy.jobRefs',
        jobs_by_id=jobs_by_id,
        job_bindings_by_job_id=job_bindings_by_job_id,
    )
    ordered_members = _derive_group_agent_refs_from_job_refs(
        ordered_job_refs,
        group_id=group_id,
        label='dependencyPolicy.orderedJobRefs',
        jobs_by_id=jobs_by_id,
        job_bindings_by_job_id=job_bindings_by_job_id,
    )
    return {
        'memberAgentRefs': member_agent_refs,
        'orderedMembers': ordered_members,
        'entryAgentRefs': [ordered_members[0]] if ordered_members else [],
        'exitAgentRefs': [ordered_members[-1]] if ordered_members else [],
    }
