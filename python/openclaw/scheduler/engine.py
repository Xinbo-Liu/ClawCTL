#!/usr/bin/env python3
"""调度器执行引擎与状态流转。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from openclaw.control_plane.extensions.api import import_extension_callable
from openclaw.control_plane.registry import CliError
from openclaw.control_plane.registry.job_execution_plans import (
    RUNNER_EXEC,
    SUBPROCESS_EXEC,
    execution_plan_from_job,
    materialized_command_from_execution_plan,
)
from openclaw.control_plane.registry.store import append_jsonl
from openclaw.lib.io.json_access import json_array, json_object
from openclaw.lib.runtime.time import app_timezone_name, ensure_aware, now_utc as _runtime_now_utc, parse_iso_datetime, utc_iso
from openclaw.scheduler.cron import cron_matches, next_cron_occurrence, resolve_timezone
from openclaw.scheduler.locking import (
    acquire_lock as _acquire_lock,
    lock_path as _lock_path,
    release_lock as _release_lock,
    scheduler_lock_stale_after_seconds as _scheduler_lock_stale_after_seconds,
)
from openclaw.scheduler.subprocess_runner import run_subprocess_job_impl


STATUS_SCHEDULED = 'scheduled'
STATUS_RUNNING = 'running'
STATUS_SUCCEEDED = 'succeeded'
STATUS_FAILED = 'failed'
STATUS_BLOCKED = 'blocked'
STATUS_RETRY_PENDING = 'retry_pending'


def runtime_job_key(job: dict[str, Any]) -> str:
    """返回调度器用于 state、lock、due key、history 的稳定 job key。"""
    return str(job.get('resolvedRuntimeJobKey') or job.get('qualifiedId') or job.get('id') or '').strip()


def local_job_id(job: dict[str, Any]) -> str:
    """返回 job 的本地显示 id。"""
    return str(job.get('id') or '').strip()


def now_utc() -> datetime:
    """返回当前 UTC 时间。"""
    return _runtime_now_utc()


def now_utc_iso() -> str:
    """返回当前 UTC 时间的 ISO 字符串。"""
    return utc_iso(now_utc())


def make_due_key(job_id: str, current: datetime, suffix: str = 'schedule') -> str:
    """生成调度执行键。"""
    return f'{job_id}@{suffix}@{current.strftime("%Y-%m-%dT%H:%M")}'


def _normalize_dep(dep: Any) -> dict[str, Any] | None:
    """规范化单个依赖声明。"""
    if isinstance(dep, str) and dep.strip():
        return {'jobId': dep.strip(), 'requiredStatuses': [STATUS_SUCCEEDED], 'maxAgeMinutes': 240}
    if isinstance(dep, dict) and str(dep.get('jobId') or '').strip():
        required_statuses = json_array(dep.get('requiredStatuses')) or [STATUS_SUCCEEDED]
        return {
            'jobId': str(dep.get('jobId') or '').strip(),
            'requiredStatuses': [str(item) for item in required_statuses],
            'maxAgeMinutes': int(dep.get('maxAgeMinutes') or 240),
        }
    return None


def _parse_iso(value: object) -> datetime | None:
    """解析 ISO 时间字符串。"""
    return parse_iso_datetime(value)


def _job_state(state: dict[str, Any], job_id: str, title: str) -> dict[str, Any]:
    """获取或初始化单个 job 的状态对象。"""
    jobs_value = state.setdefault('jobs', {}) if isinstance(state, dict) else {}
    jobs = json_object(jobs_value)
    if isinstance(state, dict) and jobs_value is not jobs:
        state['jobs'] = jobs
    payload = json_object(jobs.get(job_id))
    payload.setdefault('jobId', job_id)
    payload.setdefault('title', title)
    payload.setdefault('currentStatus', STATUS_SCHEDULED)
    payload.setdefault('consecutiveFailures', 0)
    payload.setdefault('pendingRetry', None)
    payload.setdefault('lastBlockedReason', None)
    payload.setdefault('lastBlockedRunId', None)
    payload.setdefault('lastRunId', None)
    payload.setdefault('activeRun', None)
    jobs[job_id] = payload
    return payload


def _history_row(
    *,
    job: dict[str, Any],
    status: str,
    due_key: str,
    current: datetime,
    reason: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构建 history 记录行。"""
    payload: dict[str, Any] = {
        'job_id': runtime_job_key(job),
        'jobId': runtime_job_key(job),
        'localJobId': local_job_id(job),
        'qualifiedJobId': str(job.get('qualifiedId') or '').strip(),
        'title': str(job.get('title') or ''),
        'status': status,
        'runId': due_key,
        'scheduled_for': current.isoformat(),
        'finished_at': now_utc_iso(),
    }
    if reason:
        payload['reason'] = reason
    if isinstance(extra, dict):
        payload.update(extra)
    return payload


def _dependency_block_reason(job: dict[str, Any], state: dict[str, Any], current: datetime) -> str | None:
    """计算 job 被依赖阻塞的原因。"""
    deps = [_normalize_dep(dep) for dep in (json_array(job.get('resolvedDependsOn')) or json_array(job.get('dependsOn')))]
    jobs_state = json_object(state.get('jobs'))
    for dep in deps:
        if not dep:
            continue
        dep_state = json_object(jobs_state.get(dep['jobId']))
        current_status = str(dep_state.get('currentStatus') or '')
        allowed = {str(item) for item in json_array(dep.get('requiredStatuses')) or [STATUS_SUCCEEDED]}
        if current_status not in allowed:
            return f"depends_on 未满足：{dep['jobId']} 当前状态={current_status or 'unknown'}"
        reference = _parse_iso(dep_state.get('lastFinishedAt') or dep_state.get('lastSucceededAt') or dep_state.get('lastFailedAt'))
        if reference is None:
            return f"depends_on 未满足：{dep['jobId']} 缺少完成时间"
        age_seconds = (current.astimezone(timezone.utc) - reference.astimezone(timezone.utc)).total_seconds()
        max_age_minutes = max(1, int(dep.get('maxAgeMinutes') or 240))
        if age_seconds > max_age_minutes * 60:
            return f"depends_on 未满足：{dep['jobId']} 已超过 {max_age_minutes} 分钟窗口"
    return None


def run_subprocess_job(
    *,
    job: dict[str, Any],
    config: dict[str, Any],
    files,
    job_state: dict[str, Any],
    due_key: str,
    current: datetime,
    force_all: bool = False,
    command: list[str] | None = None,
) -> dict[str, Any]:
    """通过 subprocess runner 执行 job。"""
    timeout_seconds = max(1, int(job.get('timeoutSeconds') or 900))
    lock_path = _lock_path(files, runtime_job_key(job))
    stale_after_seconds = _scheduler_lock_stale_after_seconds(timeout_seconds)
    return run_subprocess_job_impl(
        job=job,
        config=config,
        files=files,
        job_state=job_state,
        due_key=due_key,
        current=current,
        force_all=force_all,
        command=command,
        lock_path=lock_path,
        stale_after_seconds=stale_after_seconds,
        acquire_lock=_acquire_lock,
        release_lock=_release_lock,
        history_row_builder=_history_row,
        now_utc_iso=now_utc_iso,
    )


def run_job(
    *,
    job: dict[str, Any],
    config: dict[str, Any],
    files,
    job_state: dict[str, Any],
    due_key: str,
    current: datetime,
    force_all: bool = False,
) -> dict[str, Any]:
    """执行单个 job，并处理状态、重试与历史写入。"""
    execution_plan = execution_plan_from_job(job)
    plan_kind = str(execution_plan.get('kind') or '').strip()
    if plan_kind == SUBPROCESS_EXEC:
        command = materialized_command_from_execution_plan(execution_plan)
        if not command:
            raise CliError(f"job {job.get('id')} resolvedExecutionPlan 缺少可执行 command", 2)
        return run_subprocess_job(
            job=job,
            config=config,
            files=files,
            job_state=job_state,
            due_key=due_key,
            current=current,
            force_all=force_all,
            command=command,
        )
    if plan_kind != RUNNER_EXEC:
        raise CliError(f"job {job.get('id')} 使用未知 execution plan kind：{plan_kind or '<empty>'}", 2)
    runner_ref = str(execution_plan.get('runnerRef') or '').strip()
    runners_by_id = json_object(config.get('jobRunnersById'))
    runner_spec = json_object(runners_by_id.get(runner_ref))
    if not runner_ref or not runner_spec:
        raise CliError(f"job {job.get('id')} 缺少可执行 runnerRef", 2)
    runner = import_extension_callable(str(runner_spec.get('module') or '').strip(), str(runner_spec.get('callable') or '').strip())
    payload = runner(
        job=job,
        config=config,
        files=files,
        job_state=job_state,
        due_key=due_key,
        current=current,
        force_all=force_all,
    )
    if not isinstance(payload, dict):
        raise CliError(f"job {job.get('id')} runner {runner_ref} 未返回合法结果", 2)
    return payload


def _mark_blocked(
    *,
    job: dict[str, Any],
    job_state: dict[str, Any],
    files,
    due_key: str,
    current: datetime,
    reason: str,
) -> dict[str, Any] | None:
    """把 job 记录为 blocked。"""
    if job_state.get('lastBlockedRunId') == due_key and str(job_state.get('lastBlockedReason') or '') == reason:
        return None
    job_state['currentStatus'] = STATUS_BLOCKED
    job_state['lastBlockedAt'] = now_utc_iso()
    job_state['lastBlockedReason'] = reason
    job_state['lastBlockedRunId'] = due_key
    row = _history_row(job=job, status=STATUS_BLOCKED, due_key=due_key, current=current, reason=reason)
    append_jsonl(files.history_path, row)
    return row


def _retry_metadata(job: dict[str, Any], job_state: dict[str, Any], result: dict[str, Any]) -> None:
    """构建 retry 元数据。"""
    retry = json_object(job.get('retryPolicy'))
    if not bool(retry.get('enabled')):
        job_state['pendingRetry'] = None
        return
    max_attempts = max(0, int(retry.get('maxAttempts') or 0))
    backoff_seconds = [max(0, int(item)) for item in json_array(retry.get('backoffSeconds'))]
    attempt = int((job_state.get('pendingRetry') or {}).get('attempt') or 0) + 1
    if attempt > max_attempts or not backoff_seconds:
        job_state['pendingRetry'] = None
        return
    index = min(attempt - 1, len(backoff_seconds) - 1)
    next_run = now_utc() + timedelta(seconds=backoff_seconds[index])
    job_state['pendingRetry'] = {
        'attempt': attempt,
        'nextRunAt': next_run.isoformat().replace('+00:00', 'Z'),
        'reason': str(result.get('reason') or f"return_code={result.get('return_code')}"),
    }
    job_state['currentStatus'] = STATUS_RETRY_PENDING


def _update_job_state_after_run(job_state: dict[str, Any], result: dict[str, Any]) -> None:
    """根据执行结果更新 job 状态。"""
    status = str(result.get('status') or '')
    job_state['lastRunId'] = str(result.get('runId') or '')
    job_state['lastFinishedAt'] = str(result.get('finished_at') or now_utc_iso())
    job_state['lastLogPath'] = str(result.get('log_path') or '')
    job_state['lastRunDir'] = str(result.get('run_dir') or '')
    job_state['lastRunManifestPath'] = str(result.get('run_manifest_path') or '')
    job_state['lastArtifactsPath'] = str(result.get('artifacts_path') or '')
    job_state['lastResultManifestPath'] = str(result.get('result_manifest_path') or '')
    job_state['lastReturnCode'] = result.get('return_code')
    job_state['lastAcceptedByLedger'] = result.get('accepted_by_ledger')
    job_state['lastBlockedReason'] = None
    job_state['lastBlockedRunId'] = None
    if status == STATUS_SUCCEEDED:
        job_state['currentStatus'] = STATUS_SUCCEEDED
        job_state['lastSucceededAt'] = job_state['lastFinishedAt']
        job_state['consecutiveFailures'] = 0
        job_state['pendingRetry'] = None
    elif status == STATUS_FAILED:
        job_state['currentStatus'] = STATUS_FAILED
        job_state['lastFailedAt'] = job_state['lastFinishedAt']
        job_state['consecutiveFailures'] = int(job_state.get('consecutiveFailures') or 0) + 1
    elif status == STATUS_BLOCKED:
        job_state['currentStatus'] = STATUS_BLOCKED
        job_state['lastBlockedAt'] = job_state['lastFinishedAt']
        job_state['lastBlockedReason'] = str(result.get('reason') or '')
        job_state['lastBlockedRunId'] = str(result.get('runId') or '')


def _candidate_due(job: dict[str, Any], job_state: dict[str, Any], current: datetime, force_all: bool) -> tuple[bool, str | None, str | None]:
    """判断 job 当前是否到期可执行。"""
    if force_all:
        return True, make_due_key(runtime_job_key(job), current, 'force_all'), 'force_all'
    pending_retry = json_object(job_state.get('pendingRetry'))
    if pending_retry:
        next_run = _parse_iso(pending_retry.get('nextRunAt'))
        current_utc = ensure_aware(current).astimezone(timezone.utc)
        if next_run is not None and next_run <= current_utc:
            due_key = make_due_key(runtime_job_key(job), current, f"retry{int(pending_retry.get('attempt') or 1)}")
            return True, due_key, 'retry'
    schedule = json_object(job.get('schedule'))
    if str(schedule.get('kind') or 'cron') != 'cron':
        return False, None, None
    if not cron_matches(str(schedule.get('expr') or ''), current):
        return False, None, None
    due_key = make_due_key(runtime_job_key(job), current)
    if str(job_state.get('lastRunId') or '') == due_key or str(job_state.get('lastBlockedRunId') or '') == due_key:
        return False, None, None
    return True, due_key, 'schedule'


def _refresh_next_scheduled(job: dict[str, Any], job_state: dict[str, Any], current: datetime) -> None:
    """刷新 job 的 nextScheduledRunAt。"""
    schedule = json_object(job.get('schedule'))
    expr = str(schedule.get('expr') or '').strip()
    if expr:
        job_state['nextScheduledRunAt'] = next_cron_occurrence(expr, current)


def ensure_job_state(state: dict[str, Any], job_id: str, title: str) -> dict[str, Any]:
    """为全部 job 初始化状态对象。"""
    return _job_state(state, job_id, title)


def dependency_block_reason(job: dict[str, Any], state: dict[str, Any], current: datetime) -> str | None:
    """对外暴露依赖阻塞原因计算。"""
    return _dependency_block_reason(job, state, current)


def candidate_due(job: dict[str, Any], job_state: dict[str, Any], current: datetime, force_all: bool) -> tuple[bool, str | None, str | None]:
    """对外暴露到期判断。"""
    return _candidate_due(job, job_state, current, force_all)


def refresh_next_scheduled(job: dict[str, Any], job_state: dict[str, Any], current: datetime) -> None:
    """对外暴露下一次调度刷新逻辑。"""
    _refresh_next_scheduled(job, job_state, current)


def execute_due_jobs(
    *,
    config: dict[str, Any],
    files,
    state: dict[str, Any],
    force_all: bool = False,
    tick_started_at: datetime | None = None,
) -> dict[str, Any]:
    """执行当前到期的 job 集合。"""
    defaults = json_object(config.get('defaults'))
    default_tz = str(defaults.get('timezone') or app_timezone_name()).strip()
    tick_utc = ensure_aware(tick_started_at or now_utc()).astimezone(timezone.utc)
    executed: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    jobs = [job for job in json_array(config.get('jobs')) if isinstance(job, dict) and bool(job.get('enabled', True))]
    for job in jobs:
        schedule = json_object(job.get('schedule'))
        current = tick_utc.astimezone(resolve_timezone(str(schedule.get('tz') or default_tz))).replace(second=0, microsecond=0)
        job_key = runtime_job_key(job)
        job_state = _job_state(state, job_key, str(job.get('title') or ''))
        local_id = local_job_id(job)
        if local_id and local_id != job_key:
            job_state.setdefault('localJobId', local_id)
        _refresh_next_scheduled(job, job_state, current)
        due, due_key, trigger = _candidate_due(job, job_state, current, force_all)
        if not due or not due_key:
            if job_state.get('currentStatus') not in {STATUS_RUNNING, STATUS_RETRY_PENDING, STATUS_SUCCEEDED, STATUS_FAILED, STATUS_BLOCKED}:
                job_state['currentStatus'] = STATUS_SCHEDULED
            continue
        reason = _dependency_block_reason(job, state, current)
        if reason:
            row = _mark_blocked(job=job, job_state=job_state, files=files, due_key=due_key, current=current, reason=reason)
            if row:
                blocked.append(row)
            continue
        result = run_job(job=job, config=config, files=files, job_state=job_state, due_key=due_key, current=current, force_all=force_all)
        _update_job_state_after_run(job_state, result)
        if str(result.get('status') or '') == STATUS_FAILED:
            _retry_metadata(job, job_state, result)
        else:
            job_state['pendingRetry'] = None
        result['effective_status'] = job_state.get('currentStatus')
        result['trigger'] = trigger
        append_jsonl(files.history_path, result)
        executed.append(result)
    return {'executed': executed, 'blocked': blocked, 'executed_count': len(executed), 'blocked_count': len(blocked)}
