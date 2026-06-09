#!/usr/bin/env python3
"""Cold-start import scan for all repo Python modules."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.runtime.execution import build_subprocess_env

ROOT_DIR = resolve_repo_root(Path(__file__))
PACKAGE_ROOT = (ROOT_DIR / 'python' / 'openclaw').resolve()
DEFAULT_IMPORT_TIMEOUT_SECONDS = 10.0
DEFAULT_IMPORT_WORKERS = 8


def discover_module_names(package_root: Path = PACKAGE_ROOT) -> list[str]:
    modules: list[str] = []
    for path in sorted(package_root.rglob('*.py')):
        if '__pycache__' in path.parts:
            continue
        rel = path.relative_to(package_root).with_suffix('')
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


def _output_text(value: str | bytes | None) -> str:
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    return str(value)


def _import_once(module_name: str, *, env: dict[str, str], timeout_seconds: float) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [
                sys.executable,
                '-c',
                'import importlib, sys; importlib.import_module(sys.argv[1])',
                module_name,
            ],
            cwd=ROOT_DIR,
            env=env,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        detail = '\n'.join(
            part
            for part in [_output_text(exc.stdout).strip(), _output_text(exc.stderr).strip()]
            if part
        ).strip()
        return {
            'module': module_name,
            'returncode': None,
            'detail': detail or f'import timed out after {timeout_seconds:g} seconds',
            'elapsedSeconds': round(time.monotonic() - started, 3),
            'timeoutSeconds': timeout_seconds,
            'timedOut': True,
        }
    detail = '\n'.join(
        part for part in [str(completed.stdout or '').strip(), str(completed.stderr or '').strip()] if part
    ).strip()
    return {
        'module': module_name,
        'returncode': completed.returncode,
        'detail': detail,
        'elapsedSeconds': round(time.monotonic() - started, 3),
        'timeoutSeconds': timeout_seconds,
        'timedOut': False,
    }


def build_report(package_root: Path = PACKAGE_ROOT) -> dict[str, Any]:
    env = build_subprocess_env(Path(__file__), base_env=os.environ)
    modules = discover_module_names(package_root)
    timeout_seconds = _positive_float_env('OPENCLAW_COLD_START_IMPORT_TIMEOUT_SECONDS', DEFAULT_IMPORT_TIMEOUT_SECONDS)
    default_workers = min(DEFAULT_IMPORT_WORKERS, os.cpu_count() or 1)
    worker_count = min(len(modules) or 1, _positive_int_env('OPENCLAW_COLD_START_IMPORT_WORKERS', default_workers))
    started = time.monotonic()
    failures: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = executor.map(
            lambda name: _import_once(name, env=env, timeout_seconds=timeout_seconds),
            modules,
        )
        for result in results:
            if result['returncode'] == 0:
                continue
            detail = str(result.get('detail') or '')
            detail_lines = [line for line in detail.splitlines() if line.strip()]
            failure: dict[str, Any] = {
                'module': result['module'],
                'detail': detail_lines[-1] if detail_lines else f'import failed with exit code {result["returncode"]}',
                'elapsedSeconds': result['elapsedSeconds'],
                'timeoutSeconds': result['timeoutSeconds'],
            }
            if result.get('timedOut'):
                failure['timedOut'] = True
            failures.append(failure)
    return {
        'ok': not failures,
        'moduleCount': len(modules),
        'failureCount': len(failures),
        'workerCount': worker_count,
        'timeoutSeconds': timeout_seconds,
        'durationSeconds': round(time.monotonic() - started, 3),
        'failures': failures,
    }


def main() -> int:
    payload = build_report()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if bool(payload.get('ok')) else 1


if __name__ == '__main__':
    raise SystemExit(main())
