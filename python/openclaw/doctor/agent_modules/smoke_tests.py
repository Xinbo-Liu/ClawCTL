#!/usr/bin/env python3
"""执行受管显式扩展包 `agent/extensions/*/tests/modules/*` 下的 smoke / regression 测试。"""
from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import re
import sys
import types
import unittest
from pathlib import Path

from openclaw.lib.repo.managed_extensions import managed_extension_test_roots
from openclaw.lib.repo.managed_extensions import managed_explicit_extensions
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.testing.bootstrap_support import prepend_sys_path_entries

ROOT_DIR = resolve_repo_root(Path(__file__))


def discover_test_files(
    module_name: str | None = None,
    *,
    repo_root: Path | None = None,
    extension_id: str | None = None,
) -> list[Path]:
    root_dir = ROOT_DIR if repo_root is None else Path(repo_root).resolve()
    files: list[Path] = []
    candidates: list[Path] = []
    test_roots = list(managed_extension_test_roots(root_dir, extension_id=extension_id))
    if module_name:
        candidates = [root / 'modules' / module_name for root in test_roots]
    else:
        for root in test_roots:
            modules_root = root / 'modules'
            if not modules_root.exists():
                continue
            candidates.extend(path for path in sorted(modules_root.iterdir()) if path.is_dir())
    for tests_dir in candidates:
        if not tests_dir.exists():
            continue
        files.extend(sorted(tests_dir.glob('test_*.py')))
    return files


def _tests_root_for_path(path: Path) -> Path | None:
    resolved = Path(path).resolve()
    for parent in resolved.parents:
        if parent.name != 'tests':
            continue
        try:
            resolved.relative_to(parent / 'modules')
        except ValueError:
            continue
        return parent
    return None


def _clear_support_namespace() -> None:
    for name in [key for key in sys.modules if key == 'support' or key.startswith('support.')]:
        sys.modules.pop(name, None)


def _path_is_relative_to(path: Path, base: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(base).resolve())
        return True
    except ValueError:
        return False


def _extension_import_entries_for_path(path: Path, *, repo_root: Path | None = None) -> tuple[Path, ...]:
    """为扩展测试注入扩展源码和仓内离线 wheel，避免模块 smoke 依赖宿主机 Python。"""
    root_dir = ROOT_DIR if repo_root is None else Path(repo_root).resolve()
    resolved = Path(path).resolve()
    entries: list[Path] = []
    for row in managed_explicit_extensions(root_dir):
        if not _path_is_relative_to(resolved, row.root_dir):
            continue
        entries.extend(row.python_roots)
        wheelhouse = row.root_dir / 'offline_wheelhouse'
        if wheelhouse.is_dir():
            entries.extend(sorted(wheelhouse.glob('*.whl')))
        break
    return tuple(entries)


def _prepare_extension_test_imports(path: Path) -> None:
    prepend_sys_path_entries(_extension_import_entries_for_path(path))
    tests_root = _tests_root_for_path(path)
    if tests_root is None:
        return
    support_dir = tests_root / 'support'
    _clear_support_namespace()
    if not support_dir.is_dir():
        return
    support_module = types.ModuleType('support')
    support_module.__path__ = [str(support_dir)]  # type: ignore[attr-defined]
    support_module.__package__ = 'support'
    spec = importlib.machinery.ModuleSpec('support', loader=None, is_package=True)
    spec.submodule_search_locations = [str(support_dir)]
    support_module.__spec__ = spec
    sys.modules['support'] = support_module


def _test_module_name(path: Path, *, repo_root: Path | None = None) -> str:
    root_dir = ROOT_DIR if repo_root is None else Path(repo_root).resolve()
    try:
        stem_path = Path(path).resolve().relative_to(root_dir).with_suffix('')
    except ValueError:
        stem_path = Path(path).resolve().with_suffix('')
    suffix = re.sub(r'[^A-Za-z0-9_]+', '_', stem_path.as_posix()).strip('_')
    return f'openclaw_agent_test_{suffix}'


def load_test_module(path: Path, *, repo_root: Path | None = None) -> types.ModuleType:
    module_name = _test_module_name(path, repo_root=repo_root)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'无法加载测试模块：{path}')
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    _prepare_extension_test_imports(path)
    spec.loader.exec_module(module)
    return module


class _ExtensionModuleSuite(unittest.TestSuite):
    def __init__(self, path: Path, tests: unittest.TestSuite) -> None:
        super().__init__(tests)
        self._path = path

    def run(self, result: unittest.TestResult, debug: bool = False) -> unittest.TestResult:
        _prepare_extension_test_imports(self._path)
        return super().run(result, debug)


def build_suite(
    module_name: str | None = None,
    *,
    extension_id: str | None = None,
    repo_root: Path | None = None,
) -> unittest.TestSuite:
    suite = unittest.TestSuite()
    loader = unittest.defaultTestLoader
    files = discover_test_files(module_name, extension_id=extension_id, repo_root=repo_root)
    if not files:
        raise RuntimeError('未发现任何 agent 模块测试文件')
    for path in files:
        module = load_test_module(path, repo_root=repo_root)
        suite.addTest(_ExtensionModuleSuite(path, loader.loadTestsFromModule(module)))
    return suite


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='执行 agent 模块 smoke / regression 测试')
    parser.add_argument('--module', help='只跑单个模块的 tests 目录')
    parser.add_argument('--extension', help='只跑指定受管显式扩展包的 tests 目录')
    parser.add_argument('--list', action='store_true', help='只列出将执行的测试文件')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    ns = parse_args(sys.argv[1:] if argv is None else argv)
    files = discover_test_files(ns.module, extension_id=ns.extension)
    if ns.list:
        for path in files:
            print(path.relative_to(ROOT_DIR))
        return 0
    suite = build_suite(ns.module, extension_id=ns.extension)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
