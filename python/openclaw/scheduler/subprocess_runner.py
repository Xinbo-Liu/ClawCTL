#!/usr/bin/env python3
"""调度器 subprocess 执行辅助。"""
from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

from openclaw.control_plane.run_ledger import build_artifacts_manifest
from openclaw.lib.io.json_access import json_object
from openclaw.lib.repo.layout import CONTROL_PLANE_CONFIG_ENV, CONTROL_PLANE_PROFILE_ENV, resolve_selected_control_plane_config_path
from openclaw.lib.runtime.execution import build_subprocess_env
from openclaw.scheduler.subprocess_support import (
    HistoryRowBuilder,
    NowIsoBuilder,
    SubprocessExecutionOutcome,
    SubprocessRunContext,
    blocked_result,
    build_run_context,
    history_result,
    pre_execution_failure_result,
    resolve_command,
    result_payload,
    run_payload,
    running_result_payload,
    setup_failure_result,
    runtime_job_key,
    write_run_manifests,
)


def _running_artifacts_payload(context: SubprocessRunContext, *, env: dict[str, str]) -> dict[str, object]:
    return build_artifacts_manifest(
        job=context.job,
        run_id=context.due_key,
        stdout_log_path=context.log_path,
        result_status='running',
        started_at=context.started_at,
        env=env,
    )


def _mark_job_running(context: SubprocessRunContext, *, files, job_state: dict[str, object], env: dict[str, str]) -> None:
    context.run_dir.mkdir(parents=True, exist_ok=True)
    write_run_manifests(
        context.run_dir,
        run_payload(context),
        running_result_payload(context.started_at),
        _running_artifacts_payload(context, env=env),
    )
    job_state['currentStatus'] = 'running'
    job_state['activeRun'] = {'runId': context.due_key, 'startedAt': context.started_at, 'runDir': str(context.run_dir)}
    job_state['lastStartedAt'] = context.started_at
    job_state['lastScheduledFor'] = context.current.isoformat()


def _scheduler_env(context: SubprocessRunContext) -> dict[str, str]:
    env_config_path = ''
    if str(os.environ.get(CONTROL_PLANE_CONFIG_ENV) or '').strip() or str(os.environ.get(CONTROL_PLANE_PROFILE_ENV) or '').strip():
        env_config_path = str(resolve_selected_control_plane_config_path(start_path=Path(__file__)))
    config_path = str(
        context.config.get('configPath')
        or env_config_path
        or ''
    )
    return build_subprocess_env(
        Path(__file__),
        config_path=config_path,
        base_env=os.environ,
        extra_env={
            'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH': config_path,
            'OPENCLAW_RUNTIME_PATH_VIEW': 'scheduler',
            'OPENCLAW_AGENT_CALL_SOURCE': 'scheduler',
            'OPENCLAW_AGENT_CALLER': 'control-plane-scheduler',
            'OPENCLAW_CONTROL_PLANE_JOB_ID': runtime_job_key(context.job),
            'OPENCLAW_CONTROL_PLANE_LOCAL_JOB_ID': str(context.job.get('id') or ''),
            'OPENCLAW_CONTROL_PLANE_RUN_ID': context.due_key,
            'OPENCLAW_CONTROL_PLANE_TRIGGER': context.trigger,
            'OPENCLAW_CONTROL_PLANE_MODEL_PROFILE_REF': str(
                context.job.get('resolvedModelProfileQualifiedRef')
                or context.job.get('resolvedModelProfileRef')
                or context.job.get('modelProfileRef')
                or ''
            ),
            'OPENCLAW_CONTROL_PLANE_TARGET_BINDING_REF': str(context.job.get('targetBindingRef') or ''),
            'OPENCLAW_CONTROL_PLANE_GROUP_REF': str(context.job.get('groupRef') or ''),
        },
    )


def _execute_subprocess(context: SubprocessRunContext, *, env: dict[str, str]) -> SubprocessExecutionOutcome:
    started = time.monotonic()
    try:
        with context.log_path.open('w', encoding='utf-8') as log_fh:
            process = subprocess.run(
                context.command,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                cwd=str(context.repo_root),
                env=env,
                check=False,
                timeout=context.timeout_seconds,
            )
    except subprocess.TimeoutExpired:
        return SubprocessExecutionOutcome(
            status='failed',
            return_code=124,
            reason=f'timeout：超过 {context.timeout_seconds} 秒',
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    except OSError as exc:
        return SubprocessExecutionOutcome(
            status='failed',
            return_code=127,
            reason=f'执行失败：{exc}',
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    return SubprocessExecutionOutcome(
        status='succeeded' if process.returncode == 0 else 'failed',
        return_code=int(process.returncode),
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def _final_artifacts_payload(
    context: SubprocessRunContext,
    *,
    env: dict[str, str],
    final_status: str,
    finished_at: str,
) -> dict[str, object]:
    return build_artifacts_manifest(
        job=context.job,
        run_id=context.due_key,
        stdout_log_path=context.log_path,
        result_status=final_status,
        started_at=context.started_at,
        finished_at=finished_at,
        env=env,
    )


def _finalize_run(
    context: SubprocessRunContext,
    *,
    result: dict[str, object],
    env: dict[str, str],
    job_state: dict[str, object],
    now_utc_iso: NowIsoBuilder,
    release_lock,
    lock_path: Path,
) -> dict[str, object]:
    try:
        finished_at = str(result.get('finished_at') or now_utc_iso())
        final_status = str(result.get('status') or 'failed')
        artifacts_payload = _final_artifacts_payload(
            context,
            env=env,
            final_status=final_status,
            finished_at=finished_at,
        )
        acceptance = json_object(artifacts_payload.get('acceptance'))
        accepted_by_ledger = bool(final_status == 'succeeded' and acceptance.get('passed') is True)
        result['accepted_by_ledger'] = accepted_by_ledger
        write_run_manifests(
            context.run_dir,
            run_payload(context),
            result_payload(
                context,
                result=result,
                artifacts_payload=artifacts_payload,
                accepted_by_ledger=accepted_by_ledger,
                finished_at=finished_at,
            ),
            artifacts_payload,
        )
        return result
    finally:
        job_state['activeRun'] = None
        release_lock(lock_path)


def run_subprocess_job_impl(
    *,
    job: dict[str, object],
    config: dict[str, object],
    files,
    job_state: dict[str, object],
    due_key: str,
    current: datetime,
    force_all: bool = False,
    command: list[str] | None = None,
    lock_path: Path,
    stale_after_seconds: int,
    acquire_lock,
    release_lock,
    history_row_builder: HistoryRowBuilder,
    now_utc_iso: NowIsoBuilder,
) -> dict[str, object]:
    resolved_command = resolve_command(command, job=job, config=config)
    lock_payload = {
        'jobId': runtime_job_key(job),
        'localJobId': str(job.get('id') or ''),
        'runId': due_key,
        'startedAt': now_utc_iso(),
        'pid': os.getpid(),
    }
    if not acquire_lock(lock_path, lock_payload, stale_after_seconds=stale_after_seconds):
        return blocked_result(
            job=job,
            due_key=due_key,
            current=current,
            history_row_builder=history_row_builder,
        )

    trigger = 'force_all' if force_all else 'schedule'
    context: SubprocessRunContext | None = None
    env: dict[str, str] = {}
    result: dict[str, object] = {}
    run_marked = False
    try:
        context = build_run_context(
            job=job,
            config=config,
            files=files,
            due_key=due_key,
            current=current,
            force_all=force_all,
            command=resolved_command,
            now_utc_iso=now_utc_iso,
        )
        env = _scheduler_env(context)
        _mark_job_running(context, files=files, job_state=job_state, env=env)
        run_marked = True
        outcome = _execute_subprocess(context, env=env)
        result = history_result(
            context,
            outcome,
            history_row_builder=history_row_builder,
        )
    except Exception as exc:
        # 调度主循环必须把准备阶段异常转换成历史结果，避免单个 job 中断整轮 tick。
        result = (
            pre_execution_failure_result(
                context,
                exc,
                history_row_builder=history_row_builder,
            )
            if context is not None
            else setup_failure_result(
                job=job,
                due_key=due_key,
                current=current,
                command=resolved_command,
                trigger=trigger,
                exc=exc,
                history_row_builder=history_row_builder,
            )
        )
    finally:
        if run_marked and context is not None:
            result = _finalize_run(
                context,
                result=result,
                env=env,
                job_state=job_state,
                now_utc_iso=now_utc_iso,
                release_lock=release_lock,
                lock_path=lock_path,
            )
        else:
            job_state['activeRun'] = None
            release_lock(lock_path)
    return result
