#!/usr/bin/env python3
"""Scheduler job lock helpers."""
from __future__ import annotations

import json
import os
from contextlib import suppress
from pathlib import Path
from typing import Any

from openclaw.lib.io.state import (
    current_lock_hostname,
    lock_metadata_age_seconds,
    lock_metadata_payload,
    lock_metadata_stale_after_seconds,
)


def lock_path(files, job_id: str) -> Path:
    safe_job = ''.join(ch if ch.isalnum() or ch in ('-', '_') else '_' for ch in str(job_id or 'job'))
    return files.locks_dir / f'{safe_job}.lock'


def scheduler_lock_stale_after_seconds(timeout_seconds: int) -> int:
    configured = max(60, int(os.environ.get('OPENCLAW_SCHEDULER_LOCK_STALE_SECONDS', '3600')))
    return max(configured, int(timeout_seconds) * 2)


def scheduler_cycle_lock_stale_after_seconds(timeout_seconds: int) -> int:
    """返回调度器主循环锁的恢复窗口，避免容器重建后被旧 cycle lock 长时间阻塞。"""
    configured = max(60, int(os.environ.get('OPENCLAW_SCHEDULER_CYCLE_LOCK_STALE_SECONDS', '120')))
    return max(configured, int(timeout_seconds) * 2)


def scheduler_lock_metadata(payload: dict[str, Any], *, stale_after_seconds: int) -> dict[str, Any]:
    return lock_metadata_payload(payload, stale_after_seconds=stale_after_seconds, include_updated_at=False)


def read_lock_metadata(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def lock_age_seconds(path: Path, metadata: dict[str, Any]) -> float:
    return lock_metadata_age_seconds(path, metadata)


def lock_is_stale(
    path: Path,
    metadata: dict[str, Any],
    *,
    stale_after_seconds: int,
    stale_after_cap_seconds: int | None = None,
) -> bool:
    age_seconds = lock_age_seconds(path, metadata)
    file_stale_after = lock_metadata_stale_after_seconds(metadata, stale_after_seconds)
    if stale_after_cap_seconds is not None:
        file_stale_after = min(file_stale_after, max(60, int(stale_after_cap_seconds)))
    if age_seconds >= file_stale_after:
        return True
    pid = int(metadata.get('pid') or 0)
    lock_host = str(metadata.get('hostname') or '').strip()
    current_host = current_lock_hostname()
    if pid and age_seconds >= 60 and (not lock_host or lock_host == current_host) and not pid_is_running(pid):
        return True
    return False


def acquire_lock(path: Path, payload: dict[str, Any], *, stale_after_seconds: int) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    stale_after_cap_seconds = stale_after_seconds if str(payload.get('kind') or '') == 'scheduler_cycle' else None
    while True:
        try:
            fd = os.open(str(path), flags, 0o644)
            break
        except FileExistsError:
            metadata = read_lock_metadata(path)
            if not lock_is_stale(
                path,
                metadata,
                stale_after_seconds=stale_after_seconds,
                stale_after_cap_seconds=stale_after_cap_seconds,
            ):
                return False
            with suppress(FileNotFoundError):
                path.unlink()
            continue
    metadata_payload = scheduler_lock_metadata(payload, stale_after_seconds=stale_after_seconds)
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        fh.write(json.dumps(metadata_payload, ensure_ascii=False))
    return True


def release_lock(path: Path) -> None:
    with suppress(FileNotFoundError):
        path.unlink()
