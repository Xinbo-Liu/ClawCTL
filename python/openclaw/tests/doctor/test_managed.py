from __future__ import annotations

import json
import unittest
from pathlib import Path

from openclaw.doctor.agent_modules.smoke_tests import build_suite, discover_test_files, load_test_module
from openclaw.doctor.agent_modules.runtime_script_orphans import build_orphan_report, repo_text_files
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.tests.support.helpers import isolated_test_root


ROOT_DIR = resolve_repo_root(Path(__file__))


class ManagedDoctorSurfaceTest(unittest.TestCase):
    def _write_json(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def _write_workspace_policy_truth(self, repo_root: Path) -> None:
        support_dir = repo_root / 'config' / 'governance' / 'support'
        support_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(support_dir / 'install_defaults.json', {'defaults': {'host_state_root': 'state/openclaw'}})
        (support_dir / 'local_workspace_policy.json').write_text(
            (ROOT_DIR / 'config' / 'governance' / 'support' / 'local_workspace_policy.json').read_text(encoding='utf-8'),
            encoding='utf-8',
        )

    def _materialize_repo(self, extension_ids: tuple[str, ...] = ('agent_probe',)) -> tuple[Path, dict[str, Path]]:
        context = isolated_test_root('managed-doctors')
        repo_root = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

        (repo_root / 'python' / 'openclaw').mkdir(parents=True)
        (repo_root / 'python' / 'openclaw' / '__init__.py').write_text('', encoding='utf-8')
        self._write_json(repo_root / 'config' / 'runtime' / 'paths.json', {})
        self._write_json(repo_root / 'config' / 'control_plane' / 'service.json', {})

        package_roots: dict[str, Path] = {}
        index_rows: list[dict[str, object]] = []
        for extension_id in extension_ids:
            package_root = repo_root / 'agent' / 'extensions' / extension_id
            (package_root / 'config' / 'control_plane' / 'profiles').mkdir(parents=True)
            (package_root / 'config' / 'control_plane' / 'extensions.d').mkdir(parents=True)
            package_roots[extension_id] = package_root
            index_rows.append(
                {
                    'id': extension_id,
                    'title': f'{extension_id} Managed Extension',
                    'rootDir': f'agent/extensions/{extension_id}',
                    'defaultServiceConfigPath': f'agent/extensions/{extension_id}/config/control_plane/profiles/{extension_id}.service.json',
                    'manifestDir': f'agent/extensions/{extension_id}/config/control_plane/extensions.d',
                    'pythonRoots': [f'agent/extensions/{extension_id}/python'],
                    'status': 'managed_explicit_extension',
                }
            )
            self._write_json(package_root / 'config' / 'control_plane' / 'profiles' / f'{extension_id}.service.json', {})
        self._write_json(repo_root / 'agent' / 'extensions' / 'index.json', {'extensions': index_rows})
        return repo_root, package_roots

    def test_smoke_test_discovery_only_scans_managed_extension_test_roots(self) -> None:
        repo_root, package_roots = self._materialize_repo(('agent_probe', 'agent_shadow'))
        managed_test = package_roots['agent_probe'] / 'tests' / 'modules' / 'alpha_probe' / 'test_smoke.py'
        managed_test.parent.mkdir(parents=True, exist_ok=True)
        managed_test.write_text('import unittest\n', encoding='utf-8')
        self.assertFalse((managed_test.parent / '__init__.py').exists())
        secondary_test = package_roots['agent_shadow'] / 'tests' / 'modules' / 'beta_probe' / 'test_smoke.py'
        secondary_test.parent.mkdir(parents=True, exist_ok=True)
        secondary_test.write_text('import unittest\n', encoding='utf-8')

        blocked_test = repo_root / 'agent' / 'modules' / 'blocked_probe' / 'tests' / 'test_blocked.py'
        blocked_test.parent.mkdir(parents=True, exist_ok=True)
        blocked_test.write_text('import unittest\n', encoding='utf-8')

        files = discover_test_files(repo_root=repo_root)

        self.assertEqual(
            [path.relative_to(repo_root).as_posix() for path in files],
            [
                'agent/extensions/agent_probe/tests/modules/alpha_probe/test_smoke.py',
                'agent/extensions/agent_shadow/tests/modules/beta_probe/test_smoke.py',
            ],
        )

        scoped_files = discover_test_files(repo_root=repo_root, extension_id='agent_probe')
        self.assertEqual(
            [path.relative_to(repo_root).as_posix() for path in scoped_files],
            [
                'agent/extensions/agent_probe/tests/modules/alpha_probe/test_smoke.py',
            ],
        )

    def test_smoke_loader_initializes_extension_support_namespace(self) -> None:
        repo_root, package_roots = self._materialize_repo(('agent_probe',))
        support_file = package_roots['agent_probe'] / 'tests' / 'support' / 'helper.py'
        support_file.parent.mkdir(parents=True, exist_ok=True)
        support_file.write_text('VALUE = 7\n', encoding='utf-8')
        managed_test = package_roots['agent_probe'] / 'tests' / 'modules' / 'alpha_probe' / 'test_smoke.py'
        managed_test.parent.mkdir(parents=True, exist_ok=True)
        managed_test.write_text(
            '\n'.join(
                [
                    'from __future__ import annotations',
                    '',
                    'import unittest',
                    '',
                    'from support.helper import VALUE',
                    '',
                    '',
                    'class SupportImportSmokeTest(unittest.TestCase):',
                    '    def test_support_import(self) -> None:',
                    '        self.assertEqual(VALUE, 7)',
                    '',
                ]
            ),
            encoding='utf-8',
        )

        module = load_test_module(managed_test, repo_root=repo_root)
        suite = unittest.defaultTestLoader.loadTestsFromModule(module)
        result = unittest.TestResult()
        suite.run(result)

        self.assertEqual(result.errors, [])
        self.assertEqual(result.failures, [])

    def test_smoke_suite_rebinds_support_namespace_per_extension_module(self) -> None:
        repo_root, package_roots = self._materialize_repo(('agent_probe', 'agent_shadow'))
        for extension_id, package_root in package_roots.items():
            support_file = package_root / 'tests' / 'support' / 'helper.py'
            support_file.parent.mkdir(parents=True, exist_ok=True)
            support_file.write_text(f"VALUE = '{extension_id}'\n", encoding='utf-8')
            managed_test = package_root / 'tests' / 'modules' / f'{extension_id}_module' / 'test_smoke.py'
            managed_test.parent.mkdir(parents=True, exist_ok=True)
            managed_test.write_text(
                '\n'.join(
                    [
                        'from __future__ import annotations',
                        '',
                        'import unittest',
                        '',
                        '',
                        'class LazySupportImportSmokeTest(unittest.TestCase):',
                        '    def test_lazy_support_import(self) -> None:',
                        '        from support.helper import VALUE',
                        f"        self.assertEqual(VALUE, '{extension_id}')",
                        '',
                    ]
                ),
                encoding='utf-8',
            )

        suite = build_suite(repo_root=repo_root)
        result = unittest.TestResult()
        suite.run(result)

        self.assertEqual(result.errors, [])
        self.assertEqual(result.failures, [])

    def test_runtime_orphan_report_counts_managed_roots(self) -> None:
        repo_root, package_roots = self._materialize_repo(('agent_probe', 'agent_shadow'))
        script_path = package_roots['agent_probe'] / 'agent' / 'modules' / 'alpha_probe' / 'bin' / 'alpha_probe'
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text('#!/usr/bin/env bash\n', encoding='utf-8')
        (package_roots['agent_probe'] / 'agent' / 'modules' / 'alpha_probe' / 'README.md').write_text(
            'agent/extensions/agent_probe/agent/modules/alpha_probe/bin/alpha_probe\n',
            encoding='utf-8',
        )
        secondary_script = package_roots['agent_shadow'] / 'agent' / 'modules' / 'beta_probe' / 'bin' / 'beta_probe'
        secondary_script.parent.mkdir(parents=True, exist_ok=True)
        secondary_script.write_text('#!/usr/bin/env bash\n', encoding='utf-8')
        (package_roots['agent_shadow'] / 'agent' / 'modules' / 'beta_probe' / 'README.md').write_text(
            'agent/extensions/agent_shadow/agent/modules/beta_probe/bin/beta_probe\n',
            encoding='utf-8',
        )

        payload = build_orphan_report(repo_root)

        self.assertEqual(payload['scanRootCount'], 2)
        self.assertEqual(
            payload['scanRoots'],
            [
                'agent/extensions/agent_probe/agent/modules',
                'agent/extensions/agent_shadow/agent/modules',
            ],
        )
        self.assertEqual(payload['count'], 2)
        self.assertEqual(payload['orphanCount'], 0)
        self.assertEqual(payload['orphanScripts'], [])

        scoped_payload = build_orphan_report(repo_root, extension_id='agent_probe')
        self.assertEqual(scoped_payload['extensionId'], 'agent_probe')
        self.assertEqual(scoped_payload['scanRootCount'], 1)
        self.assertEqual(scoped_payload['scanRoots'], ['agent/extensions/agent_probe/agent/modules'])
        self.assertEqual(scoped_payload['count'], 1)
        self.assertEqual(scoped_payload['orphanCount'], 0)

    def test_runtime_orphan_corpus_skips_workspace_policy_targets_and_binary_residue(self) -> None:
        repo_root, package_roots = self._materialize_repo(('agent_probe',))
        self._write_workspace_policy_truth(repo_root)
        script_path = package_roots['agent_probe'] / 'agent' / 'modules' / 'alpha_probe' / 'bin' / 'alpha_probe'
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text('#!/usr/bin/env bash\n', encoding='utf-8')
        (package_roots['agent_probe'] / 'agent' / 'modules' / 'alpha_probe' / 'README.md').write_text(
            'agent/extensions/agent_probe/agent/modules/alpha_probe/bin/alpha_probe\n',
            encoding='utf-8',
        )
        runtime_blob = repo_root / 'state' / 'openclaw' / 'gateway' / 'plugin-runtime-deps' / 'pkg' / 'dist' / 'bundle.dat'
        runtime_blob.parent.mkdir(parents=True, exist_ok=True)
        runtime_blob.write_bytes(b'\xff\xfe\x00runtime')
        outside_blob = repo_root / 'tmp-outside-binary.dat'
        outside_blob.write_bytes(b'\xff\xfe\x00outside')

        corpus = {path.relative_to(repo_root).as_posix() for path in repo_text_files(repo_root)}
        payload = build_orphan_report(repo_root, extension_id='agent_probe')

        self.assertNotIn('state/openclaw/gateway/plugin-runtime-deps/pkg/dist/bundle.dat', corpus)
        self.assertIn('tmp-outside-binary.dat', corpus)
        self.assertEqual(payload['orphanScripts'], [])
