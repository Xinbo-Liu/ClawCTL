#!/usr/bin/env python3
"""Agent 模块 smoke/regression 测试辅助。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping

from openclaw.doctor.platform.temp_workspace import global_tmp_root, make_temp_dir, prune_empty_parents, remove_tree
from openclaw.lib.repo.local_workspace_policy import workspace_target_paths
from openclaw.lib.repo.layout import CONTROL_PLANE_CONFIG_ENV, resolve_repo_root
from openclaw.lib.repo.managed_extensions import managed_extension_for_config_path
from openclaw.lib.runtime.execution import build_subprocess_env

_REPO_COPY_IGNORED_NAMES = {
    '__pycache__',
    '.git',
    '.pytest_cache',
}
_REPO_COPY_IGNORED_SUFFIXES = ('.pyc', '.pyo')
_REPO_COPY_IGNORED_TOP_LEVEL_PATTERNS = ('tmp-',)


def repo_root_from(test_file: str | Path) -> Path:
    return resolve_repo_root(Path(test_file))


def repo_copy_ignore(root_dir: Path) -> Callable[[str, list[str]], set[str]]:
    resolved_root = Path(root_dir).resolve()
    try:
        ignored_policy_targets = workspace_target_paths(root_dir=resolved_root)
    except (FileNotFoundError, OSError, ValueError):
        ignored_policy_targets = ()
    ignored_children_by_parent: dict[str, set[str]] = {}
    for target_path in ignored_policy_targets:
        normalized = target_path.strip('/').replace('\\', '/')
        if not normalized:
            continue
        parent, _, child = normalized.rpartition('/')
        ignored_children_by_parent.setdefault(parent, set()).add(child)

    def _ignore(current_dir: str, entries: list[str]) -> set[str]:
        current = Path(current_dir).resolve()
        try:
            parent_key = current.relative_to(resolved_root).as_posix()
        except ValueError:
            parent_key = ''
        if parent_key == '.':
            parent_key = ''

        ignored = {
            name
            for name in entries
            if name in _REPO_COPY_IGNORED_NAMES
            or Path(name).suffix.lower() in _REPO_COPY_IGNORED_SUFFIXES
            or (
                parent_key == ''
                and any(name.startswith(prefix) for prefix in _REPO_COPY_IGNORED_TOP_LEVEL_PATTERNS)
            )
        }
        ignored.update(name for name in ignored_children_by_parent.get(parent_key, set()) if name in entries)
        return ignored

    return _ignore


def copy_repo_tree(source_root: Path, temp_root: Path) -> Path:
    resolved_source_root = Path(source_root).resolve()
    repo_copy = Path(temp_root).resolve() / 'repo'
    shutil.copytree(
        resolved_source_root,
        repo_copy,
        ignore=repo_copy_ignore(resolved_source_root),
        dirs_exist_ok=False,
    )
    return repo_copy


def base_env(repo_root: Path, temp_dir: Path, extra_env: Mapping[str, str] | None = None) -> dict[str, str]:
    resolved_repo_root = Path(repo_root).resolve()
    env = build_subprocess_env(
        resolved_repo_root,
        base_env=os.environ,
        extra_env={str(k): str(v) for k, v in (extra_env or {}).items()},
    )
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    _prepend_extension_dependency_paths(env, resolved_repo_root)
    env.setdefault('TMP', str(temp_dir))
    env.setdefault('TEMP', str(temp_dir))
    env.setdefault('TMPDIR', str(temp_dir))
    return env


def _prepend_extension_dependency_paths(env: dict[str, str], repo_root: Path) -> None:
    """将扩展源码和仓内离线 wheel 注入模块 smoke 子进程，保持测试与离线包真源一致。"""
    config_path = str(env.get(CONTROL_PLANE_CONFIG_ENV) or '').strip()
    if not config_path:
        return
    row = managed_extension_for_config_path(config_path, start_path=repo_root)
    if row is None:
        return
    entries: list[Path] = list(row.python_roots)
    wheelhouse = row.root_dir / 'offline_wheelhouse'
    if wheelhouse.is_dir():
        entries.extend(sorted(wheelhouse.glob('*.whl')))
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


def _run_with_temp_env(repo_root: Path, command: list[str], extra_env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    resolved_repo_root = Path(repo_root).resolve()
    temp_dir = make_temp_dir(resolved_repo_root, category='tests', prefix='agent_module')
    try:
        return subprocess.run(
            command,
            cwd=resolved_repo_root,
            text=True,
            capture_output=True,
            encoding='utf-8',
            errors='replace',
            env=base_env(resolved_repo_root, temp_dir, extra_env),
            check=False,
        )
    finally:
        try:
            remove_tree(temp_dir)
        except OSError:
            # 临时测试目录清理失败不能改变被测模块的真实返回码。
            pass
        prune_empty_parents(temp_dir.parent, stop_at=global_tmp_root())


def run_python_module(repo_root: Path, module: str, args: list[str], extra_env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return _run_with_temp_env(Path(repo_root), [sys.executable, '-B', '-m', module, *args], extra_env)


def _is_windows_bash_stub(path: Path) -> bool:
    normalized = str(path).replace('/', '\\').lower()
    return normalized.endswith('\\windows\\system32\\bash.exe') or '\\windowsapps\\bash.exe' in normalized


def resolve_bash_executable() -> str | None:
    override = str(os.environ.get('OPENCLAW_BASH_BIN') or '').strip()
    if override:
        candidate = Path(override).expanduser()
        return str(candidate) if candidate.exists() else override
    if os.name != 'nt':
        return shutil.which('bash') or 'bash'

    candidates: list[Path] = []
    seen: set[Path] = set()

    def add(path: str | Path | None) -> None:
        if not path:
            return
        candidate = Path(path).expanduser()
        if candidate in seen:
            return
        seen.add(candidate)
        candidates.append(candidate)

    add(shutil.which('bash'))
    for env_name in ('ProgramW6432', 'ProgramFiles', 'ProgramFiles(x86)'):
        base = str(os.environ.get(env_name) or '').strip()
        if not base:
            continue
        add(Path(base) / 'Git' / 'bin' / 'bash.exe')
        add(Path(base) / 'Git' / 'usr' / 'bin' / 'bash.exe')
    add(Path(r'C:\Download\Git\bin\bash.exe'))
    add(Path(r'C:\Download\Git\usr\bin\bash.exe'))

    for candidate in candidates:
        if candidate.exists() and not _is_windows_bash_stub(candidate):
            return str(candidate)
    return None


def run_bash_script(repo_root: Path, script_rel: str, args: list[str], extra_env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    resolved_repo_root = Path(repo_root).resolve()
    bash_executable = resolve_bash_executable()
    if not bash_executable:
        raise RuntimeError('未找到可用 bash；请安装 Git Bash 或设置 OPENCLAW_BASH_BIN')
    return _run_with_temp_env(resolved_repo_root, [bash_executable, str((resolved_repo_root / script_rel).resolve()), *args], extra_env)
