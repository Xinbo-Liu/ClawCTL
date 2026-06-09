from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from openclaw.doctor.agent_modules.managed_probe_fixture import PROBE_PACKAGE_NAME, materialize_managed_probe_extension
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.managed_extensions import (
    ManagedExtensionError,
    load_managed_extensions_index,
    managed_extension_manifest_path,
    managed_extensions_index_path,
    validate_managed_explicit_extension_index,
)
from openclaw.tests.support.helpers import isolated_test_root
from openclaw.tests.support.managed_extensions import representative_managed_extension


ROOT_DIR = resolve_repo_root(Path(__file__))


class ManagedExtensionIndexConsistencyTest(unittest.TestCase):
    def _write_json(self, path: Path, payload: dict[str, object]) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def test_repo_managed_extension_index_matches_layout(self) -> None:
        self.assertEqual(validate_managed_explicit_extension_index(ROOT_DIR), ())

    def test_index_rejects_manifest_dir_contract_drift(self) -> None:
        with isolated_test_root('managed-extension-index-drift') as repo_root:
            materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            index_path = repo_root / 'agent' / 'extensions' / 'index.json'
            payload = json.loads(index_path.read_text(encoding='utf-8'))
            payload['extensions'][0]['manifestDir'] = 'agent/extensions/agent_probe/config/control_plane/extensions.d_wrong'
            self._write_json(index_path, payload)

            with self.assertRaisesRegex(ManagedExtensionError, 'manifestDir must use extension contract path'):
                load_managed_extensions_index(repo_root)

    def test_index_rejects_root_dir_contract_drift(self) -> None:
        with isolated_test_root('managed-extension-root-dir-drift') as repo_root:
            materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            index_path = repo_root / 'agent' / 'extensions' / 'index.json'
            payload = json.loads(index_path.read_text(encoding='utf-8'))
            payload['extensions'][0]['rootDir'] = 'agent/extensions/renamed_probe'
            self._write_json(index_path, payload)

            with self.assertRaisesRegex(ManagedExtensionError, 'rootDir must use extension contract path'):
                load_managed_extensions_index(repo_root)

    def test_index_rejects_extension_id_outside_project_pattern(self) -> None:
        with isolated_test_root('managed-extension-bad-id') as repo_root:
            materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            index_path = repo_root / 'agent' / 'extensions' / 'index.json'
            payload = json.loads(index_path.read_text(encoding='utf-8'))
            payload['extensions'][0]['id'] = '../agent_probe'
            self._write_json(index_path, payload)

            with self.assertRaisesRegex(ManagedExtensionError, 'lowercase extension id pattern'):
                validate_managed_explicit_extension_index(repo_root)

    def test_index_rejects_unknown_root_fields(self) -> None:
        with isolated_test_root('managed-extension-index-unknown-root-field') as repo_root:
            materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            index_path = repo_root / 'agent' / 'extensions' / 'index.json'
            payload = json.loads(index_path.read_text(encoding='utf-8'))
            payload['generatedAt'] = '2026-04-30'
            self._write_json(index_path, payload)

            with self.assertRaisesRegex(ManagedExtensionError, 'unsupported field'):
                load_managed_extensions_index(repo_root)

    def test_index_rejects_unknown_extension_fields(self) -> None:
        with isolated_test_root('managed-extension-index-unknown-row-field') as repo_root:
            materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            index_path = repo_root / 'agent' / 'extensions' / 'index.json'
            payload = json.loads(index_path.read_text(encoding='utf-8'))
            payload['extensions'][0]['sourceUrl'] = 'https://example.invalid'
            self._write_json(index_path, payload)

            with self.assertRaisesRegex(ManagedExtensionError, 'unsupported field'):
                load_managed_extensions_index(repo_root)

    def test_index_rejects_unknown_status_values(self) -> None:
        with isolated_test_root('managed-extension-index-unknown-status') as repo_root:
            materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            index_path = repo_root / 'agent' / 'extensions' / 'index.json'
            payload = json.loads(index_path.read_text(encoding='utf-8'))
            payload['extensions'][0]['status'] = 'draft'
            self._write_json(index_path, payload)

            with self.assertRaisesRegex(ManagedExtensionError, 'status must be one of'):
                load_managed_extensions_index(repo_root)

    def test_index_rejects_python_roots_contract_drift(self) -> None:
        with isolated_test_root('managed-extension-python-roots-drift') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            alt_python_root = fixture.package_root / 'alt_python'
            (alt_python_root / 'agent_probe_alt').mkdir(parents=True, exist_ok=True)
            (alt_python_root / 'agent_probe_alt' / '__init__.py').write_text('', encoding='utf-8')
            index_path = repo_root / 'agent' / 'extensions' / 'index.json'
            payload = json.loads(index_path.read_text(encoding='utf-8'))
            payload['extensions'][0]['pythonRoots'] = [str(alt_python_root.relative_to(repo_root).as_posix())]
            self._write_json(index_path, payload)

            with self.assertRaisesRegex(ManagedExtensionError, 'pythonRoots must use extension contract path'):
                load_managed_extensions_index(repo_root)

    def test_validation_detects_profile_enablement_drift(self) -> None:
        with isolated_test_root('managed-extension-profile-drift') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            payload = json.loads(fixture.service_path.read_text(encoding='utf-8'))
            payload['extensions']['enabledExtensionIds'] = ['agent_platform']
            self._write_json(fixture.service_path, payload)

            issues = validate_managed_explicit_extension_index(repo_root)

        self.assertTrue(any('does not enable extension id' in issue for issue in issues), msg=issues)

    def test_validation_requires_profile_enable_agent_platform(self) -> None:
        with isolated_test_root('managed-extension-profile-platform-drift') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            payload = json.loads(fixture.service_path.read_text(encoding='utf-8'))
            payload['extensions']['enabledExtensionIds'] = [fixture.extension_id]
            self._write_json(fixture.service_path, payload)

            issues = validate_managed_explicit_extension_index(repo_root)

        self.assertTrue(any('does not enable agent_platform' in issue for issue in issues), msg=issues)

    def test_validation_rejects_extra_default_profile_enabled_extension(self) -> None:
        with isolated_test_root('managed-extension-profile-extra-enabled') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            payload = json.loads(fixture.service_path.read_text(encoding='utf-8'))
            payload['extensions']['enabledExtensionIds'] = ['agent_platform', fixture.extension_id, 'shadow_extension']
            self._write_json(fixture.service_path, payload)

            issues = validate_managed_explicit_extension_index(repo_root)

        self.assertTrue(any('may only enable agent_platform and own extension id' in issue for issue in issues), msg=issues)

    def test_validation_requires_profile_load_own_manifest_dir(self) -> None:
        with isolated_test_root('managed-extension-profile-manifest-dir-drift') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            payload = json.loads(fixture.service_path.read_text(encoding='utf-8'))
            payload['extensions']['manifestsDirs'] = ['@repo/config/control_plane/extensions.d']
            self._write_json(fixture.service_path, payload)

            issues = validate_managed_explicit_extension_index(repo_root)

        self.assertTrue(any('must load own manifest dir' in issue for issue in issues), msg=issues)

    def test_validation_rejects_extra_default_profile_manifest_dir(self) -> None:
        with isolated_test_root('managed-extension-profile-extra-manifest-dir') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            payload = json.loads(fixture.service_path.read_text(encoding='utf-8'))
            payload['extensions']['manifestsDirs'] = [
                '@repo/config/control_plane/extensions.d',
                '@extension/config/control_plane/extensions.d',
                '@repo/config/runtime',
            ]
            self._write_json(fixture.service_path, payload)

            issues = validate_managed_explicit_extension_index(repo_root)

        self.assertTrue(any('may only load platform and own manifest dirs' in issue for issue in issues), msg=issues)

    def test_validation_rejects_manifest_registry_paths_outside_extension_root(self) -> None:
        with isolated_test_root('managed-extension-registry-path-escape') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            (repo_root / 'config' / 'control_plane' / 'jobs').mkdir(parents=True, exist_ok=True)
            payload = json.loads(fixture.manifest_path.read_text(encoding='utf-8'))
            payload['registry']['jobsDirs'] = ['@repo/config/control_plane/jobs']
            self._write_json(fixture.manifest_path, payload)

            issues = validate_managed_explicit_extension_index(repo_root)

        self.assertTrue(any('manifest registry.jobsDirs[0] escapes extension root' in issue for issue in issues), msg=issues)

    def test_validation_rejects_manifest_fragment_paths_outside_extension_root(self) -> None:
        with isolated_test_root('managed-extension-fragment-path-escape') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            payload = json.loads(fixture.manifest_path.read_text(encoding='utf-8'))
            payload['surfaceFragments']['runtimePathsPath'] = '@repo/config/runtime/paths.json'
            self._write_json(fixture.manifest_path, payload)

            issues = validate_managed_explicit_extension_index(repo_root)

        self.assertTrue(any('manifest surfaceFragments.runtimePathsPath escapes extension root' in issue for issue in issues), msg=issues)

    def test_validation_detects_manifest_id_drift(self) -> None:
        with isolated_test_root('managed-extension-manifest-drift') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            row = representative_managed_extension(repo_root)
            manifest_path = managed_extension_manifest_path(row)
            payload = json.loads(manifest_path.read_text(encoding='utf-8'))
            payload['id'] = 'agent_probe_drifted'
            self._write_json(manifest_path, payload)

            issues = validate_managed_explicit_extension_index(repo_root)

        self.assertTrue(any('manifest id mismatch' in issue for issue in issues), msg=issues)

    def test_validation_requires_extension_test_package_bytecode_guard(self) -> None:
        with isolated_test_root('managed-extension-tests-bytecode-guard') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            unit_dir = fixture.package_root / 'tests' / 'unit'
            unit_dir.mkdir(parents=True)
            (unit_dir / 'test_probe.py').write_text('import unittest\n', encoding='utf-8')

            issues = validate_managed_explicit_extension_index(repo_root)

        self.assertTrue(any('tests package marker missing bytecode guard' in issue for issue in issues), msg=issues)
        self.assertTrue(any('/tests/__init__.py' in issue.replace('\\', '/') for issue in issues), msg=issues)
        self.assertTrue(any('/tests/unit/__init__.py' in issue.replace('\\', '/') for issue in issues), msg=issues)

    def test_validation_rejects_empty_extension_test_package_markers(self) -> None:
        with isolated_test_root('managed-extension-tests-empty-bytecode-guard') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            tests_dir = fixture.package_root / 'tests'
            unit_dir = tests_dir / 'unit'
            unit_dir.mkdir(parents=True)
            (tests_dir / '__init__.py').write_text('', encoding='utf-8')
            (unit_dir / '__init__.py').write_text('', encoding='utf-8')
            (unit_dir / 'test_probe.py').write_text('import unittest\n', encoding='utf-8')

            issues = validate_managed_explicit_extension_index(repo_root)

        self.assertTrue(any('tests package marker must disable and clean bytecode cache' in issue for issue in issues), msg=issues)
        self.assertTrue(any('/tests/__init__.py' in issue.replace('\\', '/') for issue in issues), msg=issues)
        self.assertTrue(any('/tests/unit/__init__.py' in issue.replace('\\', '/') for issue in issues), msg=issues)

    def test_validation_rejects_empty_extension_python_package_marker(self) -> None:
        with isolated_test_root('managed-extension-python-empty-bytecode-guard') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            (fixture.python_package_dir / '__init__.py').write_text('', encoding='utf-8')

            issues = validate_managed_explicit_extension_index(repo_root)

        self.assertTrue(any('python package marker must disable and clean bytecode cache' in issue for issue in issues), msg=issues)
        self.assertTrue(any('/python/openclaw_ext_probe/__init__.py' in issue.replace('\\', '/') for issue in issues), msg=issues)

    def test_extension_python_package_guard_prevents_direct_import_bytecode_residue(self) -> None:
        with isolated_test_root('managed-extension-python-bytecode-runtime-guard') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            env = dict(os.environ)
            env.pop('PYTHONDONTWRITEBYTECODE', None)
            env['PYTHONPATH'] = str(fixture.python_root)

            subprocess.run(
                [sys.executable, '-c', f'import {PROBE_PACKAGE_NAME}'],
                cwd=repo_root,
                env=env,
                check=True,
                text=True,
                capture_output=True,
            )

            residue = sorted(path.relative_to(repo_root).as_posix() for path in fixture.python_package_dir.rglob('__pycache__'))
            self.assertEqual([], residue)

    def test_index_path_accepts_explicit_minimal_repo_root_without_repo_markers(self) -> None:
        with isolated_test_root('managed-extension-minimal-root') as repo_root:
            index_path = managed_extensions_index_path(repo_root)

        self.assertEqual(index_path, (repo_root / 'agent' / 'extensions' / 'index.json').resolve())


if __name__ == '__main__':
    unittest.main()
