#!/usr/bin/env python3
"""Canonical job execution plans for scheduler-bound control-plane jobs."""
from __future__ import annotations

import sys
from typing import Any

from openclaw.control_plane.registry.command_specs import (
    DIRECT_CONTROL_PLANE_EXEC,
    SUPPORTED_EXEC_MODES,
    OpenClawCommandSpec,
    build_agent_runtime_command_spec,
    build_command_spec,
    materialize_command,
)
from openclaw.lib.cli.common import CliError
from openclaw.lib.io.json_access import json_array, json_object


SUBPROCESS_EXEC = 'subprocess_exec'
RUNNER_EXEC = 'runner_exec'
SUPPORTED_EXECUTION_PLAN_KINDS = frozenset({SUBPROCESS_EXEC, RUNNER_EXEC})


def _plan_label(job_id: str = '') -> str:
    normalized_job_id = str(job_id or '').strip()
    if normalized_job_id:
        return f'job {normalized_job_id} resolvedExecutionPlan'
    return 'resolvedExecutionPlan'


def _validated_command_spec_payload(payload: dict[str, Any], *, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or not payload:
        raise CliError(f'{label} 缺失', 2)
    exec_mode = str(payload.get('execMode') or '').strip()
    if exec_mode not in SUPPORTED_EXEC_MODES:
        raise CliError(f'{label}.execMode 非法：{exec_mode or "<empty>"}', 2)
    argv = [str(item) for item in json_array(payload.get('argv')) if str(item).strip()]
    if not argv:
        raise CliError(f'{label}.argv 不能为空', 2)
    return {
        'execMode': exec_mode,
        'argv': argv,
    }


def validate_execution_plan(plan: dict[str, Any], *, job_id: str = '') -> dict[str, Any]:
    label = _plan_label(job_id)
    if not isinstance(plan, dict) or not plan:
        raise CliError(f'{label} 缺失', 2)
    if plan.get('schemaVersion') != 1:
        raise CliError(f'{label}.schemaVersion 必须为 1', 2)
    kind = str(plan.get('kind') or '').strip()
    if kind not in SUPPORTED_EXECUTION_PLAN_KINDS:
        raise CliError(f'{label}.kind 非法：{kind or "<empty>"}', 2)
    runner_ref = str(plan.get('runnerRef') or '').strip()
    if not runner_ref:
        raise CliError(f'{label}.runnerRef 不能为空', 2)
    if kind == SUBPROCESS_EXEC:
        command_spec = json_object(plan.get('commandSpec'))
        materialized = [str(item) for item in json_array(plan.get('materializedCommand')) if str(item).strip()]
        if command_spec:
            _validated_command_spec_payload(command_spec, label=f'{label}.commandSpec')
        elif not materialized:
            raise CliError(f'{label} 缺少可执行 commandSpec', 2)
    return plan


def command_spec_payload(spec: OpenClawCommandSpec) -> dict[str, Any]:
    return {
        'execMode': spec.exec_mode,
        'argv': list(spec.argv),
    }


def command_spec_from_payload(payload: dict[str, Any]) -> OpenClawCommandSpec:
    validated = _validated_command_spec_payload(payload, label='commandSpec')
    return build_command_spec(
        *validated['argv'],
        exec_mode=str(validated.get('execMode') or DIRECT_CONTROL_PLANE_EXEC),
    )


def materialized_command_from_execution_plan(
    plan: dict[str, Any],
    *,
    python_executable: str | None = None,
) -> list[str]:
    validated_plan = validate_execution_plan(plan)
    if str(validated_plan.get('kind') or '').strip() != SUBPROCESS_EXEC:
        return []
    command_spec = json_object(validated_plan.get('commandSpec'))
    if command_spec:
        return materialize_command(
            command_spec_from_payload(command_spec),
            python_executable=python_executable or sys.executable or 'python3',
        )
    materialized = json_array(validated_plan.get('materializedCommand'))
    if materialized:
        return [str(item) for item in materialized]
    return []


def execution_plan_public_payload(plan: dict[str, Any]) -> dict[str, Any]:
    validated_plan = validate_execution_plan(plan)
    return {
        key: value
        for key, value in validated_plan.items()
        if key != 'materializedCommand'
    }


def _executor_payload(executor: dict[str, Any]) -> dict[str, Any]:
    payload = {
        'kind': str(executor.get('kind') or '').strip(),
        'argv': [str(item) for item in json_array(executor.get('argv'))],
    }
    operation = str(executor.get('operation') or '').strip()
    if operation:
        payload['operation'] = operation
    return payload


def build_bound_job_execution_plan(
    *,
    job: dict[str, Any],
    runner_ref: str,
    agent: dict[str, Any],
    module: dict[str, Any],
    operation_ref: str,
    executor: dict[str, Any],
    target: dict[str, Any] | None = None,
    target_binding_ref: str = '',
    config_path: str = '',
) -> dict[str, Any]:
    job_id = str(job.get('id') or '').strip()
    executor_kind = str(executor.get('kind') or '').strip()
    raw_args = [str(item) for item in json_array(executor.get('argv'))]
    command_args = list(raw_args)
    operation_name = ''
    if executor_kind == 'delivery_adapter':
        operation_name = str(executor.get('operation') or '').strip()
        if not operation_name:
            raise CliError(f'job {job_id} 的 delivery_adapter executor 缺少 operation', 2)
        if not isinstance(target, dict):
            raise CliError(f'job {job_id} 缺少 targetBindingRef，无法解析 delivery_adapter operation', 2)
        operations = json_object(target.get('resolvedOperations'))
        operation_args = operations.get(operation_name)
        if not isinstance(operation_args, list):
            raise CliError(f'target {target.get("id")} 未注册 operation：{operation_name}', 2)
        command_args = [str(item) for item in operation_args] + raw_args

    spec = build_agent_runtime_command_spec(
        agent=agent,
        config_path=config_path,
        extra_args=command_args,
        exec_mode=DIRECT_CONTROL_PLANE_EXEC,
    )
    return {
        'schemaVersion': 1,
        'kind': SUBPROCESS_EXEC,
        'runnerRef': str(runner_ref or '').strip(),
        'agentRef': str(agent.get('id') or '').strip(),
        'resolvedAgentRef': str(agent.get('qualifiedId') or agent.get('id') or '').strip(),
        'moduleRef': str(module.get('id') or '').strip(),
        'resolvedModuleRef': str(module.get('qualifiedId') or module.get('id') or '').strip(),
        'operationRef': str(operation_ref or '').strip(),
        'executor': _executor_payload(executor),
        'targetBindingRef': str(target_binding_ref or '').strip(),
        'execMode': spec.exec_mode,
        'commandSpec': command_spec_payload(spec),
        'materializedCommand': materialize_command(spec, python_executable=sys.executable or 'python3'),
        'source': f'module:{agent.get("id")}.{operation_ref}',
    }


def build_runner_execution_plan(*, runner_ref: str) -> dict[str, Any]:
    return {
        'schemaVersion': 1,
        'kind': RUNNER_EXEC,
        'runnerRef': str(runner_ref or '').strip(),
    }


def execution_plan_from_job(job: dict[str, Any]) -> dict[str, Any]:
    job_id = str(job.get('id') or '').strip()
    if 'resolvedExecutionPlan' in job:
        plan = json_object(job.get('resolvedExecutionPlan'))
        return validate_execution_plan(plan, job_id=job_id)
    runner_ref = str(job.get('resolvedRunnerRef') or job.get('runnerRef') or '').strip()
    if runner_ref:
        return validate_execution_plan(build_runner_execution_plan(runner_ref=runner_ref), job_id=job_id)
    raise CliError(f'job {job_id or job.get("id")} 缺少 resolvedExecutionPlan', 2)
