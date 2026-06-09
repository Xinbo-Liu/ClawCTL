#!/usr/bin/env python3
"""Agent module detach plan helpers."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from openclaw.control_plane.modules.change_set import build_write, read_json_object, summarize_files
from openclaw.control_plane.module_scheduler.group_ops import update_group_for_detach
from openclaw.control_plane.registry import CliError
from openclaw.lib.io.json_access import json_object


def resolve_detach_operation_ref(module_payload: dict[str, Any], *, module_ref: str, job_id: str, operation_ref: str) -> str:
    """Locate the operation currently bound to the requested job."""
    operations = json_object(module_payload.get('operations'))
    found_operation_ref = ''
    for current_operation_ref, current_operation in operations.items():
        if not isinstance(current_operation, dict):
            continue
        job_bindings = json_object(current_operation.get('jobBindings'))
        if job_id in job_bindings:
            found_operation_ref = str(current_operation_ref or '').strip()
            break
    if not found_operation_ref:
        raise CliError(f'module {module_ref} 未绑定 jobId：{job_id}', 2)
    normalized_operation_ref = str(operation_ref or '').strip()
    if normalized_operation_ref and normalized_operation_ref != found_operation_ref:
        raise CliError(f'job {job_id} 绑定在 {found_operation_ref}，与 --operation-ref={normalized_operation_ref} 不一致', 2)
    return found_operation_ref


def build_detach_module_write(
    module_payload: dict[str, Any],
    *,
    module_ref: str,
    operation_ref: str,
    job_id: str,
    module_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the module.json update that removes the job binding."""
    next_module_payload = copy.deepcopy(module_payload)
    operations = json_object(next_module_payload.get('operations'))
    next_operation_payload = operations.get(operation_ref)
    if not isinstance(next_operation_payload, dict):
        raise CliError(f'module {module_ref} 未注册 operationRef：{operation_ref}', 2)
    next_job_bindings = json_object(next_operation_payload.get('jobBindings'))
    if job_id not in next_job_bindings:
        raise CliError(f'module {module_ref} operations.{operation_ref}.jobBindings 中不存在 {job_id}', 2)
    next_job_bindings.pop(job_id, None)
    next_operation_payload['jobBindings'] = next_job_bindings
    return next_module_payload, build_write(
        module_path,
        action='update',
        payload=next_module_payload,
        summary=f'移除 operations.{operation_ref}.jobBindings.{job_id}',
    )


def build_detach_group_write(
    registry: dict[str, Any],
    *,
    group_ref: str,
    job_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Build the optional group update for detach."""
    if not group_ref:
        return None, None
    group_row = json_object(registry.get('agentGroupsById')).get(group_ref)
    if not isinstance(group_row, dict):
        raise CliError(f'job {job_id} 声明了不存在的 groupRef：{group_ref}', 2)
    group_path = Path(str(group_row.get('sourcePath') or '')).resolve()
    group_payload = read_json_object(group_path)
    next_group_payload = update_group_for_detach(group_payload=group_payload, job_id=job_id)
    return build_write(
        group_path,
        action='update',
        payload=next_group_payload,
        summary=f'从 group {group_ref} 移除 {job_id}',
    ), {'groupRef': group_ref, 'action': 'remove_job'}


def build_detach_target_write(
    registry: dict[str, Any],
    *,
    target_binding_ref: str,
    agent_ref: str,
    job_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Build the optional target update for detach."""
    if not target_binding_ref:
        return None, None
    target_row = json_object(registry.get('targetsById')).get(target_binding_ref)
    if not isinstance(target_row, dict):
        raise CliError(f'job {job_id} 引用了不存在的 targetBindingRef：{target_binding_ref}', 2)
    keep_support = False
    for current_job in registry.get('jobs', []):
        if not isinstance(current_job, dict):
            continue
        current_job_id = str(current_job.get('id') or '').strip()
        if current_job_id == job_id:
            continue
        if str(current_job.get('agentRef') or '').strip() != agent_ref:
            continue
        if str(current_job.get('targetBindingRef') or '').strip() == target_binding_ref:
            keep_support = True
            break
    if keep_support:
        return None, {'targetBindingRef': target_binding_ref, 'action': 'kept_supported_agent'}
    target_path = Path(str(target_row.get('sourcePath') or '')).resolve()
    target_payload = read_json_object(target_path)
    supported_agents = [str(item).strip() for item in (target_payload.get('supportedAgentRefs') or []) if str(item).strip()]
    if agent_ref not in supported_agents:
        return None, None
    next_supported_agents = [item for item in supported_agents if item != agent_ref]
    if not next_supported_agents:
        raise CliError(f'target {target_binding_ref} detach {job_id} 后 supportedAgentRefs 为空；当前命令不自动删除 target', 2)
    next_target_payload = copy.deepcopy(target_payload)
    next_target_payload['supportedAgentRefs'] = next_supported_agents
    return build_write(
        target_path,
        action='update',
        payload=next_target_payload,
        summary=f'从 target {target_binding_ref} 移除 supportedAgentRef={agent_ref}',
    ), {'targetBindingRef': target_binding_ref, 'action': 'remove_supported_agent'}


def build_detach_plan_payload(
    *,
    effective_repo_root: Path,
    module_ref: str,
    operation_ref: str,
    job_id: str,
    agent_ref: str,
    before_job_refs: list[str],
    before_target_refs: list[str],
    after_job_refs: list[str],
    after_target_refs: list[str],
    group_change: dict[str, Any] | None,
    target_change: dict[str, Any] | None,
    writes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Render the detach plan payload."""
    return {
        'status': 'ok',
        'mode': 'plan',
        'moduleRef': module_ref,
        'operationRef': operation_ref,
        'jobId': job_id,
        'agentRef': agent_ref,
        'schedulerBinding': {
            'before': {
                'bindingMode': 'scheduler_bound' if before_job_refs else 'standalone',
                'jobRefs': before_job_refs,
                'targetBindingRefs': before_target_refs,
            },
            'after': {
                'bindingMode': 'scheduler_bound' if after_job_refs else 'standalone',
                'jobRefs': after_job_refs,
                'targetBindingRefs': after_target_refs,
            },
        },
        'groupChange': group_change,
        'targetChange': target_change,
        'files': summarize_files(effective_repo_root, writes),
    }
