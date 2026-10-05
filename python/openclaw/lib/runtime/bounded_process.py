#!/usr/bin/env python3
"""为测试与发布检查提供有界、可回收的子进程执行。"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Mapping, Sequence


DEFAULT_HEARTBEAT_SECONDS = 30.0
DEFAULT_TERM_GRACE_SECONDS = 5.0
DEFAULT_OUTPUT_LIMIT_CHARS = 16_000


@dataclass(frozen=True)
class BoundedProcessResult:
    """记录一次有界子进程执行的完整机器结果。"""

    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool


def truncate_process_output(value: str, *, limit_chars: int = DEFAULT_OUTPUT_LIMIT_CHARS) -> str:
    """截断过长的失败输出，同时保留开头、结尾和被省略的字符数。

    参数：
        value（str）：待纳入机器报告或错误摘要的进程输出。
        limit_chars（int）：允许保留的最大字符数；必须大于零。

    返回：
        str：未超限时返回原文，超限时返回首尾片段及截断标记。

    异常：
        ValueError：当 ``limit_chars`` 不为正数时抛出。
    """
    if limit_chars <= 0:
        raise ValueError('limit_chars must be > 0')
    text = str(value or '')
    if len(text) <= limit_chars:
        return text
    marker_budget = min(160, max(32, limit_chars // 4))
    content_budget = max(2, limit_chars - marker_budget)
    head_budget = content_budget // 2
    tail_budget = content_budget - head_budget
    omitted = len(text) - head_budget - tail_budget
    marker = f'\n... [truncated {omitted} characters] ...\n'
    return text[:head_budget] + marker + text[-tail_budget:]


def _terminate_process_group(process: subprocess.Popen[str], *, grace_seconds: float) -> None:
    """终止目标进程组，并在宽限期后强制回收仍未退出的进程。

    参数：
        process（subprocess.Popen[str]）：待终止的进程组首进程。
        grace_seconds（float）：发送 TERM 后等待自然退出的秒数。

    副作用：
        向目标进程组发送终止信号，并等待所有可见子进程退出。
    """
    if process.poll() is not None:
        return
    if os.name == 'posix':
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    else:
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT)
        except (AttributeError, OSError, ValueError):
            pass
        try:
            subprocess.run(
                ['taskkill', '/PID', str(process.pid), '/T', '/F'],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=max(1.0, grace_seconds),
            )
        except subprocess.TimeoutExpired:
            pass
        if process.poll() is None:
            process.terminate()
        process.wait()
        return
    try:
        process.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == 'posix':
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
    process.wait()


def run_bounded_process(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    timeout_seconds: float,
    heartbeat_label: str = '',
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
    heartbeat_stream: IO[str] | None = None,
    term_grace_seconds: float = DEFAULT_TERM_GRACE_SECONDS,
) -> BoundedProcessResult:
    """在独立进程组中执行命令，并按内层时限回收整个进程组。

    参数：
        command（Sequence[str]）：无需 shell 解释的命令和参数序列。
        cwd（Path）：子进程工作目录。
        env（Mapping[str, str] | None）：完整子进程环境；为 ``None`` 时继承当前环境。
        timeout_seconds（float）：命令总时限，单位为秒。
        heartbeat_label（str）：长检查心跳中的稳定检查标识。
        heartbeat_seconds（float）：心跳间隔；设为零可关闭心跳。
        heartbeat_stream（IO[str] | None）：心跳输出流，默认使用标准错误。
        term_grace_seconds（float）：TERM 后等待 KILL 的宽限秒数。

    返回：
        BoundedProcessResult：退出码、标准输出、标准错误、耗时和超时状态。

    异常：
        ValueError：命令为空，或任一时间边界不是有效正数时抛出。

    副作用：
        创建子进程组、写入长检查心跳，并在超时时终止和回收进程组。
    """
    normalized_command = [str(item) for item in command]
    if not normalized_command:
        raise ValueError('command must not be empty')
    if timeout_seconds <= 0:
        raise ValueError('timeout_seconds must be > 0')
    if heartbeat_seconds < 0:
        raise ValueError('heartbeat_seconds must be >= 0')
    if term_grace_seconds <= 0:
        raise ValueError('term_grace_seconds must be > 0')

    popen_kwargs: dict[str, object] = {}
    if os.name == 'posix':
        popen_kwargs['start_new_session'] = True
    elif hasattr(subprocess, 'CREATE_NEW_PROCESS_GROUP'):
        popen_kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP

    started = time.monotonic()
    process = subprocess.Popen(
        normalized_command,
        cwd=Path(cwd),
        env=None if env is None else {str(key): str(value) for key, value in env.items()},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='utf-8',
        errors='replace',
        **popen_kwargs,
    )
    stream = heartbeat_stream if heartbeat_stream is not None else sys.stderr
    stdout = ''
    stderr = ''
    timed_out = False
    while True:
        elapsed = time.monotonic() - started
        remaining = timeout_seconds - elapsed
        if remaining <= 0:
            timed_out = True
            _terminate_process_group(process, grace_seconds=term_grace_seconds)
            stdout, stderr = process.communicate()
            break
        wait_seconds = remaining
        if heartbeat_seconds > 0:
            wait_seconds = min(wait_seconds, heartbeat_seconds)
        try:
            stdout, stderr = process.communicate(timeout=wait_seconds)
            break
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - started
            if elapsed >= timeout_seconds:
                timed_out = True
                _terminate_process_group(process, grace_seconds=term_grace_seconds)
                stdout, stderr = process.communicate()
                break
            if heartbeat_seconds > 0:
                label = heartbeat_label or normalized_command[0]
                stream.write(f'[heartbeat] {label} elapsed={elapsed:.1f}s\n')
                stream.flush()

    return BoundedProcessResult(
        exit_code=None if timed_out else process.returncode,
        stdout=str(stdout or ''),
        stderr=str(stderr or ''),
        duration_seconds=round(time.monotonic() - started, 3),
        timed_out=timed_out,
    )
