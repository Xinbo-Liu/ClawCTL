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
from typing import Any, Callable, Iterator


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


def write_text_atomic(target: Path, text: str, *, dir_fd: int | None = None, commit_guard: Callable[[], None] | None = None) -> None:
    """暂存并原子替换 UTF-8 文本，可由调用方固定父目录并在提交点验证前提。

    参数：
        target（Path）：默认按完整路径写入；指定 dir_fd 时只使用文件名。
        text（str）：需要写入的文本。
        dir_fd（int | None）：调用方持有的父目录描述符，暂存、替换和清理均锚定该目录。
        commit_guard（Callable[[], None] | None）：暂存完成后、每次替换尝试前执行的只读校验。
    返回：
        None：替换成功并完成可用的 fsync 后返回。
    异常：
        OSError：暂存或同步失败，或提交校验、替换的有界 I/O 重试耗尽。
        Exception：提交校验拒绝替换时原样传播调用方异常。
    副作用：
        创建同目录暂存文件并原子替换目标；未指定 dir_fd 时按目标完整路径创建父目录和暂存文件。
        本函数不提供非协作写入者之间的操作系统级 CAS，调用方仍须定义锁和校验边界。
    """
    if dir_fd is None:
        ensure_dir(target.parent)
    tmp_path = target.with_name(f".{target.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp")
    try:
        if dir_fd is None:
            staging = tmp_path.open("w", encoding="utf-8", newline="\n")
        else:
            descriptor = os.open(tmp_path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o666, dir_fd=dir_fd)
            staging = os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n')
        with staging as fh:
            fh.write(str(text or ""))
            fh.flush()
            os.fsync(fh.fileno())

        def commit() -> None:
            """在已完成暂存后检查调用方前提，并执行单次替换尝试。

            返回：
                None：校验和替换成功。
            异常：
                Exception：校验拒绝或替换失败时原样传播，由外层只重试 OSError。
            副作用：
                替换目标文件名；指定目录描述符时不重新解析父目录路径。
            """
            if commit_guard is not None:
                commit_guard()
            if dir_fd is None:
                tmp_path.replace(target)
            else:
                os.replace(tmp_path.name, target.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)

        _retry_io(commit)
        if dir_fd is None:
            _fsync_parent_dir(target)
        else:
            try:
                os.fsync(dir_fd)
            except OSError:
                pass
    finally:
        try:
            if dir_fd is None:
                _retry_io(lambda: _unlink_missing_ok(tmp_path))
            else:
                os.unlink(tmp_path.name, dir_fd=dir_fd)
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


def _cleanup_owned_lock_dir(lock_dir: Path, owner_token: str) -> None:
    """在写入已结束后核对所有者，并有界尝试删除当前调用的锁。

    参数：
        lock_dir（Path）：待释放的锁目录；调用方应确保保护块和心跳写入均已结束。
        owner_token（str）：当前调用取得锁时写入的唯一所有者标识。
    返回：
        None：所有者不匹配或元数据无法读取时不清理；匹配时完成删除尝试，I/O 失败不向外传播。
    副作用：
        所有者匹配时删除元数据并尝试删除锁目录，各步骤按配置做有界 I/O 重试。
        所有者核对与删除不构成非协作写入者之间的操作系统级 CAS。
    """
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


class _LockOwnershipLost(RuntimeError):
    """刷新提交前，锁目录或元数据已不再属于当前调用。"""


def _read_owned_lock_metadata(directory_fd: int, owner_token: str) -> dict[str, Any] | None:
    """从已打开的锁目录读取并验证所有者元数据。

    参数：
        directory_fd（int）：固定锁目录身份的文件描述符。
        owner_token（str）：当前调用取得锁时写入的唯一所有者标识。
    返回：
        dict[str, Any] | None：有效且所有者匹配的元数据；缺失、损坏或所有者不同则为 None。
    异常：
        OSError：元数据无法打开或读取，文件缺失除外。
    副作用：
        以 no-follow 打开目录内的元数据文件并读取有限字节；关闭文件，不修改内容。
    """
    try:
        descriptor = os.open(LOCK_METADATA_NAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, 'rb') as metadata_file:
        raw = metadata_file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        return None
    try:
        metadata = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return metadata if isinstance(metadata, dict) and metadata.get('ownerToken') == owner_token else None


def _refresh_lock_metadata(lock_dir: Path, owner_token: str) -> bool:
    """在目录身份与所有者仍匹配时，只刷新锁的更新时间。

    参数：
        lock_dir（Path）：当前调用取得的锁目录路径。
        owner_token（str）：当前调用的唯一所有者标识。
    返回：
        bool：刷新后仍持有该目录锁时为 True；锁缺失、所有权丢失、元数据无效，
        或平台不支持 no-follow 和目录描述符写入时为 False。
    异常：
        OSError：目录或元数据读取、暂存、同步或替换失败，路径缺失除外。
    副作用：
        打开锁目录，暂存并替换其中的元数据，仅更新 updatedAtEpoch；保留创建时间和其他字段。
        写入和暂存清理均锚定该目录，路径被替换时不会写入新目录。
        所有者校验不构成非协作写入者在同一目录内改写元数据时的操作系统级 CAS。
    """
    required_flags = ('O_DIRECTORY', 'O_NOFOLLOW')
    if any(not hasattr(os, flag) for flag in required_flags):
        return False
    if not all(operation in os.supports_dir_fd for operation in (os.open, os.rename, os.unlink)):
        return False
    try:
        directory_fd = os.open(lock_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except (FileNotFoundError, NotADirectoryError):
        return False
    try:
        identity = os.fstat(directory_fd)
        metadata = _read_owned_lock_metadata(directory_fd, owner_token)
        if metadata is None:
            return False
        refreshed = dict(metadata)
        refreshed['updatedAtEpoch'] = int(time.time())

        def verify_ownership() -> None:
            """检查路径仍指向已打开的目录，且其中的所有者标识未改变。

            返回：
                None：目录身份与所有者仍匹配。
            异常：
                _LockOwnershipLost：路径消失、目录被替换或元数据所有者失效。
                OSError：目录身份或元数据无法读取，路径缺失除外。
            副作用：
                读取路径身份和目录内元数据，不修改文件。
            """
            try:
                current = lock_dir.stat(follow_symlinks=False)
            except (FileNotFoundError, NotADirectoryError) as missing_lock:
                raise _LockOwnershipLost('锁目录已消失') from missing_lock
            if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
                raise _LockOwnershipLost('锁目录身份已改变')
            if _read_owned_lock_metadata(directory_fd, owner_token) is None:
                raise _LockOwnershipLost('锁元数据所有者已改变')

        try:
            write_text_atomic(
                _lock_metadata_path(lock_dir),
                json.dumps(refreshed, ensure_ascii=False, indent=2) + '\n',
                dir_fd=directory_fd,
                commit_guard=verify_ownership,
            )
            verify_ownership()
        except _LockOwnershipLost:
            return False
        return True
    finally:
        os.close(directory_fd)


def _start_lock_heartbeat(lock_dir: Path, owner_token: str) -> tuple[threading.Event, threading.Thread]:
    """启动目录锁续期线程，在收到保护块退出信号后承担未完成续期的锁清理。

    参数：
        lock_dir（Path）：当前调用取得的锁目录路径。
        owner_token（str）：取得锁时写入的唯一所有者标识。
    返回：
        tuple[threading.Event, threading.Thread]：供调用方请求停止并等待退出的信号与守护线程。
    异常：
        RuntimeError：系统无法启动守护线程。
    副作用：
        启动守护线程；支持安全目录描述符写入的平台上，线程定期刷新仍由该调用持有的锁。
        平台不支持该能力或刷新被拒绝时，线程停止续期。
        stop 由保护块退出时设置；线程收到该信号后，在续期写入与暂存清理结束后核对所有者并尝试释放锁。
    """
    stop = threading.Event()
    interval = max(1.0, min(30.0, LOCK_STALE_SECONDS / 3.0))

    def heartbeat() -> None:
        """按间隔续期，收到保护块退出信号后在写入结束时清理当前所有者的锁。

        返回：
            None：收到停止信号、刷新被拒绝或发生 I/O 错误后退出。
        副作用：
            等待线程信号，并通过受所有者校验约束的刷新更新锁元数据。
            若保护块已请求停止，则在退出前尝试清理其仍持有的锁；提前停止续期不会释放运行中的保护块。
        """
        try:
            while not stop.wait(interval):
                try:
                    if not _refresh_lock_metadata(lock_dir, owner_token):
                        return
                except OSError:
                    return
        finally:
            if stop.is_set():
                _cleanup_owned_lock_dir(lock_dir, owner_token)

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
    """取得目录锁并按平台能力续期，保护块结束后协调心跳完成与所有者清理。

    参数：
        lock_dir（Path）：锁目录，包含 owner token 与超时元数据。
    返回：
        Iterator[None]：供 with 使用的受保护区间；退出时有界等待心跳，未完成的清理由心跳退出时承担。
    异常：
        RuntimeError：无法在配置的有界等待内取得锁。
        BaseException：受保护操作的异常始终传播，即使锁目录已被其他写入者替换。
    副作用：
        创建锁目录和所有者元数据；支持 no-follow 与目录描述符写入时启动定期续期。
        平台不支持该能力、所有权丢失或 I/O 失败时停止续期。
        保护块完成后请求停止心跳，等待至多 1 秒；心跳仍在写入时保留锁和元数据，由其结束后尝试清理。
        心跳已结束时当前线程核对所有者后尝试清理；所有者核对与删除不构成非协作竞争下的原子操作。
    """
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
    try:
        heartbeat_stop, heartbeat_thread = _start_lock_heartbeat(lock_dir, owner_token)
    except BaseException:
        _cleanup_owned_lock_dir(lock_dir, owner_token)
        raise
    try:
        yield
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=1.0)
        if not heartbeat_thread.is_alive():
            _cleanup_owned_lock_dir(lock_dir, owner_token)
