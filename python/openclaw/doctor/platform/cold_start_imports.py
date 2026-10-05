#!/usr/bin/env python3
"""提供OpenClaw doctor子系统的生产实现。"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.runtime.bounded_process import run_bounded_process, truncate_process_output
from openclaw.lib.runtime.execution import build_subprocess_env

ROOT_DIR = resolve_repo_root(Path(__file__))
PACKAGE_ROOT = (ROOT_DIR / 'python' / 'openclaw').resolve()
DEFAULT_IMPORT_TIMEOUT_SECONDS = 10.0
DEFAULT_IMPORT_WORKERS = 8
DEFAULT_IMPORT_BATCH_SIZE = 32
IMPORT_MODES = ('closure', 'isolated')
EXCLUDED_TOP_LEVEL_PACKAGES = frozenset({'tests'})


def discover_module_names(package_root: Path = PACKAGE_ROOT) -> list[str]:
    """发现需要执行导入闭包与独立冷启动的非测试模块。

    参数：
        package_root（Path）：``openclaw`` 包根目录。

    返回：
        list[str]：确定性排序后的模块名；``openclaw.tests`` 由 repo unittest 负责加载，
        不重复进入生产模块冷启动检查。
    """
    modules: list[str] = []
    for path in sorted(package_root.rglob('*.py')):
        if '__pycache__' in path.parts:
            continue
        rel = path.relative_to(package_root).with_suffix('')
        if rel.parts[:1] and rel.parts[0] in EXCLUDED_TOP_LEVEL_PACKAGES:
            continue
        parts = list(rel.parts)
        if parts[-1] == '__init__':
            parts = parts[:-1]
        if not parts:
            continue
        modules.append('openclaw.' + '.'.join(parts))
    return sorted(set(modules))


def _positive_float_env(name: str, default: float) -> float:
    value = str(os.environ.get(name) or '').strip()
    if not value:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _positive_int_env(name: str, default: int) -> int:
    value = str(os.environ.get(name) or '').strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _import_once(module_name: str, *, env: dict[str, str], timeout_seconds: float) -> dict[str, Any]:
    started = time.monotonic()
    completed = run_bounded_process(
        [
            sys.executable,
            '-c',
            'import importlib, sys; importlib.import_module(sys.argv[1])',
            module_name,
        ],
        cwd=ROOT_DIR,
        env=env,
        timeout_seconds=timeout_seconds,
        heartbeat_seconds=0,
    )
    detail = '\n'.join(
        part for part in [str(completed.stdout or '').strip(), str(completed.stderr or '').strip()] if part
    ).strip()
    return {
        'module': module_name,
        'returncode': completed.exit_code,
        'detail': truncate_process_output(detail),
        'elapsedSeconds': round(time.monotonic() - started, 3),
        'timeoutSeconds': timeout_seconds,
        'timedOut': completed.timed_out,
    }


def _module_batches(modules: list[str], batch_size: int) -> list[list[str]]:
    """按固定上限切分待导入模块，同时保持模块原有顺序。

    参数：
        modules（list[str]）：确定性排序后的模块名。
        batch_size（int）：每批允许包含的最大模块数。

    返回：
        list[list[str]]：保持输入顺序的模块批次。
    """
    return [modules[index:index + batch_size] for index in range(0, len(modules), batch_size)]


def _import_batch(modules: list[str], *, env: dict[str, str], timeout_seconds: float) -> dict[str, Any]:
    """在一个有界子进程中导入一批模块。

    参数：
        modules（list[str]）：本批需要顺序导入的模块名。
        env（dict[str, str]）：传给独立 Python 进程的完整环境。
        timeout_seconds（float）：本批导入的最大执行秒数。

    返回：
        dict[str, Any]：模块、退出状态、耗时、超时状态与截断诊断。

    副作用：
        创建并回收一个独立 Python 子进程组。
    """
    completed = run_bounded_process(
        [
            sys.executable,
            '-c',
            'import importlib, sys; [importlib.import_module(name) for name in sys.argv[1:]]',
            *modules,
        ],
        cwd=ROOT_DIR,
        env=env,
        timeout_seconds=timeout_seconds,
        heartbeat_seconds=0,
    )
    detail = '\n'.join(
        part for part in [str(completed.stdout or '').strip(), str(completed.stderr or '').strip()] if part
    ).strip()
    return {
        'modules': modules,
        'returncode': completed.exit_code,
        'detail': truncate_process_output(detail),
        'elapsedSeconds': completed.duration_seconds,
        'timeoutSeconds': timeout_seconds,
        'timedOut': completed.timed_out,
    }


def _failure_row(result: dict[str, Any]) -> dict[str, Any]:
    """将单模块执行结果规整为稳定的失败详情。

    参数：
        result（dict[str, Any]）：单模块导入执行结果。

    返回：
        dict[str, Any]：供机器报告使用的模块失败摘要。
    """
    detail_lines = [line for line in str(result.get('detail') or '').splitlines() if line.strip()]
    failure: dict[str, Any] = {
        'module': result['module'],
        'detail': detail_lines[-1] if detail_lines else f'import failed with exit code {result["returncode"]}',
        'elapsedSeconds': result['elapsedSeconds'],
        'timeoutSeconds': result['timeoutSeconds'],
    }
    if result.get('timedOut'):
        failure['timedOut'] = True
    return failure


def build_report(package_root: Path = PACKAGE_ROOT, *, mode: str = 'isolated') -> dict[str, Any]:
    """执行批量导入闭包或逐模块独立冷启动检查。

    参数：
        package_root（Path）：待发现模块的 ``openclaw`` 包目录。
        mode（str）：``closure`` 表示批量导入并缩小失败批次，``isolated`` 表示逐模块冷启动。

    返回：
        dict[str, Any]：模块数、耗时、失败详情和执行模式组成的机器报告。

    异常：
        ValueError：当 ``mode`` 不是支持的检查模式时抛出。

    副作用：
        创建有界 Python 子进程；闭包批次失败时再执行逐模块定位。
    """
    if mode not in IMPORT_MODES:
        raise ValueError(f'unsupported import mode: {mode}')
    env = build_subprocess_env(Path(__file__), base_env=os.environ)
    modules = discover_module_names(package_root)
    timeout_seconds = _positive_float_env('OPENCLAW_COLD_START_IMPORT_TIMEOUT_SECONDS', DEFAULT_IMPORT_TIMEOUT_SECONDS)
    default_workers = min(DEFAULT_IMPORT_WORKERS, os.cpu_count() or 1)
    worker_count = min(len(modules) or 1, _positive_int_env('OPENCLAW_COLD_START_IMPORT_WORKERS', default_workers))
    started = time.monotonic()
    failures: list[dict[str, Any]] = []
    batch_count = 0
    if mode == 'isolated':
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            results = executor.map(
                lambda name: _import_once(name, env=env, timeout_seconds=timeout_seconds),
                modules,
            )
            failures.extend(_failure_row(result) for result in results if result['returncode'] != 0)
    else:
        batch_size = _positive_int_env('OPENCLAW_IMPORT_CLOSURE_BATCH_SIZE', DEFAULT_IMPORT_BATCH_SIZE)
        batches = _module_batches(modules, batch_size)
        batch_count = len(batches)
        failed_batches: list[list[str]] = []
        with ThreadPoolExecutor(max_workers=min(worker_count, len(batches) or 1)) as executor:
            results = executor.map(
                lambda batch: _import_batch(batch, env=env, timeout_seconds=timeout_seconds),
                batches,
            )
            for result in results:
                if result['returncode'] != 0:
                    failed_batches.append(list(result['modules']))
        failed_modules = [module for batch in failed_batches for module in batch]
        if failed_modules:
            with ThreadPoolExecutor(max_workers=min(worker_count, len(failed_modules))) as executor:
                isolated_results = executor.map(
                    lambda name: _import_once(name, env=env, timeout_seconds=timeout_seconds),
                    failed_modules,
                )
                failures.extend(_failure_row(result) for result in isolated_results if result['returncode'] != 0)
    return {
        'ok': not failures,
        'mode': mode,
        'moduleCount': len(modules),
        'batchCount': batch_count,
        'failureCount': len(failures),
        'workerCount': worker_count,
        'timeoutSeconds': timeout_seconds,
        'durationSeconds': round(time.monotonic() - started, 3),
        'failures': failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='验证 OpenClaw Python 模块导入闭包')
    parser.add_argument('--mode', choices=IMPORT_MODES, default='isolated')
    args = parser.parse_args(argv)
    payload = build_report(mode=args.mode)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if bool(payload.get('ok')) else 1


if __name__ == '__main__':
    raise SystemExit(main())
