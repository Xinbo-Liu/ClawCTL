#!/usr/bin/env python3
"""控制平面 registry 装配使用的运行态重校验辅助。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.control_plane.registry.owners import qualified_registry_id, row_owner_id
from openclaw.control_plane.registry.support import (
    _ensure_unique_text_list,
    _normalize_entrypoint,
    _normalize_operation_args,
)
from openclaw.control_plane.registry_validation.groups import (
    _materialize_group_resolved_fields,
    _resolve_group_release_policy,
    _runtime_acceptance_truth,
    _validate_group_topology_and_members,
)
from openclaw.control_plane.registry_validation.jobs import (
    _materialize_job_runner,
    _precompute_job_orders,
    _resolve_job_runner,
    _validate_bound_job,
    _validate_unbound_job,
)
from openclaw.lib.cli.common import CliError
from openclaw.lib.dispatch.target_registry import DispatchRegistryValidationError, load_dispatch_registry
from openclaw.lib.io.json_access import json_object
from openclaw.lib.models.cost_policy import ModelCostPolicyError, validate_model_cost_policy


def _lookup_scoped(index: dict[str, dict[str, Any]], ref: str, *, owner_id: str) -> dict[str, Any] | None:
    normalized_ref = str(ref or '').strip()
    if not normalized_ref:
        return None
    scoped_ref = normalized_ref if ':' in normalized_ref else qualified_registry_id(owner_id, normalized_ref)
    row = index.get(scoped_ref) or index.get(normalized_ref)
    return row if isinstance(row, dict) else None


def _validate_agent_group_rows(
    groups: list[dict[str, Any]],
    agents_by_id: dict[str, dict[str, Any]],
    jobs_by_id: dict[str, dict[str, Any]],
    *,
    job_bindings_by_job_id: dict[str, dict[str, Any]],
    group_topologies_by_group_id: dict[str, dict[str, Any]],
    repo_root: Path,
    config_path: Path | None = None,
    extensions: list[dict[str, Any]] | None = None,
    collections: dict[str, Any] | None = None,
) -> None:
    """校验 agent group 行及其运行态约束。"""
    acceptance_truth = _runtime_acceptance_truth(repo_root, config_path=config_path, extensions=extensions)
    for group in groups:
        topology_state = _validate_group_topology_and_members(
            group,
            agents_by_id,
            jobs_by_id,
            job_bindings_by_job_id=job_bindings_by_job_id,
            group_topologies_by_group_id=group_topologies_by_group_id,
            collections=collections,
        )
        release_policy = _resolve_group_release_policy(
            topology_state['groupId'],
            group,
            repo_root=repo_root,
            acceptance_truth=acceptance_truth,
            schedule_job_refs=topology_state.get('localScheduleJobRefs') or topology_state['scheduleJobRefs'],
        )
        _materialize_group_resolved_fields(
            group,
            topology_state=topology_state,
            release_policy=release_policy,
        )


def _validate_agent_rows(
    agents: list[dict[str, Any]],
    models_by_id: dict[str, dict[str, Any]],
    modules_by_id: dict[str, dict[str, Any]],
    implementations_by_id: dict[str, dict[str, Any]],
    groups_by_id: dict[str, dict[str, Any]],
) -> None:
    """校验 agent 行及其绑定关系。"""
    for agent in agents:
        agent_id = str(agent.get('id') or '')
        entrypoint = _normalize_entrypoint(agent.get('entrypoint'), label=f'agent {agent_id} entrypoint')
        allowed_executor_kinds = _ensure_unique_text_list(agent.get('allowedExecutorKinds'), label=f'agent {agent_id} allowedExecutorKinds')
        entrypoint_kind = str(entrypoint.get('kind') or '').strip()
        if entrypoint_kind not in allowed_executor_kinds:
            raise CliError(f'agent {agent_id} entrypoint.kind must be listed in allowedExecutorKinds', 2)
        governance = json_object(agent.get('governance'))
        module_ref = str(governance.get('moduleRef') or '').strip()
        if not module_ref:
            raise CliError(f'agent {agent_id} governance.moduleRef cannot be empty', 2)
        agent_owner_id = row_owner_id(agent)
        module = _lookup_scoped(modules_by_id, module_ref, owner_id=agent_owner_id)
        if not isinstance(module, dict):
            raise CliError(f'agent {agent_id} references unknown governance.moduleRef {module_ref}', 2)
        if str(module.get('agentRef') or '').strip() != agent_id:
            raise CliError(f'agent {agent_id} governance.moduleRef must point back to the same agent', 2)
        module_logic = json_object(module.get('logic'))
        implementation_ref = str(module_logic.get('implementationRef') or '').strip()
        if not implementation_ref:
            raise CliError(f'agent {agent_id} module {module_ref} implementationRef cannot be empty', 2)
        implementation_row = _lookup_scoped(implementations_by_id, implementation_ref, owner_id=agent_owner_id)
        if not isinstance(implementation_row, dict):
            raise CliError(f'agent {agent_id} references unknown implementationRef {implementation_ref}', 2)
        group_refs = [
            group_id
            for group_id, group in groups_by_id.items()
            if isinstance(group, dict)
            and (
                agent_id in set(group.get('resolvedMembers') or [])
                or qualified_registry_id(agent_owner_id, agent_id) in set(group.get('resolvedMembers') or [])
            )
        ]
        runtime_adapter = json_object(implementation_row.get('resolvedRuntimeAdapter'))
        if not runtime_adapter:
            raise CliError(f'agent {agent_id} implementation {implementation_ref} missing resolvedRuntimeAdapter', 2)
        supported_entrypoint_kinds = set(runtime_adapter.get('supportedEntrypointKinds') or [])
        if entrypoint_kind not in supported_entrypoint_kinds:
            raise CliError(f'agent {agent_id} entrypoint.kind is not supported by runtime adapter {runtime_adapter.get("id")}', 2)
        unsupported_executor_kinds = sorted(set(allowed_executor_kinds) - set(runtime_adapter.get('supportedExecutorKinds') or []))
        if unsupported_executor_kinds:
            raise CliError(f'agent {agent_id} executor kinds exceed runtime adapter support: {", ".join(unsupported_executor_kinds)}', 2)
        agent['resolvedModuleRef'] = str(module.get('qualifiedId') or qualified_registry_id(agent_owner_id, module_ref))
        agent['resolvedGroupRefs'] = list(group_refs)
        agent['resolvedImplementationRef'] = implementation_ref
        agent['resolvedRuntime'] = implementation_row.get('resolvedRuntime')
        agent['resolvedRuntimeAdapterRef'] = implementation_row.get('resolvedRuntimeAdapterRef')
        agent['resolvedRuntimeAdapter'] = runtime_adapter
        capabilities = json_object(agent.get('capabilities'))
        default_model_ref = str(agent.get('defaultModelProfileRef') or '').strip()
        default_model_row = _lookup_scoped(models_by_id, default_model_ref, owner_id=agent_owner_id) if default_model_ref else None
        if bool(capabilities.get('modelRequired')):
            if not default_model_ref:
                raise CliError(f'agent {agent_id} requires defaultModelProfileRef', 2)
            if not isinstance(default_model_row, dict):
                raise CliError(f'agent {agent_id} references unknown defaultModelProfileRef {default_model_ref}', 2)
        elif default_model_ref and not isinstance(default_model_row, dict):
            raise CliError(f'agent {agent_id} references unknown defaultModelProfileRef {default_model_ref}', 2) from None
        if isinstance(default_model_row, dict):
            agent['resolvedDefaultModelProfileRef'] = str(default_model_row.get('qualifiedId') or qualified_registry_id(agent_owner_id, default_model_ref))


def _validate_model_rows(
    models: list[dict[str, Any]],
) -> None:
    """校验模型画像的成本治理语义。"""
    for model in models:
        try:
            validate_model_cost_policy(model)
        except ModelCostPolicyError as exc:
            raise CliError(str(exc), 2) from exc


def _validate_target_rows(
    targets: list[dict[str, Any]],
    agents_by_id: dict[str, dict[str, Any]],
    *,
    dispatch_target_registry_paths: list[Path],
    dispatch_provider_registry_paths: list[Path],
) -> None:
    """校验 target 行及其 dispatch 约束。"""
    try:
        load_dispatch_registry(
            [item.resolve() for item in dispatch_target_registry_paths],
            provider_registry_path=[item.resolve() for item in dispatch_provider_registry_paths],
        )
    except DispatchRegistryValidationError as exc:
        raise CliError(f'dispatch registry validation failed: {exc}', 2) from exc
    for target in targets:
        target_id = str(target.get('id') or '')
        supported_agents = _ensure_unique_text_list(
            target.get('supportedAgentRefs'),
            label=f'target {target_id} supportedAgentRefs',
        )
        for agent_ref in supported_agents:
            scoped_agent_ref = qualified_registry_id(row_owner_id(target), agent_ref) if ':' not in agent_ref else agent_ref
            if agent_ref not in agents_by_id and scoped_agent_ref not in agents_by_id:
                raise CliError(f'target {target_id} references unknown agent {agent_ref}', 2)
        operations = json_object(target.get('operations'))
        target['resolvedOperations'] = {
            name: _normalize_operation_args(value, label=f'target {target_id} operations.{name}')
            for name, value in operations.items()
        }
        target['resolvedDispatchTargetRegistryPaths'] = [str(item.resolve()) for item in dispatch_target_registry_paths]
        target['resolvedDispatchTargetRegistryPath'] = (
            str(dispatch_target_registry_paths[0].resolve()) if len(dispatch_target_registry_paths) == 1 else ''
        )


def _validate_job_rows(
    jobs: list[dict[str, Any]],
    jobs_by_id: dict[str, dict[str, Any]],
    agents_by_id: dict[str, dict[str, Any]],
    models_by_id: dict[str, dict[str, Any]],
    targets_by_id: dict[str, dict[str, Any]],
    modules_by_id: dict[str, dict[str, Any]],
    groups_by_id: dict[str, dict[str, Any]],
    job_bindings_by_job_id: dict[str, dict[str, Any]],
    job_runners_by_id: dict[str, dict[str, Any]],
    binding_runner_ids: list[str],
    *,
    default_timezone: str,
    config_path: Path | None = None,
) -> None:
    """校验 job 行及其 schedule / dependency / execution 约束。"""
    resolved_orders = _precompute_job_orders(jobs, groups_by_id=groups_by_id, job_bindings_by_job_id=job_bindings_by_job_id)
    for job in jobs:
        job_id = str(job.get('id') or '')
        job_key = str(job.get('resolvedRuntimeJobKey') or job.get('qualifiedId') or job_id)
        order = int(resolved_orders.get(job_key) or resolved_orders.get(job_id) or 0)
        binding = job_bindings_by_job_id.get(job_key) or job_bindings_by_job_id.get(job_id)
        runner_ref, runner = _resolve_job_runner(job, binding, job_runners_by_id=job_runners_by_id, binding_runner_ids=binding_runner_ids)
        _materialize_job_runner(job, runner_ref=runner_ref, runner=runner)
        if not isinstance(binding, dict):
            _validate_unbound_job(job, job_id=job_id, order=order, models_by_id=models_by_id, jobs_by_id=jobs_by_id, resolved_orders=resolved_orders, default_timezone=default_timezone)
            continue
        _validate_bound_job(
            job,
            binding,
            order=order,
            agents_by_id=agents_by_id,
            models_by_id=models_by_id,
            targets_by_id=targets_by_id,
            modules_by_id=modules_by_id,
            groups_by_id=groups_by_id,
            jobs_by_id=jobs_by_id,
            resolved_orders=resolved_orders,
            default_timezone=default_timezone,
            config_path=str(config_path or ''),
        )
