#!/usr/bin/env python3
"""Neutral runner for extension-owned agent bindings."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from openclaw.control_plane.registry.job_execution_plans import (
    execution_plan_from_job,
    materialized_command_from_execution_plan,
)
from openclaw.lib.cli.common import CliError
from openclaw.lib.io.json_access import json_array, json_object
from openclaw.scheduler.runtime import run_subprocess_job


def prepare_job(*, job: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    execution_plan = execution_plan_from_job(job)
    command = materialized_command_from_execution_plan(execution_plan)
    if not command:
        raise CliError(f"job {job.get('id')} resolvedExecutionPlan 未提供可执行 command", 2)
    executor = json_object(execution_plan.get('executor') or job.get('executor'))
    operation_ref = str(execution_plan.get('operationRef') or job.get('operationRef') or '').strip()
    return {
        'jobId': str(job.get('id') or ''),
        'runnerRef': str(execution_plan.get('runnerRef') or ''),
        'operationRef': operation_ref,
        'executor': dict(executor) if executor else {},
        'executionPlan': execution_plan,
        'command': command,
    }


def build_execution_plan(*, job: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    return prepare_job(job=job, config=config)


def run_job(*, job: dict[str, Any], config: dict[str, Any], files, job_state: dict[str, Any], due_key: str, current: datetime, force_all: bool = False) -> dict[str, Any]:
    plan = build_execution_plan(job=job, config=config)
    return run_subprocess_job(
        job=job,
        config=config,
        files=files,
        job_state=job_state,
        due_key=due_key,
        current=current,
        force_all=force_all,
        command=[str(item) for item in json_array(plan.get('command'))],
    )
