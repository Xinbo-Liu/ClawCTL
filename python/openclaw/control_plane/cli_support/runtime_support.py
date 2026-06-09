#!/usr/bin/env python3
"""控制平面 CLI handler 的运行态辅助。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from openclaw.control_plane.agent.access_log import append_agent_access_log, infer_call_context
from openclaw.control_plane.registry import CliError
from openclaw.control_plane.registry.owners import resolve_collection_ref, row_owner_id, qualified_registry_id
from openclaw.control_plane.registry.store import read_json, runtime_files
from openclaw.control_plane.runtime.adapter_registry import runner as runtime_runner, runtime_adapter_specs
from openclaw.control_plane.runtime.adapters import resolve_runtime_tokens
from openclaw.lib.io.json_access import json_array, json_object
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.runtime.state import resolve_state_root
from openclaw.lib.runtime.time import now_in_app_tz, now_utc, parse_iso_datetime, utc_iso
from openclaw.scheduler.runtime import resolve_timezone


def parse_preview_time(value: str | None, timezone_name: str) -> datetime:
    if not str(value or '').strip():
        return now_in_app_tz(timezone_name).replace(second=0, microsecond=0)
    text = str(value).strip()
    parsed = parse_iso_datetime(text, assume_tz=resolve_timezone(timezone_name))
    if parsed is None:
        raise CliError(f'--at 不是合法 ISO 时间：{text}', 2)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=resolve_timezone(timezone_name))
    return parsed.astimezone(resolve_timezone(timezone_name)).replace(second=0, microsecond=0)


def load_scheduler_state(state_root: Path, registry: dict[str, Any]) -> dict[str, Any]:
    files = runtime_files(state_root, registry)
    state_path = files.state_dir / 'state.json'
    state = read_json(state_path, {'schemaVersion': 1, 'jobs': {}})
    if not isinstance(state, dict):
        state = {'schemaVersion': 1, 'jobs': {}}
    if not isinstance(state.get('jobs'), dict):
        state['jobs'] = {}
    return state


def state_root_from_arg(value: str | None) -> Path:
    text = str(value or '').strip()
    return Path(text).resolve() if text else resolve_state_root(view='host')


def run_agent_runtime(registry: dict[str, Any], *, agent_ref: str, passthrough: list[str], state_root: Path) -> int:
    agent = resolve_collection_ref(registry, 'agents', str(agent_ref or '').strip(), label='agentRef')
    resolved_agent_ref = str(agent.get('qualifiedId') or qualified_registry_id(row_owner_id(agent), agent.get('id')))
    implementation_ref = str(agent.get('resolvedImplementationRef') or '').strip()
    if not implementation_ref:
        raise CliError(f'agent {agent_ref} 缺少 resolvedImplementationRef', 2)
    runtime_binding = agent.get('resolvedRuntime') if isinstance(agent.get('resolvedRuntime'), dict) else None
    if not isinstance(runtime_binding, dict):
        raise CliError(f'agent {agent_ref} 缺少 resolvedRuntime：{implementation_ref}', 2)
    runtime_adapter = agent.get('resolvedRuntimeAdapter') if isinstance(agent.get('resolvedRuntimeAdapter'), dict) else None
    adapter_ref = str(agent.get('resolvedRuntimeAdapterRef') or runtime_binding.get('adapterRef') or '').strip()
    spec = runtime_adapter_specs({'adapters': registry.get('runtimeAdapters', [])}).get(adapter_ref)
    if spec is None or not isinstance(runtime_adapter, dict):
        raise CliError(f'agent {agent_ref} 缺少 resolvedRuntimeAdapter：{implementation_ref}', 2)
    repo_root = resolve_repo_root(Path(__file__))
    runtime_args = [
        *resolve_runtime_tokens([str(item) for item in json_array(runtime_binding.get('defaultArgs'))], state_root=state_root, repo_root=repo_root),
        *passthrough,
    ]
    governance = json_object(agent.get('governance'))
    agent_group_refs = [str(item).strip() for item in json_array(agent.get('resolvedGroupRefs')) if str(item).strip()]
    agent_module_ref = str(governance.get('moduleRef') or '').strip()
    call_context = infer_call_context()
    started = now_utc()
    started_at = utc_iso(started)
    started_epoch = started.timestamp()
    exit_code: int | None = None
    status = 'error'
    error_message = ''
    try:
        completed_exit_code: int = int(
            runtime_runner(spec)(
                runtime_config=runtime_binding.get('config') if isinstance(runtime_binding.get('config'), dict) else {},
                runtime_args=runtime_args,
                state_root=state_root,
                repo_root=repo_root,
                agent_ref=resolved_agent_ref,
                implementation_ref=implementation_ref,
            )
            or 0
        )
        exit_code = completed_exit_code
        status = 'succeeded' if exit_code == 0 else 'failed'
        return completed_exit_code
    except Exception as exc:
        # runner 异常需要原样继续抛出，但 finally 仍负责记录访问日志。
        error_message = str(exc)
        status = 'error'
        raise
    finally:
        finished = now_utc()
        finished_at = utc_iso(finished)
        duration_ms = int(max(0.0, finished.timestamp() - started_epoch) * 1000)
        try:
            append_agent_access_log(
                registry,
                state_root=state_root,
                agent_ref=str(agent_ref or '').strip(),
                implementation_ref=implementation_ref,
                runtime_adapter_ref=adapter_ref,
                agent_group_refs=agent_group_refs,
                agent_module_ref=agent_module_ref,
                runtime_args=runtime_args,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
                exit_code=exit_code,
                status=status,
                error=error_message,
                source=call_context.get('source') or '',
                caller=call_context.get('caller') or '',
                job_id=call_context.get('jobId') or '',
                run_id=call_context.get('runId') or '',
                trigger=call_context.get('trigger') or '',
            )
        except Exception as log_exc:
            # 访问日志是观测面；写入失败只报警，不改变 agent 运行结果。
            import sys

            sys.stderr.write(f'[control_plane_cli][WARN] agent access log 记录失败：{log_exc}\n')
