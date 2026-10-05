from __future__ import annotations

import contextlib
from dataclasses import replace
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openclaw.control_plane import facts
from openclaw.lib.control_plane import object_families
from openclaw.lib.runtime.path_resolver import PathResolver
from openclaw.doctor.agent_modules.managed_probe_fixture import PROBE_PACKAGE_NAME, materialize_managed_probe_extension
from openclaw.doctor.platform.architecture_import_guards import business_name_leak_tokens
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.contracts import repo_contract_path
from openclaw.tests.support.helpers import isolated_test_root


ROOT_DIR = resolve_repo_root(Path(__file__))
REDACTION_EXTENSION_ID = 'agent_overview_secret_identity'
REDACTION_PACKAGE_NAME = 'openclaw_ext_overview_secret_identity'


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
        context = isolated_test_root('facts-redaction-contract')
        repo_root = context.__enter__()
        cls.addClassCleanup(context.__exit__, None, None, None)
        cls._probe = materialize_managed_probe_extension(
            repo_root, base_repo_root=ROOT_DIR, extension_id=REDACTION_EXTENSION_ID,
        )
        # 身份脱敏测试使用独特包名，避免把平台 probe 命令误当成业务名字。
        old_package = cls._probe.python_package_dir
        new_package = old_package.with_name(REDACTION_PACKAGE_NAME)
        old_package.rename(new_package)
        for path in cls._probe.package_root.rglob('*'):
            if path.is_file() and path.suffix in {'.json', '.py', '.md'}:
                source = path.read_text(encoding='utf-8')
                updated = source.replace(PROBE_PACKAGE_NAME, REDACTION_PACKAGE_NAME)
                if updated != source:
                    path.write_text(updated, encoding='utf-8')
        cls._probe = replace(
            cls._probe,
            python_package_dir=new_package,
            primary_main_path=new_package / cls._probe.primary_main_path.relative_to(old_package),
            support_main_path=new_package / cls._probe.support_main_path.relative_to(old_package),
            shared_runtime_layout_path=new_package / cls._probe.shared_runtime_layout_path.relative_to(old_package),
        )
        for contract_id in ('governance.script_catalog_surface', 'control_plane.object_families'):
            source = repo_contract_path(contract_id, root_dir=ROOT_DIR)
            target = repo_contract_path(contract_id, root_dir=cls._probe.repo_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        cls._probe_payload = facts.build_overview_payload(
            root_dir=cls._probe.repo_root, probe_local=False, include_all_profiles=True,
        )
        cls._probe_selected_payload = facts.build_overview_payload(
            root_dir=cls._probe.repo_root,
            control_plane_profile=cls._probe.extension_id,
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
        payload = self._probe_payload
        self.assertEqual([row['id'] for row in payload['extensions']['managed_explicit']], [self._probe.extension_id])
        rendered = facts.render_overview_markdown(payload, redact_managed_extensions=True)

        self.assertIn('managed-extension-', rendered)
        tokens = business_name_leak_tokens(self._probe.repo_root)
        self.assertEqual(tokens, (REDACTION_EXTENSION_ID, REDACTION_PACKAGE_NAME, 'overview_secret_identity'))
        for token in tokens:
            self.assertNotIn(token, rendered)

    def test_redacted_managed_profile_markdown_does_not_emit_fake_repo_paths(self) -> None:
        payload = self._probe_selected_payload
        self.assertEqual(payload['selected_config']['profile_id'], self._probe.extension_id)
        rendered = facts.render_overview_markdown(payload, redact_managed_extensions=True)

        self.assertIn('managed-extension-1 selected profile config', rendered)
        self.assertIn('managed-extension-1 registry input', rendered)
        self.assertNotIn('agent/extensions/managed-extension-', rendered)
        tokens = business_name_leak_tokens(self._probe.repo_root)
        self.assertEqual(tokens, (REDACTION_EXTENSION_ID, REDACTION_PACKAGE_NAME, 'overview_secret_identity'))
        for token in tokens:
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


class StaticFactsScopeTest(unittest.TestCase):
    def _write_object_truth(self, root: Path, families: dict[str, object]) -> Path:
        """创建隔离仓库的对象族及唯一合同索引。"""
        truth = root / 'config/governance/support/repo_contracts.json'
        truth.parent.mkdir(parents=True)
        truth.write_text(json.dumps({'contracts': [{'id': 'control_plane.object_families', 'relative_path': 'objects.json', 'format': 'json'}]}), encoding='utf-8')
        target = root / 'objects.json'
        target.write_text(json.dumps({'generated_artifacts': {'objects_doc': 'docs/objects.md'}, 'families': families}), encoding='utf-8')
        return target

    def test_static_sources_read_only_registered_truth_and_selected_owner_fragments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_object_truth(root, {})
            selected = root / 'agent/extensions/selected/source.json'
            selected.parent.mkdir(parents=True)
            selected.write_text(json.dumps({'generated_artifacts': {'owned_doc': 'agent/extensions/selected/README.md'}}), encoding='utf-8')
            unrelated = root / 'agent/extensions/unselected/source.json'
            unrelated.parent.mkdir(parents=True)
            unrelated.write_text('invalid JSON must not be read', encoding='utf-8')
            (root / 'private-runtime.json').write_text('invalid JSON must not be read', encoding='utf-8')
            context = {'extensions': [{'id': 'selected', 'surfaceFragments': {'objectFamiliesPath': selected}}]}
            with patch.object(Path, 'rglob', side_effect=AssertionError('静态生成项不能递归扫描仓库')):
                rows = facts._generated_artifacts_payload(root, context=context)
            self.assertEqual([row['source'] for row in rows], ['agent/extensions/selected/source.json', 'objects.json'])
            self.assertEqual(rows[0]['artifacts'], [{'id': 'owned_doc', 'path': 'agent/extensions/selected/README.md'}])

    def test_same_entry_id_keeps_family_owner_and_environment_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = lambda path: {'id': 'shared', 'path_kind': 'host_control_plane_file', 'path_ref': path}
            self._write_object_truth(root, {
                'acceptance_state': {'entries': [entry('first.json')]},
                'runtime_evidence': {'entries': [entry('second.json')]},
            })
            fragment = root / 'owner.objects.json'
            fragment.write_text(json.dumps({'families': {'runtime_evidence': {'entries': [entry('owned.json')]}}}), encoding='utf-8')
            resolver = PathResolver(root, {'entries': {'control_plane_host_state_dir': {'paths': {'host': 'state/default'}}}})
            config = root / 'service.json'
            with patch('openclaw.control_plane.extensions.descriptors.core.iter_extension_fragment_paths', return_value=[('agent_owner', fragment)]), patch.object(object_families, 'require_path_resolver', return_value=resolver), patch.object(object_families, 'resolve_control_plane_state_root', return_value=root / 'live') as live_state, patch.dict(os.environ, {'OPENCLAW_RUNTIME_PATH_VIEW': 'scheduler'}):
                static_rows = facts._evidence_payload(root, config_path=config, environment={})
                live_state.assert_not_called()
                live_rows = facts._evidence_payload(root, config_path=config)
                self.assertEqual(live_state.call_count, 3)
            self.assertEqual([(row['id'], row.get('extensionId'), row['entries'][0]['display_path']) for row in static_rows], [
                ('acceptance_state', None, 'state/default/first.json'),
                ('runtime_evidence', None, 'state/default/second.json'),
                ('runtime_evidence', 'agent_owner', 'state/default/owned.json'),
            ])
            self.assertEqual([row['entries'][0]['display_path'] for row in live_rows], ['live/first.json', 'live/second.json', 'live/owned.json'])

    def test_object_truth_is_isolated_between_repository_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            roots = [Path(directory) / name for name in ('first', 'second')]
            for root in roots:
                self._write_object_truth(root, {root.name: {'entries': []}})
            loaded = [object_families.load_contract(root_dir=root, extensions=[]) for root in roots]
            self.assertEqual([[row['id'] for row in payload['families']] for payload in loaded], [['first'], ['second']])

    def test_selected_config_scope_cuts_catalog_scan_and_local_probe_at_input(self) -> None:
        config = ROOT_DIR / 'config/control_plane/profiles/agent_platform.service.json'
        with patch.object(facts, '_profile_payload', side_effect=AssertionError('不能读取无关 profile 目录册')), patch.object(facts, 'load_managed_extensions_index', side_effect=AssertionError('不能读取无关扩展目录册')), patch.object(facts, '_generated_artifacts_payload_cached', side_effect=AssertionError('不能扫描未登记 JSON')), patch.object(facts, '_parse_env_keys', side_effect=AssertionError('不能读取私有 env')):
            payload = facts.build_overview_payload(config_path=config, probe_local=False, scope='selected_config', path_environment={})
        self.assertEqual(payload['extensions']['known_extension_ids'], ['agent_platform'])
        self.assertEqual(payload['extensions']['managed_explicit'], [])
        self.assertEqual([row['id'] for row in payload['profiles']['items']], ['agent_platform'])
        self.assertFalse(payload['local_environment']['probed'])
        self.assertTrue(all(not row['source'].startswith('agent/extensions/') for row in payload['generated_artifacts']))


if __name__ == '__main__':
    unittest.main()
