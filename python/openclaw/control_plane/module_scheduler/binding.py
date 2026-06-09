#!/usr/bin/env python3
"""Agent module 调度 attach/detach 计划与执行器。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.control_plane.modules.change_set import (
    apply_staged_writes,
    build_write,
    read_json_object,
    repo_root_from_inputs,
)
from openclaw.control_plane.module_scheduler.attach_support import (
    build_attach_group_write as _build_attach_group_write,
    build_attach_module_write as _build_attach_module_write,
    build_attach_plan_payload as _build_attach_plan_payload,
    build_attach_target_write as _build_attach_target_write,
)
from openclaw.control_plane.module_scheduler.job_surface import (
    build_attach_job_surface as _build_attach_job_surface,
    resolve_attach_runtime_contract as _resolve_attach_runtime_contract,
)
from openclaw.control_plane.module_scheduler.group_ops import (
    normalize_before_after,
)
from openclaw.control_plane.module_scheduler.detach_support import (
    build_detach_group_write,
    build_detach_module_write,
    build_detach_plan_payload,
    build_detach_target_write,
    resolve_detach_operation_ref,
)
from openclaw.control_plane.module_scheduler.binding_support import (
    ensure_attach_job_available as _ensure_attach_job_available,
    ensure_known_module,
    ensure_known_operation,
    module_agent_ref,
    module_binding_refs,
    registry_collection_dir_for_write,
    resolve_attach_module_state as _resolve_attach_module_state,
    single_registry_collection_dir as _single_registry_collection_dir,
    validate_attach_group_options as _validate_attach_group_options,
)
from openclaw.control_plane.registry import CliError, load_registry
from openclaw.lib.io.json_access import json_object

def _build_attach_plan(
    *,
    config_path: Path,
    repo_root: Path | None,
    module_ref: str,
    operation_ref: str,
    job_id: str,
    job_title: str,
    schedule_expr: str,
    schedule_tz: str,
    group_ref: str,
    group_placement: str,
    before_job: str,
    after_job: str,
    recovery_after_minutes: int | None,
    recovery_action_kind: str,
    target_binding_ref: str,
    depends_on: list[str],
    order: int | None,
    timeout_seconds: int | None,
    retry_enabled: bool | None,
    retry_max_attempts: int | None,
    retry_backoff_seconds: list[int],
    run_artifact_root: str,
    latest_alias: str,
    retention_days: int,
    retryable_classes: list[str],
    terminal_classes: list[str],
    model_profile_ref: str,
    job_file_prefix: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], Path, Path]:
    """生成 attach 的 staged write 计划。"""
    normalized_module_ref = str(module_ref or '').strip()
    normalized_operation_ref = str(operation_ref or '').strip()
    normalized_job_id = str(job_id or '').strip()
    normalized_target_binding_ref = str(target_binding_ref or '').strip()
    normalized_before_job, normalized_after_job = normalize_before_after(before_job=before_job, after_job=after_job)
    normalized_group_ref, normalized_group_placement = _validate_attach_group_options(
        group_ref=group_ref,
        group_placement=group_placement,
        recovery_after_minutes=recovery_after_minutes,
        recovery_action_kind=recovery_action_kind,
    )

    registry = load_registry(Path(config_path).resolve())
    effective_repo_root = repo_root_from_inputs(repo_root=repo_root, config_path=Path(config_path).resolve())
    module_path, module_payload, capabilities = _resolve_attach_module_state(
        registry,
        module_ref=normalized_module_ref,
        operation_ref=normalized_operation_ref,
    )
    _ensure_attach_job_available(registry, job_id=normalized_job_id)

    if bool(capabilities.get('externalDispatch')) and not normalized_target_binding_ref:
        raise CliError(f'module {normalized_module_ref} externalDispatch=true；attach 时必须提供 --target-binding-ref', 2)
    if (not bool(capabilities.get('externalDispatch'))) and normalized_target_binding_ref:
        raise CliError(f'module {normalized_module_ref} externalDispatch=false；不得提供 --target-binding-ref', 2)
    if normalized_target_binding_ref and normalized_target_binding_ref not in json_object(registry.get('targetsById')):
        raise CliError(f'未注册的 targetBindingRef：{normalized_target_binding_ref}', 2)

    runtime_contract = _resolve_attach_runtime_contract(
        registry,
        module_payload,
        capabilities,
        schedule_tz=schedule_tz,
        group_placement=normalized_group_placement,
        timeout_seconds=timeout_seconds,
        retry_enabled=retry_enabled,
        retry_max_attempts=retry_max_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
        run_artifact_root=run_artifact_root,
        latest_alias=latest_alias,
        model_profile_ref=model_profile_ref,
        job_id=normalized_job_id,
    )
    jobs_dir = registry_collection_dir_for_write(
        registry,
        key='jobs',
        label='jobs',
        source_path=module_path,
        source_payload=module_payload,
    )
    resolved_job_payload, job_payload, job_path = _build_attach_job_surface(
        registry,
        module_payload,
        capabilities,
        runtime_contract,
        jobs_dir=jobs_dir,
        job_id=normalized_job_id,
        job_title=job_title,
        schedule_expr=schedule_expr,
        group_placement=normalized_group_placement,
        order=order,
        retention_days=int(retention_days),
        retryable_classes=retryable_classes,
        terminal_classes=terminal_classes,
        job_file_prefix=job_file_prefix,
    )
    next_module_payload, module_write = _build_attach_module_write(
        module_payload,
        module_ref=normalized_module_ref,
        operation_ref=normalized_operation_ref,
        job_id=normalized_job_id,
        depends_on=depends_on,
        target_binding_ref=normalized_target_binding_ref,
        module_path=module_path,
    )

    writes: list[dict[str, Any]] = [
        module_write,
        build_write(job_path, action='create', payload=job_payload, summary=f'创建调度 job {normalized_job_id}'),
    ]
    agent_ref = module_agent_ref(module_payload, module_ref=normalized_module_ref)
    group_write, group_change = _build_attach_group_write(
        registry,
        group_ref=normalized_group_ref,
        group_placement=normalized_group_placement,
        job_id=normalized_job_id,
        operation_ref=normalized_operation_ref,
        before_job=normalized_before_job,
        after_job=normalized_after_job,
        recovery_after_minutes=recovery_after_minutes,
        recovery_action_kind=recovery_action_kind,
    )
    if group_write is not None:
        writes.append(group_write)
    target_write, target_change = _build_attach_target_write(
        registry,
        target_binding_ref=normalized_target_binding_ref,
        agent_ref=agent_ref,
    )
    if target_write is not None:
        writes.append(target_write)
    plan = _build_attach_plan_payload(
        effective_repo_root=effective_repo_root,
        module_ref=normalized_module_ref,
        operation_ref=normalized_operation_ref,
        job_id=normalized_job_id,
        agent_ref=agent_ref,
        module_payload=module_payload,
        next_module_payload=next_module_payload,
        resolved_job_payload=resolved_job_payload,
        job_payload=job_payload,
        group_change=group_change,
        target_change=target_change,
        writes=writes,
    )
    return plan, writes, effective_repo_root, Path(config_path).resolve()


def plan_agent_module_attach(**kwargs: Any) -> dict[str, Any]:
    """生成 module attach 计划。"""
    plan, _, _, _ = _build_attach_plan(**kwargs)
    return plan


def apply_agent_module_attach(**kwargs: Any) -> dict[str, Any]:
    """落盘执行 module attach 计划。"""
    plan, writes, _, config_path = _build_attach_plan(**kwargs)
    apply_staged_writes(writes=writes, config_path=config_path)
    return {**plan, 'mode': 'apply'}


def _build_detach_plan(
    *,
    config_path: Path,
    repo_root: Path | None,
    module_ref: str,
    job_id: str,
    operation_ref: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], Path, Path]:
    """生成 detach 的 staged write 计划。"""
    normalized_module_ref = str(module_ref or '').strip()
    normalized_job_id = str(job_id or '').strip()
    normalized_operation_ref = str(operation_ref or '').strip()
    registry = load_registry(Path(config_path).resolve())
    effective_repo_root = repo_root_from_inputs(repo_root=repo_root, config_path=Path(config_path).resolve())
    module_row = ensure_known_module(registry, normalized_module_ref)
    module_path = Path(str(module_row.get('sourcePath') or '')).resolve()
    module_payload = read_json_object(module_path)
    found_operation_ref = resolve_detach_operation_ref(
        module_payload,
        module_ref=normalized_module_ref,
        job_id=normalized_job_id,
        operation_ref=normalized_operation_ref,
    )
    job_row = json_object(registry.get('jobsById')).get(normalized_job_id)
    if not isinstance(job_row, dict):
        raise CliError(f'未注册的 jobId：{normalized_job_id}', 2)
    job_path = Path(str(job_row.get('sourcePath') or '')).resolve()
    group_ref = str(job_row.get('groupRef') or '').strip()
    target_binding_ref = str(job_row.get('targetBindingRef') or '').strip()
    agent_ref = module_agent_ref(module_payload, module_ref=normalized_module_ref)

    next_module_payload, module_write = build_detach_module_write(
        module_payload,
        module_ref=normalized_module_ref,
        operation_ref=found_operation_ref,
        job_id=normalized_job_id,
        module_path=module_path,
    )
    writes: list[dict[str, Any]] = [
        module_write,
        build_write(job_path, action='delete', summary=f'删除调度 job {normalized_job_id}'),
    ]
    group_write, group_change = build_detach_group_write(
        registry,
        group_ref=group_ref,
        job_id=normalized_job_id,
    )
    if group_write is not None:
        writes.append(group_write)
    target_write, target_change = build_detach_target_write(
        registry,
        target_binding_ref=target_binding_ref,
        agent_ref=agent_ref,
        job_id=normalized_job_id,
    )
    if target_write is not None:
        writes.append(target_write)

    previous_job_refs, previous_target_refs = module_binding_refs(module_payload)
    next_job_refs, next_target_refs = module_binding_refs(next_module_payload)
    plan = build_detach_plan_payload(
        effective_repo_root=effective_repo_root,
        module_ref=normalized_module_ref,
        operation_ref=found_operation_ref,
        job_id=normalized_job_id,
        agent_ref=agent_ref,
        before_job_refs=previous_job_refs,
        before_target_refs=previous_target_refs,
        after_job_refs=next_job_refs,
        after_target_refs=next_target_refs,
        group_change=group_change,
        target_change=target_change,
        writes=writes,
    )
    return plan, writes, effective_repo_root, Path(config_path).resolve()


def plan_agent_module_detach(**kwargs: Any) -> dict[str, Any]:
    """生成 module detach 计划。"""
    plan, _, _, _ = _build_detach_plan(**kwargs)
    return plan


def apply_agent_module_detach(**kwargs: Any) -> dict[str, Any]:
    """落盘执行 module detach 计划。"""
    plan, writes, _, config_path = _build_detach_plan(**kwargs)
    apply_staged_writes(writes=writes, config_path=config_path)
    return {**plan, 'mode': 'apply'}
