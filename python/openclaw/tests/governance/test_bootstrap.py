from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from openclaw.lib.repo.bootstrap import bootstrap_env_defaults, bootstrap_env_pythonpath, bootstrap_path_entries
from openclaw.lib.repo.layout import REPO_ROOT_ENV_VARS, RepoRootResolutionError, resolve_repo_root
from openclaw.tests.support.static_text_assertions import assert_static_text_absent
from openclaw.tests.support.managed_extensions import managed_extensions

ROOT_DIR = resolve_repo_root(Path(__file__))
CANONICAL_INIT = (ROOT_DIR / 'python' / 'openclaw' / '__init__.py').resolve()
MANAGED_EXTENSIONS = tuple(sorted(managed_extensions(ROOT_DIR), key=lambda row: row.id))
MANAGED_EXTENSION = MANAGED_EXTENSIONS[0] if MANAGED_EXTENSIONS else None
MANAGED_EXTENSION_PROFILE = MANAGED_EXTENSION.default_service_config_path if MANAGED_EXTENSION is not None else None
MANAGED_EXTENSION_PYTHON_ROOT = MANAGED_EXTENSION.python_roots[0] if MANAGED_EXTENSION is not None else None
REPO_TEST_READINESS_WRAPPER = ROOT_DIR / 'scripts' / 'testing' / 'check_repo_test_readiness.sh'
REPO_UNITTEST_WRAPPER = ROOT_DIR / 'scripts' / 'testing' / 'run_repo_unittest.sh'
RETIRED_REPO_PYTEST_WRAPPER = ROOT_DIR / 'scripts' / 'testing' / 'run_repo_pytest.sh'
REPO_PYTHON_ENV_HELPER = ROOT_DIR / 'scripts' / 'lib' / 'repo_python_env.sh'
OPENCLAW_PYTHON_TOOL_WRAPPER = ROOT_DIR / 'scripts' / 'runtime' / 'run_openclaw_python_tool.sh'
BOOTSTRAP_SCRIPT = ROOT_DIR / 'scripts' / 'setup' / 'bootstrap.sh'
CONTROL_PLANE_SCHEDULER_EXEC_LIB = ROOT_DIR / 'scripts' / 'lib' / 'control_plane_scheduler_exec.sh'
AGENT_ENTRYPOINT_WRAPPER = ROOT_DIR / 'scripts' / 'agent_runtime' / 'run_agent_entrypoint.sh'
REPO_HOST_ENTRY = ROOT_DIR / 'python' / 'openclaw' / 'testing' / 'repo_host.py'
REPO_HOST_MODULE = 'openclaw.testing.repo_host'
LIGHTWEIGHT_UNITTEST_SELECTOR = (
    'openclaw.tests.testing.test_repo_unittest.'
    'RepoUnittestSupportTest.test_coerce_jobs_accepts_auto_and_explicit_count'
)
REPO_BOOTSTRAP_SHIM = ROOT_DIR / 'python' / '_repo_bootstrap.py'
ROOT_SHIM_DIR = ROOT_DIR / 'openclaw'
ROOT_SITECUSTOMIZE = ROOT_DIR / 'sitecustomize.py'
PYTHON_SITECUSTOMIZE = ROOT_DIR / 'python' / 'sitecustomize.py'
PYTHON_PACKAGE_INIT = ROOT_DIR / 'python' / '__init__.py'
REPO_HOST_SUITE_REENTRY_GUARD = 'OPENCLAW_BOOTSTRAP_SUITE_REENTRY'


class RepoBootstrapTest(unittest.TestCase):
    _repo_root_probe: dict[str, str] = {}
    _repo_pythonpath_probe: dict[str, str] = {}

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._repo_root_probe = cls._run_python_probe()
        cls._repo_pythonpath_probe = cls._run_python_probe(pythonpath='python')

    @classmethod
    def _run_python_probe(cls, *, pythonpath: str | None = None) -> dict[str, str]:
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        env['PYTHONIOENCODING'] = 'UTF-8'
        env['PYTHONUTF8'] = '1'
        if pythonpath is not None:
            env['PYTHONPATH'] = pythonpath
        result = subprocess.run(
            [
                sys.executable,
                '-c',
                (
                    'import json, pathlib, openclaw, openclaw.testing.repo_unittest as repo_unittest; '
                    'print(json.dumps({'
                    '"openclaw_file": str(pathlib.Path(openclaw.__file__).resolve()), '
                    '"repo_unittest_module": repo_unittest.__name__'
                    '}, ensure_ascii=False))'
                ),
            ],
            cwd=ROOT_DIR,
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            env=env,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr or result.stdout)
        return json.loads(result.stdout)

    def _repo_python_env(self, *, pythonpath: str | None = None, extra_env: dict[str, str] | None = None) -> dict[str, str]:
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        env['PYTHONIOENCODING'] = 'UTF-8'
        env['PYTHONUTF8'] = '1'
        if pythonpath is not None:
            env['PYTHONPATH'] = pythonpath
        if extra_env:
            env.update(extra_env)
        return env

    def _repo_root_env(self, *, extra_env: dict[str, str] | None = None) -> dict[str, str]:
        env = self._repo_python_env(extra_env=extra_env)
        env.pop('PYTHONPATH', None)
        return env

    def test_bootstrap_path_entries_keep_managed_extension_then_repo_python_order(self) -> None:
        if MANAGED_EXTENSION is None:
            self.skipTest('base release surface has no repo-managed extension')
        entries = bootstrap_path_entries(ROOT_DIR, MANAGED_EXTENSION_PROFILE)

        self.assertEqual(entries[:2], (MANAGED_EXTENSION_PYTHON_ROOT, (ROOT_DIR / 'python').resolve()))
        self.assertEqual(len(entries), len({str(item) for item in entries}))

    def test_bootstrap_path_entries_dedupe_duplicate_extension_roots(self) -> None:
        with mock.patch(
            'openclaw.lib.repo.bootstrap.managed_extension_python_roots_for_config_path',
            return_value=((ROOT_DIR / 'python').resolve(), (ROOT_DIR / 'python').resolve()),
        ):
            entries = bootstrap_path_entries(ROOT_DIR, None)

        self.assertEqual(entries, ((ROOT_DIR / 'python').resolve(),))

    def test_bootstrap_env_pythonpath_prefixes_managed_extension_python_and_repo_python_only(self) -> None:
        if MANAGED_EXTENSION is None:
            self.skipTest('base release surface has no repo-managed extension')
        env = {'PYTHONPATH': str(ROOT_DIR / 'docs')}

        bootstrap_env_pythonpath(env, ROOT_DIR, MANAGED_EXTENSION_PROFILE)

        parts = env['PYTHONPATH'].split(os.pathsep)
        self.assertEqual(parts[:2], [str(MANAGED_EXTENSION_PYTHON_ROOT), str((ROOT_DIR / 'python').resolve())])
        self.assertNotIn(str(ROOT_DIR.resolve()), parts)
        self.assertIn(str((ROOT_DIR / 'docs').resolve()), parts)

    def test_repo_root_python_without_pythonpath_imports_canonical_openclaw(self) -> None:
        self.assertEqual(Path(self._repo_root_probe['openclaw_file']), CANONICAL_INIT)

    def test_repo_root_python_without_pythonpath_imports_repo_unittest_support(self) -> None:
        self.assertEqual(self._repo_root_probe['repo_unittest_module'], 'openclaw.testing.repo_unittest')

    def test_repo_root_python_without_pythonpath_can_run_repo_cli_module(self) -> None:
        result = subprocess.run(
            [sys.executable, '-m', 'openclaw.cli'],
            cwd=ROOT_DIR,
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            env=self._repo_python_env(),
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn('当前支持', result.stderr or result.stdout)

    def test_repo_root_pythonpath_python_resolves_canonical_source_package(self) -> None:
        self.assertEqual(Path(self._repo_pythonpath_probe['openclaw_file']), CANONICAL_INIT)

    def test_repo_root_pythonpath_python_imports_repo_unittest_support(self) -> None:
        self.assertEqual(self._repo_pythonpath_probe['repo_unittest_module'], 'openclaw.testing.repo_unittest')

    def test_repo_host_suite_repo_check_resolves_to_repo_unittest_selectors(self) -> None:
        from openclaw.testing import repo_host

        selectors = repo_host.load_suite_selectors('repo-check')
        self.assertGreater(len(selectors), 0)
        self.assertTrue(any(item.startswith('python/openclaw/tests') for item in selectors))

        args = argparse.Namespace(
            quiet=True,
            jobs='1',
            start_dir='python/openclaw/tests',
            pattern='test_*.py',
            import_mode='',
            selectors=[],
        )
        argv = repo_host.build_repo_unittest_argv(args, selectors=selectors[:1])

        self.assertEqual(argv[:5], ['--quiet', '--jobs', '1', '--start-dir', 'python/openclaw/tests'])
        self.assertIn('--pattern', argv)
        self.assertEqual(argv[-1], selectors[0])

    def test_repo_python_entry_contract_files_exist(self) -> None:
        self.assertTrue(REPO_TEST_READINESS_WRAPPER.is_file())
        self.assertTrue(REPO_UNITTEST_WRAPPER.is_file())
        self.assertFalse(RETIRED_REPO_PYTEST_WRAPPER.exists())
        self.assertTrue(REPO_PYTHON_ENV_HELPER.is_file())
        self.assertFalse(REPO_BOOTSTRAP_SHIM.exists())
        self.assertTrue(ROOT_SHIM_DIR.is_dir())
        self.assertFalse(ROOT_SITECUSTOMIZE.exists())
        self.assertTrue(PYTHON_SITECUSTOMIZE.is_file())
        self.assertFalse(PYTHON_PACKAGE_INIT.exists())
        self.assertTrue(CONTROL_PLANE_SCHEDULER_EXEC_LIB.is_file())

    def test_repo_local_python_sitecustomize_prevents_bytecode_residue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir).resolve()
            python_root = repo_root / 'python'
            openclaw_root = python_root / 'openclaw'
            package_root = python_root / 'fixture_pkg'
            runtime_root = repo_root / 'config' / 'runtime'
            control_plane_root = repo_root / 'config' / 'control_plane'
            support_root = repo_root / 'config' / 'governance' / 'support'
            python_root.mkdir(parents=True)
            openclaw_root.mkdir(parents=True)
            package_root.mkdir(parents=True)
            runtime_root.mkdir(parents=True)
            control_plane_root.mkdir(parents=True)
            support_root.mkdir(parents=True)
            (python_root / 'sitecustomize.py').write_text(PYTHON_SITECUSTOMIZE.read_text(encoding='utf-8'), encoding='utf-8')
            (openclaw_root / '__init__.py').write_text('', encoding='utf-8')
            (package_root / '__init__.py').write_text("VALUE = 'ok'\n", encoding='utf-8')
            (runtime_root / 'paths.json').write_text('{}\n', encoding='utf-8')
            (control_plane_root / 'service.json').write_text('{}\n', encoding='utf-8')
            (support_root / 'repo_python_bootstrap.env').write_text(
                (ROOT_DIR / 'config' / 'governance' / 'support' / 'repo_python_bootstrap.env').read_text(encoding='utf-8'),
                encoding='utf-8',
            )

            env = dict(os.environ)
            env.pop('PYTHONHOME', None)
            env.update(bootstrap_env_defaults(repo_root))
            env['PYTHONIOENCODING'] = 'UTF-8'
            env['PYTHONUTF8'] = '1'
            env['PYTHONPATH'] = 'python'
            result = subprocess.run(
                [
                    sys.executable,
                    '-c',
                    (
                        'import json, os, sys, fixture_pkg; '
                        'print(json.dumps({'
                        '"dont_write": sys.dont_write_bytecode, '
                        '"env": os.environ.get("PYTHONDONTWRITEBYTECODE"), '
                        '"value": fixture_pkg.VALUE'
                        '}))'
                    ),
                ],
                cwd=repo_root,
                text=True,
                capture_output=True,
                env=env,
                encoding='utf-8',
                errors='replace',
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
            self.assertEqual(
                json.loads(result.stdout),
                {'dont_write': True, 'env': '1', 'value': 'ok'},
            )
            self.assertFalse((package_root / '__pycache__').exists())
            self.assertFalse((python_root / '__pycache__').exists())
            self.assertEqual(list(repo_root.rglob('*.pyc')), [])

    def test_repo_local_python_sitecustomize_cleans_its_own_cache_without_env_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir).resolve()
            python_root = repo_root / 'python'
            python_root.mkdir(parents=True)
            (python_root / 'sitecustomize.py').write_text(PYTHON_SITECUSTOMIZE.read_text(encoding='utf-8'), encoding='utf-8')

            env = dict(os.environ)
            env.pop('PYTHONHOME', None)
            env.pop('PYTHONDONTWRITEBYTECODE', None)
            env['PYTHONIOENCODING'] = 'UTF-8'
            env['PYTHONUTF8'] = '1'
            env['PYTHONPATH'] = 'python'
            result = subprocess.run(
                [
                    sys.executable,
                    '-c',
                    (
                        'import json, os, sys; '
                        'print(json.dumps({'
                        '"dont_write": sys.dont_write_bytecode, '
                        '"env": os.environ.get("PYTHONDONTWRITEBYTECODE")'
                        '}))'
                    ),
                ],
                cwd=repo_root,
                text=True,
                capture_output=True,
                env=env,
                encoding='utf-8',
                errors='replace',
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
            self.assertEqual(json.loads(result.stdout), {'dont_write': True, 'env': '1'})
            self.assertFalse((python_root / '__pycache__').exists())
            self.assertEqual(list(repo_root.rglob('*.pyc')), [])

    def test_repo_host_module_file_exists_for_repo_local_test_lane(self) -> None:
        self.assertTrue(REPO_HOST_ENTRY.is_file())

    def test_repo_host_file_path_entry_is_not_supported(self) -> None:
        result = subprocess.run(
            [sys.executable, str(REPO_HOST_ENTRY), '--help'],
            cwd=ROOT_DIR,
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            env=self._repo_root_env(),
            check=False,
        )

        output = '\n'.join(part for part in (result.stdout, result.stderr) if part)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('python -m openclaw.testing.repo_host', output)

    def test_resolve_repo_root_raises_when_start_path_is_outside_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            outsider = (Path(tmpdir) / 'outside.txt').resolve()
            outsider.write_text('outside\n', encoding='utf-8')

            with mock.patch.dict(os.environ, {name: '' for name in REPO_ROOT_ENV_VARS}):
                with self.assertRaises(RepoRootResolutionError):
                    resolve_repo_root(outsider)

    def test_docs_and_shells_do_not_reference_retired_bootstrap_entries(self) -> None:
        scan_paths = (
            ROOT_DIR / 'README.md',
            ROOT_DIR / 'docs',
            ROOT_DIR / 'scripts',
            ROOT_DIR / 'python' / 'openclaw' / 'testing' / 'repo_host.py',
        )
        scanned_files: list[Path] = []
        for base in scan_paths:
            if base.is_dir():
                scanned_files.extend(path for path in sorted(base.rglob('*')) if path.is_file())
            elif base.is_file():
                scanned_files.append(base)

        for path in scanned_files:
            source = path.read_text(encoding='utf-8')
            assert_static_text_absent(self, '_repo_bootstrap', source, msg=str(path.relative_to(ROOT_DIR)))
            assert_static_text_absent(self, 'python python/openclaw/testing/repo_host.py', source, msg=str(path.relative_to(ROOT_DIR)))

    def test_runtime_wrapper_scripts_forward_to_unified_scheduler_exec_surface(self) -> None:
        scheduler_exec_source = CONTROL_PLANE_SCHEDULER_EXEC_LIB.read_text(encoding='utf-8')
        python_tool_source = OPENCLAW_PYTHON_TOOL_WRAPPER.read_text(encoding='utf-8')
        entrypoint_source = AGENT_ENTRYPOINT_WRAPPER.read_text(encoding='utf-8')

        self.assertIn('dispatch ops run-target-operation', python_tool_source)
        self.assertIn('control-plane runtime scheduler-run-agent-runtime', python_tool_source)

        assert_static_text_absent(self, '鐢ㄦ硶', scheduler_exec_source)
        assert_static_text_absent(self, '缂哄皯', scheduler_exec_source)
        self.assertIn('缺少 --agent-ref', scheduler_exec_source)
        self.assertIn('缺少 --operation', scheduler_exec_source)
        self.assertIn('openclaw_scheduler_run_target_operation', scheduler_exec_source)
        self.assertIn('openclaw_scheduler_run_agent_runtime', scheduler_exec_source)

        assert_static_text_absent(self, '鐢ㄦ硶', entrypoint_source)
        assert_static_text_absent(self, '缂哄皯', entrypoint_source)
        self.assertIn('缺少 agent 标识', entrypoint_source)
        self.assertIn('openclaw_control_plane_agent_config_path', entrypoint_source)
        assert_static_text_absent(self, 'CONTROL_PLANE_CONFIG_PATH=', entrypoint_source)
        assert_static_text_absent(self, '${CONTROL_PLANE_CONFIG_PATH:-}', entrypoint_source)
        self.assertIn('OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH=$RESOLVED_CONFIG_PATH', entrypoint_source)
        self.assertIn('container_python', entrypoint_source)
        self.assertIn('control-plane runtime scheduler-run-agent-runtime', entrypoint_source)
        assert_static_text_absent(self, 'run_registered_agent_runtime.sh', entrypoint_source)

        container_branch = entrypoint_source.split('if [[ -x "$CONTAINER_CLI_PATH" ]]; then', 1)[1].split('fi', 1)[0]
        assert_static_text_absent(self, 'openclaw_control_plane_agent_config_path', container_branch)
        self.assertIn('resolve_container_agent_config_path', entrypoint_source)
        self.assertIn('resolve_host_agent_config_path', entrypoint_source)

    def test_managed_extension_module_launchers_do_not_embed_package_default_config_env(self) -> None:
        if MANAGED_EXTENSION is None:
            self.skipTest('base release surface has no repo-managed extension')
        launcher_paths = sorted(
            (MANAGED_EXTENSION.root_dir / 'agent' / 'modules').glob('*/bin/*')
        )
        self.assertGreater(len(launcher_paths), 0)
        for path in launcher_paths:
            source = path.read_text(encoding='utf-8')
            self.assertIn('run_agent_entrypoint.sh', source, msg=str(path.relative_to(ROOT_DIR)))
            assert_static_text_absent(self, 'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH', source, msg=str(path.relative_to(ROOT_DIR)))
            assert_static_text_absent(self, 'CONTROL_PLANE_CONFIG_PATH=', source, msg=str(path.relative_to(ROOT_DIR)))

    def test_openclaw_python_tool_wrapper_uses_explicit_env_allowlist(self) -> None:
        source = OPENCLAW_PYTHON_TOOL_WRAPPER.read_text(encoding='utf-8')
        self.assertIn('OPENCLAW_PYTHON_TOOL_EXTRA_ENV_VARS', source)
        self.assertIn('source "$ROOT_DIR/scripts/lib/repo_python_env.sh"', source)
        self.assertIn('openclaw_repo_python_env_args "$ROOT_DIR"', source)
        self.assertIn('REPO_PYTHON_ENV_ARGS', source)
        assert_static_text_absent(self, 'compgen -A variable', source)
        assert_static_text_absent(self, '*_STATE_DIR', source)

    def test_bootstrap_uses_deploy_env_control_plane_selection_for_runtime_envs(self) -> None:
        source = BOOTSTRAP_SCRIPT.read_text(encoding='utf-8')
        self.assertIn('source "$ROOT_DIR/scripts/lib/control_plane_config_paths.sh"', source)
        self.assertIn('bootstrap_deploy_env_value OPENCLAW_CONTROL_PLANE_PROFILE', source)
        self.assertIn('bootstrap_deploy_env_value OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH', source)
        self.assertIn('openclaw_control_plane_resolve_config_path "$selected_profile" "$selected_config_path" 1', source)
        self.assertIn('export OPENCLAW_CONTROL_PLANE_PROFILE="$selected_profile"', source)
        self.assertIn('export OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH="$selected_config_path"', source)
        self.assertLess(
            source.index('bootstrap_export_runtime_config_selection'),
            source.index('setup env bootstrap-runtime'),
        )
        self.assertIn('setup env bootstrap-runtime', source)
        self.assertIn('--gateway-local-ro-output', source)
        self.assertIn('runtime_permissions_harden_bootstrap_outputs "$ROOT_DIR"', source)
        assert_static_text_absent(
            self,
            'runtime_permissions_align_openclaw_runtime_owner_only "$ROOT_DIR" "$(runtime_permissions_host_state_root "$ROOT_DIR")"',
            source,
        )


if __name__ == '__main__':
    unittest.main()
