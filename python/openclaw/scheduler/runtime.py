#!/usr/bin/env python3
"""统一控制平面调度器。"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

from openclaw.control_plane.evidence_export import export_agent_group_evidence
from openclaw.control_plane.registry import load_registry
from openclaw.control_plane.registry.store import read_json, runtime_files, write_json
from openclaw.control_plane.state_paths import resolve_control_plane_state_root
from openclaw.lib.repo.layout import resolve_repo_root, resolve_selected_control_plane_config_path
from openclaw.setup.deploy_env.query import parse_env_file
from openclaw.runtime.generated_paths.gateway.cron import (
    build_gateway_cron_jobs_payload,
    gateway_cron_jobs_state_path_from_gateway_state_root,
)
from openclaw.scheduler import locking as scheduler_locking
from openclaw.scheduler.cron import resolve_timezone
from openclaw.scheduler.engine import (
    STATUS_BLOCKED,
    STATUS_FAILED,
    STATUS_RETRY_PENDING,
    STATUS_RUNNING,
    STATUS_SCHEDULED,
    STATUS_SUCCEEDED,
    candidate_due,
    dependency_block_reason,
    ensure_job_state,
    execute_due_jobs,
    make_due_key,
    now_utc_iso,
    refresh_next_scheduled,
    run_subprocess_job,
    runtime_job_key,
)
from openclaw.scheduler.evidence import maybe_export_agent_group_evidence
from openclaw.scheduler.status_surface import (
    build_status_payload as _build_status_payload,
    next_due_sleep_seconds as _next_due_sleep_seconds,
    payload_fingerprint as _payload_fingerprint,
    write_json_if_dirty as _write_json_if_dirty,
)

_STOP = False
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 60.0
SCHEDULER_CYCLE_LOCK_NAME = '.scheduler-cycle.lock'
SCHEDULER_MAINTENANCE_FILE_NAME = 'scheduler_maintenance.json'


def _handle_stop(*_: object) -> None:
    """处理停止信号。"""
    global _STOP
    _STOP = True


def fail(message: str, exit_code: int = 2) -> int:
    """输出调度器统一失败消息。"""
    sys.stderr.write(f'[control_plane_scheduler][FAIL] {message}\n')
    return exit_code


def _maybe_export_agent_group_evidence(*, config: dict[str, object], state_root: Path, execution: dict[str, object]) -> dict[str, object] | None:
    """按条件导出 agent group evidence。"""
    return maybe_export_agent_group_evidence(
        config=config,
        state_root=state_root,
        execution=execution,
        exporter=export_agent_group_evidence,
        generated_at=now_utc_iso,
        base_root=resolve_repo_root(Path(__file__)),
        warn=sys.stderr.write,
    )


def _sync_gateway_cron_jobs_projection(
    *,
    state_root: Path,
    config: dict[str, object],
    state: dict[str, object],
    previous_fingerprint: str | None,
) -> str:
    payload = build_gateway_cron_jobs_payload(config, scheduler_state=state)
    gateway_state_dir = str(os.environ.get('OPENCLAW_GATEWAY_STATE_DIR') or '').strip()
    gateway_state_root = Path(gateway_state_dir).resolve() if gateway_state_dir else state_root / 'gateway'
    target = gateway_cron_jobs_state_path_from_gateway_state_root(gateway_state_root)
    actual_payload = read_json(target, None)
    actual_fingerprint = _payload_fingerprint(actual_payload) if actual_payload is not None else None
    effective_previous = previous_fingerprint if actual_fingerprint == previous_fingerprint else None
    _, fingerprint = _write_json_if_dirty(target, payload, effective_previous, write_json=write_json)
    return fingerprint


def build_parser() -> argparse.ArgumentParser:
    """构建 scheduler runtime CLI 解析器。"""
    parser = argparse.ArgumentParser(prog='python -m openclaw.cli control-plane scheduler-runtime')
    parser.add_argument('--config-path', default='')
    parser.add_argument('--control-plane-profile', default='')
    parser.add_argument('--env-file', default='')
    parser.add_argument('--state-root', default='')
    parser.add_argument('--interval-seconds', type=float, default=float(os.environ.get('OPENCLAW_CONTROL_PLANE_TICK_SECONDS', '15')))
    parser.add_argument('--heartbeat-interval-seconds', type=float, default=float(os.environ.get('OPENCLAW_CONTROL_PLANE_HEARTBEAT_INTERVAL_SECONDS', str(DEFAULT_HEARTBEAT_INTERVAL_SECONDS))))
    parser.add_argument('--once', action='store_true', help='仅执行一次 tick 后退出')
    parser.add_argument('--run-all-once', action='store_true', help='忽略 schedule，顺序执行所有启用任务一次')
    parser.add_argument('--healthcheck', action='store_true', help='仅检查 heartbeat 是否新鲜')
    parser.add_argument('--max-stale-seconds', type=int, default=int(os.environ.get('OPENCLAW_CONTROL_PLANE_MAX_STALE_SECONDS', '900')))
    return parser


def _load_env_file_into_process(env_file: str) -> None:
    env_path_text = str(env_file or '').strip()
    if not env_path_text:
        return
    env_path = Path(env_path_text).resolve()
    if not env_path.is_file():
        raise ValueError(f'--env-file 不存在：{env_path}')
    for key, value in parse_env_file(env_path).items():
        if key:
            os.environ[str(key)] = str(value)


def _flag_provided(argv: list[str], flag: str) -> bool:
    return any(token == flag or token.startswith(f'{flag}=') for token in argv)


def _apply_env_backed_defaults(args, argv: list[str]) -> int:
    try:
        if not _flag_provided(argv, '--interval-seconds'):
            args.interval_seconds = float(os.environ.get('OPENCLAW_CONTROL_PLANE_TICK_SECONDS', str(args.interval_seconds)))
        if not _flag_provided(argv, '--heartbeat-interval-seconds'):
            args.heartbeat_interval_seconds = float(os.environ.get('OPENCLAW_CONTROL_PLANE_HEARTBEAT_INTERVAL_SECONDS', str(args.heartbeat_interval_seconds)))
        if not _flag_provided(argv, '--max-stale-seconds'):
            args.max_stale_seconds = int(os.environ.get('OPENCLAW_CONTROL_PLANE_MAX_STALE_SECONDS', str(args.max_stale_seconds)))
    except ValueError as exc:
        return fail(f'env-file 中的 scheduler runtime 数值参数无效：{exc}', 2)
    return 0


def _resolve_config_path(args) -> Path:
    return resolve_selected_control_plane_config_path(
        str(args.config_path or '').strip() or None,
        control_plane_profile=str(args.control_plane_profile or '').strip() or None,
        start_path=Path(__file__),
        default_to_base=True,
    ).resolve()


def run_healthcheck(files, max_stale_seconds: int) -> int:
    """执行调度器运行态健康检查。"""
    payload = read_json(files.heartbeat_path, None)
    if not isinstance(payload, dict):
        return fail(f'缺少 heartbeat：{files.heartbeat_path}', 3)
    updated_at = int(payload.get('updated_at_epoch') or 0)
    if updated_at <= 0:
        return fail('heartbeat 缺少 updated_at_epoch', 3)
    age = int(time.time()) - updated_at
    if age > max(30, int(max_stale_seconds)):
        return fail(f'heartbeat 已过期：age={age}s > max={max_stale_seconds}s', 4)
    return 0


def _cycle_lock_path(files) -> Path:
    return files.locks_dir / SCHEDULER_CYCLE_LOCK_NAME


def _cycle_lock_stale_after_seconds(args) -> int:
    interval_seconds = max(float(args.interval_seconds), float(args.heartbeat_interval_seconds), 60.0)
    return scheduler_locking.scheduler_cycle_lock_stale_after_seconds(int(interval_seconds))


def _cycle_lock_payload(args) -> dict[str, object]:
    return {
        'kind': 'scheduler_cycle',
        'once': bool(args.once),
        'runAllOnce': bool(args.run_all_once),
    }


def _cycle_lock_busy_sleep_seconds(args) -> float:
    return max(1.0, min(float(args.interval_seconds), float(args.heartbeat_interval_seconds)))


def scheduler_maintenance_path(state_root: Path) -> Path:
    """返回 scheduler 维护态文件路径。"""
    return state_root / SCHEDULER_MAINTENANCE_FILE_NAME


def read_scheduler_maintenance(state_root: Path) -> dict[str, object]:
    """读取 scheduler 维护态；文件损坏时按未维护处理。"""
    payload = read_json(scheduler_maintenance_path(state_root), {})
    return payload if isinstance(payload, dict) else {}


def write_scheduler_maintenance(
    state_root: Path,
    *,
    enabled: bool,
    reason: str = '',
    run_id: str = '',
) -> dict[str, object]:
    """写入 scheduler 维护态，并返回落盘载荷。"""
    payload: dict[str, object] = {
        'schemaVersion': 1,
        'enabled': bool(enabled),
        'updatedAt': now_utc_iso(),
    }
    if reason:
        payload['reason'] = reason
    if run_id:
        payload['runId'] = run_id
    target = scheduler_maintenance_path(state_root)
    write_json(target, payload)
    payload['path'] = str(target)
    return payload


def _maintenance_state_root(args: argparse.Namespace) -> Path:
    text = str(getattr(args, 'state_root', '') or '').strip()
    return Path(text).resolve() if text else resolve_control_plane_state_root()


def maintenance_entry(argv: list[str]) -> int:
    """处理 scheduler-runtime maintenance 子命令。"""
    parser = argparse.ArgumentParser(prog='python -m openclaw.cli control-plane scheduler-runtime maintenance')
    subparsers = parser.add_subparsers(dest='command', required=True)
    for command in ('enable', 'disable', 'status'):
        sub = subparsers.add_parser(command)
        sub.add_argument('--state-root', default='')
        sub.add_argument('--reason', default='')
        sub.add_argument('--run-id', default='')
        sub.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    state_root = _maintenance_state_root(args)
    if args.command == 'enable':
        payload = write_scheduler_maintenance(
            state_root,
            enabled=True,
            reason=str(args.reason or 'upgrade_maintenance'),
            run_id=str(args.run_id or ''),
        )
    elif args.command == 'disable':
        payload = write_scheduler_maintenance(
            state_root,
            enabled=False,
            reason=str(args.reason or 'maintenance_complete'),
            run_id=str(args.run_id or ''),
        )
    else:
        payload = read_scheduler_maintenance(state_root)
        payload = {
            'schemaVersion': 1,
            'enabled': bool(payload.get('enabled')) if isinstance(payload, dict) else False,
            'path': str(scheduler_maintenance_path(state_root)),
            **(payload if isinstance(payload, dict) else {}),
        }
    if bool(getattr(args, 'json', False)):
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    else:
        status = 'enabled' if bool(payload.get('enabled')) else 'disabled'
        sys.stdout.write(f'scheduler maintenance {status}: {payload.get("path") or scheduler_maintenance_path(state_root)}\n')
    return 0


def main(argv: list[str] | None = None) -> int:
    """调度器运行时主入口。"""
    global _STOP
    _STOP = False
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv[:1] == ['maintenance']:
        return maintenance_entry(raw_argv[1:])
    args = build_parser().parse_args(raw_argv)
    try:
        _load_env_file_into_process(str(args.env_file or ''))
    except ValueError as exc:
        return fail(str(exc), 2)
    defaults_exit = _apply_env_backed_defaults(args, raw_argv)
    if defaults_exit != 0:
        return defaults_exit
    if args.interval_seconds <= 0:
        return fail('--interval-seconds 必须大于 0', 2)
    if args.heartbeat_interval_seconds <= 0:
        return fail('--heartbeat-interval-seconds 必须大于 0', 2)
    try:
        config_path = str(_resolve_config_path(args))
    except ValueError as exc:
        return fail(str(exc), 2)
    config = load_registry(Path(config_path))
    state_root = Path(args.state_root).resolve() if str(args.state_root or '').strip() else resolve_control_plane_state_root()
    files = runtime_files(state_root, config)
    if args.healthcheck:
        return run_healthcheck(files, args.max_stale_seconds)

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    state_path = files.state_dir / 'state.json'
    last_state_fingerprint: str | None = None
    last_status_fingerprint: str | None = None
    last_heartbeat_fingerprint: str | None = None
    last_gateway_cron_fingerprint: str | None = None
    last_heartbeat_written_monotonic = 0.0

    while not _STOP:
        cycle_started = time.monotonic()
        cycle_lock_path = _cycle_lock_path(files)
        cycle_lock_acquired = scheduler_locking.acquire_lock(
            cycle_lock_path,
            _cycle_lock_payload(args),
            stale_after_seconds=_cycle_lock_stale_after_seconds(args),
        )
        if not cycle_lock_acquired:
            if args.once or args.run_all_once:
                return fail(f'scheduler cycle lock busy：{cycle_lock_path}', 5)
            sys.stderr.write(f'[control_plane_scheduler][WARN] scheduler cycle lock busy，跳过当前 tick：{cycle_lock_path}\n')
            time.sleep(_cycle_lock_busy_sleep_seconds(args))
            continue
        try:
            state = read_json(state_path, {'schemaVersion': 1, 'jobs': {}})
            if not isinstance(state, dict):
                state = {'schemaVersion': 1, 'jobs': {}}
            maintenance = read_scheduler_maintenance(state_root)
            if bool(maintenance.get('enabled')):
                execution = {
                    'schemaVersion': 1,
                    'mode': 'maintenance',
                    'status': 'maintenance',
                    'jobsSkipped': True,
                    'maintenance': maintenance,
                }
            else:
                execution = execute_due_jobs(config=config, files=files, state=state, force_all=bool(args.run_all_once))
                evidence_export = _maybe_export_agent_group_evidence(config=config, state_root=state_root, execution=execution)
                if evidence_export is not None:
                    state['evidenceExport'] = evidence_export
                    execution['evidenceExport'] = evidence_export
            state_after = _payload_fingerprint(state)
            updated_at_epoch = int(time.time())
            updated_at = now_utc_iso()
            status_payload = _build_status_payload(
                config=config,
                state_root=state_root,
                config_path=config_path,
                interval_seconds=float(args.interval_seconds),
                execution=execution,
                state=state,
                updated_at=updated_at,
                updated_at_epoch=updated_at_epoch,
            )
            heartbeat_due = (time.monotonic() - last_heartbeat_written_monotonic) >= float(args.heartbeat_interval_seconds)

            state_written = False
            if last_state_fingerprint is None or last_state_fingerprint != state_after:
                state_written, last_state_fingerprint = _write_json_if_dirty(
                    state_path,
                    state,
                    last_state_fingerprint,
                    write_json=write_json,
                )
            last_gateway_cron_fingerprint = _sync_gateway_cron_jobs_projection(
                state_root=state_root,
                config=config,
                state=state,
                previous_fingerprint=last_gateway_cron_fingerprint,
            )

            status_written = False
            status_dirty_payload = {**status_payload, 'updated_at': None, 'updated_at_epoch': None}
            status_dirty_fingerprint = _payload_fingerprint(status_dirty_payload)
            if last_status_fingerprint is None or last_status_fingerprint != status_dirty_fingerprint:
                write_json(files.status_path, status_payload)
                last_status_fingerprint = status_dirty_fingerprint
                status_written = True

            if heartbeat_due or last_heartbeat_fingerprint is None or status_written or state_written:
                write_json(files.heartbeat_path, status_payload)
                last_heartbeat_fingerprint = _payload_fingerprint(status_payload)
                last_heartbeat_written_monotonic = time.monotonic()

            if args.once or args.run_all_once:
                break
            elapsed = max(0.0, time.monotonic() - cycle_started)
            now_epoch = time.time()
            target_sleep = _next_due_sleep_seconds(
                state,
                now_epoch=now_epoch,
                heartbeat_interval_seconds=float(args.heartbeat_interval_seconds),
            )
            sleep_for = max(1.0, target_sleep - elapsed)
        finally:
            scheduler_locking.release_lock(cycle_lock_path)
        time.sleep(sleep_for)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
