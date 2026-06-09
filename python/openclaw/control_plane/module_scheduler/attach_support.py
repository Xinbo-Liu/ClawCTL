#!/usr/bin/env python3
"""Agent module scheduler attach helper builders."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from openclaw.control_plane.modules.change_set import build_write, read_json_object, summarize_files
from openclaw.control_plane.module_scheduler.group_ops import update_group_for_attach
from openclaw.control_plane.module_scheduler.binding_support import ensure_known_operation, module_binding_refs
from openclaw.control_plane.registry import CliError
from openclaw.lib.io.json_access import json_object


def build_attach_module_write(
    module_payload: dict[str, Any],
    *,
    module_ref: str,
    operation_ref: str,
    job_id: str,
    depends_on: list[str],
    target_binding_ref: str,
    module_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """构建 attach 对 module 的写入变更。"""
    next_module_payload = copy.deepcopy(module_payload)
    next_operation_payload = ensure_known_operation(next_module_payload, module_ref=module_ref, operation_ref=operation_ref)
    next_job_bindings = json_object(next_operation_payload.get('jobBindings'))
    binding_payload: dict[str, Any] = {}
    normalized_depends_on = [str(item).strip() for item in (depends_on or []) if str(item).strip()]
    if normalized_depends_on:
        binding_payload['dependsOn'] = normalized_depends_on
    if target_binding_ref:
        binding_payload['targetBindingRef'] = target_binding_ref
    next_job_bindings[job_id] = binding_payload
    next_operation_payload['jobBindings'] = next_job_bindings
    return next_module_payload, build_write(
        module_path,
        action='update',
        payload=next_module_payload,
        summary=f'注册 operations.{operation_ref}.jobBindings.{job_id}',
    )


def build_attach_group_write(
    registry: dict[str, Any],
    *,
    group_ref: str,
    group_placement: str,
    job_id: str,
    operation_ref: str,
    before_job: str,
    after_job: str,
    recovery_after_minutes: int | None,
    recovery_action_kind: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """构建 attach 对 group 的写入变更。"""
    if not group_ref:
        return None, None
    group_row = json_object(registry.get('agentGroupsById')).get(group_ref)
    if not isinstance(group_row, dict):
        raise CliError(f'未注册的 groupRef：{group_ref}', 2)
    group_path = Path(str(group_row.get('sourcePath') or '')).resolve()
    group_payload = read_json_object(group_path)
    next_group_payload = update_group_for_attach(
        group_payload=group_payload,
        job_id=job_id,
        operation_ref=operation_ref,
        group_placement=group_placement,
        before_job=before_job,
        after_job=after_job,
        recovery_after_minutes=int(recovery_after_minutes or 0),
        recovery_action_kind=str(recovery_action_kind or 'retry').strip() or 'retry',
    )
    group_change: dict[str, Any] = {
        'groupRef': group_ref,
        'placement': group_placement,
    }
    if group_placement == 'recovery':
        group_change['afterMinutes'] = int(recovery_after_minutes or 0)
        group_change['actionKind'] = str(recovery_action_kind or 'retry').strip() or 'retry'
    return build_write(
        group_path,
        action='update',
        payload=next_group_payload,
        summary=f'把 {job_id} 接入 group {group_ref} ({group_placement})',
    ), group_change


def build_attach_target_write(
    registry: dict[str, Any],
    *,
    target_binding_ref: str,
    agent_ref: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """构建 attach 对 target 的写入变更。"""
    if not target_binding_ref:
        return None, None
    target_row = json_object(registry.get('targetsById')).get(target_binding_ref)
    if not isinstance(target_row, dict):
        raise CliError(f'未注册的 targetBindingRef：{target_binding_ref}', 2)
    target_path = Path(str(target_row.get('sourcePath') or '')).resolve()
    target_payload = read_json_object(target_path)
    supported_agents = [str(item).strip() for item in (target_payload.get('supportedAgentRefs') or []) if str(item).strip()]
    if agent_ref in supported_agents:
        return None, {'targetBindingRef': target_binding_ref, 'action': 'already_supported'}
    next_target_payload = copy.deepcopy(target_payload)
    next_supported_agents = list(supported_agents)
    next_supported_agents.append(agent_ref)
    next_target_payload['supportedAgentRefs'] = next_supported_agents
    return build_write(
        target_path,
        action='update',
        payload=next_target_payload,
        summary=f'为 target {target_binding_ref} 增加 supportedAgentRef={agent_ref}',
    ), {'targetBindingRef': target_binding_ref, 'action': 'add_supported_agent'}


def build_attach_plan_payload(
    *,
    effective_repo_root: Path,
    module_ref: str,
    operation_ref: str,
    job_id: str,
    agent_ref: str,
    module_payload: dict[str, Any],
    next_module_payload: dict[str, Any],
    resolved_job_payload: dict[str, Any],
    job_payload: dict[str, Any],
    group_change: dict[str, Any] | None,
    target_change: dict[str, Any] | None,
    writes: list[dict[str, Any]],
) -> dict[str, Any]:
    """构建 attach 计划的展示载荷。"""
    previous_job_refs, previous_target_refs = module_binding_refs(module_payload)
    next_job_refs, next_target_refs = module_binding_refs(next_module_payload)
    return {
        'status': 'ok',
        'mode': 'plan',
        'moduleRef': module_ref,
        'operationRef': operation_ref,
        'jobId': job_id,
        'agentRef': agent_ref,
        'schedulerBinding': {
            'before': {
                'bindingMode': 'scheduler_bound' if previous_job_refs else 'standalone',
                'jobRefs': previous_job_refs,
                'targetBindingRefs': previous_target_refs,
            },
            'after': {
                'bindingMode': 'scheduler_bound' if next_job_refs else 'standalone',
                'jobRefs': next_job_refs,
                'targetBindingRefs': next_target_refs,
            },
        },
        'job': {
            'id': job_id,
            'title': str(resolved_job_payload.get('title') or ''),
            'manifest': dict(job_payload),
            'resolvedSchedule': dict(resolved_job_payload.get('schedule') or {}),
            'resolvedOrder': resolved_job_payload.get('order'),
            'resolvedArtifactPolicy': dict(resolved_job_payload.get('artifactPolicy') or {}),
            'resolvedRetryPolicy': dict(resolved_job_payload.get('retryPolicy') or {}),
            'resolvedModelProfileRef': resolved_job_payload.get('modelProfileRef'),
        },
        'groupChange': group_change,
        'targetChange': target_change,
        'files': summarize_files(effective_repo_root, writes),
    }
