#!/usr/bin/env python3
"""control-plane runtime adapter 内置实现。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from openclaw.control_plane.registry import CliError
from openclaw.lib.repo.extension_envs import (
    ExtensionEnvError,
    build_extension_subprocess_env,
    extension_env_for_agent_runtime,
)
from openclaw.lib.repo.layout import CONTROL_PLANE_CONFIG_ENV, CONTROL_PLANE_PROFILE_ENV, resolve_selected_control_plane_config_path
from openclaw.lib.runtime.execution import build_subprocess_env, import_callable


def _require_object(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CliError(f'{label} 必须为对象', 2)
    return value


def _require_non_empty_text(value: Any, *, label: str) -> str:
    text = str(value or '').strip()
    if not text:
        raise CliError(f'{label} 不能为空', 2)
    return text


def _require_text_list(value: Any, *, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise CliError(f'{label} 必须为非空数组', 2)
    rows: list[str] = []
    for idx, item in enumerate(value):
        text = str(item or '').strip()
        if not text:
            raise CliError(f'{label}[{idx}] 必须为非空字符串', 2)
        rows.append(text)
    return rows


def resolve_runtime_tokens(args: list[str], *, state_root: Path, repo_root: Path) -> list[str]:
    return [str(item).replace('{state_root}', str(state_root)).replace('{repo_root}', str(repo_root)) for item in args]


def validate_python_module_config(config: Any, *, label: str) -> dict[str, Any]:
    payload = _require_object(config, label=label)
    module = _require_non_empty_text(payload.get('module'), label=f'{label}.module')
    return {'module': module}


def validate_shell_argv_config(config: Any, *, label: str) -> dict[str, Any]:
    payload = _require_object(config, label=label)
    argv = _require_text_list(payload.get('argv'), label=f'{label}.argv')
    return {'argv': argv}


def _selected_env_config_path(repo_root: Path) -> str | None:
    if not (str(os.environ.get(CONTROL_PLANE_CONFIG_ENV) or '').strip() or str(os.environ.get(CONTROL_PLANE_PROFILE_ENV) or '').strip()):
        return None
    try:
        return str(resolve_selected_control_plane_config_path(start_path=repo_root))
    except ValueError as exc:
        raise CliError(str(exc), 2) from exc


def run_python_module(*, runtime_config: dict[str, Any], runtime_args: list[str], state_root: Path, repo_root: Path, agent_ref: str, implementation_ref: str) -> int:
    config = validate_python_module_config(runtime_config, label=f'implementation {implementation_ref} runtime.config')
    module_name = str(config.get('module') or '').strip()
    resolved_args = resolve_runtime_tokens(list(runtime_args), state_root=state_root, repo_root=repo_root)
    config_path = _selected_env_config_path(repo_root)
    try:
        prepared_env = extension_env_for_agent_runtime(
            agent_ref,
            repo_root=repo_root,
            config_path=config_path,
            env=os.environ,
        )
    except ExtensionEnvError as exc:
        raise CliError(str(exc), 2) from exc
    if prepared_env is not None:
        env = build_extension_subprocess_env(prepared_env, repo_root=repo_root, base_env=os.environ, config_path=config_path)
        command = [
            str(prepared_env.python_executable),
            '-B',
            '-c',
            (
                'import sys\n'
                'from openclaw.control_plane.registry import CliError\n'
                'from openclaw.lib.runtime.execution import run_module_main\n'
                "raise SystemExit(run_module_main(sys.argv[1], sys.argv[2:], CliError, 'extension agent runtime'))\n"
            ),
            module_name,
            *resolved_args,
        ]
        process = subprocess.run(command, cwd=str(repo_root), env=env, check=False)
        return int(process.returncode)
    try:
        entry = import_callable(module_name, 'main', CliError, f'agent {agent_ref} 对应模块')
    except CliError as exc:
        if f'缺少可调用成员：{module_name}.main' in str(exc):
            raise CliError(f'agent {agent_ref} 对应模块缺少 main(argv) 入口：{module_name}', 2) from exc
        raise
    result = entry(resolved_args)
    if result is None:
        return 0
    if isinstance(result, bool):
        return 0 if result else 1
    if isinstance(result, int):
        return result
    if isinstance(result, str):
        try:
            return int(result.strip())
        except ValueError as exc:
            raise CliError(f'agent {agent_ref} 返回了无法解析为退出码的字符串：{result}', 2) from exc
    raise CliError(f'agent {agent_ref} 返回了不支持的 main() 返回值类型：{type(result).__name__}', 2)


def run_shell_argv(*, runtime_config: dict[str, Any], runtime_args: list[str], state_root: Path, repo_root: Path, agent_ref: str, implementation_ref: str) -> int:
    config = validate_shell_argv_config(runtime_config, label=f'agent {agent_ref} implementation {implementation_ref} runtime.config')
    argv = resolve_runtime_tokens(list(config.get('argv') or []), state_root=state_root, repo_root=repo_root)
    passthrough = resolve_runtime_tokens(list(runtime_args), state_root=state_root, repo_root=repo_root)
    env = build_subprocess_env(
        repo_root,
        config_path=_selected_env_config_path(repo_root),
        base_env=os.environ,
    )
    process = subprocess.run([*argv, *passthrough], cwd=str(repo_root), env=env, check=False)
    return int(process.returncode)
