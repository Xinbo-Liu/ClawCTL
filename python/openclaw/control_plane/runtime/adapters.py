#!/usr/bin/env python3
"""control-plane runtime adapter 内置实现。"""
from __future__ import annotations

import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, MutableMapping

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


def _optional_text_list(value: Any, *, label: str) -> list[str]:
    """读取可选字符串列表配置。

    参数：
        value（Any）：配置中的原始字段值。
        label（str）：错误消息中的字段路径。

    返回：
        返回 list[str]；字段缺失时返回空列表，存在时返回去空白后的字符串集合。

    异常：
        CliError：字段存在但不是数组，或数组项为空字符串。
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise CliError(f'{label} 必须为数组', 2)
    rows: list[str] = []
    for idx, item in enumerate(value):
        text = str(item or '').strip()
        if not text:
            raise CliError(f'{label}[{idx}] 必须为非空字符串', 2)
        rows.append(text)
    return rows


def _optional_env_policy(value: Any, *, label: str) -> dict[str, list[str]]:
    """读取 Python module runtime 的环境隔离策略。

    参数：
        value（Any）：`runtime.config.envPolicy` 原始值。
        label（str）：错误消息中的字段路径。

    返回：
        返回 dict[str, list[str]]；包含 `denyExact` 和 `denyPrefixes` 两个列表，缺省为空。

    异常：
        CliError：`envPolicy` 不是对象或列表字段格式非法。
    """
    if value is None:
        return {'denyExact': [], 'denyPrefixes': []}
    payload = _require_object(value, label=label)
    return {
        'denyExact': _optional_text_list(payload.get('denyExact'), label=f'{label}.denyExact'),
        'denyPrefixes': _optional_text_list(payload.get('denyPrefixes'), label=f'{label}.denyPrefixes'),
    }


def resolve_runtime_tokens(args: list[str], *, state_root: Path, repo_root: Path) -> list[str]:
    return [str(item).replace('{state_root}', str(state_root)).replace('{repo_root}', str(repo_root)) for item in args]


def validate_python_module_config(config: Any, *, label: str) -> dict[str, Any]:
    """校验 Python module runtime 配置。

    参数：
        config（Any）：模块 manifest 中的 `runtime.config` 原始对象。
        label（str）：错误消息中的配置路径标签。

    返回：
        返回 dict[str, Any]；包含入口模块名和可选 envPolicy 环境隔离策略。

    异常：
        CliError：配置不是对象、module 为空或 envPolicy 格式非法。
    """
    payload = _require_object(config, label=label)
    module = _require_non_empty_text(payload.get('module'), label=f'{label}.module')
    env_policy = _optional_env_policy(payload.get('envPolicy'), label=f'{label}.envPolicy')
    return {'module': module, 'envPolicy': env_policy}


def _env_key_denied_by_policy(key: str, policy: dict[str, list[str]]) -> bool:
    """判断单个环境变量名是否命中 runtime envPolicy。

    参数：
        key（str）：环境变量名。
        policy（dict[str, list[str]]）：包含 `denyExact` 和 `denyPrefixes` 的隔离策略。

    返回：
        返回 bool；大小写不敏感命中精确拒绝或前缀拒绝时为 True。
    """
    marker = key.casefold()
    deny_exact = {item.casefold() for item in policy.get('denyExact', [])}
    deny_prefixes = tuple(item.casefold() for item in policy.get('denyPrefixes', []))
    return marker in deny_exact or any(marker.startswith(prefix) for prefix in deny_prefixes)


def _apply_env_policy(env: MutableMapping[str, str], policy: dict[str, list[str]]) -> None:
    """按 runtime envPolicy 原地删除环境变量映射中的键。

    参数：
        env（MutableMapping[str, str]）：即将传给 Python module 的环境变量映射；可以是子进程 env 字典或 `os.environ`。
        policy（dict[str, list[str]]）：包含 `denyExact` 和 `denyPrefixes` 的隔离策略。

    副作用：
        从 `env` 中删除大小写不敏感精确匹配或前缀匹配的键；缺省空策略不修改环境。
    """
    if not policy.get('denyExact') and not policy.get('denyPrefixes'):
        return
    for key in list(env):
        if _env_key_denied_by_policy(key, policy):
            env.pop(key, None)


@contextmanager
def _temporary_env_policy(policy: dict[str, list[str]]) -> Iterator[None]:
    """在 in-process Python module 执行期间临时应用 envPolicy。

    参数：
        policy（dict[str, list[str]]）：包含 `denyExact` 和 `denyPrefixes` 的隔离策略。

    产出：
        产出 Iterator[None]；`with` 块内被拒绝的环境变量对模块导入和 `main()` 均不可见。

    副作用：
        临时修改当前进程 `os.environ`，退出时恢复原值，并清理执行期间新增且命中策略的变量。
    """
    if not policy.get('denyExact') and not policy.get('denyPrefixes'):
        yield
        return
    old_values = {key: os.environ.get(key) for key in list(os.environ) if _env_key_denied_by_policy(key, policy)}
    try:
        _apply_env_policy(os.environ, policy)
        yield
    finally:
        for key in list(os.environ):
            if key not in old_values and _env_key_denied_by_policy(key, policy):
                os.environ.pop(key, None)
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


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
    """执行 Python module runtime adapter。

    参数：
        runtime_config（dict[str, Any]）：实现声明中的 runtime config，包含 `module` 和可选 `envPolicy`。
        runtime_args（list[str]）：控制面传入的运行参数，支持 `{state_root}` 和 `{repo_root}` 占位符。
        state_root（Path）：本次运行状态根目录。
        repo_root（Path）：仓库根目录。
        agent_ref（str）：被调度 agent 的引用，用于判断是否属于 managed extension。
        implementation_ref（str）：实现引用，用于错误消息定位。

    返回：
        返回 int；子进程或 in-process `main()` 的退出码。

    异常：
        CliError：配置非法、扩展 venv 未准备、入口缺失或返回值无法映射为退出码。

    副作用：
        可能启动扩展虚拟环境子进程；in-process 路径会在模块导入和 `main()` 执行期间临时应用 envPolicy，并在退出后恢复当前进程环境。
    """
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
        _apply_env_policy(env, config.get('envPolicy') if isinstance(config.get('envPolicy'), dict) else {})
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
    env_policy = config.get('envPolicy') if isinstance(config.get('envPolicy'), dict) else {}
    with _temporary_env_policy(env_policy):
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
