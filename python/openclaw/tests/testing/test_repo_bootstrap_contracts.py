from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from openclaw.lib.repo.layout import resolve_repo_root


ROOT_DIR = resolve_repo_root(Path(__file__))
CANONICAL_PACKAGE_INIT = (ROOT_DIR / 'python' / 'openclaw' / '__init__.py').resolve()
REENTRY_GUARD = 'OPENCLAW_BOOTSTRAP_CONTRACT_REENTRY'


class RepoBootstrapContractSmokeTest(unittest.TestCase):
    def _clean_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        env.pop('PYTHONHOME', None)
        env.pop('OPENCLAW_REPO_ROOT', None)
        env.pop('OPENCLAW_TOOLS_ROOT', None)
        return env

    def _repo_python_env(self) -> dict[str, str]:
        env = self._clean_env()
        env['PYTHONPATH'] = 'python'
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        env['PYTHONIOENCODING'] = 'UTF-8'
        env['PYTHONUTF8'] = '1'
        return env

    def _repo_root_env(self) -> dict[str, str]:
        env = self._clean_env()
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        env['PYTHONIOENCODING'] = 'UTF-8'
        env['PYTHONUTF8'] = '1'
        return env

    def _bytecode_residue_dirs(self) -> tuple[Path, ...]:
        return tuple(sorted([
            *(ROOT_DIR / 'openclaw').rglob('__pycache__'),
            *(ROOT_DIR / 'python').rglob('__pycache__'),
        ]))

    def _clear_bytecode_residue(self) -> None:
        for path in self._bytecode_residue_dirs():
            shutil.rmtree(path, ignore_errors=False)

    def _assert_no_bytecode_residue(self) -> None:
        self.assertEqual(
            self._bytecode_residue_dirs(),
            (),
            msg='repo-local entrypoint re-created repo package __pycache__ residue',
        )

    def test_repo_bootstrap_shim_module_is_absent(self) -> None:
        self.assertFalse((ROOT_DIR / 'python' / '_repo_bootstrap.py').exists())
        result = subprocess.run(
            [
                sys.executable,
                '-c',
                'import importlib.util; print(importlib.util.find_spec("_repo_bootstrap"))',
            ],
            cwd=ROOT_DIR,
            env=self._repo_python_env(),
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
        self.assertEqual(result.stdout.strip(), 'None')

    def test_repo_root_shim_cleans_its_own_bytecode_residue(self) -> None:
        self._clear_bytecode_residue()
        env = self._clean_env()
        env['PYTHONIOENCODING'] = 'UTF-8'
        env['PYTHONUTF8'] = '1'
        result = subprocess.run(
            [
                sys.executable,
                '-c',
                (
                    'import json, os, pathlib, sys, openclaw; '
                    'print(json.dumps({'
                    '"dont_write": sys.dont_write_bytecode, '
                    '"env": os.environ.get("PYTHONDONTWRITEBYTECODE"), '
                    '"file": str(pathlib.Path(openclaw.__file__).resolve())'
                    '}))'
                ),
            ],
            cwd=ROOT_DIR,
            env=env,
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            check=False,
        )

        output = '\n'.join(part for part in [result.stdout, result.stderr] if part)
        self.assertEqual(result.returncode, 0, msg=output)
        payload = json.loads(result.stdout)
        self.assertTrue(payload['dont_write'])
        self.assertEqual(payload['env'], '1')
        self.assertEqual(Path(payload['file']).resolve(), CANONICAL_PACKAGE_INIT)
        self._assert_no_bytecode_residue()

    def test_canonical_package_cleans_bytecode_when_python_root_is_added_after_startup(self) -> None:
        self._clear_bytecode_residue()
        env = self._clean_env()
        env['PYTHONIOENCODING'] = 'UTF-8'
        env['PYTHONUTF8'] = '1'
        result = subprocess.run(
            [
                sys.executable,
                '-c',
                (
                    'import json, pathlib, sys; '
                    'sys.path.insert(0, "python"); '
                    'import openclaw; '
                    'print(json.dumps({'
                    '"dont_write": sys.dont_write_bytecode, '
                    '"file": str(pathlib.Path(openclaw.__file__).resolve())'
                    '}))'
                ),
            ],
            cwd=ROOT_DIR,
            env=env,
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            check=False,
        )

        output = '\n'.join(part for part in [result.stdout, result.stderr] if part)
        self.assertEqual(result.returncode, 0, msg=output)
        payload = json.loads(result.stdout)
        self.assertTrue(payload['dont_write'])
        self.assertEqual(Path(payload['file']).resolve(), CANONICAL_PACKAGE_INIT)
        self._assert_no_bytecode_residue()

    def test_repo_host_module_entry_runs_single_unittest_without_pythonpath(self) -> None:
        self._clear_bytecode_residue()
        result = subprocess.run(
            [
                sys.executable,
                '-m',
                'openclaw.testing.repo_host',
                'unittest',
                '--quiet',
                '--jobs',
                '1',
                'python/openclaw/tests/testing/test_repo_unittest.py::RepoUnittestSupportTest::test_coerce_jobs_accepts_auto_and_explicit_count',
            ],
            cwd=ROOT_DIR,
            env=self._clean_env(),
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            check=False,
        )

        output = '\n'.join(part for part in [result.stdout, result.stderr] if part)
        self.assertEqual(result.returncode, 0, msg=output)
        self.assertIn('OK', output)
        self._assert_no_bytecode_residue()

    def test_architecture_import_guards_module_entrypoint_runs_from_repo_root_without_pythonpath(self) -> None:
        self._clear_bytecode_residue()
        result = subprocess.run(
            [
                sys.executable,
                '-m',
                'openclaw.doctor.platform.architecture_import_guards',
            ],
            cwd=ROOT_DIR,
            env=self._repo_root_env(),
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            check=False,
        )

        output = '\n'.join(part for part in [result.stdout, result.stderr] if part)
        self.assertEqual(result.returncode, 0, msg=output)
        payload = json.loads(result.stdout)
        self.assertTrue(payload['ok'])
        self._assert_no_bytecode_residue()

    def test_repo_cli_module_entrypoint_runs_from_repo_root_without_pythonpath(self) -> None:
        self._clear_bytecode_residue()
        result = subprocess.run(
            [
                sys.executable,
                '-m',
                'openclaw.cli',
            ],
            cwd=ROOT_DIR,
            env=self._repo_root_env(),
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            check=False,
        )

        output = '\n'.join(part for part in [result.stdout, result.stderr] if part)
        self.assertEqual(result.returncode, 2, msg=output)
        self.assertIn('当前支持', output)
        self._assert_no_bytecode_residue()

    def test_repo_root_shim_points_importlib_resources_at_canonical_package(self) -> None:
        self._clear_bytecode_residue()
        result = subprocess.run(
            [
                sys.executable,
                '-c',
                (
                    'import importlib.resources as resources, json, openclaw; '
                    'package_dir = resources.files(openclaw); '
                    'print(json.dumps({'
                    '"package_dir": str(package_dir), '
                    '"origin": getattr(openclaw.__spec__, "origin", None), '
                    '"has_lib": (package_dir / "lib").is_dir(), '
                    '"has_tests": (package_dir / "tests").is_dir()'
                    '}, ensure_ascii=False))'
                ),
            ],
            cwd=ROOT_DIR,
            env=self._repo_root_env(),
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            check=False,
        )

        output = '\n'.join(part for part in [result.stdout, result.stderr] if part)
        self.assertEqual(result.returncode, 0, msg=output)
        payload = json.loads(result.stdout)
        self.assertEqual(Path(payload['package_dir']).resolve(), (ROOT_DIR / 'python' / 'openclaw').resolve())
        self.assertEqual(Path(payload['origin']).resolve(), CANONICAL_PACKAGE_INIT)
        self.assertTrue(payload['has_lib'])
        self.assertTrue(payload['has_tests'])
        self._assert_no_bytecode_residue()

    def test_runtime_script_orphans_module_entrypoint_runs_with_repo_pythonpath(self) -> None:
        self._clear_bytecode_residue()
        result = subprocess.run(
            [
                sys.executable,
                '-m',
                'openclaw.doctor.agent_modules.runtime_script_orphans',
            ],
            cwd=ROOT_DIR,
            env=self._repo_python_env(),
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            check=False,
        )

        output = '\n'.join(part for part in [result.stdout, result.stderr] if part)
        self.assertEqual(result.returncode, 0, msg=output)
        payload = json.loads(result.stdout)
        self.assertTrue(payload['ok'])
        self._assert_no_bytecode_residue()

    def test_wrong_python_namespace_unittest_entry_fails_closed_without_recreating_bytecode_residue(self) -> None:
        self._clear_bytecode_residue()
        env = self._clean_env()
        env['PYTHONIOENCODING'] = 'UTF-8'
        env['PYTHONUTF8'] = '1'
        result = subprocess.run(
            [
                sys.executable,
                '-m',
                'unittest',
                'python.openclaw.tests.governance.test_delivery_cleanliness',
                '-q',
            ],
            cwd=ROOT_DIR,
            env=env,
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            check=False,
        )

        output = '\n'.join(part for part in [result.stdout, result.stderr] if part)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('python.openclaw', output)
        self.assertIn('请改用 `openclaw...`', output)
        self._assert_no_bytecode_residue()


if __name__ == '__main__':
    unittest.main()
