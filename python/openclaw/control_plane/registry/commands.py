#!/usr/bin/env python3
"""控制平面 registry 消费侧的命令解析辅助。"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from openclaw.control_plane.manifest_fields import (
    DISPATCH_PROVIDER_REGISTRY_PATHS_KEY,
    DISPATCH_TARGET_REGISTRY_PATHS_KEY,
)
from openclaw.control_plane.registry.command_specs import (
    DIRECT_CONTROL_PLANE_EXEC,
    OpenClawCommandSpec,
    SCHEDULER_SERVICE_EXEC,
    build_agent_runtime_command_spec as build_agent_runtime_command_spec_impl,
    build_command_spec,
    materialize_command,
    runtime_passthrough_args,
)
from openclaw.control_plane.registry.owners import resolve_collection_ref
from openclaw.control_plane.registry.job_execution_plans import (
    RUNNER_EXEC,
    execution_plan_from_job,
    materialized_command_from_execution_plan,
)
from openclaw.control_plane.registry.support import _ensure_unique_text_list
from openclaw.lib.cli.common import CliError
from openclaw.lib.dispatch.target_registry import load_dispatch_registry
from openclaw.lib.io.json_access import json_array, json_object
from openclaw.lib.runtime.execution import import_callable


def _strip_public_target_selector_args(
    extra_args: list[str] | None,
    *,
    dispatch_target_id: str,
    target_binding_ref: str,
) -> list[str]:
    normalized = [str(item) for item in list(extra_args or []) if str(item).strip()]
    if not normalized:
        return []
    if dispatch_target_id and len(normalized) >= 2 and normalized[:2] == ['--target', dispatch_target_id]:
        return normalized[2:]
    if dispatch_target_id and normalized[0] == f'--target={dispatch_target_id}':
        return normalized[1:]
    if target_binding_ref and len(normalized) >= 2 and normalized[:2] == ['--target-binding-ref', target_binding_ref]:
        return normalized[2:]
    if target_binding_ref and normalized[0] == f'--target-binding-ref={target_binding_ref}':
        return normalized[1:]
    return normalized


def build_agent_runtime_command_spec(
    *,
    agent: dict[str, Any],
    extra_args: list[str] | None = None,
    config_path: str | None = None,
    exec_mode: str = DIRECT_CONTROL_PLANE_EXEC,
) -> OpenClawCommandSpec:
    return build_agent_runtime_command_spec_impl(
        agent=agent,
        extra_args=extra_args,
        config_path=config_path,
        exec_mode=exec_mode,
    )


def build_agent_runtime_command(
    *,
    agent: dict[str, Any],
    extra_args: list[str] | None = None,
    config_path: str | None = None,
    exec_mode: str = DIRECT_CONTROL_PLANE_EXEC,
) -> list[str]:
    return materialize_command(
        build_agent_runtime_command_spec(
            agent=agent,
            extra_args=extra_args,
            config_path=config_path,
            exec_mode=exec_mode,
        ),
        python_executable=sys.executable or 'python3',
    )


def _owned_collection_index(registry: dict[str, Any], collection_key: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    qualified = registry.get(f'{collection_key}ByQualifiedId')
    if isinstance(qualified, dict):
        result.update({str(key): value for key, value in qualified.items() if isinstance(value, dict)})
    by_local_id = registry.get(f'{collection_key}ById')
    if isinstance(by_local_id, dict):
        result.update({str(key): value for key, value in by_local_id.items() if isinstance(value, dict)})
    return result


def _target_runtime_ref(target: dict[str, Any]) -> str:
    return str(target.get('qualifiedId') or target.get('id') or '').strip()


def resolve_target_operation_command_spec(
    registry: dict[str, Any],
    *,
    target_binding_ref: str | None = None,
    dispatch_target_id: str | None = None,
    operation: str,
    extra_args: list[str] | None = None,
    agent_ref: str | None = None,
    exec_mode: str = DIRECT_CONTROL_PLANE_EXEC,
) -> OpenClawCommandSpec:
    resolved_target_binding_ref = resolve_target_binding_ref_for_operation(
        registry,
        target_binding_ref=target_binding_ref,
        dispatch_target_id=dispatch_target_id,
        operation=operation,
        agent_ref=agent_ref,
    )
    if exec_mode == SCHEDULER_SERVICE_EXEC:
        command = [
            'dispatch',
            'ops',
            'run-target-operation',
            '--operation',
            str(operation or '').strip(),
        ]
        normalized_dispatch_target_id = str(dispatch_target_id or '').strip()
        normalized_target_binding_ref = str(target_binding_ref or '').strip() or resolved_target_binding_ref
        if normalized_dispatch_target_id:
            command.extend(['--target', normalized_dispatch_target_id])
        else:
            command.extend(['--target-binding-ref', normalized_target_binding_ref])
        normalized_config_path = str(registry.get('configPath') or '').strip()
        if normalized_config_path:
            command.extend(['--config-path', normalized_config_path])
        command.extend(
            runtime_passthrough_args(
                _strip_public_target_selector_args(
                    extra_args,
                    dispatch_target_id=normalized_dispatch_target_id,
                    target_binding_ref=normalized_target_binding_ref,
                )
            )
        )
        return build_command_spec(*command, exec_mode=exec_mode)
    targets_by_ref = _owned_collection_index(registry, 'targets')
    target = targets_by_ref.get(resolved_target_binding_ref)
    if not isinstance(target, dict):
        raise CliError(f'未注册的 targetBindingRef：{resolved_target_binding_ref}', 2)
    return resolve_target_operation_command_spec_from_target(
        registry,
        target=target,
        operation=operation,
        extra_args=extra_args or [],
        agent_ref=agent_ref,
        exec_mode=exec_mode,
    )


def resolve_target_operation_command(
    registry: dict[str, Any],
    *,
    target_binding_ref: str | None = None,
    dispatch_target_id: str | None = None,
    operation: str,
    extra_args: list[str] | None = None,
    agent_ref: str | None = None,
    exec_mode: str = DIRECT_CONTROL_PLANE_EXEC,
) -> list[str]:
    return materialize_command(
        resolve_target_operation_command_spec(
            registry,
            target_binding_ref=target_binding_ref,
            dispatch_target_id=dispatch_target_id,
            operation=operation,
            extra_args=extra_args,
            agent_ref=agent_ref,
            exec_mode=exec_mode,
        ),
        python_executable=sys.executable or 'python3',
    )


def _dispatch_target_rows(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    registry_paths = json_object(registry.get('registryPaths'))
    target_paths = [Path(str(item)).resolve() for item in json_array(registry_paths.get(DISPATCH_TARGET_REGISTRY_PATHS_KEY)) if str(item).strip()]
    if not target_paths:
        raise CliError('当前 profile 未启用 dispatch target registry', 2)
    provider_paths = [Path(str(item)).resolve() for item in json_array(registry_paths.get(DISPATCH_PROVIDER_REGISTRY_PATHS_KEY)) if str(item).strip()]
    payload = load_dispatch_registry(target_paths, provider_registry_path=provider_paths or None)
    return {
        str(row.get('id') or '').strip(): row
        for row in json_array(payload.get('targets'))
        if isinstance(row, dict) and str(row.get('id') or '').strip()
    }


def _target_supports_dispatch_agent(registry: dict[str, Any], target: dict[str, Any], *, agent_ref: str | None = None) -> bool:
    agents_by_id = _owned_collection_index(registry, 'agents')
    supported_agents = _ensure_unique_text_list(target.get('supportedAgentRefs'), label=f'target {target.get("id")} supportedAgentRefs')
    normalized_agent_ref = str(agent_ref or '').strip()
    if normalized_agent_ref:
        if normalized_agent_ref in supported_agents:
            return True
        try:
            agent = resolve_collection_ref(registry, 'agents', normalized_agent_ref, label='agentRef')
        except CliError:
            return False
        return str(agent.get('id') or '').strip() in supported_agents or str(agent.get('qualifiedId') or '').strip() in supported_agents
    for supported_agent in supported_agents:
        agent = agents_by_id.get(supported_agent)
        if isinstance(agent, dict) and bool(json_object(agent.get('capabilities')).get('externalDispatch')):
            return True
    return False


def _target_supports_agent_ref(registry: dict[str, Any], target: dict[str, Any], *, agent_ref: str | None = None) -> bool:
    normalized_agent_ref = str(agent_ref or '').strip()
    if not normalized_agent_ref:
        return True
    supported_agents = _ensure_unique_text_list(target.get('supportedAgentRefs'), label=f'target {target.get("id")} supportedAgentRefs')
    if normalized_agent_ref in supported_agents:
        return True
    try:
        agent = resolve_collection_ref(registry, 'agents', normalized_agent_ref, label='agentRef')
    except CliError:
        return False
    return str(agent.get('id') or '').strip() in supported_agents or str(agent.get('qualifiedId') or '').strip() in supported_agents


def resolve_dispatch_target_binding_ref(
    registry: dict[str, Any],
    *,
    dispatch_target_id: str,
    operation: str,
    agent_ref: str | None = None,
    preferred_target_binding_ref: str | None = None,
) -> str:
    normalized_target_id = str(dispatch_target_id or '').strip()
    if not normalized_target_id:
        raise CliError('dispatch target id 不能为空', 2)
    normalized_operation = str(operation or '').strip()
    if not normalized_operation:
        raise CliError('dispatch target operation 不能为空', 2)
    direct_target: dict[str, Any] | None = None
    try:
        direct_candidate = resolve_collection_ref(registry, 'targets', normalized_target_id, label='targetBindingRef')
    except CliError:
        direct_candidate = None
    if isinstance(direct_candidate, dict):
        direct_target = direct_candidate
    if isinstance(direct_target, dict):
        operations = json_object(direct_target.get('resolvedOperations'))
        if normalized_operation in operations and _target_supports_dispatch_agent(registry, direct_target, agent_ref=agent_ref):
            return _target_runtime_ref(direct_target)
    dispatch_target = _dispatch_target_rows(registry).get(normalized_target_id)
    if not isinstance(dispatch_target, dict):
        raise CliError(f'未注册的 dispatch target：{normalized_target_id}', 2)
    dispatch_provider = str(dispatch_target.get('provider') or '').strip()
    dispatch_transport = str(dispatch_target.get('transport') or '').strip()
    candidates: list[str] = []
    for target in registry.get('targets', []):
        if not isinstance(target, dict):
            continue
        target_binding_ref = _target_runtime_ref(target)
        operations = json_object(target.get('resolvedOperations'))
        if normalized_operation not in operations:
            continue
        if str(target.get('provider') or '').strip() != dispatch_provider:
            continue
        if str(target.get('transport') or '').strip() != dispatch_transport:
            continue
        if not _target_supports_dispatch_agent(registry, target, agent_ref=agent_ref):
            continue
        candidates.append(str(target_binding_ref).strip())
    preferred_ref = str(preferred_target_binding_ref or '').strip()
    if preferred_ref:
        try:
            preferred_target = resolve_collection_ref(registry, 'targets', preferred_ref, label='targetBindingRef')
        except CliError as exc:
            raise CliError(f'dispatch target {normalized_target_id} 未命中指定 targetBindingRef：{preferred_ref}', 2) from exc
        preferred_runtime_ref = _target_runtime_ref(preferred_target)
        if preferred_runtime_ref in candidates:
            return preferred_runtime_ref
        raise CliError(f'dispatch target {normalized_target_id} 未命中指定 targetBindingRef：{preferred_ref}', 2)
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise CliError(f'dispatch target {normalized_target_id} 无法解析唯一 targetBindingRef；operation={normalized_operation}', 2)
    raise CliError(
        f'dispatch target {normalized_target_id} 命中多个 targetBindingRef：{", ".join(sorted(candidates))}；请显式指定',
        2,
    )


def resolve_target_binding_ref_for_operation(
    registry: dict[str, Any],
    *,
    operation: str,
    target_binding_ref: str | None = None,
    dispatch_target_id: str | None = None,
    agent_ref: str | None = None,
) -> str:
    normalized_operation = str(operation or '').strip()
    if not normalized_operation:
        raise CliError('target operation 不能为空', 2)
    normalized_target_binding_ref = str(target_binding_ref or '').strip()
    normalized_dispatch_target_id = str(dispatch_target_id or '').strip()
    if normalized_target_binding_ref and normalized_dispatch_target_id:
        raise CliError('--target-binding-ref 与 --dispatch-target-id 不能同时指定', 2)
    if normalized_target_binding_ref:
        target = resolve_collection_ref(registry, 'targets', normalized_target_binding_ref, label='targetBindingRef')
        if not isinstance(target, dict):
            raise CliError(f'未注册的 targetBindingRef：{normalized_target_binding_ref}', 2)
        operations = json_object(target.get('resolvedOperations'))
        if normalized_operation not in operations:
            raise CliError(
                f'targetBindingRef {normalized_target_binding_ref} 未注册 operation：{normalized_operation}',
                2,
            )
        if not _target_supports_agent_ref(registry, target, agent_ref=agent_ref):
            normalized_agent_ref = str(agent_ref or '').strip()
            raise CliError(
                f'targetBindingRef {normalized_target_binding_ref} 不支持 agentRef：{normalized_agent_ref or "<empty>"}',
                2,
            )
        return _target_runtime_ref(target)
    if normalized_dispatch_target_id:
        return resolve_dispatch_target_binding_ref(
            registry,
            dispatch_target_id=normalized_dispatch_target_id,
            operation=normalized_operation,
            agent_ref=agent_ref,
        )
    candidates: list[str] = []
    for target in registry.get('targets', []):
        if not isinstance(target, dict):
            continue
        candidate_target_binding_ref = _target_runtime_ref(target)
        operations = json_object(target.get('resolvedOperations'))
        if normalized_operation not in operations:
            continue
        if not _target_supports_agent_ref(registry, target, agent_ref=agent_ref):
            continue
        candidates.append(str(candidate_target_binding_ref).strip())
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise CliError(
            f'未找到支持 operation={normalized_operation} 的 targetBindingRef，请显式传入 --target-binding-ref 或 --dispatch-target-id',
            2,
        )
    raise CliError(
        f'找到多个支持 operation={normalized_operation} 的 targetBindingRef：{", ".join(sorted(candidates))}；请显式指定',
        2,
    )


def resolve_dispatch_target_operation_command(
    registry: dict[str, Any],
    *,
    dispatch_target_id: str,
    operation: str,
    extra_args: list[str] | None = None,
    agent_ref: str | None = None,
    preferred_target_binding_ref: str | None = None,
    exec_mode: str = DIRECT_CONTROL_PLANE_EXEC,
) -> list[str]:
    if exec_mode == SCHEDULER_SERVICE_EXEC:
        return materialize_command(
            resolve_target_operation_command_spec(
                registry,
                dispatch_target_id=dispatch_target_id,
                operation=operation,
                extra_args=extra_args or [],
                agent_ref=agent_ref,
                exec_mode=exec_mode,
            ),
            python_executable=sys.executable or 'python3',
        )
    target_binding_ref = resolve_dispatch_target_binding_ref(
        registry,
        dispatch_target_id=dispatch_target_id,
        operation=operation,
        agent_ref=agent_ref,
        preferred_target_binding_ref=preferred_target_binding_ref,
    )
    return resolve_target_operation_command(
        registry,
        target_binding_ref=target_binding_ref,
        operation=operation,
        extra_args=extra_args or [],
        agent_ref=agent_ref,
        exec_mode=exec_mode,
    )


def resolve_target_operation_command_spec_from_target(
    registry: dict[str, Any],
    *,
    target: dict[str, Any],
    operation: str,
    extra_args: list[str] | None = None,
    agent_ref: str | None = None,
    exec_mode: str = DIRECT_CONTROL_PLANE_EXEC,
) -> OpenClawCommandSpec:
    op_name = str(operation or '').strip()
    if not op_name:
        raise CliError(f'target {target.get("id")} 缺少 operation', 2)
    operations = json_object(target.get('resolvedOperations'))
    if op_name not in operations:
        raise CliError(f'target {target.get("id")} 未注册 operation：{op_name}', 2)
    supported_agents = _ensure_unique_text_list(target.get('supportedAgentRefs'), label=f'target {target.get("id")} supportedAgentRefs')
    selected_agent_ref = str(agent_ref or '').strip()
    if selected_agent_ref:
        if selected_agent_ref not in supported_agents:
            raise CliError(f'target {target.get("id")} 不支持 agentRef：{selected_agent_ref}', 2)
    else:
        if len(supported_agents) != 1:
            raise CliError(f'target {target.get("id")} 支持多个 agentRef，必须显式指定 agent_ref', 2)
        selected_agent_ref = supported_agents[0]
    agents_by_id = _owned_collection_index(registry, 'agents')
    agent = agents_by_id.get(selected_agent_ref)
    if not isinstance(agent, dict):
        try:
            agent = resolve_collection_ref(registry, 'agents', selected_agent_ref, label='agentRef')
        except CliError:
            agent = None
    if not isinstance(agent, dict):
        raise CliError(f'未注册的 agentRef：{selected_agent_ref}', 2)
    operation_args = operations.get(op_name)
    if not isinstance(operation_args, list):
        raise CliError(f'target {target.get("id")} operation={op_name} 参数格式无效', 2)
    return build_agent_runtime_command_spec(
        agent=agent,
        extra_args=[*operation_args, *(extra_args or [])],
        config_path=str(registry.get('configPath') or target.get('configPath') or agent.get('configPath') or ''),
        exec_mode=exec_mode,
    )


def resolve_target_operation_command_from_target(
    registry: dict[str, Any],
    *,
    target: dict[str, Any],
    operation: str,
    extra_args: list[str] | None = None,
    agent_ref: str | None = None,
    exec_mode: str = DIRECT_CONTROL_PLANE_EXEC,
) -> list[str]:
    return materialize_command(
        resolve_target_operation_command_spec_from_target(
            registry,
            target=target,
            operation=operation,
            extra_args=extra_args,
            agent_ref=agent_ref,
            exec_mode=exec_mode,
        ),
        python_executable=sys.executable or 'python3',
    )


def resolve_job_command(registry: dict[str, Any], job_id: str) -> list[str]:
    plan = resolve_job_execution_plan(registry, job_id)
    command = materialized_command_from_execution_plan(plan, python_executable=sys.executable or 'python3')
    if command:
        return command
    if str(plan.get('kind') or '').strip() != RUNNER_EXEC:
        raise CliError(f'job {job_id} execution plan 未返回 command', 2)
    job = resolve_collection_ref(registry, 'jobs', job_id, label='job')
    runner_ref = str(plan.get('runnerRef') or '').strip()
    if not runner_ref:
        raise CliError(f'job {job_id} 缺少 runnerRef', 2)
    job_runners_by_id = json_object(registry.get('jobRunnersById'))
    runner = job_runners_by_id.get(runner_ref)
    if not isinstance(runner, dict):
        raise CliError(f'job {job_id} 引用了不存在的 runnerRef：{runner_ref}', 2)
    module_name = str(runner.get('module') or '').strip()
    if not module_name:
        raise CliError(f'job {job_id} runner {runner_ref} 缺少 module', 2)
    try:
        planner = import_callable(module_name, 'build_execution_plan', CliError, f'job {job_id} runner {runner_ref}')
    except CliError as exc:
        if f'缺少可调用成员：{module_name}.build_execution_plan' in str(exc):
            raise CliError(f'job {job_id} runner {runner_ref} 缺少 build_execution_plan()', 2) from exc
        raise
    try:
        plan = planner(job=job, config=registry)
    except Exception as exc:  # pragma: no cover - planner fault collapses to user-facing CLI error
        # runner planner 属于外部可插拔实现，失败时折叠成 job 级 CLI 诊断。
        raise CliError(f'job {job_id} runner {runner_ref} build_execution_plan() 失败：{exc}', 2) from exc
    if not isinstance(plan, dict):
        raise CliError(f'job {job_id} runner {runner_ref} build_execution_plan() 必须返回对象', 2)
    planned_command = json_array(plan.get('command'))
    if not planned_command:
        raise CliError(f'job {job_id} runner {runner_ref} build_execution_plan() 未返回 command', 2)
    return [str(item) for item in planned_command]


def resolve_job_execution_plan(registry: dict[str, Any], job_id: str) -> dict[str, Any]:
    job = resolve_collection_ref(registry, 'jobs', job_id, label='job')
    return execution_plan_from_job(job)
