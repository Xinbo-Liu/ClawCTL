from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.tests.support.managed_extensions import managed_extensions


ROOT_DIR = resolve_repo_root(Path(__file__))
MANAGED_EXTENSIONS = tuple(sorted(managed_extensions(ROOT_DIR), key=lambda row: row.id))
MANAGED_EXTENSION = MANAGED_EXTENSIONS[0] if MANAGED_EXTENSIONS else None
AGENT_PLATFORM_CONFIG = (ROOT_DIR / 'config' / 'control_plane' / 'profiles' / 'agent_platform.service.json').resolve()


class CliSurfaceSmokeTest(unittest.TestCase):
    def _base_env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = dict(os.environ)
        for key in (
            'HOST_STATE_DIR',
            'OPENCLAW_CONFIG_PATH',
            'OPENCLAW_CONTROL_PLANE_PROFILE',
            'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH',
            'OPENCLAW_OFFICIAL_GATEWAY_IMAGE',
            'OPENCLAW_RUNTIME_PATH_VIEW',
            'OPENCLAW_STATE_DIR',
            'PYTHONHOME',
            'PYTHONPATH',
        ):
            env.pop(key, None)
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        env['PYTHONPATH'] = str((ROOT_DIR / 'python').resolve())
        env['PYTHONUTF8'] = '1'
        if extra:
            env.update(extra)
        return env

    def _run_cli(self, *args: str, env: dict[str, str] | None = None) -> tuple[subprocess.CompletedProcess[str], str]:
        result = subprocess.run(
            [sys.executable, '-B', '-m', 'openclaw.cli', *args],
            cwd=ROOT_DIR,
            env=self._base_env(env),
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            check=False,
        )
        output = '\n'.join(part for part in (result.stdout, result.stderr) if part)
        return result, output

    def _run_cli_in_process(self, *args: str, env: dict[str, str] | None = None) -> tuple[int, str]:
        from openclaw.cli import main as cli_main

        stdout = io.StringIO()
        stderr = io.StringIO()
        exit_code = 0
        with mock.patch.dict(os.environ, self._base_env(env), clear=True):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                try:
                    exit_code = int(cli_main(list(args)) or 0)
                except SystemExit as exc:
                    exit_code = int(exc.code) if isinstance(exc.code, int) else 1
                    if isinstance(exc.code, str):
                        stderr.write(exc.code)
        output = '\n'.join(part for part in (stdout.getvalue(), stderr.getvalue()) if part)
        return exit_code, output

    def test_images_check_overlay_contract_smoke(self) -> None:
        contract = json.loads((ROOT_DIR / 'config' / 'upstream' / 'overlay_contract.json').read_text(encoding='utf-8'))
        allowed_repo = str((contract.get('allowed_base_image_repositories') or [])[0])
        result, output = self._run_cli(
            'images',
            'check-overlay-contract',
            env={'OPENCLAW_OFFICIAL_GATEWAY_IMAGE': f'{allowed_repo}:smoke'},
        )

        self.assertEqual(result.returncode, 0, msg=output)
        self.assertIn('overlay contract passed', result.stdout)

    def test_release_bundles_list_and_manifest_smoke(self) -> None:
        exit_code, output = self._run_cli_in_process('runtime', 'release', 'list-bundles')
        self.assertEqual(exit_code, 0, msg=output)
        bundles_payload = json.loads(output)
        bundle_ids = {str(row.get('bundle') or '') for row in bundles_payload.get('bundles') or []}
        self.assertIn('runtime-core', bundle_ids)

        exit_code, output = self._run_cli_in_process('runtime', 'release', 'manifest', '--bundle', 'runtime-core')
        self.assertEqual(exit_code, 0, msg=output)
        manifest_payload = json.loads(output)
        self.assertEqual(manifest_payload['bundle'], 'runtime-core')
        self.assertTrue(manifest_payload['resolvedFiles'])

    def test_release_bundles_manifest_rejects_unknown_bundle(self) -> None:
        exit_code, output = self._run_cli_in_process('runtime', 'release', 'manifest', '--bundle', 'missing-bundle')

        self.assertNotEqual(exit_code, 0)
        self.assertIn('unknown bundle: missing-bundle', output)

    def test_deploy_env_docs_render_deployment_inputs_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / 'deployment-inputs.md'
            result, output = self._run_cli(
                'setup',
                'env',
                'docs',
                'render-deployment-inputs',
                '--output',
                str(output_path),
            )
            self.assertEqual(result.returncode, 0, msg=output)
            self.assertTrue(output_path.is_file())
            self.assertIn('# 部署输入说明', output_path.read_text(encoding='utf-8'))

    def test_deploy_env_docs_render_site_env_example_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / 'site.env.example'
            result, output = self._run_cli(
                'setup',
                'env',
                'docs',
                'render-site-env-example',
                '--output',
                str(output_path),
            )
            self.assertEqual(result.returncode, 0, msg=output)
            rendered = output_path.read_text(encoding='utf-8')
            self.assertIn('OPENCLAW_CONTROL_PLANE_PROFILE=agent_platform', rendered)
            if MANAGED_EXTENSION is not None:
                self.assertNotIn(f'OPENCLAW_CONTROL_PLANE_PROFILE={MANAGED_EXTENSION.id}', rendered)

    def test_docs_renderers_accept_control_plane_profile_selection(self) -> None:
        for command in ('render-getting-started', 'render-runtime-surface', 'render-maintenance-map'):
            with self.subTest(command=command):
                exit_code, output = self._run_cli_in_process(
                    'docs',
                    command,
                    '--check',
                    '--control-plane-profile',
                    'agent_platform',
                )
                self.assertEqual(exit_code, 0, msg=output)

    def test_docs_renderer_config_path_selection_accepts_container_path(self) -> None:
        exit_code, output = self._run_cli_in_process(
            'docs',
            'render-runtime-surface',
            '--check',
            '--config-path',
            '/opt/openclaw-tools/config/control_plane/profiles/agent_platform.service.json',
        )

        self.assertEqual(exit_code, 0, msg=output)

    def test_runtime_paths_show_index_smoke(self) -> None:
        exit_code, output = self._run_cli_in_process('runtime', 'paths', 'show-index')

        self.assertEqual(exit_code, 0, msg=output)
        payload = json.loads(output)
        self.assertTrue(payload['views'])
        self.assertTrue(payload['entries'])

    def test_top_level_help_smoke(self) -> None:
        result, output = self._run_cli('--help')

        self.assertEqual(result.returncode, 0, msg=output)
        self.assertIn('control-plane', result.stdout)
        self.assertIn('runtime', result.stdout)

    def test_control_plane_group_help_smoke(self) -> None:
        exit_code, output = self._run_cli_in_process('control-plane', 'summary', '--help')

        self.assertEqual(exit_code, 0, msg=output)
        self.assertIn('overview', output)
        self.assertIn('agent-groups', output)

        exit_code, output = self._run_cli_in_process('control-plane', 'validate', '--help')
        self.assertEqual(exit_code, 0, msg=output)
        self.assertIn('registry', output)
        self.assertIn('agent-assembly', output)

    def test_extension_env_old_split_subcommands_are_not_registered(self) -> None:
        for command in ('prepare', 'sync-wheelhouse'):
            with self.subTest(command=command):
                exit_code, output = self._run_cli_in_process(
                    'control-plane',
                    'runtime',
                    'extension-env',
                    command,
                    '--extension',
                    'agent_probe',
                )

                self.assertEqual(exit_code, 2, msg=output)
                self.assertIn('invalid choice', output)
                self.assertIn('{ensure,status,verify,prune}', output)

    def test_control_plane_facts_overview_json_smoke(self) -> None:
        exit_code, output = self._run_cli_in_process(
            'control-plane',
            'facts',
            'overview',
            '--format',
            'json',
            '--no-local-probe',
        )

        self.assertEqual(exit_code, 0, msg=output)
        payload = json.loads(output)
        self.assertEqual(payload['schema_version'], 1)
        self.assertEqual(payload['selected_config']['profile_id'], 'agent_platform')
        self.assertIn('runtime_services', payload)

    def test_control_plane_config_path_selection_is_position_tolerant(self) -> None:
        placements = [
            ('--config-path', str(AGENT_PLATFORM_CONFIG), 'control-plane', 'summary', 'models'),
            ('control-plane', '--config-path', str(AGENT_PLATFORM_CONFIG), 'summary', 'models'),
            ('control-plane', 'summary', '--config-path', str(AGENT_PLATFORM_CONFIG), 'models'),
            ('control-plane', 'summary', 'models', '--config-path', str(AGENT_PLATFORM_CONFIG)),
        ]
        expected_target = 'openclaw.control_plane.cli:summary_entry'
        seen: list[tuple[str, list[str]]] = []

        def fake_run_target(target: str, argv: list[str]) -> int:
            seen.append((target, list(argv)))
            return 0

        for args in placements:
            with self.subTest(args=args):
                seen.clear()
                with mock.patch('openclaw.cli.run_target', side_effect=fake_run_target):
                    exit_code, output = self._run_cli_in_process(*args)
                self.assertEqual(exit_code, 0, msg=output)
                self.assertEqual(len(seen), 1)
                self.assertEqual(seen[0][0], expected_target)
                self.assertIn('--config-path', seen[0][1])
                self.assertIn(str(AGENT_PLATFORM_CONFIG), seen[0][1])
                self.assertIn('models', seen[0][1])

    def test_control_plane_profile_selection_smoke(self) -> None:
        exit_code, output = self._run_cli_in_process(
            'control-plane',
            'summary',
            '--control-plane-profile',
            'agent_platform',
            'models',
        )

        self.assertEqual(exit_code, 0, msg=output)
        payload = json.loads(output)
        self.assertEqual(len(payload.get('items') or []), 0)


if __name__ == '__main__':
    unittest.main()
