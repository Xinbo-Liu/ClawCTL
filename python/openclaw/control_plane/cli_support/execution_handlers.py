#!/usr/bin/env python3
"""Execution-oriented control-plane CLI handlers."""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Any

from openclaw.control_plane.cli_support import handler_support as cli_support
from openclaw.control_plane.cli_support.runtime_support import (
    load_scheduler_state,
    parse_preview_time,
    run_agent_runtime,
    state_root_from_arg,
)
from openclaw.control_plane.registry import (
    resolve_job_command,
    resolve_job_execution_plan,
    resolve_target_binding_ref_for_operation,
    resolve_target_operation_command,
)
from openclaw.lib.io.json_access import json_object
from openclaw.lib.runtime.time import app_timezone_name
from openclaw.scheduler.runtime import candidate_due, dependency_block_reason, ensure_job_state, refresh_next_scheduled, runtime_job_key


def _qualify_ref_for_extension(ref: str | None, extension_id: str | None) -> str:
    normalized_ref = str(ref or '').strip()
    normalized_extension = str(extension_id or '').strip()
    if not normalized_ref or not normalized_extension or ':' in normalized_ref:
        return normalized_ref
    return f'{normalized_extension}:{normalized_ref}'


def cmd_due_preview(args: argparse.Namespace) -> int:
    """预览当前到期 job 的调度判断结果。"""
    registry = cli_support._load_registry_from_args(args)
    defaults = json_object(registry.get('defaults'))
    default_tz = str(defaults.get('timezone') or app_timezone_name()).strip()
    state_root = Path(args.state_root).resolve()
    state = load_scheduler_state(state_root, registry)

    rows: list[dict[str, Any]] = []
    for job in registry.get('jobs', []):
        if not isinstance(job, dict):
            continue
        schedule = json_object(job.get('schedule'))
        timezone_name = str(schedule.get('tz') or default_tz)
        current = parse_preview_time(args.at, timezone_name)
        job_id = str(job.get('id') or '')
        job_key = runtime_job_key(job)
        job_runtime_state = ensure_job_state(state, job_key, str(job.get('title') or ''))
        refresh_next_scheduled(job, job_runtime_state, current)
        due, due_key, trigger = candidate_due(job, job_runtime_state, current, bool(args.force_all))
        blocked_reason = dependency_block_reason(job, state, current) if due and due_key else None
        rows.append({
            'id': job_id,
            'runtimeJobKey': job_key,
            'title': str(job.get('title') or ''),
            'enabled': bool(job.get('enabled', True)),
            'resolvedOrder': int(job.get('resolvedOrder') or job.get('order') or 0),
            'currentStatus': str(job_runtime_state.get('currentStatus') or ''),
            'nextScheduledRunAt': job_runtime_state.get('nextScheduledRunAt'),
            'due': bool(due and due_key),
            'dueKey': due_key,
            'trigger': trigger,
            'blocked': bool(blocked_reason),
            'blockedReason': blocked_reason,
            'pendingRetry': job_runtime_state.get('pendingRetry') if isinstance(job_runtime_state.get('pendingRetry'), dict) else None,
            'dependsOn': job.get('resolvedDependsOn') if isinstance(job.get('resolvedDependsOn'), list) else (job.get('dependsOn') if isinstance(job.get('dependsOn'), list) else []),
            'resolvedRecoveryStep': job.get('resolvedRecoveryStep') if isinstance(job.get('resolvedRecoveryStep'), dict) else {},
            'schedule': schedule,
        })
    payload = {
        'service': str((registry.get('service') or {}).get('name') or 'openclaw-control-plane'),
        'configPath': str(registry.get('configPath') or ''),
        'stateRoot': str(state_root),
        'previewAt': args.at or 'now',
        'forceAll': bool(args.force_all),
        'items': rows,
    }
    return cli_support._print_json(payload)


def cmd_resolve_job_command(args: argparse.Namespace) -> int:
    """解析单个 job 的最终执行命令。"""
    registry = cli_support._load_registry_from_args(args)
    job_ref = _qualify_ref_for_extension(args.job_id, getattr(args, 'extension', ''))
    command = resolve_job_command(registry, job_ref)
    return cli_support._print_json({
        'jobId': args.job_id,
        'resolvedJobRef': job_ref,
        'command': command,
    })


def cmd_resolve_job_plan(args: argparse.Namespace) -> int:
    """解析单个 job 的执行计划。"""
    registry = cli_support._load_registry_from_args(args)
    job_ref = _qualify_ref_for_extension(args.job_id, getattr(args, 'extension', ''))
    plan = resolve_job_execution_plan(registry, job_ref)
    return cli_support._print_json({
        'jobId': args.job_id,
        'resolvedJobRef': job_ref,
        'executionPlan': plan,
    })


def cmd_resolve_target_operation(args: argparse.Namespace) -> int:
    """解析 target operation 的最终执行命令。"""
    registry = cli_support._load_registry_from_args(args)
    passthrough = cli_support._passthrough_args(args.passthrough)
    resolved_target_binding_ref = resolve_target_binding_ref_for_operation(
        registry,
        target_binding_ref=_qualify_ref_for_extension(getattr(args, 'target_binding_ref', None), getattr(args, 'extension', '')),
        dispatch_target_id=getattr(args, 'dispatch_target_id', None),
        operation=args.operation,
        agent_ref=getattr(args, 'agent_ref', None),
    )
    command = resolve_target_operation_command(
        registry,
        target_binding_ref=resolved_target_binding_ref,
        operation=args.operation,
        extra_args=passthrough,
        agent_ref=getattr(args, 'agent_ref', None),
    )
    return cli_support._print_json({
        'targetBindingRef': resolved_target_binding_ref,
        'dispatchTargetId': getattr(args, 'dispatch_target_id', '') or None,
        'operation': args.operation,
        'agentRef': getattr(args, 'agent_ref', '') or None,
        'command': command,
    })


def cmd_run_target_operation(args: argparse.Namespace) -> int:
    """执行 target operation。"""
    registry = cli_support._load_registry_from_args(args)
    passthrough = cli_support._passthrough_args(args.passthrough)
    resolved_target_binding_ref = resolve_target_binding_ref_for_operation(
        registry,
        target_binding_ref=_qualify_ref_for_extension(getattr(args, 'target_binding_ref', None), getattr(args, 'extension', '')),
        dispatch_target_id=getattr(args, 'dispatch_target_id', None),
        operation=args.operation,
        agent_ref=getattr(args, 'agent_ref', None),
    )
    command = resolve_target_operation_command(
        registry,
        target_binding_ref=resolved_target_binding_ref,
        operation=args.operation,
        extra_args=passthrough,
        agent_ref=getattr(args, 'agent_ref', None),
    )
    process = subprocess.run(command, cwd=str(cli_support._repo_root()), check=False)
    return int(process.returncode)


def cmd_run_agent_runtime(args: argparse.Namespace) -> int:
    """执行已注册的 agent runtime。"""
    with cli_support._control_plane_config_override(cli_support._config_path_from_args(args)):
        registry = cli_support._load_registry_from_args(args)
        passthrough = cli_support._passthrough_args(args.passthrough)
        return run_agent_runtime(
            registry,
            agent_ref=args.agent_ref,
            passthrough=passthrough,
            state_root=state_root_from_arg(args.state_root),
        )
