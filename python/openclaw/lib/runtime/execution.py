#!/usr/bin/env python3
"""动态导入与子进程环境的共享执行辅助。"""
from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any, Callable

from ..cli.common import CliError
from ..repo.bootstrap import bootstrap_env_defaults, bootstrap_env_pythonpath
from ..repo.layout import (
    CONTROL_PLANE_CONFIG_ENV,
    CONTROL_PLANE_CONTAINER_REPO_ROOT,
    CONTROL_PLANE_PROFILE_ENV,
    control_plane_profile_id_for_config_path,
    resolve_control_plane_profile_service_config_path,
    resolve_repo_root,
)


ExecutionCallable = Callable[..., Any]


class DynamicImportError(RuntimeError):
    """动态模块或 callable 加载失败的基础错误。"""


class DynamicModuleImportError(DynamicImportError):
    """目标模块无法导入。"""


class DynamicCallableLookupError(DynamicImportError):
    """目标模块中无法解析指定 callable。"""


def _raise(exc_type: type[BaseException], message: str, *, cause: Exception | None = None) -> None:
    if issubclass(exc_type, DynamicImportError):
        error = exc_type(message)
        if cause is not None:
            raise error from cause
        raise error
    if issubclass(exc_type, CliError):
        error = exc_type(message, 2)
        if cause is not None:
            raise error from cause
        raise error
    error = exc_type(message)
    if cause is not None:
        raise error from cause
    raise error


def import_module_reference(
    module_name: str,
    exc_type: type[BaseException],
    label: str,
):
    """按模块名导入目标模块，并把底层异常收口为调用方指定的错误类型。"""
    try:
        return importlib.import_module(module_name)
    except Exception as exc:
        _raise(
            exc_type,
            f'{label} 模块导入失败：{module_name} ({exc})',
            cause=exc,
        )


def import_callable(
    module_name: str,
    attr_name: str,
    exc_type: type[BaseException],
    label: str,
) -> ExecutionCallable:
    """导入模块中的 callable，用于 CLI、扩展与运行适配器的统一入口解析。"""
    module = import_module_reference(module_name, exc_type, label)
    func = getattr(module, attr_name, None)
    if not callable(func):
        _raise(exc_type, f'{label} 缺少可调用成员：{module_name}.{attr_name}')
    return func


def validate_callable_reference(
    module_name: str,
    attr_name: str,
    exc_type: type[BaseException],
    label: str,
) -> None:
    """只校验 callable 引用是否可解析，不执行目标函数。"""
    import_callable(module_name, attr_name, exc_type, label)


def run_module_main(
    module_name: str,
    argv: list[str],
    exc_type: type[BaseException],
    label: str,
) -> int:
    """执行模块的 main(argv)，并把 None 结果规范为退出码 0。"""
    entry = import_callable(module_name, 'main', exc_type, label)
    result = entry(list(argv))
    return 0 if result is None else int(result)


def _normalize_env_config_path(value: str, start_path) -> Path:
    text = str(value or '').strip().replace('\\', '/')
    container_root = str(CONTROL_PLANE_CONTAINER_REPO_ROOT).rstrip('/')
    if text == container_root:
        return resolve_repo_root(Path(start_path)).resolve()
    prefix = f'{container_root}/'
    if text.startswith(prefix):
        return (resolve_repo_root(Path(start_path)) / text[len(prefix):]).resolve()
    return Path(value).resolve()


def _selected_config_path_from_env(env: dict[str, str], start_path) -> str:
    env_config_path = str(env.get(CONTROL_PLANE_CONFIG_ENV) or '').strip()
    env_profile = str(env.get(CONTROL_PLANE_PROFILE_ENV) or '').strip()
    if env_config_path and env_profile:
        path_profile = control_plane_profile_id_for_config_path(_normalize_env_config_path(env_config_path, start_path), start_path=Path(start_path))
        if path_profile != env_profile:
            raise ValueError(
                f'{CONTROL_PLANE_CONFIG_ENV} 与 {CONTROL_PLANE_PROFILE_ENV} 不一致：'
                f'{CONTROL_PLANE_CONFIG_ENV} -> {path_profile}, {CONTROL_PLANE_PROFILE_ENV}={env_profile}'
            )
    if env_config_path:
        return str(_normalize_env_config_path(env_config_path, start_path))
    if env_profile:
        return str(resolve_control_plane_profile_service_config_path(env_profile, start_path=Path(start_path)))
    return ''


def build_subprocess_env(
    start_path,
    config_path: str | os.PathLike[str] | None = None,
    *,
    base_env: dict[str, str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """构造子进程环境，统一注入仓库 Python 路径和控制面配置上下文。"""
    env = dict(os.environ if base_env is None else base_env)
    if extra_env:
        env.update({str(key): str(value) for key, value in extra_env.items()})
    env.update(bootstrap_env_defaults(start_path))
    selected_config_path = config_path if config_path is not None else _selected_config_path_from_env(env, start_path)
    bootstrap_env_pythonpath(
        env,
        start_path,
        config_path=selected_config_path,
    )
    return env
