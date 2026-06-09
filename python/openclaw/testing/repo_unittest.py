#!/usr/bin/env python3
"""Run the repository unittest suite behind the shell entrypoint.

The supported shell entrypoint is ``scripts/testing/run_repo_unittest.sh``.
This module remains the Python truth source behind that wrapper.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable, Sequence

from openclaw.testing.bootstrap_support import ensure_repo_pythonpath, prepend_sys_path_entries


BOOTSTRAP_ROOT = ensure_repo_pythonpath(Path(__file__))

from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.managed_extensions import managed_extension_test_roots


SKIP_BYTECODE_CLEANUP_ENV = 'OPENCLAW_REPO_UNITTEST_SKIP_BYTECODE_CLEANUP'
DEFAULT_START_DIR = 'python/openclaw/tests'


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
    parser.add_argument('-s', '--start-dir', default=DEFAULT_START_DIR, action=StartDirAction)
    parser.add_argument('-p', '--pattern', default='test_*.py')
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


def _suite_from_args(args: argparse.Namespace, root: Path) -> unittest.TestSuite:
    selectors = [str(item).strip() for item in list(args.selectors or []) if str(item).strip()]
    if selectors:
        return _suite_from_selectors(selectors, root)
    start_dir = (root / str(args.start_dir)).resolve()
    if not bool(getattr(args, 'start_dir_explicit', False)) and str(args.start_dir) == DEFAULT_START_DIR:
        return _suite_from_start_dirs(root, [start_dir, *managed_extension_test_roots(root)], str(args.pattern))
    return _suite_from_start_dirs(root, [start_dir], str(args.pattern))


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
        self._openclaw_test_started_at = 0.0

    def startTest(self, test: unittest.TestCase) -> None:
        self._openclaw_test_started_at = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test: unittest.TestCase) -> None:
        elapsed = time.perf_counter() - self._openclaw_test_started_at
        test_id = str(test.id()).strip() or repr(test)
        module_name = str(test.__class__.__module__).strip() or '<unknown>'
        self.test_durations.append((elapsed, test_id, module_name))
        super().stopTest(test)


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
    if durations > 0:
        return TimingTextTestRunner(verbosity=1 if args.quiet else 2, durations=durations)
    return unittest.TextTestRunner(verbosity=1 if args.quiet else 2)


def _worker_command(args: argparse.Namespace, selectors: Sequence[str]) -> list[str]:
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
    command.extend(selectors)
    return command


def _should_clean_bytecode_residue() -> bool:
    return str(os.environ.get(SKIP_BYTECODE_CLEANUP_ENV) or '').strip().lower() not in {'1', 'true', 'yes'}


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
        completed = subprocess.run(
            _worker_command(args, selectors),
            cwd=root,
            text=True,
            capture_output=True,
            encoding='utf-8',
            errors='replace',
            check=False,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', **{SKIP_BYTECODE_CLEANUP_ENV: '1'}),
        )
        return {
            'index': index,
            'selectors': tuple(selectors),
            'case_count': sum(selector_counts[selector] for selector in selectors),
            'returncode': completed.returncode,
            'stdout': completed.stdout,
            'stderr': completed.stderr,
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


def main(argv: Sequence[str] | None = None) -> int:
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    sys.dont_write_bytecode = True
    args = build_parser().parse_args(list(argv or sys.argv[1:]))
    root = repo_root()
    ensure_repo_pythonpath(root)
    clean_bytecode_residue = _should_clean_bytecode_residue()
    if clean_bytecode_residue:
        _clean_repo_bytecode_residue(root)
    try:
        suite = _suite_from_args(args, root)
        if _parallelizable_suite(args, suite):
            return _run_parallel_suite(args, root, suite)
        runner = _serial_runner_for(args)
        result = runner.run(suite)
        return 0 if result.wasSuccessful() else 1
    finally:
        if clean_bytecode_residue:
            _clean_repo_bytecode_residue(root)


if __name__ == '__main__':
    raise SystemExit(main())
