"""Gateway cron jobs 派生产物。"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, List

from openclaw.lib.runtime.path_resolver import PathResolver
from openclaw.lib.runtime.time import DEFAULT_APP_TZ, now_utc, parse_iso_datetime
from openclaw.scheduler.cron import next_cron_occurrence, resolve_timezone

from ..io import write_text
from ..registry import _load_registry, _registry_rows
from ..shared import _json_object, _text

def gateway_cron_jobs_state_path(resolver: PathResolver) -> Path:
    return resolver.absolute_host_path('gateway_host_state_dir') / 'cron' / 'jobs.json'


def gateway_cron_jobs_state_path_from_gateway_state_root(gateway_state_root: Path) -> Path:
    return gateway_state_root / 'cron' / 'jobs.json'


def stale_gateway_cron_migration_paths_from_gateway_state_root(gateway_state_root: Path) -> List[Path]:
    """按 Gateway state 根列出旧 cron 迁移备份文件。

    参数：
        gateway_state_root（Path）：Gateway state 根目录；函数只检查其 `cron/` 子目录。

    返回：
        返回 List[Path]，表示当前运行态不再读取的旧 Gateway cron 迁移备份文件。
    """
    cron_dir = gateway_cron_jobs_state_path_from_gateway_state_root(gateway_state_root).parent
    if not cron_dir.is_dir():
        return []
    return sorted(path for path in cron_dir.glob('jobs.json.migrated*') if path.is_file())


def stale_gateway_cron_migration_paths(resolver: PathResolver) -> List[Path]:
    """列出 Gateway cron 旧迁移备份文件。

    参数：
        resolver（PathResolver）：运行态路径解析器，用来定位 Gateway state 根目录。

    返回：
        返回 List[Path]，表示当前运行态不再读取的旧 Gateway cron 迁移备份文件。
    """
    return stale_gateway_cron_migration_paths_from_gateway_state_root(
        resolver.absolute_host_path('gateway_host_state_dir')
    )


def prune_stale_gateway_cron_migration_paths_from_gateway_state_root(gateway_state_root: Path) -> List[Path]:
    """按 Gateway state 根删除旧 cron 迁移备份文件。

    参数：
        gateway_state_root（Path）：Gateway state 根目录；函数只删除其 `cron/` 子目录下的旧迁移备份。

    返回：
        返回 List[Path]，表示本次删除的旧 Gateway cron 迁移备份文件。

    副作用：
        删除 Gateway state 根内不再被当前运行态读取的 cron 迁移备份文件。
    """
    removed = stale_gateway_cron_migration_paths_from_gateway_state_root(gateway_state_root)
    for path in removed:
        path.unlink()
    return removed


def prune_stale_gateway_cron_migration_paths(resolver: PathResolver) -> List[Path]:
    """删除 Gateway cron 旧迁移备份文件。

    参数：
        resolver（PathResolver）：运行态路径解析器，用来定位 Gateway state 根目录。

    返回：
        返回 List[Path]，表示本次删除的旧 Gateway cron 迁移备份文件。
    """
    return prune_stale_gateway_cron_migration_paths_from_gateway_state_root(
        resolver.absolute_host_path('gateway_host_state_dir')
    )


def _cron_schedule(job: Dict[str, Any]) -> Dict[str, Any]:
    schedule = _json_object(job.get('schedule'))
    return {
        'kind': _text(schedule.get('kind')) or 'cron',
        'expr': _text(schedule.get('expr')),
        'tz': _text(schedule.get('tz')) or DEFAULT_APP_TZ,
    }


def _cron_sort_key(job: Dict[str, Any]) -> tuple[int, str]:
    order = job.get('resolvedOrder')
    try:
        order_int = int(order)
    except (TypeError, ValueError):
        order_int = 100000
    return order_int, _text(job.get('id'))


def _coerce_aware_datetime(value: datetime | None) -> datetime:
    current = value or now_utc()
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current


def _gateway_cron_next_run_at_ms(schedule: Dict[str, Any], *, now: datetime | None = None) -> int | None:
    if schedule.get('kind') != 'cron' or not schedule.get('expr'):
        return None
    tz = resolve_timezone(_text(schedule.get('tz')) or DEFAULT_APP_TZ)
    current = _coerce_aware_datetime(now).astimezone(tz)
    next_iso = next_cron_occurrence(_text(schedule.get('expr')), current)
    if not next_iso:
        return None
    next_run = parse_iso_datetime(next_iso, assume_tz=tz)
    if next_run is None:
        return None
    return int(next_run.timestamp() * 1000)


def _iso_to_epoch_ms(value: Any) -> int | None:
    parsed = parse_iso_datetime(value)
    if parsed is None:
        return None
    return int(parsed.timestamp() * 1000)


def _gateway_status_from_scheduler_status(value: Any) -> str:
    status = _text(value)
    if status == 'succeeded':
        return 'ok'
    if status == 'failed':
        return 'error'
    return 'skipped'


def _gateway_cron_display_state(
    job_id: str,
    schedule: Dict[str, Any],
    *,
    now: datetime | None = None,
    scheduler_state: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    jobs_state = _json_object((scheduler_state or {}).get('jobs'))
    job_state = _json_object(jobs_state.get(job_id))
    state: Dict[str, Any] = {
        'lastStatus': _gateway_status_from_scheduler_status(job_state.get('currentStatus')),
        'lastSummary': _text(job_state.get('lastBlockedReason'))
        or _text(job_state.get('currentStatus'))
        or 'Display mirror only; business execution is handled by the OpenClaw control-plane scheduler.',
    }
    last_run_at_ms = _iso_to_epoch_ms(job_state.get('lastFinishedAt') or job_state.get('lastStartedAt'))
    if last_run_at_ms is not None:
        state['lastRunAtMs'] = last_run_at_ms
    next_run_at_ms = _iso_to_epoch_ms(job_state.get('nextScheduledRunAt')) or _gateway_cron_next_run_at_ms(schedule, now=now)
    if next_run_at_ms is not None:
        state['nextRunAtMs'] = next_run_at_ms
    return state


def build_gateway_cron_jobs_projection(
    registry: Dict[str, Any],
    *,
    now: datetime | None = None,
    scheduler_state: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    jobs: List[Dict[str, Any]] = []
    for job in sorted(_registry_rows(registry, 'jobs'), key=_cron_sort_key):
        job_id = _text(job.get('id'))
        runtime_job_key = _text(job.get('resolvedRuntimeJobKey') or job.get('qualifiedId') or job_id)
        agent_id = _text(job.get('agentRef'))
        schedule = _cron_schedule(job)
        if not job_id or not agent_id or schedule['kind'] != 'cron' or not schedule['expr']:
            continue
        jobs.append({
            'id': job_id,
            'agentId': agent_id,
            'name': _text(job.get('title')) or job_id,
            'enabled': bool(job.get('enabled', True)),
            'createdAtMs': 0,
            'updatedAtMs': 0,
            'schedule': schedule,
            'sessionTarget': 'main',
            'wakeMode': 'now',
            'payload': {
                'kind': 'systemEvent',
                'text': 'NO_REPLY',
            },
            'delivery': {'mode': 'none', 'channel': 'last'},
            'state': _gateway_cron_display_state(runtime_job_key, schedule, now=now, scheduler_state=scheduler_state),
        })
    return jobs


def build_gateway_cron_jobs_payload(
    registry: Dict[str, Any],
    *,
    now: datetime | None = None,
    scheduler_state: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {'version': 1, 'jobs': build_gateway_cron_jobs_projection(registry, now=now, scheduler_state=scheduler_state)}


def build_gateway_cron_jobs_output(
    config_path: Path | None,
    *,
    now: datetime | None = None,
    scheduler_state: Dict[str, Any] | None = None,
) -> str:
    registry = _load_registry(config_path)
    return json.dumps(
        build_gateway_cron_jobs_payload(registry, now=now, scheduler_state=scheduler_state),
        ensure_ascii=False,
        indent=2,
    ) + '\n'


def render_gateway_cron_jobs(resolver: PathResolver, config_path: Path | None = None) -> None:
    prune_stale_gateway_cron_migration_paths(resolver)
    write_text(gateway_cron_jobs_state_path(resolver), build_gateway_cron_jobs_output(config_path or resolver.config_path))
