#!/usr/bin/env python3
"""Run the repository unittest suite behind the shell entrypoint.

The supported shell entrypoint is ``scripts/testing/run_repo_unittest.sh``.
This module remains the Python truth source behind that wrapper.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from openclaw.testing.bootstrap_support import ensure_repo_pythonpath, prepend_sys_path_entries


BOOTSTRAP_ROOT = ensure_repo_pythonpath(Path(__file__))

from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.managed_extensions import (
    ManagedExtensionRow,
    managed_explicit_extensions,
    managed_extension_test_roots,
)
from openclaw.lib.runtime.bounded_process import run_bounded_process, truncate_process_output


SKIP_BYTECODE_CLEANUP_ENV = 'OPENCLAW_REPO_UNITTEST_SKIP_BYTECODE_CLEANUP'
DEFAULT_START_DIR = 'python/openclaw/tests'
DEFAULT_WORKER_TIMEOUT_SECONDS = 300.0
DEFAULT_SUITE_TIMEOUT_SECONDS = 900.0
DEFAULT_WORK_UNITS_PER_JOB = 2
WORKER_TIMEOUT_ENV = 'OPENCLAW_REPO_UNITTEST_WORKER_TIMEOUT_SECONDS'
SUITE_TIMEOUT_ENV = 'OPENCLAW_REPO_UNITTEST_SUITE_TIMEOUT_SECONDS'


@dataclass(frozen=True)
class TestFile:
    """记录无需导入即可完成分桶的测试文件与静态权重。"""

    selector: str
    estimated_cases: int


def _require_managed_extension_row(row: object) -> ManagedExtensionRow:
    """确认受管扩展枚举返回完整的仓库合同记录。

    参数：
        row（object）：来自 ``managed_explicit_extensions`` 的扩展记录对象。

    返回：
        ManagedExtensionRow：可直接用于测试导入路径和 wheelhouse 解析的扩展记录。

    异常：
        TypeError：当调用方或测试替身没有遵守受管扩展记录契约时抛出清晰错误。
    """
    if isinstance(row, ManagedExtensionRow):
        return row
    extension_id = str(getattr(row, 'id', '<unknown>') or '<unknown>')
    raise TypeError(
        'managed_explicit_extensions() must return ManagedExtensionRow objects; '
        f'got {type(row).__name__} for extension {extension_id}'
    )


def _managed_extension_import_entries(root: Path) -> tuple[Path, ...]:
    entries: list[Path] = []
    for raw_row in managed_explicit_extensions(root):
        row = _require_managed_extension_row(raw_row)
        for python_root in row.python_roots:
            if python_root.is_dir() and python_root not in entries:
                entries.append(python_root)
        wheelhouse = row.root_dir / 'offline_wheelhouse'
        if wheelhouse.is_dir():
            for wheel in sorted(wheelhouse.glob('*.whl')):
                if wheel not in entries:
                    entries.append(wheel)
    return tuple(entries)


def repo_root() -> Path:
    return resolve_repo_root(Path(__file__))


def _repo_bytecode_roots(root: Path) -> tuple[Path, ...]:
    repo_root = Path(root).resolve()
    candidates: list[Path] = [(repo_root / 'python').resolve()]
    extensions_root = (repo_root / 'agent' / 'extensions').resolve()
    if extensions_root.is_dir():
        for extension_root in sorted(path for path in extensions_root.iterdir() if path.is_dir()):
            candidates.extend(
                [
                    (extension_root / 'python').resolve(),
                    (extension_root / 'tests').resolve(),
                ]
            )
    roots: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            candidate.relative_to(repo_root)
        except ValueError:
            continue
        marker = str(candidate)
        if marker in seen:
            continue
        seen.add(marker)
        roots.append(candidate)
    return tuple(roots)


def _clean_repo_bytecode_residue(root: Path) -> None:
    for python_root in _repo_bytecode_roots(root):
        for path in sorted(python_root.rglob('__pycache__'), reverse=True):
            try:
                path.relative_to(python_root)
            except ValueError:
                continue
            shutil.rmtree(path, ignore_errors=True)


class StartDirAction(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[str] | None,
        option_string: str | None = None,
    ) -> None:
        setattr(namespace, self.dest, values)
        setattr(namespace, 'start_dir_explicit', True)


def selector_to_test_name(selector: str, root: Path) -> str:
    root = Path(root).resolve()
    parts = [part.strip() for part in str(selector or '').split('::')]
    target = parts[0]
    if not target:
        raise ValueError('empty test selector')
    module_name = target
    if target.endswith('.py') or '/' in target or '\\' in target:
        candidate = Path(target)
        candidate = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        relative = candidate.relative_to(root)
        module_parts = list(relative.with_suffix('').parts)
        if module_parts and module_parts[0] == 'python':
            module_parts = module_parts[1:]
        module_name = '.'.join(module_parts)
    if module_name.endswith('.__init__'):
        module_name = module_name[:-9]
    extras = [part for part in parts[1:] if part]
    return '.'.join([module_name, *extras]) if extras else module_name


def add_parser_arguments(parser: argparse.ArgumentParser, *, include_selectors: bool = True) -> argparse.ArgumentParser:
    parser.set_defaults(start_dir_explicit=False)
    parser.add_argument('-q', '--quiet', action='store_true')
    parser.add_argument(
        '-j',
        '--jobs',
        default=os.environ.get('OPENCLAW_REPO_UNITTEST_JOBS', 'auto'),
        help='parallel worker count for discovered suites; use 1 to disable or auto for the default',
    )
    parser.add_argument('--import-mode', default='')
    parser.add_argument('--durations', type=int, default=0, help='print the N slowest tests; disables worker parallelism for accurate timings')
    parser.add_argument('--report-json', default='', help='write the deterministic machine execution report to this path')
    parser.add_argument('--list-tests', action='store_true', help='list deterministic test IDs without running tests')
    parser.add_argument('--json', dest='json_output', action='store_true', help='emit --list-tests output as JSON')
    parser.add_argument('-s', '--start-dir', default=DEFAULT_START_DIR, action=StartDirAction)
    parser.add_argument('-p', '--pattern', default='test_*.py')
    parser.add_argument('--worker-mode', choices=('run', 'list'), default='', help=argparse.SUPPRESS)
    parser.add_argument('--worker-report', default='', help=argparse.SUPPRESS)
    parser.add_argument('--worker-index', type=int, default=0, help=argparse.SUPPRESS)
    if include_selectors:
        parser.add_argument('selectors', nargs='*')
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='run_repo_unittest',
        description='run the repository unittest suite via the supported shell wrapper',
        epilog='recommended preflight:\n  bash ./scripts/testing/check_repo_test_readiness.sh',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    return add_parser_arguments(parser)


def _test_support_root_for_path(path: Path) -> Path:
    candidates = [path.parent, *path.parents]
    for candidate in candidates:
        if candidate.name == 'tests':
            return candidate
    return path.parent


def _extension_import_entries_for_path(path: Path, root: Path) -> tuple[Path, ...]:
    try:
        relative_parts = Path(path).resolve().relative_to(Path(root).resolve()).parts
    except ValueError:
        return ()
    if len(relative_parts) < 4 or relative_parts[0:2] != ('agent', 'extensions'):
        return ()
    extension_root = Path(root).resolve().joinpath(*relative_parts[:3])
    entries: list[Path] = []
    python_root = extension_root / 'python'
    if python_root.is_dir():
        entries.append(python_root)
    wheelhouse = extension_root / 'offline_wheelhouse'
    if wheelhouse.is_dir():
        entries.extend(sorted(wheelhouse.glob('*.whl')))
    return tuple(entries)


def _bind_test_support_package(support_root: Path) -> None:
    support_path = (Path(support_root) / 'support').resolve()
    if not support_path.is_dir():
        return
    existing = sys.modules.get('support')
    if existing is not None:
        existing_paths = [Path(item).resolve() for item in getattr(existing, '__path__', []) if str(item).strip()]
        if support_path in existing_paths:
            return
        for module_name in sorted(
            [name for name in sys.modules if name == 'support' or name.startswith('support.')],
            reverse=True,
        ):
            sys.modules.pop(module_name, None)
    package = types.ModuleType('support')
    package.__path__ = [str(support_path)]  # type: ignore[attr-defined]
    sys.modules['support'] = package


def _load_suite_from_file_selector(selector: str, root: Path) -> unittest.TestSuite:
    root = Path(root).resolve()
    parts = [part.strip() for part in str(selector or '').split('::')]
    target = parts[0]
    candidate = Path(target)
    if candidate.is_absolute():
        path = candidate.resolve()
        try:
            relative = path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f'test selector path must stay inside repository: {selector}') from exc
    else:
        path = (root / candidate).resolve()
        try:
            relative = path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f'test selector path must stay inside repository: {selector}') from exc
    if not path.is_file():
        raise ValueError(f'test selector path does not exist: {relative.as_posix()}')
    support_root = _test_support_root_for_path(path)
    prepend_sys_path_entries([*_extension_import_entries_for_path(path, root), support_root])
    _bind_test_support_package(support_root)
    module_name = 'openclaw_repo_file_test_' + '_'.join(relative.with_suffix('').parts)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'cannot load test selector: {relative.as_posix()}')
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    name = '.'.join([module_name, *[part for part in parts[1:] if part]])
    return unittest.defaultTestLoader.loadTestsFromName(name)


def _module_file(module: object) -> Path | None:
    module_file = getattr(module, '__file__', None)
    if not module_file:
        return None
    try:
        return Path(str(module_file)).resolve()
    except OSError:
        return None


def _discover_module_name(start_dir: Path, path: Path) -> str:
    return '.'.join(path.resolve().relative_to(start_dir.resolve()).with_suffix('').parts)


def _clear_discover_module_conflicts(start_dir: Path, pattern: str) -> None:
    for path in sorted(start_dir.rglob(pattern)):
        if not path.is_file():
            continue
        module_name = _discover_module_name(start_dir, path)
        existing = sys.modules.get(module_name)
        existing_file = _module_file(existing) if existing is not None else None
        if existing_file is not None and existing_file != path.resolve():
            del sys.modules[module_name]


def _suite_from_selectors(selectors: Iterable[str], root: Path) -> unittest.TestSuite:
    root = Path(root).resolve()
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    for selector in selectors:
        target = str(selector or '').split('::', 1)[0]
        if target.endswith('.py') or '/' in target or '\\' in target:
            candidate = Path(target)
            if candidate.is_absolute():
                path = candidate.resolve()
                try:
                    relative = path.relative_to(root)
                except ValueError:
                    relative = path
            else:
                relative = candidate
            if relative.parts[:1] != ('python',):
                suite.addTests(_load_suite_from_file_selector(selector, root))
                continue
        suite.addTests(loader.loadTestsFromName(selector_to_test_name(selector, root)))
    return suite


def _suite_from_start_dir(root: Path, start_dir: Path, pattern: str) -> unittest.TestSuite:
    if not start_dir.exists():
        return unittest.TestSuite()
    aggregate = start_dir / 'test_all.py'
    if str(pattern) == 'test_*.py' and aggregate.is_file():
        return _load_suite_from_file_selector(aggregate.resolve().relative_to(root.resolve()).as_posix(), root)
    _clear_discover_module_conflicts(start_dir, pattern)
    loader = unittest.TestLoader()
    return loader.discover(start_dir=str(start_dir), pattern=pattern)


def _suite_from_start_dirs(root: Path, start_dirs: Sequence[Path], pattern: str) -> unittest.TestSuite:
    suite = unittest.TestSuite()
    for start_dir in start_dirs:
        suite.addTests(_suite_from_start_dir(root, start_dir, pattern))
    return suite


def _default_start_dirs(args: argparse.Namespace, root: Path) -> tuple[Path, ...]:
    start_dir = (root / str(args.start_dir)).resolve()
    if not bool(getattr(args, 'start_dir_explicit', False)) and str(args.start_dir) == DEFAULT_START_DIR:
        return (start_dir, *managed_extension_test_roots(root))
    return (start_dir,)


def _estimate_test_cases(path: Path) -> int:
    try:
        source = path.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return 1
    return max(1, sum(1 for line in source.splitlines() if line.lstrip().startswith('def test_')))


def _test_file_inventory(args: argparse.Namespace, root: Path) -> tuple[TestFile, ...]:
    """仅枚举测试文件并估算分桶权重，不在父进程导入测试模块。

    参数：
        args（argparse.Namespace）：包含 start-dir、pattern 和 selector 的 runner 参数。
        root（Path）：仓库根目录。

    返回：
        tuple[TestFile, ...]：按仓库相对路径稳定排序且排除聚合入口的测试文件。
    """
    if list(getattr(args, 'selectors', []) or []):
        return ()
    rows: dict[str, TestFile] = {}
    for start_dir in _default_start_dirs(args, root):
        if not start_dir.is_dir():
            continue
        for path in sorted(start_dir.rglob(str(args.pattern))):
            if not path.is_file() or path.name == 'test_all.py':
                continue
            selector = path.resolve().relative_to(root.resolve()).as_posix()
            rows[selector] = TestFile(selector=selector, estimated_cases=_estimate_test_cases(path))
    return tuple(rows[key] for key in sorted(rows))


def _suite_from_args(args: argparse.Namespace, root: Path) -> unittest.TestSuite:
    selectors = [str(item).strip() for item in list(args.selectors or []) if str(item).strip()]
    prepend_sys_path_entries(_managed_extension_import_entries(root))
    if selectors:
        return _suite_from_selectors(selectors, root)
    inventory = _test_file_inventory(args, root)
    return _suite_from_selectors([row.selector for row in inventory], root)


def _iter_test_cases(suite: unittest.TestSuite) -> Iterable[unittest.TestCase]:
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _iter_test_cases(item)
        else:
            yield item


def _coerce_jobs(raw: str | int | None) -> int | None:
    token = str(raw or '').strip().lower()
    if not token or token == 'auto':
        return None
    value = int(token)
    if value < 1:
        raise ValueError('jobs must be >= 1')
    return value


def _default_jobs() -> int:
    cpu_count = os.cpu_count() or 1
    return max(1, min(4, cpu_count))


def _parallel_jobs_for(args: argparse.Namespace) -> int:
    configured = _coerce_jobs(args.jobs)
    return configured if configured is not None else _default_jobs()


def _module_case_counts(suite: unittest.TestSuite) -> dict[str, int]:
    counts: dict[str, int] = {}
    for test in _iter_test_cases(suite):
        module_name = str(test.__class__.__module__).strip()
        if not module_name:
            continue
        counts[module_name] = counts.get(module_name, 0) + 1
    return counts


def _selector_for_module(module_name: str, root: Path) -> str:
    module = sys.modules.get(module_name)
    module_file = getattr(module, '__file__', None)
    if not module_file:
        return module_name
    try:
        return str(Path(module_file).resolve().relative_to(root))
    except ValueError:
        return module_name


def _selector_for_test(test: unittest.TestCase, root: Path) -> str:
    module_name = str(test.__class__.__module__).strip()
    module_selector = _selector_for_module(module_name, root)
    class_name = str(test.__class__.__name__).strip()
    method_name = str(getattr(test, '_testMethodName', '') or '').strip()
    if module_selector != module_name and class_name and method_name:
        return f'{module_selector}::{class_name}::{method_name}'
    test_id = str(test.id()).strip()
    return test_id or module_selector


def _selector_case_counts(suite: unittest.TestSuite, root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for test in _iter_test_cases(suite):
        selector = _selector_for_test(test, root)
        counts[selector] = counts.get(selector, 0) + 1
    return counts


def _module_selector_case_counts(suite: unittest.TestSuite, root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for test in _iter_test_cases(suite):
        module_name = str(test.__class__.__module__).strip()
        if not module_name:
            continue
        selector = _selector_for_module(module_name, root)
        counts[selector] = counts.get(selector, 0) + 1
    return counts


def _is_plain_file_selector(selector: str) -> bool:
    target = str(selector or '').split('::', 1)[0].strip()
    return bool(target) and target.endswith('.py') and '::' not in str(selector or '')


def _all_selectors_are_plain_files(selectors: Iterable[str]) -> bool:
    rows = [str(selector or '').strip() for selector in selectors if str(selector or '').strip()]
    return bool(rows) and all(_is_plain_file_selector(selector) for selector in rows)


def _parallel_selector_counts(args: argparse.Namespace, suite: unittest.TestSuite, root: Path) -> dict[str, int]:
    selectors = list(getattr(args, 'selectors', []) or [])
    if not selectors or _all_selectors_are_plain_files(selectors):
        return _module_selector_case_counts(suite, root)
    return _selector_case_counts(suite, root)


def _build_parallel_buckets(selector_counts: dict[str, int], jobs: int) -> list[tuple[str, ...]]:
    if jobs < 1:
        raise ValueError('jobs must be >= 1')
    buckets: list[dict[str, object]] = [
        {'selectors': [], 'case_count': 0}
        for _ in range(min(jobs, max(1, len(selector_counts))))
    ]
    for selector, case_count in sorted(selector_counts.items(), key=lambda item: (-item[1], item[0])):
        bucket = min(buckets, key=lambda item: (int(item['case_count']), len(item['selectors'])))  # type: ignore[arg-type]
        selectors = bucket['selectors']
        assert isinstance(selectors, list)
        selectors.append(selector)
        bucket['case_count'] = int(bucket['case_count']) + int(case_count)
    return [tuple(bucket['selectors']) for bucket in buckets if bucket['selectors']]


def _build_file_buckets(inventory: Sequence[TestFile], jobs: int) -> list[tuple[str, ...]]:
    """按静态测试方法数平衡文件清单，不触发测试模块导入。"""
    if jobs < 1:
        raise ValueError('jobs must be >= 1')
    if not inventory:
        return []
    buckets: list[dict[str, object]] = [
        {'selectors': [], 'weight': 0}
        for _ in range(min(jobs, len(inventory)))
    ]
    for row in sorted(inventory, key=lambda item: (-item.estimated_cases, item.selector)):
        bucket = min(buckets, key=lambda item: (int(item['weight']), len(item['selectors'])))  # type: ignore[arg-type]
        selectors = bucket['selectors']
        assert isinstance(selectors, list)
        selectors.append(row.selector)
        bucket['weight'] = int(bucket['weight']) + row.estimated_cases
    return [tuple(str(selector) for selector in bucket['selectors']) for bucket in buckets if bucket['selectors']]


def _inventory_work_unit_count(file_count: int, jobs: int, *, list_only: bool) -> int:
    if file_count < 1 or jobs < 1:
        raise ValueError('file_count and jobs must be >= 1')
    multiplier = 1 if list_only else DEFAULT_WORK_UNITS_PER_JOB
    return min(file_count, jobs * multiplier)


def _remaining_worker_timeout(worker_timeout: float, suite_deadline: float, *, now: float) -> float:
    return max(0.0, min(worker_timeout, suite_deadline - now))


def _parallelizable_suite(args: argparse.Namespace, suite: unittest.TestSuite) -> bool:
    test_count = suite.countTestCases()
    if int(getattr(args, 'durations', 0) or 0) > 0 or _parallel_jobs_for(args) <= 1 or test_count <= 1:
        return False
    if list(getattr(args, 'selectors', []) or []) and test_count < 12:
        return False
    return True


class TimingTextTestResult(unittest.TextTestResult):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.test_durations: list[tuple[float, str, str]] = []
        self.test_statuses: dict[str, str] = {}
        self._openclaw_test_started_at = 0.0

    def startTest(self, test: unittest.TestCase) -> None:
        self._openclaw_test_started_at = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test: unittest.TestCase) -> None:
        elapsed = time.perf_counter() - self._openclaw_test_started_at
        test_id = str(test.id()).strip() or repr(test)
        module_name = str(test.__class__.__module__).strip() or '<unknown>'
        self.test_durations.append((elapsed, test_id, module_name))
        self.test_statuses.setdefault(test_id, 'PASS')
        super().stopTest(test)

    def addError(self, test: unittest.TestCase, err: tuple[type[BaseException], BaseException, object]) -> None:
        self.test_statuses[str(test.id())] = 'ERROR'
        super().addError(test, err)  # type: ignore[arg-type]

    def addFailure(self, test: unittest.TestCase, err: tuple[type[BaseException], BaseException, object]) -> None:
        self.test_statuses[str(test.id())] = 'FAIL'
        super().addFailure(test, err)  # type: ignore[arg-type]

    def addSkip(self, test: unittest.TestCase, reason: str) -> None:
        self.test_statuses[str(test.id())] = 'SKIP'
        super().addSkip(test, reason)

    def addExpectedFailure(
        self,
        test: unittest.TestCase,
        err: tuple[type[BaseException], BaseException, object],
    ) -> None:
        self.test_statuses[str(test.id())] = 'EXPECTED_FAILURE'
        super().addExpectedFailure(test, err)  # type: ignore[arg-type]

    def addUnexpectedSuccess(self, test: unittest.TestCase) -> None:
        self.test_statuses[str(test.id())] = 'UNEXPECTED_SUCCESS'
        super().addUnexpectedSuccess(test)


class TimingTextTestRunner(unittest.TextTestRunner):
    resultclass = TimingTextTestResult

    def __init__(self, *args: object, durations: int = 0, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._openclaw_duration_limit = max(0, int(durations or 0))

    def run(self, test: unittest.TestSuite) -> unittest.result.TestResult:
        result = super().run(test)
        if self._openclaw_duration_limit <= 0 or not isinstance(result, TimingTextTestResult):
            return result
        rows = sorted(result.test_durations, key=lambda item: (-item[0], item[1]))[: self._openclaw_duration_limit]
        if not rows:
            return result
        self.stream.writeln()
        self.stream.writeln(f'Slowest tests (top {len(rows)}):')
        for elapsed, test_id, _module_name in rows:
            self.stream.writeln(f'{elapsed:.3f}s {test_id}')
        module_totals: dict[str, tuple[float, int]] = {}
        for elapsed, _test_id, module_name in result.test_durations:
            total, count = module_totals.get(module_name, (0.0, 0))
            module_totals[module_name] = (total + elapsed, count + 1)
        module_rows = sorted(module_totals.items(), key=lambda item: (-item[1][0], item[0]))[: self._openclaw_duration_limit]
        self.stream.writeln()
        self.stream.writeln(f'Slowest modules (top {len(module_rows)}):')
        for module_name, (elapsed, count) in module_rows:
            self.stream.writeln(f'{elapsed:.3f}s {module_name} ({count} tests)')
        return result


def _serial_runner_for(args: argparse.Namespace) -> unittest.TextTestRunner:
    durations = int(getattr(args, 'durations', 0) or 0)
    if durations < 0:
        raise ValueError('durations must be >= 0')
    return TimingTextTestRunner(verbosity=1 if args.quiet else 2, durations=durations)


def _worker_command(
    args: argparse.Namespace,
    selectors: Sequence[str],
    *,
    worker_mode: str = '',
    worker_report: Path | None = None,
    worker_index: int = 0,
) -> list[str]:
    command = [
        sys.executable,
        '-B',
        '-m',
        'openclaw.testing.repo_unittest',
        '--jobs',
        '1',
        '--start-dir',
        str(args.start_dir),
        '--pattern',
        str(args.pattern),
    ]
    command.append('--quiet')
    if worker_mode:
        command.extend(['--worker-mode', worker_mode, '--worker-index', str(worker_index)])
    if worker_report is not None:
        command.extend(['--worker-report', str(worker_report)])
    command.extend(selectors)
    return command


def _should_clean_bytecode_residue() -> bool:
    return str(os.environ.get(SKIP_BYTECODE_CLEANUP_ENV) or '').strip().lower() not in {'1', 'true', 'yes'}


def _positive_timeout(name: str, default: float) -> float:
    raw = str(os.environ.get(name) or '').strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _test_identity_rows(suite: unittest.TestSuite) -> list[dict[str, str]]:
    rows = [
        {
            'id': str(test.id()).strip() or repr(test),
            'module': str(test.__class__.__module__).strip() or '<unknown>',
        }
        for test in _iter_test_cases(suite)
    ]
    return sorted(rows, key=lambda item: (item['id'], item['module']))


def _write_json_report(path: Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _worker_result_payload(
    *,
    args: argparse.Namespace,
    selectors: Sequence[str],
    result: TimingTextTestResult,
    duration_seconds: float,
) -> dict[str, Any]:
    tests = [
        {
            'id': test_id,
            'module': module_name,
            'worker': int(args.worker_index),
            'durationSeconds': round(elapsed, 6),
            'status': result.test_statuses.get(test_id, 'PASS'),
        }
        for elapsed, test_id, module_name in result.test_durations
    ]
    tests.sort(key=lambda item: str(item['id']))
    return {
        'worker': int(args.worker_index),
        'selectors': list(selectors),
        'modules': sorted({str(item['module']) for item in tests}),
        'testCount': result.testsRun,
        'durationSeconds': round(duration_seconds, 3),
        'exitCode': 0 if result.wasSuccessful() else 1,
        'timedOut': False,
        'status': 'PASS' if result.wasSuccessful() else 'FAIL',
        'tests': tests,
    }


def _run_worker(args: argparse.Namespace, root: Path, suite: unittest.TestSuite) -> int:
    report_path = Path(str(args.worker_report)).resolve() if str(args.worker_report).strip() else None
    selectors = [str(item) for item in list(args.selectors or [])]
    if args.worker_mode == 'list':
        tests = _test_identity_rows(suite)
        payload = {
            'worker': int(args.worker_index),
            'selectors': selectors,
            'modules': sorted({item['module'] for item in tests}),
            'testCount': len(tests),
            'durationSeconds': 0.0,
            'exitCode': 0,
            'timedOut': False,
            'status': 'PASS',
            'tests': tests,
        }
        if report_path is None:
            raise ValueError('--worker-report is required for worker mode')
        _write_json_report(report_path, payload)
        return 0

    started = time.perf_counter()
    runner = _serial_runner_for(args)
    result = runner.run(suite)
    if not isinstance(result, TimingTextTestResult):
        raise TypeError('repo unittest worker requires TimingTextTestResult')
    payload = _worker_result_payload(
        args=args,
        selectors=selectors,
        result=result,
        duration_seconds=time.perf_counter() - started,
    )
    if report_path is None:
        raise ValueError('--worker-report is required for worker mode')
    _write_json_report(report_path, payload)
    return int(payload['exitCode'])


def _read_worker_report(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise ValueError(f'worker report must be a JSON object: {path}')
    return payload


def _aggregate_worker_reports(
    *,
    reports: Sequence[dict[str, Any]],
    duration_seconds: float,
    timed_out: bool,
) -> dict[str, Any]:
    ordered_reports = sorted(reports, key=lambda item: int(item.get('worker') or 0))
    tests = [dict(test) for report in ordered_reports for test in list(report.get('tests') or [])]
    tests.sort(key=lambda item: str(item.get('id') or ''))
    test_id_counts: dict[str, int] = {}
    for item in tests:
        test_id = str(item.get('id') or '')
        test_id_counts[test_id] = test_id_counts.get(test_id, 0) + 1
    duplicate_test_ids = sorted(test_id for test_id, count in test_id_counts.items() if test_id and count > 1)
    exit_code = (
        1
        if timed_out
        or duplicate_test_ids
        or any(int(item.get('exitCode') or 0) != 0 for item in ordered_reports)
        else 0
    )
    return {
        'suite': 'repo_unittest',
        'status': 'PASS' if exit_code == 0 else 'FAIL',
        'summary': {
            'tests': len(tests),
            'workers': len(ordered_reports),
            'durationSeconds': round(duration_seconds, 3),
            'exitCode': exit_code,
            'timedOut': timed_out,
            'duplicateTestIds': duplicate_test_ids,
        },
        'workers': ordered_reports,
        'tests': tests,
    }


def _run_inventory(args: argparse.Namespace, root: Path, inventory: Sequence[TestFile]) -> int:
    started = time.monotonic()
    jobs = min(_parallel_jobs_for(args), max(1, len(inventory)))
    work_units = _inventory_work_unit_count(len(inventory), jobs, list_only=bool(args.list_tests))
    buckets = _build_file_buckets(inventory, work_units)
    worker_timeout = _positive_timeout(WORKER_TIMEOUT_ENV, DEFAULT_WORKER_TIMEOUT_SECONDS)
    suite_timeout = _positive_timeout(SUITE_TIMEOUT_ENV, DEFAULT_SUITE_TIMEOUT_SECONDS)
    suite_deadline = started + suite_timeout
    worker_mode = 'list' if args.list_tests else 'run'
    reports: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix='openclaw_repo_unittest_reports_') as temp_dir:
        report_root = Path(temp_dir)

        def run_bucket(index: int, selectors: Sequence[str]) -> dict[str, Any]:
            report_path = report_root / f'worker-{index:03d}.json'
            effective_timeout = _remaining_worker_timeout(
                worker_timeout,
                suite_deadline,
                now=time.monotonic(),
            )
            if effective_timeout <= 0:
                return {
                    'worker': index,
                    'selectors': list(selectors),
                    'modules': [],
                    'testCount': 0,
                    'durationSeconds': 0.0,
                    'exitCode': 1,
                    'timedOut': True,
                    'status': 'TIMEOUT',
                    'tests': [],
                    'output': 'repo unittest suite exceeded its internal timeout before this work unit started',
                }
            outcome = run_bounded_process(
                _worker_command(
                    args,
                    selectors,
                    worker_mode=worker_mode,
                    worker_report=report_path,
                    worker_index=index,
                ),
                cwd=root,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', **{SKIP_BYTECODE_CLEANUP_ENV: '1'}),
                timeout_seconds=effective_timeout,
                heartbeat_label=f'repo-unittest-worker-{index}',
            )
            payload: dict[str, Any]
            if report_path.is_file():
                payload = _read_worker_report(report_path)
            else:
                payload = {
                    'worker': index,
                    'selectors': list(selectors),
                    'modules': [],
                    'testCount': 0,
                    'durationSeconds': outcome.duration_seconds,
                    'exitCode': outcome.exit_code,
                    'timedOut': outcome.timed_out,
                    'status': 'TIMEOUT' if outcome.timed_out else 'FAIL',
                    'tests': [],
                }
            payload['durationSeconds'] = outcome.duration_seconds
            payload['exitCode'] = outcome.exit_code if outcome.exit_code is not None else 1
            payload['timedOut'] = outcome.timed_out
            if outcome.exit_code != 0 or outcome.timed_out:
                payload['status'] = 'TIMEOUT' if outcome.timed_out else 'FAIL'
                payload['output'] = truncate_process_output(
                    '\n'.join(
                        part
                        for part in [str(outcome.stdout or '').strip(), str(outcome.stderr or '').strip()]
                        if part
                    ).strip()
                )
            return payload

        with ThreadPoolExecutor(max_workers=jobs) as executor:
            futures = [executor.submit(run_bucket, index, selectors) for index, selectors in enumerate(buckets, start=1)]
            for future in futures:
                payload = future.result()
                reports.append(payload)
                if str(payload.get('status')) not in {'PASS'}:
                    failures.append(payload)

    total_elapsed = time.monotonic() - started
    suite_timed_out = total_elapsed > suite_timeout or any(bool(item.get('timedOut')) for item in reports)
    aggregate = _aggregate_worker_reports(
        reports=reports,
        duration_seconds=total_elapsed,
        timed_out=suite_timed_out,
    )
    aggregate['summary']['parallelJobs'] = jobs
    aggregate['summary']['workUnits'] = len(reports)
    report_path = str(args.report_json or '').strip()
    if report_path:
        _write_json_report(Path(report_path).resolve(), aggregate)

    if args.list_tests:
        if args.json_output:
            print(json.dumps({'suite': 'repo_unittest_inventory', 'tests': aggregate['tests']}, ensure_ascii=False))
        else:
            for item in aggregate['tests']:
                print(item['id'])
        return int(aggregate['summary']['exitCode'])

    duplicate_test_ids = list(aggregate['summary'].get('duplicateTestIds') or [])
    aggregate_failed = bool(failures or duplicate_test_ids)
    stream = sys.stderr if aggregate_failed else sys.stdout
    if not args.quiet:
        stream.write(
            f"Parallel repo unittest: {aggregate['summary']['tests']} tests, "
            f"{len(reports)} work units, max {jobs} concurrent workers\n"
        )
        for item in sorted(reports, key=lambda row: int(row.get('worker') or 0)):
            stream.write(
                f"[worker {item['worker']}] {item.get('testCount', 0)} tests "
                f"in {float(item.get('durationSeconds') or 0):.3f}s status={item.get('status')}\n"
            )
    for item in failures:
        stream.write(f"\n=== worker {item.get('worker')} {item.get('status')} ===\n")
        if item.get('output'):
            stream.write(str(item['output']).rstrip() + '\n')
    if duplicate_test_ids:
        stream.write('\n=== duplicate test IDs ===\n')
        for test_id in duplicate_test_ids:
            stream.write(str(test_id) + '\n')
    if aggregate_failed:
        stream.write(
            f"\nFAILED (workers={len(failures)}/{len(reports)}, tests={aggregate['summary']['tests']}, "
            f"elapsed={total_elapsed:.3f}s)\n"
        )
    else:
        stream.write(f"Ran {aggregate['summary']['tests']} tests in {total_elapsed:.3f}s\n\nOK\n")
    return int(aggregate['summary']['exitCode'])


def _run_parallel_suite(args: argparse.Namespace, root: Path, suite: unittest.TestSuite) -> int:
    module_counts = _module_case_counts(suite)
    selector_counts = _parallel_selector_counts(args, suite, root)
    buckets = _build_parallel_buckets(selector_counts, _parallel_jobs_for(args))
    if len(buckets) <= 1:
        runner = _serial_runner_for(args)
        result = runner.run(suite)
        return 0 if result.wasSuccessful() else 1

    total_tests = suite.countTestCases()
    total_start = time.perf_counter()

    def run_bucket(index: int, selectors: Sequence[str]) -> dict[str, object]:
        started = time.perf_counter()
        completed = run_bounded_process(
            _worker_command(args, selectors),
            cwd=root,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', **{SKIP_BYTECODE_CLEANUP_ENV: '1'}),
            timeout_seconds=_positive_timeout(WORKER_TIMEOUT_ENV, DEFAULT_WORKER_TIMEOUT_SECONDS),
            heartbeat_label=f'repo-unittest-selector-worker-{index + 1}',
        )
        return {
            'index': index,
            'selectors': tuple(selectors),
            'case_count': sum(selector_counts[selector] for selector in selectors),
            'returncode': completed.exit_code if completed.exit_code is not None else 1,
            'stdout': completed.stdout,
            'stderr': completed.stderr,
            'timed_out': completed.timed_out,
            'elapsed': time.perf_counter() - started,
        }

    with ThreadPoolExecutor(max_workers=len(buckets)) as executor:
        futures = [executor.submit(run_bucket, index, selectors) for index, selectors in enumerate(buckets)]
        results = [future.result() for future in futures]

    total_elapsed = time.perf_counter() - total_start
    ordered_results = sorted(results, key=lambda item: int(item['index']))
    failed_results = [item for item in ordered_results if int(item['returncode']) != 0]

    stream = sys.stderr if failed_results else sys.stdout
    if not args.quiet:
        stream.write(
            f'Parallel repo unittest: {total_tests} tests, {len(module_counts)} modules, '
            f'{len(buckets)} workers\n'
        )
        for item in ordered_results:
            stream.write(
                f"[worker {int(item['index']) + 1}] {int(item['case_count'])} tests "
                f"in {float(item['elapsed']):.3f}s\n"
            )
    if failed_results:
        for item in failed_results:
            stream.write(f"\n=== worker {int(item['index']) + 1} failed ===\n")
            stdout = str(item['stdout'])
            stderr = str(item['stderr'])
            if bool(item.get('timed_out')):
                stream.write('[repo_unittest][FAIL] worker exceeded its internal timeout\n')
            if stdout:
                stream.write(stdout.rstrip() + '\n')
            if stderr:
                stream.write(stderr.rstrip() + '\n')
        stream.write(
            f'\nFAILED (workers={len(failed_results)}/{len(buckets)}, tests={total_tests}, '
            f'elapsed={total_elapsed:.3f}s)\n'
        )
        return 1

    stream.write(f'Ran {total_tests} tests in {total_elapsed:.3f}s\n\n')
    stream.write('OK\n')
    return 0


def _run_serial_suite(args: argparse.Namespace, suite: unittest.TestSuite) -> int:
    if args.list_tests:
        tests = _test_identity_rows(suite)
        payload = {'suite': 'repo_unittest_inventory', 'tests': tests}
        if str(args.report_json or '').strip():
            _write_json_report(Path(str(args.report_json)).resolve(), payload)
        if args.json_output:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            for item in tests:
                print(item['id'])
        return 0
    started = time.perf_counter()
    runner = _serial_runner_for(args)
    result = runner.run(suite)
    if not isinstance(result, TimingTextTestResult):
        raise TypeError('repo unittest requires TimingTextTestResult')
    worker_payload = _worker_result_payload(
        args=argparse.Namespace(worker_index=0),
        selectors=list(args.selectors or []),
        result=result,
        duration_seconds=time.perf_counter() - started,
    )
    aggregate = _aggregate_worker_reports(
        reports=[worker_payload],
        duration_seconds=float(worker_payload['durationSeconds']),
        timed_out=False,
    )
    if str(args.report_json or '').strip():
        _write_json_report(Path(str(args.report_json)).resolve(), aggregate)
    return int(worker_payload['exitCode'])


def main(argv: Sequence[str] | None = None) -> int:
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    sys.dont_write_bytecode = True
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.json_output and not args.list_tests:
        raise SystemExit('--json is only valid with --list-tests')
    if args.worker_mode and not str(args.worker_report).strip():
        raise SystemExit('--worker-report is required with --worker-mode')
    if args.list_tests and int(args.durations or 0) > 0:
        raise SystemExit('--durations cannot be combined with --list-tests')
    root = repo_root()
    ensure_repo_pythonpath(root)
    clean_bytecode_residue = _should_clean_bytecode_residue()
    if clean_bytecode_residue:
        _clean_repo_bytecode_residue(root)
    try:
        inventory = _test_file_inventory(args, root)
        if inventory and int(args.durations or 0) == 0 and not args.worker_mode:
            return _run_inventory(args, root, inventory)
        suite = _suite_from_args(args, root)
        if args.worker_mode:
            return _run_worker(args, root, suite)
        if args.list_tests or str(args.report_json or '').strip():
            return _run_serial_suite(args, suite)
        if _parallelizable_suite(args, suite):
            return _run_parallel_suite(args, root, suite)
        return _run_serial_suite(args, suite)
    finally:
        if clean_bytecode_residue:
            _clean_repo_bytecode_residue(root)


if __name__ == '__main__':
    raise SystemExit(main())
