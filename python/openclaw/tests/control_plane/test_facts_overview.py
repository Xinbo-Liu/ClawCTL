from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from openclaw.control_plane import facts
from openclaw.doctor.platform.architecture_import_guards import business_name_leak_tokens
from openclaw.lib.repo.layout import resolve_repo_root


ROOT_DIR = resolve_repo_root(Path(__file__))


class FactsOverviewTest(unittest.TestCase):
    _payload: dict[str, object] | None = None
    _all_profiles_payload: dict[str, object] | None = None

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._payload = facts.build_overview_payload(root_dir=ROOT_DIR, probe_local=False)
        cls._all_profiles_payload = facts.build_overview_payload(
            root_dir=ROOT_DIR,
            probe_local=False,
            include_all_profiles=True,
        )

    @classmethod
    def payload(cls) -> dict[str, object]:
        if cls._payload is None:
            cls._payload = facts.build_overview_payload(root_dir=ROOT_DIR, probe_local=False)
        return cls._payload

    @classmethod
    def all_profiles_payload(cls) -> dict[str, object]:
        if cls._all_profiles_payload is None:
            cls._all_profiles_payload = facts.build_overview_payload(
                root_dir=ROOT_DIR,
                probe_local=False,
                include_all_profiles=True,
            )
        return cls._all_profiles_payload

    def test_payload_exposes_stable_top_level_schema(self) -> None:
        payload = self.payload()

        self.assertEqual(
            list(payload),
            [
                'schema_version',
                'selected_config',
                'profiles',
                'extensions',
                'truth_surfaces',
                'generated_artifacts',
                'scripts',
                'runtime_services',
                'evidence',
                'local_environment',
            ],
        )
        self.assertEqual(payload['schema_version'], 1)
        self.assertEqual(payload['selected_config']['profile_id'], 'agent_platform')
        self.assertEqual(
            payload['selected_config']['config_relpath'],
            'config/control_plane/profiles/agent_platform.service.json',
        )
        self.assertIn('agent_platform', payload['extensions']['enabled_extension_ids'])
        self.assertIn('jobs_dirs', payload['extensions']['registry_inputs'])
        self.assertTrue(payload['runtime_services'])

    def test_profile_selection_uses_profile_registry_truth(self) -> None:
        payload = facts.build_overview_payload(
            root_dir=ROOT_DIR,
            control_plane_profile='base',
            probe_local=False,
        )

        self.assertEqual(payload['selected_config']['profile_id'], 'base')
        self.assertEqual(payload['selected_config']['config_relpath'], 'config/control_plane/service.json')
        self.assertTrue(
            any(row['id'] == 'base' and row['status'] == 'valid' for row in payload['profiles']['items'])
        )

    def test_all_profiles_payload_exposes_profile_deltas_and_verification_commands(self) -> None:
        payload = self.all_profiles_payload()

        self.assertIn('profile_overviews', payload)
        self.assertIn('verification_commands', payload)
        by_id = {row['id']: row for row in payload['profile_overviews']}
        self.assertTrue(by_id['agent_platform']['default_profile'])
        self.assertEqual(by_id['agent_platform']['enabled_extension_ids'], ['agent_platform'])
        managed_ids = {
            row['id']
            for row in payload['extensions']['managed_explicit']
            if isinstance(row, dict) and row.get('id')
        }
        managed_profiles = [row for row in payload['profile_overviews'] if row['id'] in managed_ids]
        self.assertEqual(len(managed_profiles), len(managed_ids))
        if managed_ids:
            for row in managed_profiles:
                self.assertIn(row['id'], row['enabled_extension_ids'])
            self.assertTrue(any(row['registry_input_counts']['agent_modules_dirs'] for row in managed_profiles))
        commands = '\n'.join(
            command
            for group in payload['verification_commands']
            for command in group.get('commands', [])
        )
        self.assertIn('--all-profiles --format json', commands)
        self.assertIn('scripts/testing/check_repo_test_readiness.sh', commands)
        verification_by_id = {group['id']: group for group in payload['verification_commands']}
        self.assertIn('official_release', verification_by_id)
        self.assertIn('host_diagnostic', verification_by_id)
        self.assertNotIn(''.join(('v', 'm_formal')), verification_by_id)
        self.assertEqual(verification_by_id['official_release']['title'], '正式 Docker / 控制面容器门禁')
        self.assertTrue(verification_by_id['official_release']['release_required'])
        self.assertTrue(verification_by_id['host_diagnostic']['diagnostic_only'])

    def test_env_file_probe_reports_keys_without_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / 'private.env'
            env_path.write_text(
                '\n'.join(
                    [
                        'OPENCLAW_GATEWAY_TOKEN=super-secret-value',
                        'OPENCLAW_MODE=plain-value',
                        'EMPTY_VALUE=',
                    ]
                ),
                encoding='utf-8',
            )

            payload = facts.build_overview_payload(root_dir=ROOT_DIR, env_file=env_path, probe_local=True)

        encoded = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn('super-secret-value', encoded)
        self.assertNotIn('plain-value', encoded)
        selected_env = payload['local_environment']['selected_env_file']
        self.assertEqual(selected_env['key_count'], 3)
        self.assertEqual(selected_env['sensitive_key_count'], 1)
        self.assertTrue(selected_env['private_gitignored'])
        by_name = {row['name']: row for row in selected_env['keys']}
        self.assertTrue(by_name['OPENCLAW_GATEWAY_TOKEN']['sensitive'])
        self.assertTrue(by_name['OPENCLAW_MODE']['value_present'])
        self.assertFalse(by_name['EMPTY_VALUE']['value_present'])

    def test_markdown_renderer_uses_relative_evidence_paths(self) -> None:
        payload = self.payload()
        rendered = facts.render_overview_markdown(payload)

        self.assertIn('# OpenClaw 维护事实总览', rendered)
        self.assertIn('control-plane facts overview', rendered)
        self.assertIn('## Registry 输入', rendered)
        self.assertIn('state/openclaw/control_plane/setup/deployment_acceptance.json', rendered)
        self.assertNotIn(str(ROOT_DIR), rendered)
        for token in business_name_leak_tokens(ROOT_DIR):
            self.assertNotIn(token, rendered)

    def test_all_profiles_maintenance_markdown_redacts_managed_business_names(self) -> None:
        if not self.all_profiles_payload()['extensions']['managed_explicit']:
            self.skipTest('base release surface has no repo-managed extension')
        payload = self.all_profiles_payload()
        rendered = facts.render_overview_markdown(payload, redact_managed_extensions=True)

        self.assertIn('managed-extension-', rendered)
        for token in business_name_leak_tokens(ROOT_DIR):
            self.assertNotIn(token, rendered)

    def test_redacted_managed_profile_markdown_does_not_emit_fake_repo_paths(self) -> None:
        managed_ids = [
            row['id']
            for row in self.payload()['extensions']['managed_explicit']
            if isinstance(row, dict) and row.get('id')
        ]
        if not managed_ids:
            self.skipTest('base release surface has no repo-managed extension')

        payload = facts.build_overview_payload(
            root_dir=ROOT_DIR,
            control_plane_profile=managed_ids[0],
            probe_local=False,
            include_all_profiles=True,
        )
        rendered = facts.render_overview_markdown(payload, redact_managed_extensions=True)

        self.assertIn('managed-extension-1 selected profile config', rendered)
        self.assertIn('managed-extension-1 registry input', rendered)
        self.assertNotIn('agent/extensions/managed-extension-', rendered)
        for token in business_name_leak_tokens(ROOT_DIR):
            self.assertNotIn(token, rendered)

    def test_cli_json_output_is_machine_readable(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = facts.overview_entry(['--format', 'json', '--no-local-probe', '--repo-root', str(ROOT_DIR)])

        self.assertEqual(exit_code, 0, msg=stderr.getvalue())
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload['schema_version'], 1)
        self.assertEqual(payload['local_environment']['probed'], False)

    def test_cli_all_profiles_json_output_is_machine_readable(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = facts.overview_entry([
                '--format',
                'json',
                '--all-profiles',
                '--no-local-probe',
                '--repo-root',
                str(ROOT_DIR),
            ])

        self.assertEqual(exit_code, 0, msg=stderr.getvalue())
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload['profile_overviews'])
        self.assertTrue(payload['verification_commands'])


if __name__ == '__main__':
    unittest.main()
