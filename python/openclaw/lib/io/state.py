#!/usr/bin/env python3
"""状态文件与原子写入辅助。"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


MAX_BYTES = max(4096, int(os.environ.get("OPENCLAW_IO_MAX_BYTES", "2097152")))
LOCK_WAIT_MS = max(10, int(os.environ.get("OPENCLAW_LOCK_WAIT_MS", "25")))
LOCK_TIMEOUT_MS = max(2000, int(os.environ.get("OPENCLAW_LOCK_TIMEOUT_MS", "8000")))
LOCK_STALE_SECONDS = max(60, int(os.environ.get("OPENCLAW_LOCK_STALE_SECONDS", "3600")))
LOCK_METADATA_NAME = 'lock.json'
IO_RETRY_WAIT_MS = max(1, int(os.environ.get("OPENCLAW_IO_RETRY_WAIT_MS", "10")))
IO_RETRY_ATTEMPTS = max(3, int(os.environ.get("OPENCLAW_IO_RETRY_ATTEMPTS", "50")))


def ensure_dir(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)


def _read_text(target: Path, default: str | None = None) -> tuple[str, str | None, str | None]:
    try:
        if not target.exists():
            return "missing", default, None
        with target.open("rb") as fh:
            raw = fh.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError(f"文件超过大小限制 {MAX_BYTES} bytes")
        data = raw.decode("utf-8")
        return "ok", data, None
    except (OSError, UnicodeDecodeError, ValueError) as read_error:
        return "corrupt", default, str(read_error)


def _read_json(target: Path, default: Any = None) -> tuple[str, Any, str | None]:
    status, text, error = _read_text(target, None)
    if status == "missing":
        return "missing", default, None
    if status == "corrupt":
        return "corrupt", default, error
    try:
        return "ok", json.loads(text or ""), None
    except json.JSONDecodeError as decode_error:
        return "corrupt", default, str(decode_error)


def read_json_state(target: Path, default: Any = None) -> dict[str, Any]:
    status, data, error = _read_json(target, default)
    return {"status": status, "data": data, "error": error}


def read_text_state(target: Path, default: str = "") -> dict[str, Any]:
    status, data, error = _read_text(target, default)
    return {"status": status, "data": data, "error": error}


def read_json_if_exists(target: Path, default: Any = None) -> Any:
    status, data, _ = _read_json(target, default)
    return data if status == "ok" else default


def _retry_io(action) -> None:
    last_error: OSError | None = None
    for _ in range(IO_RETRY_ATTEMPTS):
        try:
            action()
            return
        except OSError as exc:
            last_error = exc
            time.sleep(IO_RETRY_WAIT_MS / 1000.0)
    if last_error is not None:
        raise last_error


def _unlink_missing_ok(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except FileNotFoundError:
        return


def _fsync_parent_dir(path: Path) -> None:
    if not hasattr(os, "O_RDONLY"):
        return
    try:
        fd = os.open(str(path.parent), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_text_atomic(target: Path, text: str) -> None:
    ensure_dir(target.parent)
    tmp_path = target.with_name(f".{target.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(str(text or ""))
            fh.flush()
            os.fsync(fh.fileno())
        _retry_io(lambda: tmp_path.replace(target))
        _fsync_parent_dir(target)
    finally:
        try:
            _retry_io(lambda: _unlink_missing_ok(tmp_path))
        except OSError:
            pass


def write_json_atomic(target: Path, payload: Any) -> None:
    write_text_atomic(target, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def append_jsonl(target: Path, payload: Any) -> None:
    ensure_dir(target.parent)
    lock_dir = target.with_name(f".{target.name}.lock")
    with with_lock_dir(lock_dir):
        with target.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())


def _lock_metadata_path(lock_dir: Path) -> Path:
    return lock_dir / LOCK_METADATA_NAME


def current_lock_hostname() -> str:
    return os.uname().nodename if hasattr(os, 'uname') else ''


def lock_metadata_payload(
    payload: dict[str, Any] | None = None,
    *,
    stale_after_seconds: int,
    owner_token: str | None = None,
    include_updated_at: bool = True,
) -> dict[str, Any]:
    now = time.time()
    materialized = dict(payload or {})
    materialized.setdefault('pid', os.getpid())
    materialized.setdefault('hostname', current_lock_hostname())
    if owner_token:
        materialized['ownerToken'] = owner_token
    materialized['createdAtEpoch'] = int(now)
    if include_updated_at:
        materialized['updatedAtEpoch'] = int(now)
    materialized['staleAfterSeconds'] = int(stale_after_seconds)
    return materialized


def lock_metadata_age_seconds(path: Path, metadata: dict[str, Any]) -> float:
    epoch = metadata.get('updatedAtEpoch') or metadata.get('createdAtEpoch')
    try:
        if epoch is not None:
            return max(0.0, time.time() - float(epoch))
    except (TypeError, ValueError):
        pass
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return 0.0


def lock_metadata_stale_after_seconds(metadata: dict[str, Any], default_seconds: int) -> int:
    try:
        return max(60, int(metadata.get('staleAfterSeconds') or default_seconds))
    except (TypeError, ValueError):
        return int(default_seconds)


def _lock_metadata(lock_dir: Path, owner_token: str) -> dict[str, Any]:
    return lock_metadata_payload(stale_after_seconds=LOCK_STALE_SECONDS, owner_token=owner_token)


def _lock_owner_token() -> str:
    return f"{os.getpid()}-{threading.get_ident()}-{time.time_ns()}-{uuid.uuid4().hex}"


def _lock_owned_by(lock_dir: Path, owner_token: str) -> bool:
    metadata = read_json_if_exists(_lock_metadata_path(lock_dir), default={})
    return isinstance(metadata, dict) and metadata.get('ownerToken') == owner_token


def _refresh_lock_metadata(lock_dir: Path, owner_token: str) -> None:
    metadata = read_json_if_exists(_lock_metadata_path(lock_dir), default={})
    base = metadata if isinstance(metadata, dict) and metadata.get('ownerToken') == owner_token else {'ownerToken': owner_token}
    write_json_atomic(
        _lock_metadata_path(lock_dir),
        lock_metadata_payload(base, stale_after_seconds=LOCK_STALE_SECONDS, owner_token=owner_token),
    )


def _start_lock_heartbeat(lock_dir: Path, owner_token: str) -> tuple[threading.Event, threading.Thread]:
    stop = threading.Event()
    interval = max(1.0, min(30.0, LOCK_STALE_SECONDS / 3.0))

    def heartbeat() -> None:
        while not stop.wait(interval):
            try:
                _refresh_lock_metadata(lock_dir, owner_token)
            except OSError:
                return

    thread = threading.Thread(target=heartbeat, name=f'openclaw-lock-heartbeat-{lock_dir.name}', daemon=True)
    thread.start()
    return stop, thread


def _lock_age_seconds(lock_dir: Path) -> float:
    metadata_path = _lock_metadata_path(lock_dir)
    try:
        metadata_exists = metadata_path.exists()
    except OSError:
        return 0.0
    metadata = read_json_if_exists(metadata_path, default={}) if metadata_exists else {}
    if isinstance(metadata, dict):
        return lock_metadata_age_seconds(lock_dir, metadata)
    return lock_metadata_age_seconds(lock_dir, {})


def _clear_lock_dir(lock_dir: Path) -> None:
    for child in sorted(lock_dir.iterdir(), reverse=True):
        if child.is_file() or child.is_symlink():
            child.unlink(missing_ok=True)
            continue
        try:
            child.rmdir()
        except OSError:
            pass
    lock_dir.rmdir()


def _recover_stale_lock_dir(lock_dir: Path) -> bool:
    try:
        if not lock_dir.exists() or not lock_dir.is_dir():
            return False
    except OSError:
        return False
    stale_after_seconds = LOCK_STALE_SECONDS
    metadata = read_json_if_exists(_lock_metadata_path(lock_dir), default={})
    if isinstance(metadata, dict):
        stale_after_seconds = lock_metadata_stale_after_seconds(metadata, stale_after_seconds)
    if _lock_age_seconds(lock_dir) < stale_after_seconds:
        return False
    try:
        _clear_lock_dir(lock_dir)
    except OSError:
        return False
    return True


@contextmanager
def with_lock_dir(lock_dir: Path) -> Iterator[None]:
    deadline = time.time() + (LOCK_TIMEOUT_MS / 1000.0)
    owner_token = _lock_owner_token()
    while True:
        try:
            lock_dir.mkdir(parents=True, exist_ok=False)
        except (FileExistsError, PermissionError):
            if _recover_stale_lock_dir(lock_dir):
                continue
            if time.time() >= deadline:
                raise RuntimeError(f"获取锁超时：{lock_dir}")
            time.sleep(LOCK_WAIT_MS / 1000.0)
            continue
        try:
            write_json_atomic(_lock_metadata_path(lock_dir), _lock_metadata(lock_dir, owner_token))
        except BaseException:
            try:
                _retry_io(lambda: _unlink_missing_ok(_lock_metadata_path(lock_dir)))
            except OSError:
                pass
            try:
                _retry_io(lock_dir.rmdir)
            except OSError:
                pass
            raise
        break
    heartbeat_stop, heartbeat_thread = _start_lock_heartbeat(lock_dir, owner_token)
    try:
        yield
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=1.0)
        if not _lock_owned_by(lock_dir, owner_token):
            return
        try:
            _retry_io(lambda: _unlink_missing_ok(_lock_metadata_path(lock_dir)))
        except OSError:
            pass
        try:
            _retry_io(lock_dir.rmdir)
        except OSError:
            pass
