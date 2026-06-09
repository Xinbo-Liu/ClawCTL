from __future__ import annotations

import hashlib
import json
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from openclaw.lib.repo import extension_envs as extension_envs_module
from openclaw.lib.repo.extension_envs import (
    ACTIVE_MANIFEST_NAME,
    ExtensionEnvError,
    dependency_snapshot,
    extension_env_path,
    extension_env_status,
    extension_envs_dir,
    extension_python_executable,
    extension_wheelhouse_dir,
    prepare_extension_env,
    select_extension_rows,
    sync_extension_wheelhouse,
    validate_extension_repo_wheelhouse,
)
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.managed_extensions import ManagedExtensionRow
from openclaw.lib.runtime.resolver_loader import require_path_resolver
from openclaw.tests.support.managed_extensions import managed_extensions, representative_managed_extension


ROOT_DIR = resolve_repo_root(Path(__file__))
MANAGED_EXTENSIONS = tuple(sorted(managed_extensions(ROOT_DIR), key=lambda row: row.id))


def _write_pyproject(extension_root: Path, *, dependencies: tuple[str, ...] = ()) -> None:
    dependency_lines = ''
    if dependencies:
        dependency_lines = 'dependencies = [\n' + ''.join(f'  "{item}",\n' for item in dependencies) + ']\n'
    (extension_root / 'pyproject.toml').write_text(
        textwrap.dedent(
            f'''\
            [build-system]
            requires = ["setuptools>=69"]
            build-backend = "setuptools.build_meta"

            [project]
            name = "openclaw-agent-probe"
            version = "0.0.0"
            requires-python = ">=3.11"
            {dependency_lines}
            '''
        ),
        encoding='utf-8',
    )


def _fake_row(base: Path, *, dependencies: tuple[str, ...] = ()) -> ManagedExtensionRow:
    extension_root = base / 'agent' / 'extensions' / 'agent_probe'
    python_root = extension_root / 'python'
    manifest_dir = extension_root / 'config' / 'control_plane' / 'extensions.d'
    profile_path = extension_root / 'config' / 'control_plane' / 'profiles' / 'agent_probe.service.json'
    python_root.mkdir(parents=True)
    manifest_dir.mkdir(parents=True)
    profile_path.parent.mkdir(parents=True)
    _write_pyproject(extension_root, dependencies=dependencies)
    (extension_root / 'requirements.lock').write_text('# empty\n', encoding='utf-8')
    return ManagedExtensionRow(
        id='agent_probe',
        title='Probe',
        root_dir=extension_root,
        default_service_config_path=profile_path,
        manifest_dir=manifest_dir,
        python_roots=(python_root,),
        status='managed_explicit_extension',
    )


def _fake_runtime_env(base: Path) -> dict[str, str]:
    return {
        'HOST_STATE_DIR': str(base / 'state' / 'openclaw'),
        'OPENCLAW_RUNTIME_PATH_VIEW': 'host',
    }


def _write_repo_wheelhouse(
    row: ManagedExtensionRow,
    *,
    package: str = 'demo-lib',
    wheel_package: str = 'demo_lib',
    version: str = '1.0.0',
    content: bytes = b'demo wheel payload',
) -> Path:
    repo_wheelhouse = row.root_dir / 'offline_wheelhouse'
    repo_wheelhouse.mkdir(parents=True, exist_ok=True)
    filename = f'{wheel_package}-{version}-py3-none-any.whl'
    wheel_path = repo_wheelhouse / filename
    wheel_path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    lock_text = f'{package}=={version} --hash=sha256:{digest}\n'
    lock_path = row.root_dir / 'requirements.lock'
    lock_path.write_text(lock_text, encoding='utf-8')
    manifest = {
        'schemaVersion': 1,
        'extensionId': row.id,
        'lockPath': 'requirements.lock',
        'lockHash': hashlib.sha256(lock_path.read_bytes()).hexdigest(),
        'wheels': [
            {
                'filename': filename,
                'package': package,
                'version': version,
                'sha256': digest,
                'size': len(content),
            },
        ],
    }
    (repo_wheelhouse / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    return wheel_path


class ExtensionEnvsTest(unittest.TestCase):
    def test_dependency_hash_changes_when_pyproject_or_lock_changes(self) -> None:
        with TemporaryDirectory() as tmp:
            row = _fake_row(Path(tmp))
            initial = dependency_snapshot(row).dependency_hash
            _write_pyproject(row.root_dir, dependencies=('demo-lib==1.0.0',))
            (row.root_dir / 'requirements.lock').write_text(
                'demo-lib==1.0.0 --hash=sha256:' + '1' * 64 + '\n',
                encoding='utf-8',
            )
            after_pyproject = dependency_snapshot(row).dependency_hash
            (row.root_dir / 'requirements.lock').write_text(
                'demo-lib==1.0.1 --hash=sha256:' + '2' * 64 + '\n',
                encoding='utf-8',
            )
            after_lock = dependency_snapshot(row).dependency_hash

        self.assertNotEqual(initial, after_pyproject)
        self.assertNotEqual(after_pyproject, after_lock)

    def test_verify_reports_missing_corrupt_hash_and_python_tag_failures(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            row = _fake_row(base)
            env = _fake_runtime_env(base)
            missing = extension_env_status(row, repo_root=ROOT_DIR, env=env)
            self.assertFalse(missing.ok)
            self.assertTrue(any('active manifest 缺失' in item for item in missing.issues))

            active = extension_envs_dir(repo_root=ROOT_DIR, env=env) / row.id / ACTIVE_MANIFEST_NAME
            active.parent.mkdir(parents=True, exist_ok=True)
            active.write_text('{"broken": ', encoding='utf-8')
            corrupt = extension_env_status(row, repo_root=ROOT_DIR, env=env)
            self.assertFalse(corrupt.ok)
            self.assertTrue(any('无法读取' in item for item in corrupt.issues))

            snapshot = dependency_snapshot(row)
            env_path = extension_env_path(row, snapshot, envs_dir=extension_envs_dir(repo_root=ROOT_DIR, env=env))
            env_path.mkdir(parents=True)
            manifest = extension_envs_module._build_manifest(
                row,
                snapshot=snapshot,
                repo_root=ROOT_DIR,
                config_path=None,
                env=env,
            )
            manifest['dependencyHash'] = 'stale'
            manifest['pythonTag'] = 'stale-python'
            active.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2)
                + '\n',
                encoding='utf-8',
            )
            stale = extension_env_status(row, repo_root=ROOT_DIR, env=env)
            self.assertFalse(stale.ok)
            self.assertTrue(any('dependencyHash 不匹配' in item for item in stale.issues))
            self.assertTrue(any('Python 版本不匹配' in item for item in stale.issues))

    def test_host_status_accepts_manifest_prepared_from_scheduler_view(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            row = _fake_row(base)
            host_env = _fake_runtime_env(base)
            scheduler_env = {
                **host_env,
                'OPENCLAW_RUNTIME_PATH_VIEW': 'scheduler',
            }
            snapshot = dependency_snapshot(row)
            active = extension_envs_dir(repo_root=ROOT_DIR, env=host_env) / row.id / ACTIVE_MANIFEST_NAME
            env_path = extension_env_path(row, snapshot, envs_dir=extension_envs_dir(repo_root=ROOT_DIR, env=host_env))
            python_executable = extension_python_executable(env_path)
            python_executable.parent.mkdir(parents=True)
            python_executable.write_text('', encoding='utf-8')
            active.parent.mkdir(parents=True, exist_ok=True)
            manifest = extension_envs_module._build_manifest(
                row,
                snapshot=snapshot,
                repo_root=ROOT_DIR,
                config_path=None,
                env=scheduler_env,
            )
            active.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

            with mock.patch.object(extension_envs_module, '_probe_python_tag', return_value=snapshot.python_tag):
                status = extension_env_status(row, repo_root=ROOT_DIR, env=host_env)

            self.assertTrue(status.ok, msg=status.issues)
            self.assertEqual(status.env_path, env_path)
            self.assertEqual(status.python_executable, python_executable)
            self.assertEqual(status.manifest['runtimePathView'], 'scheduler')
            self.assertIn('host', status.manifest['runtimePathViews'])
            self.assertIn('scheduler', status.manifest['runtimePathViews'])

    def test_missing_lock_file_invalidates_extension_env_even_without_dependencies(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            row = _fake_row(base)
            row.root_dir.joinpath('requirements.lock').unlink()
            env = _fake_runtime_env(base)

            missing_lock = extension_env_status(row, repo_root=ROOT_DIR, env=env)
            self.assertFalse(missing_lock.ok)
            self.assertTrue(any('requirements.lock' in item for item in missing_lock.issues))
            with self.assertRaises(ExtensionEnvError) as ctx:
                prepare_extension_env(row, repo_root=ROOT_DIR, env=env)

        self.assertIn('requirements.lock', str(ctx.exception))

    def test_prepare_failure_does_not_overwrite_existing_active_manifest(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            row = _fake_row(base)
            env = _fake_runtime_env(base)
            active = extension_envs_dir(repo_root=ROOT_DIR, env=env) / row.id / ACTIVE_MANIFEST_NAME
            active.parent.mkdir(parents=True)
            original = {'schemaVersion': 1, 'extensionId': row.id, 'envPath': 'old'}
            active.write_text(json.dumps(original, ensure_ascii=False) + '\n', encoding='utf-8')

            with mock.patch('openclaw.lib.repo.extension_envs.venv.EnvBuilder.create', side_effect=RuntimeError('boom')):
                with self.assertRaises(RuntimeError):
                    prepare_extension_env(row, repo_root=ROOT_DIR, env=env)

            self.assertEqual(json.loads(active.read_text(encoding='utf-8')), original)

    def test_prepare_rejects_dependency_extension_without_lock_requirements(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            row = _fake_row(base, dependencies=('demo-lib>=1',))
            env = _fake_runtime_env(base)

            with self.assertRaises(ExtensionEnvError) as ctx:
                prepare_extension_env(row, repo_root=ROOT_DIR, env=env)

        self.assertIn('没有可安装依赖', str(ctx.exception))

    def test_repo_wheelhouse_sync_copies_locked_wheels_and_removes_stale_runtime_wheels(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            row = _fake_row(base, dependencies=('demo-lib==1.0.0',))
            wheel_path = _write_repo_wheelhouse(row)
            env = _fake_runtime_env(base)
            runtime_dir = extension_wheelhouse_dir(repo_root=ROOT_DIR, env=env) / row.id
            runtime_dir.mkdir(parents=True)
            stale_path = runtime_dir / 'demo_lib-0.9.0-py3-none-any.whl'
            stale_path.write_bytes(b'stale')

            result = sync_extension_wheelhouse(row, repo_root=ROOT_DIR, env=env)

            self.assertTrue(result['changed'])
            self.assertFalse(stale_path.exists())
            self.assertTrue((runtime_dir / wheel_path.name).is_file())
            self.assertTrue((runtime_dir / 'manifest.json').is_file())
            self.assertEqual(result['validation']['wheels'][0]['filename'], wheel_path.name)

    def test_ensure_extension_env_reuses_sync_prepare_and_verify_result(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            row = _fake_row(base)
            env = _fake_runtime_env(base)
            status = mock.Mock()
            status.ok = True
            status.to_json.return_value = {'ok': True, 'extensionId': row.id}

            with mock.patch.object(extension_envs_module, 'sync_extension_wheelhouse', return_value={'changed': True}) as sync_mock:
                with mock.patch.object(extension_envs_module, 'prepare_extension_env', return_value={'changed': True}) as prepare_mock:
                    with mock.patch.object(extension_envs_module, 'extension_env_status', return_value=status):
                        result = extension_envs_module.ensure_extension_env(row, repo_root=ROOT_DIR, env=env)

            self.assertTrue(result['ok'])
            self.assertTrue(result['changed'])
            self.assertEqual(result['syncWheelhouse']['changed'], True)
            self.assertEqual(result['status']['extensionId'], row.id)
            sync_mock.assert_called_once()
            prepare_mock.assert_called_once()

    def test_repo_wheelhouse_validation_rejects_non_unique_wheel_for_lock(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            row = _fake_row(base, dependencies=('demo-lib==1.0.0',))
            _write_repo_wheelhouse(row)
            duplicate = row.root_dir / 'offline_wheelhouse' / 'demo_lib-1.0.0-py2.py3-none-any.whl'
            duplicate.write_bytes(b'duplicate')

            with self.assertRaises(ExtensionEnvError) as ctx:
                validate_extension_repo_wheelhouse(row)

        self.assertIn('必须唯一', str(ctx.exception))

    def test_python_executable_path_is_platform_specific_inside_env(self) -> None:
        path = extension_python_executable(Path('/tmp/env-root'))
        self.assertTrue(str(path).endswith('python.exe') or str(path).endswith('/bin/python'))

    def test_pip_install_command_disables_bytecode_compile(self) -> None:
        command = extension_envs_module._pip_install_command(
            python_executable=Path('/tmp/env/bin/python'),
            lock_path=Path('/tmp/requirements.lock'),
            wheelhouse_dir=Path('/tmp/wheelhouse'),
            extension_id='agent_probe',
            offline=True,
        )

        self.assertIn('--no-compile', command)

    def test_extension_env_bytecode_cleanup_removes_venv_residue(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / 'env'
            cache_dir = root / 'lib' / 'python3.11' / 'site-packages' / 'demo' / '__pycache__'
            cache_dir.mkdir(parents=True)
            (cache_dir / 'module.cpython-311.pyc').write_bytes(b'x')
            (root / 'standalone.pyc').write_bytes(b'x')

            extension_envs_module._remove_extension_env_bytecode(root)

            self.assertFalse(cache_dir.exists())
            self.assertFalse((root / 'standalone.pyc').exists())

    def test_extension_runtime_paths_stay_under_control_plane_state(self) -> None:
        resolver = require_path_resolver(repo_root=ROOT_DIR)
        host_control_plane_root = Path(resolver.resolve_path('control_plane_host_state_dir', 'host')).resolve()
        for entry_id in ('extension_envs_dir', 'extension_wheelhouse_dir'):
            with self.subTest(entry_id=entry_id, view='host'):
                host_path = Path(resolver.resolve_path(entry_id, 'host')).resolve()
                host_path.relative_to(host_control_plane_root)
            with self.subTest(entry_id=entry_id, view='scheduler'):
                scheduler_path = str(resolver.resolve_path(entry_id, 'scheduler')).replace('\\', '/')
                self.assertTrue(scheduler_path.startswith('/home/openclaw/.openclaw/'), msg=scheduler_path)

    def test_extension_runtime_path_env_override_cannot_escape_control_plane_state(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            env = {
                'HOST_STATE_DIR': str(base / 'state' / 'openclaw'),
                'HOST_EXTENSION_ENVS_DIR': str(base / 'outside' / 'extension_envs'),
                'OPENCLAW_RUNTIME_PATH_VIEW': 'host',
            }

            with self.assertRaisesRegex(ExtensionEnvError, 'control-plane runtime state'):
                extension_envs_dir(repo_root=ROOT_DIR, env=env)

    def test_enabled_selector_uses_control_plane_config_context(self) -> None:
        if not MANAGED_EXTENSIONS:
            self.skipTest('base release surface has no repo-managed extension')
        extension = representative_managed_extension(ROOT_DIR)
        rows = select_extension_rows(
            repo_root=ROOT_DIR,
            include_enabled=True,
            config_path=extension.default_service_config_path,
        )

        self.assertEqual([row.id for row in rows], [extension.id])

    def test_env_contract_scanner_includes_env_prefixed_runtime_inputs(self) -> None:
        names: set[str] = set()
        extension_envs_module._add_env_ref_names(
            {
                'contract': {
                    'runtimeInputs': [
                        'task_id',
                        'env:EXTENSION_TASK_ID',
                        'env:not-valid-name',
                    ],
                    'model': {
                        'apiKeyEnv': 'EXTENSION_API_KEY',
                    },
                },
            },
            names,
        )

        self.assertEqual(names, {'EXTENSION_API_KEY', 'EXTENSION_TASK_ID'})


if __name__ == '__main__':
    unittest.main()
