from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from openclaw import cli as root_cli
from openclaw.control_plane.extensions.api import enabled_extensions_from_config
from openclaw.control_plane.extensions.normalization import ExtensionError
from openclaw.doctor.agent_modules.managed_probe_fixture_repo_markers import ensure_repo_markers
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.testing.bootstrap_support import prepend_sys_path_entries


ROOT_DIR = resolve_repo_root(Path(__file__))


class ExternalExtensionFixtureTest(unittest.TestCase):
    def _write_json(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    def test_nonstandard_external_extension_is_rejected_by_explicit_config_path(self) -> None:
        with TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / 'repo'
            ensure_repo_markers(repo_root, ROOT_DIR)
            manifests_dir = repo_root / 'external_ext'
            manifests_dir.mkdir(parents=True)
            self._write_json(
                manifests_dir / 'external_fixture.json',
                {
                    'id': 'external_fixture',
                    'title': 'External Fixture',
                    'cliCommands': [
                        {
                            'command': 'external-fixture',
                            'module': 'openclaw.cli_registry',
                        }
                    ],
                },
            )
            service_path = repo_root / 'external.service.json'
            self._write_json(
                service_path,
                {
                    'extends': '@repo/config/control_plane/service.json',
                    'extensions': {
                        'manifestsDirs': [
                            '@repo/config/control_plane/extensions.d',
                            str(manifests_dir),
                        ],
                        'enabledExtensionIds': ['agent_platform', 'external_fixture'],
                    },
                },
            )

            with self.assertRaisesRegex(ExtensionError, 'repository contract'):
                enabled_extensions_from_config(service_path)

    def test_managed_extension_command_only_runs_under_control_plane_extension_namespace(self) -> None:
        with TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / 'repo'
            ensure_repo_markers(repo_root, ROOT_DIR)
            extension_id = 'agent_probe'
            extension_root = repo_root / 'agent' / 'extensions' / extension_id
            service_path = extension_root / 'config' / 'control_plane' / 'profiles' / f'{extension_id}.service.json'
            manifest_dir = extension_root / 'config' / 'control_plane' / 'extensions.d'
            manifest_path = manifest_dir / f'{extension_id}.json'
            python_root = extension_root / 'python'
            package_root = python_root / 'openclaw_ext_probe_cli_smoke'
            module_path = package_root / '_cli_smoke.py'
            package_root.mkdir(parents=True)
            service_path.parent.mkdir(parents=True)
            (package_root / '__init__.py').write_text('', encoding='utf-8')
            module_path.write_text('def main(argv):\n    print("managed cli smoke")\n    return 0\n', encoding='utf-8')
            manifest_dir.mkdir(parents=True)
            self._write_json(
                manifest_path,
                {
                    'id': extension_id,
                    'title': 'Probe CLI Smoke',
                    'cliCommands': [
                        {
                            'command': 'managed-cli-smoke',
                            'module': 'openclaw_ext_probe_cli_smoke._cli_smoke',
                        }
                    ],
                },
            )
            self._write_json(
                service_path,
                {
                    'extends': '@repo/config/control_plane/service.json',
                    'extensions': {
                        'manifestsDirs': [
                            '@repo/config/control_plane/extensions.d',
                            '@extension/config/control_plane/extensions.d',
                        ],
                        'enabledExtensionIds': ['agent_platform', extension_id],
                    },
                },
            )
            env = {
                'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH': str(service_path),
                'OPENCLAW_REPO_ROOT': str(repo_root),
            }
            original_sys_path = list(sys.path)
            stdout = io.StringIO()
            stderr = io.StringIO()
            direct_stdout = io.StringIO()
            direct_stderr = io.StringIO()
            try:
                prepend_sys_path_entries([python_root])
                with mock.patch.dict(os.environ, env, clear=False):
                    with contextlib.redirect_stdout(direct_stdout), contextlib.redirect_stderr(direct_stderr):
                        with self.assertRaises(SystemExit) as direct_error:
                            root_cli.main([
                                'managed-cli-smoke',
                                '--config-path',
                                str(service_path),
                            ])
                    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                        return_code = root_cli.main([
                            'control-plane',
                            'extension',
                            'managed-cli-smoke',
                            '--config-path',
                            str(service_path),
                        ])
            finally:
                sys.path[:] = original_sys_path
                sys.modules.pop('openclaw_ext_probe_cli_smoke._cli_smoke', None)
                sys.modules.pop('openclaw_ext_probe_cli_smoke', None)

        self.assertEqual(direct_error.exception.code, 2)
        self.assertIn('未知命令：managed-cli-smoke', direct_stderr.getvalue())
        self.assertEqual(return_code, 0, msg=stderr.getvalue() or stdout.getvalue())
        self.assertIn('managed cli smoke', stdout.getvalue())


if __name__ == '__main__':
    unittest.main()
