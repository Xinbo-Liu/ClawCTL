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
    return str(job.get('resolvedRuntimeJobKey') or job.get('qualifiedId') or job.get('id') or '').strip()


def active_runtime_job_keys(config: dict[str, Any]) -> set[str]:
    """返回当前 registry 声明的调度 job key 集合。

    参数：
        config（dict[str, Any]）：控制面 registry；函数从其中的 jobs 列表提取运行态 job key。

    返回：
        返回 set[str]，表示当前 registry 中所有可识别 job 的稳定运行态键。
    """
    return {
        key
        for key in (runtime_job_key(job) for job in json_array(config.get('jobs')) if isinstance(job, dict))
        if key
    }


def prune_scheduler_state_jobs(state: dict[str, Any], config: dict[str, Any]) -> list[str]:
    """删除 scheduler state 中当前 registry 已不存在的 job 状态。

    参数：
        state（dict[str, Any]）：scheduler 持久状态；函数会原地删除 stale job 条目。
        config（dict[str, Any]）：控制面 registry，用来判断哪些 job 仍属于当前运行面。

    返回：
        返回 list[str]，表示本次移除的旧 job key。
    """
    jobs = state.get('jobs')
    if not isinstance(jobs, dict):
        state['jobs'] = {}
        return []
    active_keys = active_runtime_job_keys(config)
    removed = sorted(key for key in jobs if str(key) not in active_keys)
    for key in removed:
        jobs.pop(key, None)
    return removed


def local_job_id(job: dict[str, Any]) -> str:
    return str(job.get('id') or '').strip()


def now_utc() -> datetime:
    return _runtime_now_utc()


def now_utc_iso() -> str:
    return utc_iso(now_utc())


def make_due_key(job_id: str, current: datetime, suffix: str = 'schedule') -> str:
    return f'{job_id}@{suffix}@{current.strftime("%Y-%m-%dT%H:%M")}'


def _normalize_dep(dep: Any) -> dict[str, Any] | None:
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
    return parse_iso_datetime(value)


def _job_state(state: dict[str, Any], job_id: str, title: str) -> dict[str, Any]:
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
    trigger_override: str | None = None,
    execution_env: dict[str, str] | None = None,
) -> dict[str, Any]:
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
        trigger_override=trigger_override,
        execution_env=execution_env,
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
    trigger_override: str | None = None,
    execution_env: dict[str, str] | None = None,
) -> dict[str, Any]:
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
            trigger_override=trigger_override,
            execution_env=execution_env,
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
    if str(job.get('retryOwnership') or 'stage_owned').strip() != 'stage_owned':
        job_state['pendingRetry'] = None
        return
    retry = json_object(job.get('retryPolicy'))
    if not bool(retry.get('enabled')):
        job_state['pendingRetry'] = None
        return
    failure_class = str(result.get('failure_class') or result.get('failureClass') or '').strip()
    retryable_classes = {
        str(item).strip()
        for item in json_array(json_object(job.get('failureClassPolicy')).get('retryableClasses'))
        if str(item).strip()
    }
    if not failure_class or failure_class not in retryable_classes:
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
        'failureClass': failure_class,
    }
    job_state['currentStatus'] = STATUS_RETRY_PENDING


def _update_job_state_after_run(job_state: dict[str, Any], result: dict[str, Any]) -> None:
    status = str(result.get('status') or '')
    blocked_without_manifests = (
        status == STATUS_BLOCKED
        and not str(result.get('run_manifest_path') or '').strip()
        and not str(result.get('artifacts_path') or '').strip()
        and not str(result.get('result_manifest_path') or '').strip()
    )
    if blocked_without_manifests:
        job_state['currentStatus'] = STATUS_BLOCKED
        job_state['lastBlockedAt'] = str(result.get('finished_at') or now_utc_iso())
        job_state['lastBlockedReason'] = str(result.get('reason') or '')
        job_state['lastBlockedRunId'] = str(result.get('runId') or '')
        job_state['activeRun'] = None
        return
    job_state['lastRunId'] = str(result.get('runId') or '')
    job_state['lastFinishedAt'] = str(result.get('finished_at') or now_utc_iso())
    job_state['lastLogPath'] = str(result.get('log_path') or '')
    job_state['lastRunDir'] = str(result.get('run_dir') or '')
    job_state['lastRunManifestPath'] = str(result.get('run_manifest_path') or '')
    job_state['lastArtifactsPath'] = str(result.get('artifacts_path') or '')
    job_state['lastResultManifestPath'] = str(result.get('result_manifest_path') or '')
    job_state['lastReturnCode'] = result.get('return_code')
    job_state['lastProcessAccepted'] = result.get('process_accepted')
    job_state['lastContractAccepted'] = result.get('contract_accepted')
    job_state['lastArtifactAccepted'] = result.get('artifact_accepted')
    job_state['lastExecutionAccepted'] = result.get('execution_accepted')
    job_state['lastAcceptedByLedger'] = result.get('accepted_by_ledger')
    job_state['lastBusinessStatus'] = result.get('business_status')
    job_state['lastFailureClass'] = result.get('failure_class')
    job_state['lastOutcomeManifestPath'] = str(result.get('outcome_manifest_path') or '')
    job_state['lastRecoveries'] = list(result.get('recoveries') or [])
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
        job_state['pendingRetry'] = None
    elif status == STATUS_RETRY_PENDING:
        job_state['currentStatus'] = STATUS_RETRY_PENDING
        job_state['lastFailedAt'] = job_state['lastFinishedAt']
        job_state['consecutiveFailures'] = int(job_state.get('consecutiveFailures') or 0) + 1


def _candidate_due(job: dict[str, Any], job_state: dict[str, Any], current: datetime, force_all: bool) -> tuple[bool, str | None, str | None]:
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
    schedule = json_object(job.get('schedule'))
    expr = str(schedule.get('expr') or '').strip()
    if expr:
        job_state['nextScheduledRunAt'] = next_cron_occurrence(expr, current)


def ensure_job_state(state: dict[str, Any], job_id: str, title: str) -> dict[str, Any]:
    return _job_state(state, job_id, title)


def dependency_block_reason(job: dict[str, Any], state: dict[str, Any], current: datetime) -> str | None:
    return _dependency_block_reason(job, state, current)


def candidate_due(job: dict[str, Any], job_state: dict[str, Any], current: datetime, force_all: bool) -> tuple[bool, str | None, str | None]:
    return _candidate_due(job, job_state, current, force_all)


def refresh_next_scheduled(job: dict[str, Any], job_state: dict[str, Any], current: datetime) -> None:
    _refresh_next_scheduled(job, job_state, current)


def execute_job_once(
    *,
    job: dict[str, Any],
    config: dict[str, Any],
    files,
    state: dict[str, Any],
    due_key: str,
    current: datetime,
    execution_env: dict[str, str],
) -> dict[str, Any]:
    """在保留依赖、job lock、契约与账本验收的前提下执行单个 job。

    参数：
        job（dict[str, Any]）：注册表中唯一解析出的已启用 job。
        config（dict[str, Any]）：完整控制平面注册表。
        files（未标注类型）：scheduler 状态、运行目录与历史文件集合。
        state（dict[str, Any]）：当前 mutable scheduler state。
        due_key（str）：本次人工执行的唯一 scheduler run id。
        current（datetime）：按 job 时区解析后的执行时间。
        execution_env（dict[str, str]）：仅注入当前子进程的受控业务与操作审计变量。

    返回：
        dict[str, Any]：包含业务状态及各接受维度的 scheduler 历史结果。

    副作用：
        执行目标 job、更新 scheduler state，并追加不可变 history JSONL。

    异常：
        CliError：job 已禁用时拒绝人工执行；底层执行或持久化异常继续向上传播。
    """
    if not bool(job.get('enabled', True)):
        raise CliError(f"job {job.get('id')} 未启用", 2)
    job_key = runtime_job_key(job)
    job_state = _job_state(state, job_key, str(job.get('title') or ''))
    local_id = local_job_id(job)
    if local_id and local_id != job_key:
        job_state.setdefault('localJobId', local_id)
    reason = _dependency_block_reason(job, state, current)
    if reason:
        blocked = _mark_blocked(
            job=job,
            job_state=job_state,
            files=files,
            due_key=due_key,
            current=current,
            reason=reason,
        )
        return blocked or _history_row(job=job, status=STATUS_BLOCKED, due_key=due_key, current=current, reason=reason)
    result = run_job(
        job=job,
        config=config,
        files=files,
        job_state=job_state,
        due_key=due_key,
        current=current,
        force_all=False,
        trigger_override='operator_once',
        execution_env=execution_env,
    )
    _update_job_state_after_run(job_state, result)
    if str(result.get('status') or '') in {STATUS_FAILED, STATUS_RETRY_PENDING}:
        _retry_metadata(job, job_state, result)
    else:
        job_state['pendingRetry'] = None
    result['effective_status'] = job_state.get('currentStatus')
    result['trigger'] = 'operator_once'
    append_jsonl(files.history_path, result)
    return result


def execute_due_jobs(
    *,
    config: dict[str, Any],
    files,
    state: dict[str, Any],
    force_all: bool = False,
    tick_started_at: datetime | None = None,
) -> dict[str, Any]:
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
        if str(result.get('status') or '') in {STATUS_FAILED, STATUS_RETRY_PENDING}:
            _retry_metadata(job, job_state, result)
        else:
            job_state['pendingRetry'] = None
        result['effective_status'] = job_state.get('currentStatus')
        result['trigger'] = trigger
        append_jsonl(files.history_path, result)
        executed.append(result)
    return {'executed': executed, 'blocked': blocked, 'executed_count': len(executed), 'blocked_count': len(blocked)}
