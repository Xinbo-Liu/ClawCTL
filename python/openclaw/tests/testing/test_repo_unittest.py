"""验证仓库 unittest 入口的 selector、并行分桶和字节码清理辅助逻辑。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock
import zipfile

from openclaw.testing import repo_unittest, syntax_check


class AlphaCase(unittest.TestCase):
    def test_one(self) -> None:
        self.assertTrue(True)

    def test_two(self) -> None:
        self.assertTrue(True)


class BetaCase(unittest.TestCase):
    def test_one(self) -> None:
        self.assertTrue(True)


class GammaCase(unittest.TestCase):
    def test_one(self) -> None:
        self.assertTrue(True)


@contextmanager
def patched_case_modules():
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(AlphaCase, '__module__', 'alpha.tests.test_alpha'))
        stack.enter_context(mock.patch.object(BetaCase, '__module__', 'beta.tests.test_beta'))
        stack.enter_context(mock.patch.object(GammaCase, '__module__', 'gamma.tests.test_gamma'))
        yield


class RepoUnittestSupportTest(unittest.TestCase):
    def test_parser_help_mentions_readiness_precheck(self) -> None:
        help_text = repo_unittest.build_parser().format_help()

        self.assertIn('bash ./scripts/testing/check_repo_test_readiness.sh', help_text)
        self.assertIn('--durations', help_text)

    def test_clean_repo_bytecode_residue_removes_repo_python_and_extension_pycache_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pycache = root / 'python' / '__pycache__'
            package_pycache = root / 'python' / 'openclaw' / '__pycache__'
            extension_pycache = root / 'agent' / 'extensions' / 'agent_probe' / 'python' / 'agent_probe' / '__pycache__'
            extension_tests_pycache = root / 'agent' / 'extensions' / 'agent_probe' / 'tests' / '__pycache__'
            outside_pycache = root / 'agent' / '__pycache__'
            pycache.mkdir(parents=True)
            package_pycache.mkdir(parents=True)
            extension_pycache.mkdir(parents=True)
            extension_tests_pycache.mkdir(parents=True)
            outside_pycache.mkdir(parents=True)
            (pycache / 'sitecustomize.pyc').write_bytes(b'x')
            (package_pycache / 'module.pyc').write_bytes(b'x')
            (extension_pycache / 'module.pyc').write_bytes(b'x')
            (extension_tests_pycache / 'test_module.pyc').write_bytes(b'x')
            (outside_pycache / 'module.pyc').write_bytes(b'x')

            repo_unittest._clean_repo_bytecode_residue(root)

            self.assertFalse(pycache.exists())
            self.assertFalse(package_pycache.exists())
            self.assertFalse(extension_pycache.exists())
            self.assertFalse(extension_tests_pycache.exists())
            self.assertTrue(outside_pycache.exists())

    def test_syntax_check_uses_compile_without_bytecode_residue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package_dir = root / 'python' / 'sample'
            package_dir.mkdir(parents=True)
            (package_dir / '__init__.py').write_text('', encoding='utf-8')
            (package_dir / 'module.py').write_text('VALUE = 1\n', encoding='utf-8')

            failures = syntax_check.check_python_syntax(root, [Path('python')])

            self.assertEqual(failures, [])
            self.assertFalse(any(path.name == '__pycache__' for path in root.rglob('__pycache__')))
            self.assertFalse(any(path.suffix == '.pyc' for path in root.rglob('*.pyc')))

    def test_syntax_check_reports_syntax_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package_dir = root / 'python' / 'sample'
            package_dir.mkdir(parents=True)
            (package_dir / 'bad.py').write_text('def broken(:\n    pass\n', encoding='utf-8')

            failures = syntax_check.check_python_syntax(root, [Path('python')])

            self.assertEqual(failures[0]['path'], 'python/sample/bad.py')
            self.assertIn('invalid syntax', failures[0]['error'])

    def test_coerce_jobs_accepts_auto_and_explicit_count(self) -> None:
        self.assertIsNone(repo_unittest._coerce_jobs('auto'))
        self.assertEqual(repo_unittest._coerce_jobs('3'), 3)

    def test_coerce_jobs_rejects_zero(self) -> None:
        with self.assertRaises(ValueError):
            repo_unittest._coerce_jobs('0')

    def test_default_jobs_caps_parallelism(self) -> None:
        with mock.patch('openclaw.testing.repo_unittest.os.cpu_count', return_value=12):
            self.assertEqual(repo_unittest._default_jobs(), 4)
        with mock.patch('openclaw.testing.repo_unittest.os.cpu_count', return_value=2):
            self.assertEqual(repo_unittest._default_jobs(), 2)

    def test_module_case_counts_groups_discovered_cases_by_module(self) -> None:
        loader = unittest.defaultTestLoader
        with patched_case_modules():
            suite = unittest.TestSuite(
                [
                    loader.loadTestsFromTestCase(AlphaCase),
                    loader.loadTestsFromTestCase(BetaCase),
                    loader.loadTestsFromTestCase(GammaCase),
                ]
            )
            self.assertEqual(
                repo_unittest._module_case_counts(suite),
                {
                    'alpha.tests.test_alpha': 2,
                    'beta.tests.test_beta': 1,
                    'gamma.tests.test_gamma': 1,
                },
            )

    def test_build_parallel_buckets_balances_case_counts(self) -> None:
        buckets = repo_unittest._build_parallel_buckets(
            {
                'alpha.tests.test_alpha': 5,
                'beta.tests.test_beta': 4,
                'delta.tests.test_delta': 1,
                'gamma.tests.test_gamma': 1,
            },
            jobs=2,
        )
        self.assertEqual(len(buckets), 2)
        self.assertEqual(
            {frozenset(bucket) for bucket in buckets},
            {
                frozenset({'alpha.tests.test_alpha', 'gamma.tests.test_gamma'}),
                frozenset({'beta.tests.test_beta', 'delta.tests.test_delta'}),
            },
        )

    def test_selector_for_module_prefers_repo_relative_file_selector(self) -> None:
        module = types.ModuleType('openclaw.tests.testing.fake_worker_module')
        module_path = repo_unittest.repo_root() / 'python' / 'openclaw' / 'tests' / 'testing' / 'fake_worker_module.py'
        module.__file__ = str(module_path)
        with mock.patch.dict('sys.modules', {module.__name__: module}, clear=False):
            selector = repo_unittest._selector_for_module(module.__name__, repo_unittest.repo_root())
        self.assertEqual(selector, str(module_path.relative_to(repo_unittest.repo_root())))

    def test_selector_for_test_prefers_file_selector_with_case_suffix(self) -> None:
        module = types.ModuleType('alpha.tests.test_alpha')
        module_path = repo_unittest.repo_root() / 'python' / 'openclaw' / 'tests' / 'alpha' / 'test_alpha.py'
        module.__file__ = str(module_path)
        with patched_case_modules(), mock.patch.dict('sys.modules', {module.__name__: module}, clear=False):
            selector = repo_unittest._selector_for_test(AlphaCase('test_one'), repo_unittest.repo_root())
        self.assertEqual(
            selector,
            f'{module_path.relative_to(repo_unittest.repo_root())}::AlphaCase::test_one',
        )

    def test_selector_case_counts_tracks_individual_cases(self) -> None:
        loader = unittest.defaultTestLoader
        module = types.ModuleType('alpha.tests.test_alpha')
        module_path = repo_unittest.repo_root() / 'python' / 'openclaw' / 'tests' / 'alpha' / 'test_alpha.py'
        module.__file__ = str(module_path)
        with patched_case_modules():
            suite = unittest.TestSuite([loader.loadTestsFromTestCase(AlphaCase)])
            with mock.patch.dict('sys.modules', {module.__name__: module}, clear=False):
                counts = repo_unittest._selector_case_counts(suite, repo_unittest.repo_root())
        self.assertEqual(
            counts,
            {
                f'{module_path.relative_to(repo_unittest.repo_root())}::AlphaCase::test_one': 1,
                f'{module_path.relative_to(repo_unittest.repo_root())}::AlphaCase::test_two': 1,
            },
        )

    def test_parallel_selector_counts_keeps_file_selector_parallelism_at_module_level(self) -> None:
        """普通文件 selector 应保持模块级分桶，不依赖真实业务扩展路径。"""
        loader = unittest.defaultTestLoader
        root = repo_unittest.repo_root()
        modules = {}
        for module_name in ('alpha.tests.test_alpha', 'beta.tests.test_beta', 'gamma.tests.test_gamma'):
            module = types.ModuleType(module_name)
            module.__file__ = str(root / 'python' / 'openclaw' / 'tests' / module_name.replace('.', '/') / 'test_file.py')
            modules[module_name] = module
        with patched_case_modules(), mock.patch.dict('sys.modules', modules, clear=False):
            suite = unittest.TestSuite(
                [
                    loader.loadTestsFromTestCase(AlphaCase),
                    loader.loadTestsFromTestCase(BetaCase),
                    loader.loadTestsFromTestCase(GammaCase),
                ]
            )
            counts = repo_unittest._parallel_selector_counts(
                argparse.Namespace(selectors=['synthetic/tests/test_all.py']),
                suite,
                root,
            )

        self.assertEqual(len(counts), 3)
        self.assertTrue(all('::' not in selector for selector in counts))
        self.assertEqual(sorted(counts.values()), [1, 1, 2])

    def test_parallel_selector_counts_keeps_explicit_case_selectors_at_case_level(self) -> None:
        module = types.ModuleType('alpha.tests.test_alpha')
        module_path = repo_unittest.repo_root() / 'python' / 'openclaw' / 'tests' / 'alpha' / 'test_alpha.py'
        module.__file__ = str(module_path)
        with patched_case_modules(), mock.patch.dict('sys.modules', {module.__name__: module}, clear=False):
            suite = unittest.TestSuite([AlphaCase('test_one')])
            counts = repo_unittest._parallel_selector_counts(
                argparse.Namespace(selectors=['alpha.tests.test_alpha::AlphaCase::test_one']),
                suite,
                repo_unittest.repo_root(),
            )

        self.assertEqual(
            counts,
            {
                f'{module_path.relative_to(repo_unittest.repo_root())}::AlphaCase::test_one': 1,
            },
        )

    def test_parallel_selector_counts_keeps_dotted_selectors_at_case_level(self) -> None:
        loader = unittest.defaultTestLoader
        module = types.ModuleType('alpha.tests.test_alpha')
        module_path = repo_unittest.repo_root() / 'python' / 'openclaw' / 'tests' / 'alpha' / 'test_alpha.py'
        module.__file__ = str(module_path)
        with patched_case_modules(), mock.patch.dict('sys.modules', {module.__name__: module}, clear=False):
            suite = unittest.TestSuite([loader.loadTestsFromTestCase(AlphaCase)])
            counts = repo_unittest._parallel_selector_counts(
                argparse.Namespace(selectors=['alpha.tests.test_alpha.AlphaCase']),
                suite,
                repo_unittest.repo_root(),
            )

        self.assertEqual(
            counts,
            {
                f'{module_path.relative_to(repo_unittest.repo_root())}::AlphaCase::test_one': 1,
                f'{module_path.relative_to(repo_unittest.repo_root())}::AlphaCase::test_two': 1,
            },
        )

    def test_parallelizable_suite_rejects_small_selector_batches(self) -> None:
        loader = unittest.defaultTestLoader
        suite = unittest.TestSuite(
            [
                loader.loadTestsFromTestCase(AlphaCase),
                loader.loadTestsFromTestCase(BetaCase),
            ]
        )
        args = argparse.Namespace(
            quiet=False,
            jobs='auto',
            import_mode='',
            start_dir='python/openclaw/tests',
            pattern='test_*.py',
            selectors=[],
        )
        with mock.patch('openclaw.testing.repo_unittest._default_jobs', return_value=2):
            self.assertTrue(repo_unittest._parallelizable_suite(args, suite))
        args.selectors = ['openclaw.tests.testing.test_repo_unittest']
        self.assertFalse(repo_unittest._parallelizable_suite(args, suite))

    def test_parallelizable_suite_rejects_single_case_selector(self) -> None:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(BetaCase)
        args = argparse.Namespace(
            quiet=False,
            jobs='auto',
            import_mode='',
            start_dir='python/openclaw/tests',
            pattern='test_*.py',
            selectors=['openclaw.tests.testing.test_repo_unittest::BetaCase::test_one'],
        )
        with mock.patch('openclaw.testing.repo_unittest._default_jobs', return_value=2):
            self.assertFalse(repo_unittest._parallelizable_suite(args, suite))

    def test_parallelizable_suite_rejects_jobs_equal_one(self) -> None:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(AlphaCase)
        args = argparse.Namespace(
            quiet=False,
            jobs='1',
            import_mode='',
            start_dir='python/openclaw/tests',
            pattern='test_*.py',
            selectors=['openclaw.tests.testing.test_repo_unittest'],
        )
        self.assertFalse(repo_unittest._parallelizable_suite(args, suite))

    def test_parallelizable_suite_rejects_duration_profile(self) -> None:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(AlphaCase)
        args = argparse.Namespace(
            quiet=False,
            jobs='auto',
            import_mode='',
            durations=5,
            start_dir='python/openclaw/tests',
            pattern='test_*.py',
            selectors=[],
        )
        with mock.patch('openclaw.testing.repo_unittest._default_jobs', return_value=2):
            self.assertFalse(repo_unittest._parallelizable_suite(args, suite))

    def test_path_selector_loads_extension_internal_test_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            test_dir = root / 'agent' / 'extensions' / 'agent_probe' / 'tests' / 'unit'
            test_dir.mkdir(parents=True)
            (test_dir.parent / '__init__.py').write_text('', encoding='utf-8')
            (test_dir / '__init__.py').write_text('', encoding='utf-8')
            test_path = test_dir / 'test_alpha.py'
            test_path.write_text(
                'import unittest\n\nclass AlphaExtensionTest(unittest.TestCase):\n    def test_one(self):\n        self.assertTrue(True)\n',
                encoding='utf-8',
            )

            suite = repo_unittest._suite_from_selectors([str(test_path.relative_to(root))], root)

        self.assertEqual(suite.countTestCases(), 1)

    def test_path_selector_adds_extension_offline_wheelhouse(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            extension_root = root / 'agent' / 'extensions' / 'agent_probe'
            test_dir = extension_root / 'tests' / 'unit'
            wheelhouse = extension_root / 'offline_wheelhouse'
            test_dir.mkdir(parents=True)
            wheelhouse.mkdir(parents=True)
            (test_dir.parent / '__init__.py').write_text('', encoding='utf-8')
            (test_dir / '__init__.py').write_text('', encoding='utf-8')
            with zipfile.ZipFile(wheelhouse / 'fixture_dep-1.0.0-py3-none-any.whl', 'w') as archive:
                archive.writestr('fixture_dep.py', 'VALUE = 42\n')
            test_path = test_dir / 'test_dep.py'
            test_path.write_text(
                'import fixture_dep\nimport unittest\n\n'
                'class ExtensionDependencyTest(unittest.TestCase):\n'
                '    def test_dep(self):\n'
                '        self.assertEqual(fixture_dep.VALUE, 42)\n',
                encoding='utf-8',
            )

            suite = repo_unittest._suite_from_selectors([str(test_path.relative_to(root))], root)

        self.assertEqual(suite.countTestCases(), 1)

    def test_path_selector_rebinds_extension_support_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            extension_root = root / 'agent' / 'extensions' / 'agent_probe'
            test_dir = extension_root / 'tests' / 'unit'
            support_dir = extension_root / 'tests' / 'support'
            other_support = root / 'agent' / 'extensions' / 'agent_other' / 'tests' / 'support'
            test_dir.mkdir(parents=True)
            support_dir.mkdir(parents=True)
            other_support.mkdir(parents=True)
            (test_dir.parent / '__init__.py').write_text('', encoding='utf-8')
            (test_dir / '__init__.py').write_text('', encoding='utf-8')
            (support_dir / 'helper.py').write_text('VALUE = "current"\n', encoding='utf-8')
            package = types.ModuleType('support')
            package.__path__ = [str(other_support.resolve())]  # type: ignore[attr-defined]
            test_path = test_dir / 'test_support.py'
            test_path.write_text(
                'from support.helper import VALUE\nimport unittest\n\n'
                'class ExtensionSupportTest(unittest.TestCase):\n'
                '    def test_support(self):\n'
                '        self.assertEqual(VALUE, "current")\n',
                encoding='utf-8',
            )

            with mock.patch.dict('sys.modules', {'support': package}, clear=False):
                suite = repo_unittest._suite_from_selectors([str(test_path.relative_to(root))], root)

        self.assertEqual(suite.countTestCases(), 1)

    def test_path_selector_rejects_relative_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / 'repo'
            root.mkdir()
            outside_test = base / 'outside' / 'test_escape.py'
            outside_test.parent.mkdir()
            outside_test.write_text('import unittest\n', encoding='utf-8')

            with self.assertRaisesRegex(ValueError, 'inside repository'):
                repo_unittest._suite_from_selectors(['../outside/test_escape.py'], root)

    def test_default_suite_discovers_repo_and_managed_extension_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_tests = root / 'python' / 'openclaw' / 'tests'
            extension_tests = root / 'agent' / 'extensions' / 'agent_probe' / 'tests'
            (repo_tests).mkdir(parents=True)
            (extension_tests / 'unit').mkdir(parents=True)
            for package_dir in (repo_tests, extension_tests, extension_tests / 'unit'):
                (package_dir / '__init__.py').write_text('', encoding='utf-8')
            (repo_tests / 'test_repo.py').write_text(
                'import unittest\n\nclass RepoTest(unittest.TestCase):\n    def test_repo(self):\n        self.assertTrue(True)\n',
                encoding='utf-8',
            )
            (extension_tests / 'unit' / 'test_ext.py').write_text(
                'import unittest\n\nclass ExtensionTest(unittest.TestCase):\n    def test_ext(self):\n        self.assertTrue(True)\n',
                encoding='utf-8',
            )
            args = argparse.Namespace(
                quiet=False,
                jobs='1',
                import_mode='',
                durations=0,
                start_dir=repo_unittest.DEFAULT_START_DIR,
                start_dir_explicit=False,
                pattern='test_*.py',
                selectors=[],
            )
            with mock.patch('openclaw.testing.repo_unittest.managed_extension_test_roots', return_value=(extension_tests,)):
                suite = repo_unittest._suite_from_args(args, root)

        self.assertEqual(suite.countTestCases(), 2)

    def test_default_suite_loads_managed_extension_test_all_files_without_module_name_collision(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_tests = root / 'python' / 'openclaw' / 'tests'
            alpha_tests = root / 'agent' / 'extensions' / 'agent_alpha' / 'tests'
            beta_tests = root / 'agent' / 'extensions' / 'agent_beta' / 'tests'
            for package_dir in (repo_tests, alpha_tests, beta_tests):
                package_dir.mkdir(parents=True)
                (package_dir / '__init__.py').write_text('', encoding='utf-8')
            (repo_tests / 'test_repo.py').write_text(
                'import unittest\n\nclass RepoTest(unittest.TestCase):\n    def test_repo(self):\n        self.assertTrue(True)\n',
                encoding='utf-8',
            )
            for test_root, class_name in ((alpha_tests, 'AlphaExtensionTest'), (beta_tests, 'BetaExtensionTest')):
                (test_root / 'test_all.py').write_text(
                    'import unittest\n\n'
                    f'class {class_name}(unittest.TestCase):\n'
                    '    def test_one(self):\n'
                    '        self.assertTrue(True)\n',
                    encoding='utf-8',
                )
            args = argparse.Namespace(
                quiet=False,
                jobs='1',
                import_mode='',
                durations=0,
                start_dir=repo_unittest.DEFAULT_START_DIR,
                start_dir_explicit=False,
                pattern='test_*.py',
                selectors=[],
            )
            with mock.patch(
                'openclaw.testing.repo_unittest.managed_extension_test_roots',
                return_value=(alpha_tests, beta_tests),
            ):
                suite = repo_unittest._suite_from_args(args, root)

        self.assertEqual(suite.countTestCases(), 3)

    def test_start_dir_discovery_clears_stale_same_named_modules(self) -> None:
        with tempfile.TemporaryDirectory() as first_tmpdir, tempfile.TemporaryDirectory() as second_tmpdir:
            first_root = Path(first_tmpdir)
            second_root = Path(second_tmpdir)
            first_tests = first_root / 'python' / 'openclaw' / 'tests'
            second_tests = second_root / 'python' / 'openclaw' / 'tests'
            for test_root, class_name in ((first_tests, 'FirstRepoTest'), (second_tests, 'SecondRepoTest')):
                test_root.mkdir(parents=True)
                (test_root / '__init__.py').write_text('', encoding='utf-8')
                (test_root / 'test_repo.py').write_text(
                    'import unittest\n\n'
                    f'class {class_name}(unittest.TestCase):\n'
                    '    def test_repo(self):\n'
                    '        self.assertTrue(True)\n',
                    encoding='utf-8',
                )

            first_suite = repo_unittest._suite_from_start_dirs(first_root, [first_tests], 'test_*.py')
            second_suite = repo_unittest._suite_from_start_dirs(second_root, [second_tests], 'test_*.py')

        self.assertEqual(first_suite.countTestCases(), 1)
        self.assertEqual(second_suite.countTestCases(), 1)


if __name__ == '__main__':
    unittest.main()
