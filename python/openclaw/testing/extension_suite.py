#!/usr/bin/env python3
"""为扩展包的兼容测试入口提供作用域化薄加载器。"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping, Sequence

from openclaw.testing.bootstrap_support import prepend_sys_path_entries


@dataclass(frozen=True)
class ExtensionSuiteConfig:
    """声明一个扩展测试聚合入口的加载边界。"""

    namespace: str
    test_root: Path
    python_entries: tuple[Path, ...] = ()
    include_subtrees: tuple[str, ...] = ()
    env_defaults: Mapping[str, str] | None = None
    remove_env_prefixes: tuple[str, ...] = ()


def _support_module_snapshot() -> dict[str, object]:
    return {
        name: module
        for name, module in sys.modules.items()
        if name == 'support' or name.startswith('support.')
    }


def _clear_support_modules() -> None:
    for name in sorted(
        [item for item in sys.modules if item == 'support' or item.startswith('support.')],
        reverse=True,
    ):
        sys.modules.pop(name, None)


def _namespace_module_snapshot(namespace: str) -> dict[str, object]:
    prefix = f'{namespace}_'
    return {
        name: module
        for name, module in sys.modules.items()
        if name == namespace or name.startswith(prefix)
    }


def _clear_namespace_modules(namespace: str) -> None:
    prefix = f'{namespace}_'
    for name in sorted(
        [item for item in sys.modules if item == namespace or item.startswith(prefix)],
        reverse=True,
    ):
        sys.modules.pop(name, None)


@contextmanager
def _extension_context(
    config: ExtensionSuiteConfig,
    *,
    module_bindings: Mapping[str, object] | None = None,
) -> Iterator[None]:
    original_path = list(sys.path)
    support_snapshot = _support_module_snapshot()
    namespace_snapshot = _namespace_module_snapshot(config.namespace)
    affected_env = {
        key: value
        for key, value in os.environ.items()
        if key in dict(config.env_defaults or {}) or any(key.startswith(prefix) for prefix in config.remove_env_prefixes)
    }
    missing_env = set(config.env_defaults or {}) - set(os.environ)
    try:
        for key in list(os.environ):
            if any(key.startswith(prefix) for prefix in config.remove_env_prefixes):
                os.environ.pop(key, None)
        for key, value in dict(config.env_defaults or {}).items():
            os.environ.setdefault(str(key), str(value))
        prepend_sys_path_entries([*config.python_entries, config.test_root])
        _clear_support_modules()
        _clear_namespace_modules(config.namespace)
        sys.modules.update(dict(module_bindings or {}))
        support_path = (config.test_root / 'support').resolve()
        if support_path.is_dir():
            package = types.ModuleType('support')
            package.__path__ = [str(support_path)]  # type: ignore[attr-defined]
            sys.modules['support'] = package
        yield
    finally:
        sys.path[:] = original_path
        _clear_support_modules()
        sys.modules.update(support_snapshot)
        _clear_namespace_modules(config.namespace)
        sys.modules.update(namespace_snapshot)
        for key in missing_env:
            os.environ.pop(key, None)
        for key in list(os.environ):
            if any(key.startswith(prefix) for prefix in config.remove_env_prefixes) and key not in affected_env:
                os.environ.pop(key, None)
        os.environ.update(affected_env)


def _test_paths(config: ExtensionSuiteConfig) -> list[Path]:
    roots = (
        [config.test_root / subtree for subtree in config.include_subtrees]
        if config.include_subtrees
        else [config.test_root]
    )
    aggregate = (config.test_root / 'test_all.py').resolve()
    return [
        path
        for root in roots
        for path in sorted(root.rglob('test_*.py'))
        if path.resolve() != aggregate
    ]


def _load_module(config: ExtensionSuiteConfig, path: Path) -> object:
    relative = path.resolve().relative_to(config.test_root.resolve())
    module_name = f'{config.namespace}_' + '_'.join(relative.with_suffix('').parts)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'cannot load extension test module: {relative.as_posix()}')
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class ScopedExtensionSuite(unittest.TestSuite):
    """在加载和执行阶段均恢复环境、导入路径与通用 support 命名空间。"""

    def __init__(
        self,
        config: ExtensionSuiteConfig,
        tests: Sequence[unittest.TestSuite],
        module_bindings: Mapping[str, object],
    ) -> None:
        """保存 suite 执行所需的作用域配置和已加载测试模块。

        参数：
            config（ExtensionSuiteConfig）：扩展测试的路径与环境边界。
            tests（Sequence[unittest.TestSuite]）：已经完成发现的子 suite。
            module_bindings（Mapping[str, object]）：执行阶段临时恢复的测试模块映射。
        """
        super().__init__(tests)
        self._config = config
        self._module_bindings = dict(module_bindings)

    def run(self, result: unittest.TestResult, debug: bool = False) -> unittest.TestResult:
        """在完整恢复进程状态的上下文中执行聚合 suite。

        参数：
            result（unittest.TestResult）：接收用例状态的结果对象。
            debug（bool）：是否让用例异常直接传播。

        返回：
            unittest.TestResult：执行完成后的同一结果对象。
        """
        with _extension_context(self._config, module_bindings=self._module_bindings):
            return super().run(result, debug)


def load_extension_suite(loader: unittest.TestLoader, config: ExtensionSuiteConfig) -> unittest.TestSuite:
    """从真实测试文件构建兼容聚合 suite，并把所有进程状态修改限制在 suite 作用域。

    参数：
        loader（unittest.TestLoader）：调用入口提供的 unittest 加载器。
        config（ExtensionSuiteConfig）：测试根、导入路径、环境默认值和命名空间声明。

    返回：
        unittest.TestSuite：执行后自动恢复环境和通用模块绑定的聚合 suite。
    """
    suites: list[unittest.TestSuite] = []
    module_bindings: dict[str, object] = {}
    with _extension_context(config):
        for path in _test_paths(config):
            suites.append(loader.loadTestsFromModule(_load_module(config, path)))
        module_bindings = _namespace_module_snapshot(config.namespace)
    return ScopedExtensionSuite(config, suites, module_bindings)
