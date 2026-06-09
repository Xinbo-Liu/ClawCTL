#!/usr/bin/env python3
"""module 调度绑定场景下的 group 变更辅助。"""
from __future__ import annotations

import copy
from typing import Any

from openclaw.control_plane.registry import CliError
from openclaw.lib.io.json_access import json_object


def normalize_before_after(*, before_job: str, after_job: str) -> tuple[str, str]:
    normalized_before = str(before_job or '').strip()
    normalized_after = str(after_job or '').strip()
    if normalized_before and normalized_after:
        raise CliError('--insert-before-job 与 --insert-after-job 不能同时使用', 2)
    return normalized_before, normalized_after


def _insert_job_ref(job_refs: list[str], job_id: str, *, before_job: str, after_job: str, default_index: int | None = None) -> list[str]:
    if job_id in job_refs:
        raise CliError(f'group 调度列表已存在 jobId：{job_id}', 2)
    if before_job:
        if before_job not in job_refs:
            raise CliError(f'--insert-before-job 未出现在 group 调度列表中：{before_job}', 2)
        index = job_refs.index(before_job)
    elif after_job:
        if after_job not in job_refs:
            raise CliError(f'--insert-after-job 未出现在 group 调度列表中：{after_job}', 2)
        index = job_refs.index(after_job) + 1
    elif default_index is None:
        index = len(job_refs)
    else:
        index = max(0, min(int(default_index), len(job_refs)))
    next_job_refs = list(job_refs)
    next_job_refs.insert(index, job_id)
    return next_job_refs


def _default_schedule_index_for_ordered(group_payload: dict[str, Any]) -> int:
    schedule_policy = json_object(group_payload.get('schedulePolicy'))
    dependency_policy = json_object(group_payload.get('dependencyPolicy'))
    schedule_job_refs = [str(item).strip() for item in (schedule_policy.get('jobRefs') or []) if str(item).strip()]
    ordered_job_refs = [str(item).strip() for item in (dependency_policy.get('orderedJobRefs') or []) if str(item).strip()]
    if not ordered_job_refs:
        return len(schedule_job_refs)
    last_ordered_job = ordered_job_refs[-1]
    if last_ordered_job in schedule_job_refs:
        return schedule_job_refs.index(last_ordered_job) + 1
    return len(schedule_job_refs)


def _default_schedule_index_for_recovery(group_payload: dict[str, Any], *, after_minutes: int) -> int:
    schedule_policy = json_object(group_payload.get('schedulePolicy'))
    schedule_job_refs = [str(item).strip() for item in (schedule_policy.get('jobRefs') or []) if str(item).strip()]
    recovery_policy = json_object(group_payload.get('recoveryPolicy'))
    steps = [item for item in (recovery_policy.get('steps') or []) if isinstance(item, dict)]
    if not steps:
        return len(schedule_job_refs)
    for step in steps:
        current_after = int(step.get('afterMinutes') or 0)
        current_job_ref = str(step.get('jobRef') or '').strip()
        if current_after > after_minutes and current_job_ref in schedule_job_refs:
            return schedule_job_refs.index(current_job_ref)
    last_recovery_job = str(steps[-1].get('jobRef') or '').strip()
    if last_recovery_job in schedule_job_refs:
        return schedule_job_refs.index(last_recovery_job) + 1
    return len(schedule_job_refs)


def update_group_for_attach(
    *,
    group_payload: dict[str, Any],
    job_id: str,
    operation_ref: str,
    group_placement: str,
    before_job: str,
    after_job: str,
    recovery_after_minutes: int,
    recovery_action_kind: str,
) -> dict[str, Any]:
    next_group = copy.deepcopy(group_payload)
    schedule_policy = json_object(next_group.get('schedulePolicy'))
    dependency_policy = json_object(next_group.get('dependencyPolicy'))
    schedule_job_refs = [str(item).strip() for item in (schedule_policy.get('jobRefs') or []) if str(item).strip()]
    ordered_job_refs = [str(item).strip() for item in (dependency_policy.get('orderedJobRefs') or []) if str(item).strip()]

    if group_placement == 'ordered':
        schedule_job_refs = _insert_job_ref(
            schedule_job_refs,
            job_id,
            before_job=before_job,
            after_job=after_job,
            default_index=_default_schedule_index_for_ordered(group_payload),
        )
        if before_job and before_job not in ordered_job_refs:
            raise CliError(f'ordered attach 的 --insert-before-job 必须属于 dependencyPolicy.orderedJobRefs：{before_job}', 2)
        if after_job and after_job not in ordered_job_refs:
            raise CliError(f'ordered attach 的 --insert-after-job 必须属于 dependencyPolicy.orderedJobRefs：{after_job}', 2)
        ordered_job_refs = _insert_job_ref(
            ordered_job_refs,
            job_id,
            before_job=before_job,
            after_job=after_job,
            default_index=len(ordered_job_refs),
        )
        dependency_policy['orderedJobRefs'] = ordered_job_refs
        schedule_policy['jobRefs'] = schedule_job_refs
        next_group['dependencyPolicy'] = dependency_policy
        next_group['schedulePolicy'] = schedule_policy
        return next_group

    if group_placement != 'recovery':
        raise CliError(f'group placement 未知：{group_placement}', 2)

    retry_mode = str(dependency_policy.get('retryMode') or '').strip()
    if retry_mode != 'group_owned':
        raise CliError(f'group {next_group.get("id")} 当前 retryMode={retry_mode}，不支持 recovery attach', 2)
    if recovery_after_minutes < 1:
        raise CliError('recovery attach 必须提供 --recovery-after-minutes 且值 >= 1', 2)
    recovery_policy = json_object(next_group.get('recoveryPolicy'))
    if not recovery_policy:
        raise CliError(f'group {next_group.get("id")} 缺少 recoveryPolicy，无法插入 recovery job', 2)
    if str(recovery_policy.get('mode') or '').strip() != 'scheduled_job_sequence':
        raise CliError(f'group {next_group.get("id")} recoveryPolicy.mode 必须为 scheduled_job_sequence', 2)
    steps = [copy.deepcopy(item) for item in (recovery_policy.get('steps') or []) if isinstance(item, dict)]
    if not steps:
        raise CliError(f'group {next_group.get("id")} recoveryPolicy.steps 不能为空，无法追加 recovery job', 2)
    seen_after_minutes = {int(item.get('afterMinutes') or 0) for item in steps}
    if recovery_after_minutes in seen_after_minutes:
        raise CliError(f'group {next_group.get("id")} recoveryPolicy.steps.afterMinutes 已存在：{recovery_after_minutes}', 2)
    schedule_job_refs = _insert_job_ref(
        schedule_job_refs,
        job_id,
        before_job=before_job,
        after_job=after_job,
        default_index=_default_schedule_index_for_recovery(group_payload, after_minutes=recovery_after_minutes),
    )
    steps.append(
        {
            'jobRef': job_id,
            'actionKind': recovery_action_kind,
            'afterMinutes': recovery_after_minutes,
            'operationRef': operation_ref,
            'notes': ['由 control-plane agent-module-attach 自动插入的 recovery step。'],
        }
    )
    steps.sort(key=lambda item: (int(item.get('afterMinutes') or 0), str(item.get('jobRef') or '')))
    recovery_policy['steps'] = steps
    schedule_policy['jobRefs'] = schedule_job_refs
    next_group['schedulePolicy'] = schedule_policy
    next_group['recoveryPolicy'] = recovery_policy
    return next_group


def update_group_for_detach(*, group_payload: dict[str, Any], job_id: str) -> dict[str, Any]:
    next_group = copy.deepcopy(group_payload)
    schedule_policy = json_object(next_group.get('schedulePolicy'))
    dependency_policy = json_object(next_group.get('dependencyPolicy'))
    recovery_policy = json_object(next_group.get('recoveryPolicy'))
    schedule_job_refs = [str(item).strip() for item in (schedule_policy.get('jobRefs') or []) if str(item).strip()]
    ordered_job_refs = [str(item).strip() for item in (dependency_policy.get('orderedJobRefs') or []) if str(item).strip()]
    if job_id not in schedule_job_refs:
        raise CliError(f'job {job_id} 未出现在 group 调度列表中，无法 detach', 2)
    next_schedule_job_refs = [item for item in schedule_job_refs if item != job_id]
    if not next_schedule_job_refs:
        raise CliError(f'group {next_group.get("id")} 只剩 job {job_id}；detach 会导致 schedulePolicy.jobRefs 为空，当前命令不负责删除 group', 2)
    schedule_policy['jobRefs'] = next_schedule_job_refs
    if job_id in ordered_job_refs:
        dependency_policy['orderedJobRefs'] = [item for item in ordered_job_refs if item != job_id]
    if recovery_policy:
        steps = [copy.deepcopy(item) for item in (recovery_policy.get('steps') or []) if isinstance(item, dict)]
        if any(str(item.get('jobRef') or '').strip() == job_id for item in steps):
            remaining_steps = [item for item in steps if str(item.get('jobRef') or '').strip() != job_id]
            retry_mode = str(dependency_policy.get('retryMode') or '').strip()
            if retry_mode == 'group_owned' and not remaining_steps:
                raise CliError(f'group {next_group.get("id")} detach {job_id} 后 recoveryPolicy.steps 为空；当前命令不自动重写 retryMode', 2)
            recovery_policy['steps'] = remaining_steps
            next_group['recoveryPolicy'] = recovery_policy
    next_group['schedulePolicy'] = schedule_policy
    next_group['dependencyPolicy'] = dependency_policy
    return next_group
