#!/usr/bin/env python3
"""调度器 subprocess 执行辅助。"""
from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

from openclaw.control_plane.dispatch.delivery_outcome import (
    OUTCOME_PATH_ENV,
    SCHEDULER_RUN_ID_ENV,
    is_delivery_job,
    load_and_validate_outcome_manifest,
)
from openclaw.control_plane.run_ledger import build_artifacts_manifest
from openclaw.control_plane.registry.store import read_json
from openclaw.lib.io.json_access import json_array, json_object
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
    safe_fragment,
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
    extra_env = {
        'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH': config_path,
        'OPENCLAW_RUNTIME_PATH_VIEW': 'scheduler',
        'OPENCLAW_AGENT_CALL_SOURCE': 'scheduler',
        'OPENCLAW_AGENT_CALLER': 'control-plane-scheduler',
        'OPENCLAW_CONTROL_PLANE_JOB_ID': runtime_job_key(context.job),
        'OPENCLAW_CONTROL_PLANE_LOCAL_JOB_ID': str(context.job.get('id') or ''),
        'OPENCLAW_CONTROL_PLANE_RUN_ID': context.due_key,
        SCHEDULER_RUN_ID_ENV: context.due_key,
        'OPENCLAW_CONTROL_PLANE_TRIGGER': context.trigger,
        'OPENCLAW_CONTROL_PLANE_MODEL_PROFILE_REF': str(
            context.job.get('resolvedModelProfileQualifiedRef')
            or context.job.get('resolvedModelProfileRef')
            or context.job.get('modelProfileRef')
            or ''
        ),
        'OPENCLAW_CONTROL_PLANE_TARGET_BINDING_REF': str(context.job.get('targetBindingRef') or ''),
        'OPENCLAW_CONTROL_PLANE_GROUP_REF': str(context.job.get('groupRef') or ''),
    }
    if is_delivery_job(context.job):
        extra_env[OUTCOME_PATH_ENV] = str(context.outcome_path)
    execution_env = getattr(context, 'execution_env', {})
    extra_env.update({str(key): str(value) for key, value in execution_env.items()})
    return build_subprocess_env(
        Path(__file__),
        config_path=config_path,
        base_env=os.environ,
        extra_env=extra_env,
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


def _delivery_artifacts_payload(
    context: SubprocessRunContext,
    *,
    validation: dict[str, object],
    finished_at: str,
) -> dict[str, object]:
    """从 outcome 精确证据构造 delivery job 的 artifacts manifest。

    参数：
        context（SubprocessRunContext）：当前 job、运行目录和 outcome 路径上下文。
        validation（dict[str, object]）：统一 outcome 验收器的结构化结果。
        finished_at（str）：子进程结束时间的 ISO 8601 文本。

    返回：
        dict[str, object]：只列出 outcome 精确证据及接受维度的 artifacts manifest。
    """
    evidence = [dict(item) for item in (validation.get('evidence') or []) if isinstance(item, dict)]
    artifact_policy = json_object(context.job.get('artifactPolicy'))
    return {
        'schemaVersion': 1,
        'generatedAt': finished_at,
        'jobId': runtime_job_key(context.job),
        'localJobId': str(context.job.get('id') or ''),
        'qualifiedJobId': str(context.job.get('qualifiedId') or ''),
        'runId': context.due_key,
        'stdoutLogPath': str(context.log_path),
        'declaredInputArtifacts': list(json_object(context.job.get('resolvedInputs')).get('artifacts') or []),
        'declaredOutputArtifacts': list(json_object(context.job.get('resolvedOutputs')).get('artifacts') or []),
        'declaredStatusSignals': list(json_object(context.job.get('resolvedOutputs')).get('statusSignals') or []),
        'artifactPolicy': {
            'runArtifactRoot': str(artifact_policy.get('runArtifactRoot') or '') or None,
            'latestAlias': str(artifact_policy.get('latestAlias') or '') or None,
            'retentionDays': int(artifact_policy.get('retentionDays') or 0) if artifact_policy else None,
        },
        'latestEntries': [],
        'observedEntries': evidence,
        'schedulerEntries': [],
        'deliveryOutcomePath': str(context.outcome_path),
        'acceptance': {
            'status': 'pass' if validation.get('artifactAccepted') is True else 'fail',
            'passed': validation.get('artifactAccepted') is True,
            'hasDeclaredOutputs': True,
            'artifactRootConfigured': bool(artifact_policy.get('runArtifactRoot')),
            'artifactRootPresent': None,
            'evidencePresent': bool(evidence),
            'evidenceSources': ['delivery_outcome_manifest'] if evidence else [],
            'reasons': list(validation.get('reasons') or []),
            'startedAt': context.started_at,
            'finishedAt': finished_at,
        },
    }


def _job_for_recovery_ref(config: dict[str, object], job_ref: str) -> dict[str, object] | None:
    """从配置中唯一解析 recovery 引用的来源 job。

    参数：
        config（dict[str, object]）：当前完整控制平面配置。
        job_ref（str）：recovery 行声明的 local、qualified 或 runtime job 引用。

    返回：
        dict[str, object] | None：唯一匹配的来源 job；缺失或不唯一时返回 ``None``。
    """
    matches = [
        job
        for job in json_array(config.get('jobs'))
        if isinstance(job, dict)
        and job_ref in {
            str(job.get('id') or ''),
            str(job.get('qualifiedId') or ''),
            str(job.get('resolvedRuntimeJobKey') or ''),
        }
    ]
    return matches[0] if len(matches) == 1 else None


def _recovery_origin_reasons(
    context: SubprocessRunContext,
    manifest: dict[str, object],
) -> list[str]:
    """验证 recovery 引用确实指向同业务运行的未接受 scheduler 结果。

    参数：
        context（SubprocessRunContext）：当前恢复 job 的配置与 runs 根目录上下文。
        manifest（dict[str, object]）：已通过基础 schema 校验的 outcome manifest。

    返回：
        list[str]：来源 job/run、业务日期或原接受状态不匹配的原因码；空列表表示有效。
    """
    reasons: list[str] = []
    for index, recovery in enumerate(json_array(manifest.get('recoveries'))):
        if not isinstance(recovery, dict):
            continue
        job_ref = str(recovery.get('ofJobId') or '').strip()
        origin_run_id = str(recovery.get('ofSchedulerRunId') or '').strip()
        business_run_id = str(recovery.get('businessRunId') or '').strip()
        origin_job = _job_for_recovery_ref(context.config, job_ref)
        if origin_job is None:
            reasons.append(f'recoveries[{index}]_origin_job_not_unique_or_missing')
            continue
        origin_job_key = runtime_job_key(origin_job)
        origin_root = context.runs_root / safe_fragment(origin_job_key)
        matches: list[dict[str, object]] = []
        if origin_root.is_dir():
            for result_path in sorted(origin_root.glob('*/result.json')):
                payload = read_json(result_path, None)
                if isinstance(payload, dict) and str(payload.get('runId') or '') == origin_run_id:
                    matches.append(payload)
        if len(matches) != 1:
            reasons.append(f'recoveries[{index}]_origin_run_not_unique_or_missing')
            continue
        origin = matches[0]
        if str(origin.get('jobId') or '') != origin_job_key:
            reasons.append(f'recoveries[{index}]_origin_result_job_mismatch')
        if str(origin.get('businessRunId') or '') != business_run_id:
            reasons.append(f'recoveries[{index}]_origin_result_business_run_mismatch')
        if origin.get('acceptedByLedger') is not False:
            reasons.append(f'recoveries[{index}]_origin_result_not_unaccepted')
        if str(origin.get('status') or '') not in {'failed', 'blocked', 'retry_pending'}:
            reasons.append(f'recoveries[{index}]_origin_result_status_not_recoverable')
    return reasons


def _validate_recovery_origins(
    context: SubprocessRunContext,
    validation: dict[str, object],
) -> dict[str, object]:
    """把 recovery 来源验收合并回统一 outcome 验收结果。

    参数：
        context（SubprocessRunContext）：当前恢复 job 的运行与配置上下文。
        validation（dict[str, object]）：基础 outcome manifest 验收结果。

    返回：
        dict[str, object]：来源有效时返回原结果；无效时返回 fail-closed 的 blocked 副本。
    """
    manifest = json_object(validation.get('manifest'))
    if not json_array(manifest.get('recoveries')):
        return validation
    reasons = _recovery_origin_reasons(context, manifest)
    if not reasons:
        return validation
    updated = dict(validation)
    updated['manifestValid'] = False
    updated['contractAccepted'] = False
    updated['schedulerStatus'] = 'blocked'
    updated['failureClass'] = 'target_contract_violation'
    updated['reasons'] = [*list(validation.get('reasons') or []), *reasons]
    return updated


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
        process_status = str(result.get('status') or 'failed')
        process_accepted = bool(process_status == 'succeeded' and result.get('return_code') == 0)
        result['process_accepted'] = process_accepted
        if is_delivery_job(context.job):
            validation = load_and_validate_outcome_manifest(
                context.outcome_path,
                job=context.job,
                scheduler_run_id=context.due_key,
                started_at=context.started_at,
                env=env,
                expected_business_run_id=str(env.get('OPENCLAW_CONTROL_PLANE_BUSINESS_RUN_ID') or '').strip() or None,
            )
            validation = _validate_recovery_origins(context, validation)
            manifest = json_object(validation.get('manifest'))
            contract_accepted = validation.get('contractAccepted') is True
            artifact_accepted = validation.get('artifactAccepted') is True
            execution_accepted = bool(process_accepted and contract_accepted)
            accepted_by_ledger = bool(execution_accepted and artifact_accepted)
            final_status = str(validation.get('schedulerStatus') or 'failed') if process_accepted else 'failed'
            failure_class = str(validation.get('failureClass') or '').strip() or None
            if not process_accepted:
                failure_class = 'process_execution_failure'
            result['status'] = final_status
            result['contract_accepted'] = contract_accepted
            result['artifact_accepted'] = artifact_accepted
            result['execution_accepted'] = execution_accepted
            result['business_status'] = str(manifest.get('status') or '').strip() or None
            result['business_run_id'] = str(manifest.get('businessRunId') or '').strip() or None
            result['failure_class'] = failure_class
            result['status_signals'] = list(manifest.get('statusSignals') or [])
            result['recoveries'] = list(manifest.get('recoveries') or [])
            result['outcome_manifest_path'] = str(context.outcome_path)
            if not accepted_by_ledger:
                reasons = [str(item) for item in (validation.get('reasons') or [])]
                if not process_accepted:
                    reasons.insert(0, f'process_return_code={result.get("return_code")}')
                if reasons:
                    result['reason'] = '; '.join(reasons)
            artifacts_payload = _delivery_artifacts_payload(
                context,
                validation=validation,
                finished_at=finished_at,
            )
        else:
            final_status = process_status
            artifacts_payload = _final_artifacts_payload(
                context,
                env=env,
                final_status=final_status,
                finished_at=finished_at,
            )
            acceptance = json_object(artifacts_payload.get('acceptance'))
            artifact_accepted = acceptance.get('passed') is True
            contract_accepted = None
            execution_accepted = process_accepted
            accepted_by_ledger = bool(execution_accepted and artifact_accepted)
            result['contract_accepted'] = contract_accepted
            result['artifact_accepted'] = artifact_accepted
            result['execution_accepted'] = execution_accepted
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
    trigger_override: str | None = None,
    execution_env: dict[str, str] | None = None,
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

    trigger = str(trigger_override or ('force_all' if force_all else 'schedule'))
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
            trigger_override=trigger,
            execution_env=execution_env,
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
