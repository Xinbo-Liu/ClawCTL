#!/usr/bin/env python3
"""调度器 subprocess 执行流的支撑辅助。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from openclaw.control_plane.registry import CliError, resolve_job_command
from openclaw.control_plane.registry.job_execution_plans import (
    execution_plan_from_job,
    materialized_command_from_execution_plan,
)
from openclaw.control_plane.registry.store import write_json
from openclaw.lib.repo.layout import resolve_repo_root

HistoryRowBuilder = Callable[..., dict[str, Any]]
NowIsoBuilder = Callable[[], str]


def runtime_job_key(job: dict[str, Any]) -> str:
    return str(job.get('resolvedRuntimeJobKey') or job.get('qualifiedId') or job.get('id') or '').strip()


def local_job_id(job: dict[str, Any]) -> str:
    return str(job.get('id') or '').strip()


@dataclass(frozen=True)
class SubprocessRunContext:
    """单次 subprocess job 运行所需的完整上下文。"""

    job: dict[str, Any]
    config: dict[str, Any]
    due_key: str
    current: datetime
    command: list[str]
    timeout_seconds: int
    trigger: str
    started_at: str
    run_dir: Path
    log_path: Path
    artifacts_path: Path
    run_manifest_path: Path
    result_manifest_path: Path
    repo_root: Path


@dataclass(frozen=True)
class SubprocessExecutionOutcome:
    """subprocess 执行结果的标准化表示。"""

    status: str
    return_code: int | None
    duration_ms: int
    reason: str | None = None


def safe_fragment(value: object) -> str:
    text = str(value or '').strip()
    if not text:
        return 'item'
    return ''.join(ch if ch.isalnum() or ch in ('-', '_', '.') else '_' for ch in text)


def run_materialized_dir(files, job_id: str, due_key: str, started_at: str) -> Path:
    run_stamp = safe_fragment(started_at.replace(':', '').replace('+', '_'))
    due_stamp = safe_fragment(due_key)
    return files.runs_dir / safe_fragment(job_id) / f'{run_stamp}__{due_stamp}'


def write_run_manifests(
    run_dir: Path,
    run_payload: dict[str, Any],
    result_payload: dict[str, Any],
    artifacts_payload: dict[str, Any],
) -> None:
    write_json(run_dir / 'run.json', run_payload)
    write_json(run_dir / 'result.json', result_payload)
    write_json(run_dir / 'artifacts.json', artifacts_payload)


def build_command(job: dict[str, Any], config: dict[str, Any] | None = None) -> list[str]:
    """从 job 的执行计划解析最终命令，必要时回退到 registry 命令解析。"""
    plan = execution_plan_from_job(job)
    resolved = materialized_command_from_execution_plan(plan)
    if resolved:
        return resolved
    job_id = runtime_job_key(job)
    if not job_id or not isinstance(config, dict):
        raise CliError(f"任务 {job.get('id')} 缺少 resolvedExecutionPlan", 2)
    return resolve_job_command(config, job_id)


def resolve_command(command: list[str] | None, *, job: dict[str, Any], config: dict[str, Any]) -> list[str]:
    explicit = [str(item) for item in (command or []) if str(item).strip()]
    return explicit or build_command(job, config=config)


def blocked_result(
    *,
    job: dict[str, Any],
    due_key: str,
    current: datetime,
    history_row_builder: HistoryRowBuilder,
) -> dict[str, Any]:
    return history_row_builder(
        job=job,
        status='blocked',
        due_key=due_key,
        current=current,
        reason='concurrency_policy=forbid：已有活动运行',
        extra={'log_path': None, 'return_code': None, 'run_dir': None},
    )


def build_run_context(
    *,
    job: dict[str, Any],
    config: dict[str, Any],
    files,
    due_key: str,
    current: datetime,
    force_all: bool,
    command: list[str],
    now_utc_iso: NowIsoBuilder,
) -> SubprocessRunContext:
    timeout_seconds = max(1, int(job.get('timeoutSeconds') or 900))
    started_at = now_utc_iso()
    trigger = 'force_all' if force_all else 'schedule'
    run_dir = run_materialized_dir(files, runtime_job_key(job), due_key, started_at)
    log_path = run_dir / 'stdout.log'
    return SubprocessRunContext(
        job=job,
        config=config,
        due_key=due_key,
        current=current,
        command=command,
        timeout_seconds=timeout_seconds,
        trigger=trigger,
        started_at=started_at,
        run_dir=run_dir,
        log_path=log_path,
        artifacts_path=run_dir / 'artifacts.json',
        run_manifest_path=run_dir / 'run.json',
        result_manifest_path=run_dir / 'result.json',
        repo_root=resolve_repo_root(Path(__file__)),
    )


def run_payload(context: SubprocessRunContext) -> dict[str, Any]:
    job = context.job
    return {
        'schemaVersion': 1,
        'jobId': runtime_job_key(job),
        'localJobId': local_job_id(job),
        'qualifiedJobId': str(job.get('qualifiedId') or '').strip(),
        'title': str(job.get('title') or ''),
        'runId': context.due_key,
        'trigger': context.trigger,
        'scheduledFor': context.current.isoformat(),
        'startedAt': context.started_at,
        'timeoutSeconds': context.timeout_seconds,
        'command': context.command,
        'agentRef': str(job.get('agentRef') or ''),
        'modelProfileRef': str(
            job.get('resolvedModelProfileQualifiedRef')
            or job.get('resolvedModelProfileRef')
            or job.get('modelProfileRef')
            or ''
        ),
        'targetBindingRef': str(job.get('targetBindingRef') or ''),
        'groupRef': str(job.get('groupRef') or ''),
        'contract': job.get('resolvedContract') if isinstance(job.get('resolvedContract'), dict) else {},
        'recoveryStep': job.get('resolvedRecoveryStep') if isinstance(job.get('resolvedRecoveryStep'), dict) else {},
        'inputs': job.get('resolvedInputs') if isinstance(job.get('resolvedInputs'), dict) else {},
        'outputs': job.get('resolvedOutputs') if isinstance(job.get('resolvedOutputs'), dict) else {},
        'artifactPolicy': job.get('artifactPolicy') if isinstance(job.get('artifactPolicy'), dict) else {},
        'runDir': str(context.run_dir),
        'stdoutLogPath': str(context.log_path),
        'artifactsPath': str(context.artifacts_path),
        'resultPath': str(context.result_manifest_path),
    }


def running_result_payload(started_at: str) -> dict[str, Any]:
    return {
        'status': 'running',
        'updatedAt': started_at,
        'acceptedByLedger': None,
    }


def history_result(
    context: SubprocessRunContext,
    outcome: SubprocessExecutionOutcome,
    *,
    history_row_builder: HistoryRowBuilder,
) -> dict[str, Any]:
    return history_row_builder(
        job=context.job,
        status=outcome.status,
        due_key=context.due_key,
        current=context.current,
        reason=outcome.reason,
        extra={
            'started_at': context.started_at,
            'duration_ms': outcome.duration_ms,
            'return_code': outcome.return_code,
            'command': context.command,
            'log_path': str(context.log_path),
            'run_dir': str(context.run_dir),
            'run_manifest_path': str(context.run_manifest_path),
            'artifacts_path': str(context.artifacts_path),
            'result_manifest_path': str(context.result_manifest_path),
            'trigger': context.trigger,
        },
    )


def pre_execution_failure_result(
    context: SubprocessRunContext,
    exc: Exception,
    *,
    history_row_builder: HistoryRowBuilder,
) -> dict[str, Any]:
    return history_row_builder(
        job=context.job,
        status='failed',
        due_key=context.due_key,
        current=context.current,
        reason=f'执行前失败：{type(exc).__name__}: {exc}',
        extra={
            'started_at': context.started_at,
            'duration_ms': 0,
            'return_code': None,
            'command': context.command,
            'log_path': str(context.log_path),
            'run_dir': str(context.run_dir),
            'run_manifest_path': str(context.run_manifest_path),
            'artifacts_path': str(context.artifacts_path),
            'result_manifest_path': str(context.result_manifest_path),
            'trigger': context.trigger,
        },
    )


def setup_failure_result(
    *,
    job: dict[str, Any],
    due_key: str,
    current: datetime,
    command: list[str],
    trigger: str,
    exc: Exception,
    history_row_builder: HistoryRowBuilder,
) -> dict[str, Any]:
    return history_row_builder(
        job=job,
        status='failed',
        due_key=due_key,
        current=current,
        reason=f'执行前失败：{type(exc).__name__}: {exc}',
        extra={
            'started_at': None,
            'duration_ms': 0,
            'return_code': None,
            'command': command,
            'log_path': None,
            'run_dir': None,
            'run_manifest_path': None,
            'artifacts_path': None,
            'result_manifest_path': None,
            'trigger': trigger,
        },
    )


def result_payload(
    context: SubprocessRunContext,
    *,
    result: dict[str, Any],
    artifacts_payload: dict[str, Any],
    accepted_by_ledger: bool,
    finished_at: str,
) -> dict[str, Any]:
    return {
        'schemaVersion': 1,
        'jobId': runtime_job_key(context.job),
        'localJobId': local_job_id(context.job),
        'qualifiedJobId': str(context.job.get('qualifiedId') or '').strip(),
        'title': str(context.job.get('title') or ''),
        'runId': context.due_key,
        'status': str(result.get('status') or 'failed'),
        'trigger': context.trigger,
        'scheduledFor': context.current.isoformat(),
        'startedAt': context.started_at,
        'finishedAt': finished_at,
        'durationMs': int(result.get('duration_ms') or 0),
        'returnCode': result.get('return_code'),
        'reason': result.get('reason'),
        'command': context.command,
        'stdoutLogPath': str(context.log_path),
        'runDir': str(context.run_dir),
        'acceptedByLedger': accepted_by_ledger,
        'acceptance': artifacts_payload.get('acceptance') if isinstance(artifacts_payload.get('acceptance'), dict) else {},
    }
