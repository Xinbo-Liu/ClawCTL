#!/usr/bin/env python3
"""Job-level runtime validation helpers for the control-plane registry."""
from __future__ import annotations

from typing import Any

from openclaw.control_plane.jobs.defaults import agent_capabilities
from openclaw.control_plane.registry.owners import qualified_registry_id, resolve_collection_ref, row_owner_id
from openclaw.control_plane.registry.binding_topology import _infer_binding_runner_ref
from openclaw.control_plane.registry.job_execution_plans import (
    build_bound_job_execution_plan,
    build_runner_execution_plan,
)
from openclaw.control_plane.registry_validation.runtime_policy import (
    _normalize_generic_job_runtime_policy,
    _normalize_job_runtime_policy,
    _normalize_job_schedule,
    _resolve_model_ref,
    _resolved_group_dependencies,
    _resolved_job_order,
)
from openclaw.control_plane.registry.support import (
    _ensure_unique_text_list,
    _normalize_executor_contract,
    _normalized_dependencies,
    _validate_resolved_dependencies,
)
from openclaw.lib.cli.common import CliError
from openclaw.lib.io.json_access import json_object


def _lookup_owner_scoped_row(
    index: dict[str, dict[str, Any]],
    ref: str,
    *,
    owner_id: str,
) -> tuple[str, dict[str, Any] | None]:
    normalized_ref = str(ref or '').strip()
    if not normalized_ref:
        return '', None
    scoped_ref = normalized_ref if ':' in normalized_ref else qualified_registry_id(owner_id, normalized_ref)
    row = index.get(scoped_ref)
    if isinstance(row, dict):
        return str(row.get('qualifiedId') or scoped_ref), row
    row = index.get(normalized_ref)
    if isinstance(row, dict):
        return str(row.get('qualifiedId') or qualified_registry_id(row_owner_id(row), row.get('id'))), row
    return scoped_ref, None


def _precompute_job_orders(
    jobs: list[dict[str, Any]],
    *,
    groups_by_id: dict[str, dict[str, Any]],
    job_bindings_by_job_id: dict[str, dict[str, Any]],
) -> dict[str, int]:
    resolved_orders: dict[str, int] = {}
    seen_orders: set[tuple[str, int]] = set()
    for job in jobs:
        job_id = str(job.get('id') or '')
        job_key = str(job.get('qualifiedId') or qualified_registry_id(row_owner_id(job), job_id))
        job['resolvedRuntimeJobKey'] = job_key
        binding = job_bindings_by_job_id.get(job_key) or job_bindings_by_job_id.get(job_id)
        derived_group_ref = str(binding.get('groupRef') or '').strip() if isinstance(binding, dict) else ''
        derived_group_qualified_ref = str(binding.get('resolvedGroupRef') or '').strip() if isinstance(binding, dict) else ''
        declared_group_ref = str(job.get('groupRef') or '').strip()
        if derived_group_ref and declared_group_ref and declared_group_ref != derived_group_ref:
            raise CliError(f'job {job_id} groupRef must match bound groupRef {derived_group_ref}', 2)
        if derived_group_ref:
            job['groupRef'] = derived_group_ref
            if derived_group_qualified_ref:
                job['resolvedGroupRef'] = derived_group_qualified_ref
        else:
            job.pop('groupRef', None)
            job.pop('resolvedGroupRef', None)
        order = _resolved_job_order(job, groups_by_id)
        if not derived_group_ref and declared_group_ref:
            job['groupRef'] = declared_group_ref
        order_key = (row_owner_id(job), order)
        if order_key in seen_orders:
            raise CliError(f'duplicate resolved job order {order_key[0]}:{order}', 2)
        seen_orders.add(order_key)
        resolved_orders[job_key] = order
        resolved_orders.setdefault(job_id, order)
        job['resolvedOrder'] = order
    return resolved_orders


def _resolve_job_runner(
    job: dict[str, Any],
    binding: dict[str, Any] | None,
    *,
    job_runners_by_id: dict[str, dict[str, Any]],
    binding_runner_ids: list[str],
) -> tuple[str, dict[str, Any]]:
    job_id = str(job.get('id') or '')
    runner_ref = str(job.get('runnerRef') or '').strip()
    if not runner_ref and isinstance(binding, dict):
        runner_ref = _infer_binding_runner_ref(
            job_id=job_id,
            binding=binding,
            job_runners_by_id=job_runners_by_id,
            binding_runner_ids=binding_runner_ids,
        )
    if not runner_ref:
        raise CliError(f'job {job_id} is missing runnerRef', 2)
    runner = job_runners_by_id.get(runner_ref)
    if not isinstance(runner, dict):
        raise CliError(f'job {job_id} references unknown runnerRef {runner_ref}', 2)
    return runner_ref, dict(runner)


def _materialize_job_runner(
    job: dict[str, Any],
    *,
    runner_ref: str,
    runner: dict[str, Any],
) -> None:
    job['runnerRef'] = runner_ref
    job['resolvedRunnerRef'] = runner_ref
    job['resolvedRunner'] = dict(runner)


def _validate_unbound_job(
    job: dict[str, Any],
    *,
    job_id: str,
    order: int,
    models_by_id: dict[str, dict[str, Any]],
    jobs_by_id: dict[str, dict[str, Any]],
    resolved_orders: dict[str, int],
    default_timezone: str,
) -> None:
    if str(job.get('groupRef') or '').strip():
        raise CliError(f'job {job_id} cannot declare groupRef without module binding', 2)
    _normalize_job_schedule(job, default_timezone=default_timezone)
    _normalize_generic_job_runtime_policy(job)
    model_ref = str(job.get('modelProfileRef') or '').strip()
    resolved_model_ref, model_row = _lookup_owner_scoped_row(models_by_id, model_ref, owner_id=row_owner_id(job))
    if model_ref and not isinstance(model_row, dict):
        raise CliError(f'job {job_id} references unknown modelProfileRef {model_ref}', 2)
    dependencies = _normalized_dependencies(job)
    _validate_resolved_dependencies(
        job_id=job_id,
        order=order,
        dependencies=dependencies,
        jobs_by_id=jobs_by_id,
        resolved_orders=resolved_orders,
    )
    job['resolvedModelProfileRef'] = str(model_row.get('id') or model_ref) if isinstance(model_row, dict) else model_ref
    if resolved_model_ref:
        job['resolvedModelProfileQualifiedRef'] = resolved_model_ref
    job['resolvedContract'] = {}
    job['resolvedInputs'] = {}
    job['resolvedOutputs'] = {}
    job['resolvedRecoveryStep'] = {}
    job['dependsOn'] = [dict(item) for item in dependencies]
    job['resolvedDependsOn'] = dependencies
    job['normalizedDependsOn'] = dependencies
    job['resolvedExecutionPlan'] = build_runner_execution_plan(runner_ref=str(job.get('resolvedRunnerRef') or job.get('runnerRef') or ''))
    job.pop('resolvedCommand', None)
    job.pop('resolvedExecutor', None)
    job.pop('resolvedOperationRef', None)


def _resolve_bound_job_context(
    job: dict[str, Any],
    binding: dict[str, Any],
    *,
    agents_by_id: dict[str, dict[str, Any]],
    modules_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    job_id = str(job.get('id') or '')
    job_owner_id = row_owner_id(job)
    derived_agent_ref = str(binding.get('agentRef') or '').strip()
    derived_agent_qualified_ref = str(binding.get('resolvedAgentRef') or '').strip()
    declared_agent_ref = str(job.get('agentRef') or '').strip()
    if declared_agent_ref and declared_agent_ref != derived_agent_ref:
        raise CliError(f'job {job_id} agentRef must match bound agentRef {derived_agent_ref}', 2)
    agent_lookup_ref = derived_agent_qualified_ref or derived_agent_ref
    if not derived_agent_ref or agent_lookup_ref not in agents_by_id:
        raise CliError(f'job {job_id} references unknown bound agentRef {derived_agent_ref}', 2)
    job['agentRef'] = derived_agent_ref
    if derived_agent_qualified_ref:
        job['resolvedAgentQualifiedRef'] = derived_agent_qualified_ref
    agent = agents_by_id[agent_lookup_ref]
    module_lookup_ref = str(binding.get('resolvedModuleRef') or binding.get('moduleRef') or derived_agent_qualified_ref or derived_agent_ref).strip()
    module = modules_by_id.get(module_lookup_ref)
    if not isinstance(module, dict):
        raise CliError(f'job {job_id} agent {derived_agent_ref} is missing registered module', 2)
    derived_operation_ref = str(binding.get('operationRef') or '').strip()
    declared_operation_ref = str(job.get('operationRef') or '').strip()
    if declared_operation_ref and declared_operation_ref != derived_operation_ref:
        raise CliError(f'job {job_id} operationRef must match bound operationRef {derived_operation_ref}', 2)
    if not derived_operation_ref:
        raise CliError(f'job {job_id} operationRef cannot be empty', 2)
    job['operationRef'] = derived_operation_ref
    module_operations = json_object(module.get('resolvedOperations'))
    operation_payload = module_operations.get(derived_operation_ref)
    if not isinstance(operation_payload, dict):
        raise CliError(
            f'job {job_id} operationRef {derived_operation_ref} is not registered in module {derived_agent_ref}',
            2,
        )
    op_executor = json_object(operation_payload.get('executor'))
    executor = _normalize_executor_contract(
        op_executor,
        label=f'module {derived_agent_ref} operations.{derived_operation_ref}.executor',
    )
    executor_kind = str(executor.get('kind') or '').strip()
    allowed_executor_kinds = _ensure_unique_text_list(
        agent.get('allowedExecutorKinds'),
        label=f'agent {derived_agent_ref} allowedExecutorKinds',
    )
    if executor_kind not in allowed_executor_kinds:
        raise CliError(
            f'module {derived_agent_ref} operation {derived_operation_ref} executor kind {executor_kind} '
            f'is not allowed for agent {derived_agent_ref}',
            2,
        )
    resolved_contract = json_object(module.get('resolvedContract'))
    if not resolved_contract:
        raise CliError(f'job {job_id} module {derived_agent_ref} is missing resolvedContract', 2)
    op_job_refs = _ensure_unique_text_list(
        operation_payload.get('jobRefs') or [],
        label=f'module {derived_agent_ref} operations.{derived_operation_ref}.jobRefs',
    )
    if job_id not in op_job_refs:
        raise CliError(
            f'job {job_id} must appear in module {derived_agent_ref} operation {derived_operation_ref} jobRefs',
            2,
        )
    return {
        'agentRef': derived_agent_ref,
        'agent': agent,
        'module': module,
        'operationRef': derived_operation_ref,
        'operationPayload': operation_payload,
        'executor': executor,
        'resolvedContract': resolved_contract,
    }


def _resolve_job_group_context(
    job: dict[str, Any],
    *,
    job_id: str,
    agent_ref: str,
    groups_by_id: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    group_ref = str(job.get('resolvedGroupRef') or job.get('groupRef') or '').strip()
    job_key = str(job.get('resolvedRuntimeJobKey') or job_id).strip()
    if not group_ref:
        return None, {}
    group = groups_by_id.get(group_ref)
    if not isinstance(group, dict):
        raise CliError(f'job {job_id} references unknown groupRef {group_ref}', 2)
    members = _ensure_unique_text_list(
        group.get('resolvedMembers') or [],
        label=f'agent group {group_ref} resolvedMembers',
    )
    resolved_agent_ref = str(job.get('resolvedAgentQualifiedRef') or '').strip()
    if agent_ref not in members and resolved_agent_ref not in members:
        raise CliError(f'job {job_id} agentRef {agent_ref} is not a member of group {group_ref}', 2)
    schedule_policy = json_object(group.get('resolvedSchedulePolicy'))
    job_refs = _ensure_unique_text_list(
        schedule_policy.get('jobRefs') or [],
        label=f'agent group {group_ref} schedulePolicy.jobRefs',
    )
    if job_key not in job_refs:
        raise CliError(f'job {job_id} groupRef {group_ref} is missing from group schedulePolicy.jobRefs', 2)
    recovery_policy = json_object(group.get('resolvedRecoveryPolicy'))
    for step in (recovery_policy.get('steps') or []):
        if not isinstance(step, dict):
            continue
        if str(step.get('jobRef') or '').strip() == job_key:
            return group, {**step, 'groupRef': group_ref, 'source': f'group:{group_ref}'}
    return group, {}


def _resolve_job_model_profile_ref(
    job: dict[str, Any],
    *,
    job_id: str,
    agent_ref: str,
    agent: dict[str, Any],
    module: dict[str, Any],
    models_by_id: dict[str, dict[str, Any]],
    resolved_recovery_step: dict[str, Any],
    default_timezone: str,
) -> dict[str, Any]:
    _normalize_job_schedule(job, default_timezone=default_timezone)
    _normalize_job_runtime_policy(
        job,
        agent=agent,
        module=module,
        is_recovery_job=bool(resolved_recovery_step),
    )
    model_ref = _resolve_model_ref(job, agent)
    resolved_model_ref = ''
    model_row: dict[str, Any] | None = None
    if model_ref:
        resolved_model_ref, model_row = _lookup_owner_scoped_row(models_by_id, model_ref, owner_id=row_owner_id(job))
        if isinstance(model_row, dict):
            model_ref = str(model_row.get('id') or model_ref)
            resolved_model_ref = str(model_row.get('qualifiedId') or resolved_model_ref)
    capabilities = agent_capabilities(agent)
    if bool(capabilities.get('modelRequired')) and not model_ref:
        raise CliError(f'job {job_id} agent {agent_ref} requires modelProfileRef', 2)
    if model_ref and not isinstance(model_row, dict):
        raise CliError(f'job {job_id} references unknown modelProfileRef {model_ref}', 2)
    job['resolvedModelProfileRef'] = model_ref
    if resolved_model_ref:
        job['resolvedModelProfileQualifiedRef'] = resolved_model_ref
    return {
        'modelRef': model_ref,
        'capabilities': capabilities,
    }


def _resolve_job_target_binding(
    job: dict[str, Any],
    binding: dict[str, Any],
    *,
    job_id: str,
    agent_ref: str,
    capabilities: dict[str, Any],
    targets_by_id: dict[str, dict[str, Any]],
) -> str:
    derived_target_ref = str(binding.get('targetBindingRef') or '').strip()
    resolved_target_ref = str(binding.get('resolvedTargetBindingRef') or '').strip()
    declared_target_ref = str(job.get('targetBindingRef') or '').strip()
    if declared_target_ref and declared_target_ref != derived_target_ref:
        raise CliError(f'job {job_id} targetBindingRef must match bound targetBindingRef {derived_target_ref}', 2)
    if derived_target_ref:
        job['targetBindingRef'] = derived_target_ref
    else:
        job.pop('targetBindingRef', None)
        job.pop('resolvedTargetBindingRef', None)
    target_lookup_ref = resolved_target_ref or ''
    if bool(capabilities.get('externalDispatch')):
        if not derived_target_ref:
            raise CliError(f'job {job_id} agent {agent_ref} requires targetBindingRef', 2)
        target_lookup_ref, target_row = _lookup_owner_scoped_row(targets_by_id, derived_target_ref, owner_id=row_owner_id(job))
        if not isinstance(target_row, dict):
            raise CliError(f'job {job_id} references unknown targetBindingRef {derived_target_ref}', 2)
        job['resolvedTargetBindingRef'] = target_lookup_ref
    elif derived_target_ref:
        raise CliError(
            f'job {job_id} agent {agent_ref} cannot declare targetBindingRef when externalDispatch=false',
            2,
        )
    if derived_target_ref:
        target = targets_by_id[target_lookup_ref or derived_target_ref]
        supported_agents = _ensure_unique_text_list(
            target.get('supportedAgentRefs'),
            label=f'target {derived_target_ref} supportedAgentRefs',
        )
        if agent_ref not in supported_agents:
            raise CliError(f'job {job_id} agentRef {agent_ref} is not supported by target {derived_target_ref}', 2)
    return target_lookup_ref or derived_target_ref


def _resolve_job_dependencies(
    job: dict[str, Any],
    binding: dict[str, Any],
    group: dict[str, Any] | None,
    *,
    job_id: str,
    order: int,
    jobs_by_id: dict[str, dict[str, Any]],
    resolved_orders: dict[str, int],
) -> list[dict[str, Any]]:
    derived_binding_dependencies = list(binding.get('dependsOn') or []) if isinstance(binding.get('dependsOn'), list) else []
    declared_dependencies = _normalized_dependencies(job)
    if declared_dependencies and declared_dependencies != derived_binding_dependencies:
        raise CliError(f'job {job_id} dependsOn must match bound module dependencies', 2)
    job['dependsOn'] = [dict(item) for item in derived_binding_dependencies] if derived_binding_dependencies else []
    dependencies = _resolved_group_dependencies(job, group)
    _validate_resolved_dependencies(
        job_id=job_id,
        order=order,
        dependencies=dependencies,
        jobs_by_id=jobs_by_id,
        resolved_orders=resolved_orders,
    )
    return dependencies


def _materialize_bound_job_fields(
    job: dict[str, Any],
    *,
    resolved_contract: dict[str, Any],
    resolved_recovery_step: dict[str, Any],
    dependencies: list[dict[str, Any]],
    execution_plan: dict[str, Any],
) -> None:
    job.pop('resolvedCommand', None)
    job.pop('resolvedOperationRef', None)
    job.pop('resolvedExecutor', None)
    job['resolvedExecutionPlan'] = execution_plan
    job['resolvedContract'] = dict(resolved_contract)
    job['resolvedInputs'] = dict(resolved_contract.get('inputs') or {})
    job['resolvedOutputs'] = dict(resolved_contract.get('outputs') or {})
    job['resolvedRecoveryStep'] = dict(resolved_recovery_step) if resolved_recovery_step else {}
    job['resolvedDependsOn'] = dependencies
    job['normalizedDependsOn'] = dependencies


def _validate_bound_job(
    job: dict[str, Any],
    binding: dict[str, Any],
    *,
    order: int,
    agents_by_id: dict[str, dict[str, Any]],
    models_by_id: dict[str, dict[str, Any]],
    targets_by_id: dict[str, dict[str, Any]],
    modules_by_id: dict[str, dict[str, Any]],
    groups_by_id: dict[str, dict[str, Any]],
    jobs_by_id: dict[str, dict[str, Any]],
    resolved_orders: dict[str, int],
    default_timezone: str,
    config_path: str = '',
) -> None:
    job_id = str(job.get('id') or '')
    job_key = str(job.get('resolvedRuntimeJobKey') or job_id)
    context = _resolve_bound_job_context(
        job,
        binding,
        agents_by_id=agents_by_id,
        modules_by_id=modules_by_id,
    )
    group, resolved_recovery_step = _resolve_job_group_context(
        job,
        job_id=job_id,
        agent_ref=context['agentRef'],
        groups_by_id=groups_by_id,
    )
    model_state = _resolve_job_model_profile_ref(
        job,
        job_id=job_id,
        agent_ref=context['agentRef'],
        agent=context['agent'],
        module=context['module'],
        models_by_id=models_by_id,
        resolved_recovery_step=resolved_recovery_step,
        default_timezone=default_timezone,
    )
    target_binding_ref = _resolve_job_target_binding(
        job,
        binding,
        job_id=job_id,
        agent_ref=context['agentRef'],
        capabilities=model_state['capabilities'],
        targets_by_id=targets_by_id,
    )
    dependencies = _resolve_job_dependencies(
        job,
        binding,
        group,
        job_id=job_id,
        order=order,
        jobs_by_id=jobs_by_id,
        resolved_orders=resolved_orders,
    )
    _materialize_bound_job_fields(
        job,
        resolved_contract=context['resolvedContract'],
        resolved_recovery_step=resolved_recovery_step,
        dependencies=dependencies,
        execution_plan=build_bound_job_execution_plan(
            job=job,
            runner_ref=str(job.get('resolvedRunnerRef') or job.get('runnerRef') or ''),
            agent=context['agent'],
            module=context['module'],
            operation_ref=context['operationRef'],
            executor=context['executor'],
            target=targets_by_id.get(target_binding_ref) if target_binding_ref else None,
            target_binding_ref=target_binding_ref,
            config_path=config_path,
        ),
    )
