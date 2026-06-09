from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from openclaw.testing import repo_host


class RepoHostTest(unittest.TestCase):
    def test_bootstrap_sets_repo_pythonpath_and_utf8_env(self) -> None:
        normalized_sys_path = {repo_host._normalize_path(item) for item in sys.path if str(item).strip()}
        normalized_env_entries = [
            repo_host._normalize_path(item)
            for item in os.environ.get('PYTHONPATH', '').split(os.pathsep)
            if str(item).strip()
        ]
        normalized_env_path = set(normalized_env_entries)

        self.assertIn(repo_host._normalize_path(repo_host.PYTHON_DIR), normalized_sys_path)
        self.assertIn(repo_host._normalize_path(repo_host.PYTHON_DIR), normalized_env_path)
        self.assertNotIn(repo_host._normalize_path(repo_host.ROOT_DIR), normalized_env_path)
        self.assertEqual(len(normalized_env_entries), len(normalized_env_path))
        self.assertEqual(os.environ.get('PYTHONDONTWRITEBYTECODE'), '1')
        self.assertEqual(os.environ.get('PYTHONIOENCODING'), 'UTF-8')
        self.assertEqual(os.environ.get('PYTHONUTF8'), '1')
        self.assertTrue(sys.dont_write_bytecode)

    def test_unittest_subcommand_forwards_args_to_repo_unittest(self) -> None:
        with mock.patch('openclaw.testing.repo_host.invoke_repo_unittest', return_value=0) as mocked_run:
            exit_code = repo_host.main(
                [
                    'unittest',
                    '--jobs',
                    '3',
                    '-q',
                    '--pattern',
                    'test_*.py',
                    'python/openclaw/tests/testing/test_repo_unittest.py',
                ]
            )

        self.assertEqual(exit_code, 0)
        mocked_run.assert_called_once_with(
            [
                '--quiet',
                '--jobs',
                '3',
                '--start-dir',
                'python/openclaw/tests',
                '--pattern',
                'test_*.py',
                'python/openclaw/tests/testing/test_repo_unittest.py',
            ]
        )

    def test_suite_subcommand_loads_manifest_and_expands_selectors(self) -> None:
        with (
            mock.patch('openclaw.testing.repo_host.resolve_suite_selectors', return_value=('alpha', 'beta')) as mocked_load,
            mock.patch('openclaw.testing.repo_host.invoke_repo_unittest', return_value=0) as mocked_run,
        ):
            exit_code = repo_host.main(['suite', 'repo-check', '-q'])

        self.assertEqual(exit_code, 0)
        mocked_load.assert_called_once()
        self.assertEqual(mocked_load.call_args.args[0], 'repo-check')
        mocked_run.assert_called_once_with(
            [
                '--quiet',
                '--jobs',
                'auto',
                '--start-dir',
                'python/openclaw/tests',
                '--pattern',
                'test_*.py',
                'alpha',
                'beta',
            ]
        )

    def test_help_describes_host_python_boundary(self) -> None:
        help_text = repo_host.build_parser().format_help()

        self.assertIn('Windows 无容器宿主机上的仓库级 Python 回归入口', help_text)
        self.assertIn('正式 shell 入口以容器执行介质为准', help_text)
        self.assertIn('python -m openclaw.testing.repo_host suite repo-check -q', help_text)

    def test_unknown_suite_reports_available_names(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            repo_host.main(['suite', 'missing'])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn('未知 suite：missing', stderr.getvalue())
        self.assertIn('repo-check', stderr.getvalue())
        self.assertIn('repo-shell-check', stderr.getvalue())

    def test_invalid_manifest_reports_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'repo_host_lane.json'
            manifest_path.write_text('{"suites": {"repo-check": [}', encoding='utf-8')
            stderr = io.StringIO()
            with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                repo_host.resolve_suite_selectors('repo-check', repo_host.build_parser(), path=manifest_path)

        self.assertEqual(raised.exception.code, 2)
        self.assertIn('repo_host_lane manifest 无效', stderr.getvalue())
        self.assertIn('repo_host_lane.json 解析失败', stderr.getvalue())

    def test_invalid_suite_spec_reports_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / 'repo_host_lane.json'
            manifest_path.write_text('{"suites": {"repo-check": []}}', encoding='utf-8')
            stderr = io.StringIO()
            with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                repo_host.resolve_suite_selectors('repo-check', repo_host.build_parser(), path=manifest_path)

        self.assertEqual(raised.exception.code, 2)
        self.assertIn('repo_host_lane manifest 无效', stderr.getvalue())
        self.assertIn('repo_host_lane.json -> suites.repo-check 顶层必须为对象', stderr.getvalue())

    def test_parallel_workers_inherit_bootstrapped_pythonpath(self) -> None:
        command = [
            sys.executable,
            '-m',
            'openclaw.testing.repo_host',
            'unittest',
            '--jobs',
            '2',
            '-q',
            'python/openclaw/tests/testing/test_repo_unittest.py',
        ]
        result = subprocess.run(
            command,
            cwd=repo_host.ROOT_DIR,
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            env=dict(os.environ),
            check=False,
        )

        output = '\n'.join(part for part in [result.stdout, result.stderr] if part)
        self.assertEqual(result.returncode, 0, msg=output)
        self.assertIn('OK', output)

    def test_load_suite_selectors_reads_repo_check_manifest(self) -> None:
        selectors = repo_host.load_suite_selectors('repo-check')

        self.assertIn('python/openclaw/tests/testing/test_repo_host.py', selectors)
        self.assertIn('python/openclaw/tests/testing/test_repo_unittest.py', selectors)
        self.assertNotIn('python/openclaw/tests/testing/test_repo_test_readiness.py', selectors)

    def test_load_suite_selectors_reads_repo_shell_check_manifest(self) -> None:
        selectors = repo_host.load_suite_selectors('repo-shell-check')

        self.assertEqual(
            selectors,
            (
                'python/openclaw/tests/testing/test_repo_test_readiness.py',
                'python/openclaw/tests/testing/test_repo_python_env.py',
            ),
        )


if __name__ == '__main__':
    unittest.main()
