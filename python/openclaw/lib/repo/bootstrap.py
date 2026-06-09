#!/usr/bin/env python3
"""Shared repository bootstrap helpers for imports and Python path setup."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
import sys
from typing import Iterable

from .layout import resolve_repo_root
from .managed_extensions import managed_extension_python_roots_for_config_path

REPO_PYTHON_BOOTSTRAP_ENV_REL_PATH = Path('config/governance/support/repo_python_bootstrap.env')
BOOTSTRAP_PYTHONPATH_RELS_KEY = 'OPENCLAW_REPO_BOOTSTRAP_PYTHONPATH_RELS'
BOOTSTRAP_ENV_KEYS = {
    'PYTHONDONTWRITEBYTECODE': 'OPENCLAW_REPO_BOOTSTRAP_PYTHONDONTWRITEBYTECODE',
    'PYTHONIOENCODING': 'OPENCLAW_REPO_BOOTSTRAP_PYTHONIOENCODING',
    'PYTHONUTF8': 'OPENCLAW_REPO_BOOTSTRAP_PYTHONUTF8',
}


def _dedupe_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[str] = set()
    for item in paths:
        resolved = Path(item).resolve()
        marker = str(resolved)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(resolved)
    return tuple(result)


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _bootstrap_env_path(repo_root: Path) -> Path:
    return (repo_root / REPO_PYTHON_BOOTSTRAP_ENV_REL_PATH).resolve()


def _resolve_repo_relative_dir(repo_root: Path, rel_path: str, *, label: str) -> Path:
    resolved = (repo_root / rel_path).resolve()
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise RuntimeError(f'{label} 越界：{rel_path}') from exc
    if not resolved.is_dir():
        raise RuntimeError(f'{label} 指向的目录不存在：{resolved}')
    return resolved


@lru_cache(maxsize=None)
def _load_bootstrap_truth(repo_root_text: str) -> tuple[tuple[Path, ...], dict[str, str]]:
    repo_root = Path(repo_root_text).resolve()
    env_path = _bootstrap_env_path(repo_root)
    if not env_path.is_file():
        raise RuntimeError(f'repo python bootstrap truth is missing: {env_path}')

    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            raise RuntimeError(f'repo python bootstrap truth contains invalid line: {raw_line}')
        key, raw_value = line.split('=', 1)
        values[key.strip()] = _unquote(raw_value.strip())

    rel_paths = [
        str(item).strip()
        for item in str(values.get(BOOTSTRAP_PYTHONPATH_RELS_KEY) or '').split('|')
        if str(item).strip()
    ]
    if not rel_paths:
        raise RuntimeError(f'{BOOTSTRAP_PYTHONPATH_RELS_KEY} 不能为空：{env_path}')
    python_roots = tuple(
        _resolve_repo_relative_dir(repo_root, rel_path, label=BOOTSTRAP_PYTHONPATH_RELS_KEY)
        for rel_path in rel_paths
    )

    env_defaults: dict[str, str] = {}
    for env_name, key in BOOTSTRAP_ENV_KEYS.items():
        value = str(values.get(key) or '').strip()
        if not value:
            raise RuntimeError(f'{key} 不能为空：{env_path}')
        env_defaults[env_name] = value
    return python_roots, env_defaults


def bootstrap_env_defaults(start_path: str | Path | None) -> dict[str, str]:
    repo_root = resolve_repo_root(None if start_path is None else Path(start_path))
    return bootstrap_env_defaults_for_repo_root(repo_root)


def bootstrap_env_defaults_for_repo_root(repo_root: str | Path) -> dict[str, str]:
    repo_root = Path(repo_root).resolve()
    _, env_defaults = _load_bootstrap_truth(str(repo_root))
    return dict(env_defaults)


def _repo_python_roots(start_path: str | Path | None) -> tuple[Path, ...]:
    repo_root = resolve_repo_root(None if start_path is None else Path(start_path))
    python_roots, _ = _load_bootstrap_truth(str(repo_root))
    return python_roots


def bootstrap_path_entries(
    start_path: str | Path | None,
    config_path: str | Path | None = None,
) -> tuple[Path, ...]:
    repo_root = resolve_repo_root(None if start_path is None else Path(start_path))
    entries: list[Path] = list(
        managed_extension_python_roots_for_config_path(
            config_path,
            start_path=repo_root,
        )
    )
    entries.extend(_repo_python_roots(repo_root))
    return _dedupe_paths(entries)


def prepend_python_roots(entries: Iterable[Path]) -> tuple[Path, ...]:
    inserted: list[Path] = []
    seen = {str(Path(item).resolve()) for item in sys.path if str(item).strip()}
    for entry in reversed(_dedupe_paths(entries)):
        marker = str(entry.resolve())
        if marker in seen:
            continue
        sys.path.insert(0, marker)
        seen.add(marker)
        inserted.append(entry.resolve())
    return tuple(reversed(inserted))


def bootstrap_sys_path(
    start_path: str | Path | None,
    config_path: str | Path | None = None,
) -> tuple[Path, ...]:
    return prepend_python_roots(
        bootstrap_path_entries(
            start_path,
            config_path=config_path,
        )
    )


def bootstrap_env_pythonpath(
    env: dict[str, str],
    start_path: str | Path | None,
    config_path: str | Path | None = None,
) -> dict[str, str]:
    entries = list(
        bootstrap_path_entries(
            start_path,
            config_path=config_path,
        )
    )
    normalized: list[str] = []
    seen: set[str] = set()
    for item in entries:
        marker = str(Path(item).resolve())
        if marker in seen:
            continue
        seen.add(marker)
        normalized.append(marker)
    existing = str(env.get('PYTHONPATH') or '').strip()
    if existing:
        for item in existing.split(os.pathsep):
            text = str(item or '').strip()
            if not text:
                continue
            marker = str(Path(text).resolve())
            if marker in seen:
                continue
            seen.add(marker)
            normalized.append(marker)
    env['PYTHONPATH'] = os.pathsep.join(normalized)
    return env
