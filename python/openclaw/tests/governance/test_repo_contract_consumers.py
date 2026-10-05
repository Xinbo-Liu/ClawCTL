from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from unittest import mock

from openclaw.control_plane.dispatch import dispatch_runtime_audit
from openclaw.docs.renderers import runtime_surface
from openclaw.docs.support import reference_specs
from openclaw.guards.host_python.scanner import scan_shell_source
from openclaw.docs.validators import object_closure
from openclaw.lib.control_plane import agent_cli_surface, script_catalog_surface
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.static_truth import governance_default_path, repo_contract_path
from openclaw.lib.testing import acceptance_surface
from openclaw.lib.testing.acceptance import render as acceptance_render
from openclaw.setup.flow import deploy_success, run_failure
from openclaw.setup.flow.deploy_success_support import summary as deploy_success_summary
from openclaw.setup.upgrade.main import (
    _validate_live_acceptance_check,
    build_live_acceptance_env_keys_payload,
    build_live_acceptance_plan_payload,
)
from openclaw.setup.surface import control_plane_medium, entrypoint, followup
from openclaw.tests.support.helpers import isolated_test_root
from openclaw.tests.support.managed_extensions import managed_extensions
from openclaw.tests.support.managed_probe import managed_probe_repo
from openclaw.tests.support.static_text_assertions import assert_static_text_absent

ROOT_DIR = resolve_repo_root(Path(__file__))


def _write_image_pin_repo_contracts(root: Path) -> None:
    contracts_path = root / 'config' / 'governance' / 'support' / 'repo_contracts.json'
    contracts_path.parent.mkdir(parents=True, exist_ok=True)
    contracts_path.write_text(
        json.dumps(
            {
                'contracts': [
                    {'id': 'image_pins.openclaw', 'relative_path': 'config/image_pins/openclaw.env', 'format': 'env'},
                    {'id': 'image_pins.runtime', 'relative_path': 'config/image_pins/runtime.env', 'format': 'env'},
                    {'id': 'runtime.source_strategy', 'relative_path': 'config/runtime/source_strategy.json', 'format': 'json'},
                ]
            }
        ),
        encoding='utf-8',
    )
    strategy_path = root / 'config' / 'runtime' / 'source_strategy.json'
    strategy_path.parent.mkdir(parents=True, exist_ok=True)
    strategy_path.write_text(
        json.dumps(
            {
                'schema_version': 1,
                'images': {
                    'official_gateway': _strategy_image('OPENCLAW_OFFICIAL_GATEWAY_IMAGE', 'config/image_pins/openclaw.env', 'official_gateway', 'gateway'),
                    'control_plane_python': _strategy_image('OPENCLAW_CONTROL_PLANE_IMAGE', 'config/image_pins/runtime.env', 'control_plane_python', ''),
                    'runtime_python': _strategy_image('OPENCLAW_RUNTIME_PYTHON_IMAGE', 'config/image_pins/runtime.env', 'runtime_python', 'default'),
                    'nginx_runtime': _strategy_image('NGINX_IMAGE', 'config/image_pins/runtime.env', 'nginx', 'ingress'),
                },
            },
            ensure_ascii=False,
        )
        + '\n',
        encoding='utf-8',
    )


def _strategy_image(env_key: str, pin_file: str, role: str, selector: str) -> dict[str, object]:
    """生成部署成功摘要测试使用的最小 source_strategy image 条目。"""
    return {
        'selected_runtime_source': {'ref_env': env_key, 'pin_file': pin_file},
        'deployment_contract': {'role': role, 'label': role, 'scope': 'runtime', 'enabled': True},
        'compose_runtime': {'enabled': bool(selector), 'target_selector': selector} if selector else {'enabled': False},
    }


class RepoContractConsumerRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        """建立真实受管扩展，并登记供升级合同读取的 live check 与部署字段。

        参数：cls（type）：保存类级 fixture 的测试类。
        返回：None；fixture 在整个测试类执行期间有效。
        副作用：在临时仓库写入 testing/schema fragments；类清理时删除仓库。
        异常：fixture 物化、JSON 解析或文件写入失败直接传播。
        """
        super().setUpClass()
        cls._fixture_context = managed_probe_repo('repo-contract-consumer')
        cls.fixture = cls._fixture_context.__enter__()
        cls.addClassCleanup(cls._fixture_context.__exit__, None, None, None)
        fixture = cls.fixture
        testing = json.loads(fixture.testing_manifest_path.read_text(encoding='utf-8'))
        testing['live_acceptance_checks'] = [{
            'id': 'probe_live_acceptance',
            'extensionId': fixture.extension_id,
            'requiresExplicitLive': True,
            'command': {'module': f'{fixture.python_package_dir.name}.modules.{fixture.primary_module_dir.name}.main'},
        }]
        fixture.testing_manifest_path.write_text(json.dumps(testing) + '\n', encoding='utf-8')
        schema_path = fixture.manifest_dir / f'{fixture.extension_id}.deploy_env_schema.json'
        descriptor = json.loads(fixture.manifest_path.read_text(encoding='utf-8'))
        descriptor['surfaceFragments']['deployEnvSchemaPath'] = schema_path.name
        fixture.manifest_path.write_text(json.dumps(descriptor) + '\n', encoding='utf-8')
        schema_path.write_text(json.dumps({
            'groups': [{'id': 'probe_live', 'title': '探针验收输入'}],
            'fields': [{
                'key': key,
                'group': 'probe_live',
                'required': False,
                'manual_required': False,
                'default_kind': 'literal',
                'default': '',
                'doc_summary': '探针扩展输入',
                'doc_location': f'agent/extensions/{fixture.extension_id}/deploy/extension.env',
                'validator': {'type': 'non_empty'},
                'live_acceptance_env': live,
            } for key, live in [
                ('PROBE_LIVE_ACCEPTANCE_TASK_ID', True),
                ('PROBE_ORDINARY_INPUT', False),
            ]],
        }, ensure_ascii=False) + '\n', encoding='utf-8')

    def test_full_test_registry_commands_do_not_invoke_host_python(self) -> None:
        registry_paths = [
            ROOT_DIR / 'config' / 'governance' / 'flows' / 'full_test_group_registry.json',
            *sorted(
                (ROOT_DIR / 'agent' / 'extensions').glob(
                    '*/config/control_plane/extensions.d/*.full_test_group_registry.json'
                )
            ),
        ]
        checked = 0
        violations: list[str] = []
        for registry_path in registry_paths:
            registry = json.loads(registry_path.read_text(encoding='utf-8'))
            for group_id, group in (registry.get('groups') or {}).items():
                for check in list((group or {}).get('script_checks') or []):
                    if not isinstance(check, dict):
                        continue
                    command = str(check.get('command') or '').strip()
                    if not command:
                        continue
                    check_id = str(check.get('id') or '<missing-check-id>')
                    label = f'{registry_path.relative_to(ROOT_DIR).as_posix()}:{group_id}:{check_id}'
                    violations.extend(scan_shell_source(command, label))
                    checked += 1

        self.assertGreater(checked, 0)
        self.assertEqual([], violations)

    def test_managed_extensions_do_not_contribute_platform_full_test_checks(self) -> None:
        checked_extensions = 0
        for extension in (*managed_extensions(ROOT_DIR), *managed_extensions(self.fixture.repo_root)):
            extension_descriptor = json.loads((extension.manifest_dir / f'{extension.id}.json').read_text(encoding='utf-8'))
            governance_surfaces = extension_descriptor.get('governanceSurfaces') if isinstance(extension_descriptor.get('governanceSurfaces'), dict) else {}
            with self.subTest(extension_id=extension.id, surface='fullTestGroupRegistryPath'):
                self.assertNotIn('fullTestGroupRegistryPath', governance_surfaces)
                self.assertEqual([], sorted(extension.manifest_dir.glob('*.full_test_group_registry.json')))
            for manifest_path in sorted(extension.manifest_dir.glob('*.testing_manifest.json')):
                with self.subTest(extension_id=extension.id, manifest=manifest_path.name):
                    testing_manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                    disallowed_keys = {
                        'valid_groups',
                        'groups',
                        'checks',
                        'execution_order',
                        'acceptance_reference',
                        'acceptance_contract',
                    }
                    self.assertEqual(set(), disallowed_keys & set(testing_manifest))
                    self.assertLessEqual(set(testing_manifest), {'release_gate_checks', 'live_acceptance_checks'})
                    checked_extensions += 1
        self.assertGreater(checked_extensions, 0)

    def test_acceptance_summary_uses_non_ok_group_label_for_agent_group_health(self) -> None:
        summary = {
            'deployment_acceptance': {'exists': True, 'eligible': True, 'accepted': True},
            'ingress_boundary_evidence': {
                'exists': True,
                'accepted': True,
                'compose_contract_ok': True,
                'runtime_contract_ok': True,
                'nginx_policy_ok': True,
                'nginx_policy_rewrite_phase_default_deny': True,
                'nginx_policy_access_phase_default_deny': True,
                'boundary_method': 'iptables',
            },
            'runtime_acceptance': {
                'exists': True,
                'eligible': True,
                'accepted': True,
                'control_plane_scheduler_healthy': True,
                'control_plane_heartbeat_age_seconds': 30,
                'control_plane_run_ledger_accepted': True,
                'control_plane_run_ledger_missing_jobs': [],
                'control_plane_run_ledger_failing_jobs': [],
                'control_plane_agent_group_count': 1,
                'control_plane_agent_module_count': 4,
                'control_plane_recent_agent_access_count': 20,
                'control_plane_recent_agent_access_group_count': 1,
                'control_plane_agent_access_log_exists': True,
                'control_plane_agent_group_access_exists': True,
                'control_plane_required_agent_groups': ['sample_pipeline'],
                'control_plane_failing_agent_groups': ['sample_pipeline'],
                'control_plane_blocked_agent_group_release_gates': [],
                'control_plane_frozen_agent_group_release_gates': ['sample_pipeline'],
            },
            'dispatch_runtime_check': {'exists': True, 'ok': True, 'signal_id': None},
            'control_plane_run_ledger': {'exists': True, 'artifact_accepted_jobs': 6, 'artifact_failed_jobs': 0, 'artifact_missing_jobs': 0},
            'official_cli': {'control_plane': {'exists': True, 'doctor_passed': True, 'blocking_findings': 0}},
            'control_plane_job_artifact_policies': {'exists': True, 'job_count': 6},
        }

        text = acceptance_render.render_acceptance_summary_text(summary)

        self.assertIn("non_ok_groups=['sample_pipeline']", text)
        self.assertIn("frozen_release_gates=['sample_pipeline']", text)
        self.assertNotIn('failing_groups=', text)

    def test_full_test_hardcoded_checks_match_testing_manifest_and_service_registry(self) -> None:
        source = (Path(ROOT_DIR) / 'scripts' / 'setup' / 'lib' / 'full_test_group_runner.sh').read_text(encoding='utf-8')
        manifest = json.loads((Path(ROOT_DIR) / 'config' / 'runtime' / 'testing_manifest.json').read_text(encoding='utf-8'))
        registry = json.loads((Path(ROOT_DIR) / 'config' / 'runtime' / 'service_registry.json').read_text(encoding='utf-8'))
        declared_checks = {str(item.get('id')) for item in list(manifest.get('checks') or []) if isinstance(item, dict)}
        declared_targets = {str(item.get('target')) for item in list(registry.get('targets') or []) if isinstance(item, dict)}

        service_body = source.split('full_test_run_service_group() {', 1)[1].split('# 执行 dispatch 分组', 1)[0]
        check_ids = set(
            re.findall(
                r'full_test_run_(?:script_check|dual_healthz_check|target_health_check)\s+([A-Za-z0-9_]+)\s+',
                service_body,
            )
        )
        target_rows = re.findall(
            r'full_test_run_target_health_check\s+([A-Za-z0-9_]+)\s+"\$group"\s+([A-Za-z0-9_-]+)\s+',
            service_body,
        )

        self.assertFalse(check_ids - declared_checks)
        self.assertFalse({target for _, target in target_rows} - declared_targets)

    def test_runtime_testing_manifest_group_references_are_closed(self) -> None:
        manifest = json.loads((Path(ROOT_DIR) / 'config' / 'runtime' / 'testing_manifest.json').read_text(encoding='utf-8'))
        group_ids = {str(item.get('id')) for item in manifest.get('groups') or [] if isinstance(item, dict)}
        valid_groups = {str(item) for item in manifest.get('valid_groups') or []}
        execution_order = {str(item) for item in manifest.get('execution_order') or []}
        check_groups = {str(item.get('group')) for item in manifest.get('checks') or [] if isinstance(item, dict)}
        check_ids = {str(item.get('id')) for item in manifest.get('checks') or [] if isinstance(item, dict)}

        self.assertIn('all', valid_groups)
        self.assertFalse((valid_groups - {'all'}) - group_ids)
        self.assertFalse(execution_order - group_ids)
        self.assertFalse(check_groups - group_ids)
        self.assertNotIn('process', valid_groups)
        self.assertIn('full_test_process_exit_code', check_ids)

    def test_managed_extension_groups_do_not_bind_platform_deployment_acceptance(self) -> None:
        checked = 0
        for extension in (*managed_extensions(ROOT_DIR), *managed_extensions(self.fixture.repo_root)):
            for group_path in sorted((extension.root_dir / 'agent' / 'control_plane' / 'groups').glob('*.json')):
                group = json.loads(group_path.read_text(encoding='utf-8'))
                release_policy = group.get('releasePolicy') if isinstance(group.get('releasePolicy'), dict) else {}
                release_gate = release_policy.get('releaseGate') if isinstance(release_policy.get('releaseGate'), dict) else {}
                with self.subTest(extension_id=extension.id, group=group_path.name):
                    self.assertNotIn('acceptance' + 'Binding', release_policy)
                    self.assertNotIn('acceptance' + '_binding', release_gate.get('requiredCheckIds') or [])
                    checked += 1
        self.assertGreater(checked, 0)

    def test_live_acceptance_plan_is_manifest_declared(self) -> None:
        payload = build_live_acceptance_plan_payload(
            self.fixture.repo_root,
            self.fixture.extension_id,
            config_path=self.fixture.service_path,
        )

        self.assertEqual(payload['status'], 'ok')
        checks = {str(row.get('id') or '') for row in payload.get('checks') or [] if isinstance(row, dict)}
        self.assertIn('probe_live_acceptance', checks)

    def test_live_acceptance_env_keys_are_extension_schema_owned(self) -> None:
        payload = build_live_acceptance_env_keys_payload(
            self.fixture.repo_root,
            self.fixture.extension_id,
            config_path=self.fixture.service_path,
        )

        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(payload['envKeys'], ['PROBE_LIVE_ACCEPTANCE_TASK_ID'])

    def test_live_acceptance_requirement_rejects_disabled_extension(self) -> None:
        payload = build_live_acceptance_plan_payload(
            self.fixture.repo_root,
            self.fixture.extension_id,
            config_path=self.fixture.repo_root / 'config' / 'control_plane' / 'service.json',
        )

        self.assertEqual(payload['status'], 'blocked')
        issue_codes = {str(row.get('code') or '') for row in payload.get('blockingIssues') or [] if isinstance(row, dict)}
        self.assertIn('extension_not_enabled', issue_codes)

    def test_live_acceptance_module_must_belong_to_extension_python_package(self) -> None:
        package_name = self.fixture.python_package_dir.name
        self.assertTrue((self.fixture.python_package_dir / '__init__.py').is_file())
        base_check = {
            'id': 'module_owner_check',
            'extensionId': self.fixture.extension_id,
            'requiresExplicitLive': True,
        }
        valid_check = {
            **base_check,
            'command': {'module': package_name},
        }
        invalid_check = {
            **base_check,
            'command': {'module': 'openclaw.setup.upgrade.main'},
        }

        self.assertEqual(
            [],
            _validate_live_acceptance_check(valid_check, repo_root=self.fixture.repo_root, extension_id=self.fixture.extension_id),
        )
        issue_codes = {
            str(row.get('code') or '')
            for row in _validate_live_acceptance_check(invalid_check, repo_root=self.fixture.repo_root, extension_id=self.fixture.extension_id)
        }
        self.assertIn('live_acceptance_module_owner_mismatch', issue_codes)

    def test_extension_runtime_mounts_only_include_self_or_required_dependency_extension_roots(self) -> None:
        violations: list[str] = []
        for extension in managed_extensions(ROOT_DIR):
            manifest_path = extension.manifest_dir / f'{extension.id}.json'
            if not manifest_path.is_file():
                continue
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            required_dependency_ids = {
                str(row.get('id') or '').strip()
                for row in manifest.get('dependencies') or []
                if isinstance(row, dict) and str(row.get('id') or '').strip() and row.get('optional') is not True
            }
            allowed_agent_roots = {f'agent/extensions/{extension.id}'}
            allowed_agent_roots.update(
                f'agent/extensions/{dependency_id}'
                for dependency_id in required_dependency_ids
                if dependency_id != 'agent_platform'
            )
            for runtime_mounts_path in sorted(extension.manifest_dir.glob('*.runtime_mounts.json')):
                payload = json.loads(runtime_mounts_path.read_text(encoding='utf-8'))
                for service in payload.get('services') or []:
                    if not isinstance(service, dict):
                        continue
                    for mount in service.get('mounts') or []:
                        if not isinstance(mount, dict) or mount.get('source_type') != 'repo_path':
                            continue
                        relpath = str(mount.get('relative_path') or '').strip().replace('\\', '/')
                        if relpath == 'agent' or relpath == 'agent/extensions':
                            violations.append(f'{runtime_mounts_path.relative_to(ROOT_DIR).as_posix()}:{service.get("service")} mounts broad {relpath}')
                            continue
                        if relpath.startswith('agent/extensions/'):
                            parts = relpath.split('/')
                            mounted_root = '/'.join(parts[:3]) if len(parts) >= 3 else relpath
                            if mounted_root not in allowed_agent_roots:
                                violations.append(
                                    f'{runtime_mounts_path.relative_to(ROOT_DIR).as_posix()}:{service.get("service")} mounts {mounted_root} without required dependency'
                                )

        self.assertEqual([], violations)

    def test_getting_started_curl_resolve_examples_are_ipv6_safe(self) -> None:
        surface = json.loads((Path(ROOT_DIR) / 'config' / 'governance' / 'docs' / 'getting_started_surface.json').read_text(encoding='utf-8'))
        verify_commands = dict((surface.get('quickstart') or {}).get('verify_commands_by_mode') or {})
        unsafe_fragment = ''.join(('--resolve ${', 'OPENCLAW_TLS_CN', '}:443:${', 'OPENCLAW_INGRESS_LISTEN_IP', '}'))
        for mode, commands in verify_commands.items():
            with self.subTest(mode=mode):
                rendered = '\n'.join(str(command) for command in list(commands or []))
                assert_static_text_absent(self, unsafe_fragment, rendered)
                if '--resolve ' in rendered:
                    self.assertIn('OPENCLAW_CURL_RESOLVE_IP', rendered)

    def test_contract_backed_surface_loaders_stay_available(self) -> None:
        surface = acceptance_surface.read_surface()
        manifest = acceptance_surface.read_manifest()
        active_docs = object_closure.load_active_docs_paths()
        medium_command = control_plane_medium.command()
        default_flow = entrypoint.render_default_flow()
        followup_index = followup.render_index()

        self.assertIsInstance(surface.get('usage_commands'), list)
        self.assertIsInstance(manifest.get('required_checks'), list)
        self.assertIn('docs/getting-started/quickstart.md', active_docs)
        self.assertTrue(medium_command)
        self.assertIn('1. ', default_flow)
        self.assertIn('one_click_deploy', followup_index)

    def test_object_closure_command_candidates_tolerate_optional_args(self) -> None:
        candidates = object_closure.normalized_token_candidates(
            'bash ./scripts/doctor/check_docker_host_readiness.sh [--offline] [--env-file <path>]'
        )

        self.assertIn('bash ./scripts/doctor/check_docker_host_readiness.sh', candidates)

    def test_runtime_and_reference_outputs_still_use_contract_backed_truth(self) -> None:
        manifest = runtime_surface.read_manifest()
        agent_specs = reference_specs.load_specs('agent_command_specs.json')
        script_surface_manifest = reference_specs.load_specs('script_surface_manifest.json')
        router_section = reference_specs.render_router_workspace_section()
        agent_index = agent_cli_surface.render_index()
        script_index = script_catalog_surface.render_index()

        self.assertIsInstance(manifest.get('targets'), list)
        self.assertIsInstance(manifest.get('runtime_contract'), dict)
        self.assertIsInstance(manifest.get('source_strategy'), dict)
        self.assertIsInstance(agent_specs, dict)
        self.assertIsInstance(script_surface_manifest, dict)
        self.assertIn('显式路由指令', router_section)
        self.assertIn('router_local_ro', router_section)
        self.assertIn('agent CLI reference', agent_index)
        self.assertIn('script catalog surface 入口', script_index)

    def test_deploy_flow_failure_stage_surface_has_no_unrouted_nodes(self) -> None:
        deploy_flow = json.loads(repo_contract_path('governance.default_deployment_flow').read_text(encoding='utf-8'))
        stage_flow = json.loads(repo_contract_path('governance.deploy_stage_flow').read_text(encoding='utf-8'))
        setup_failures = json.loads(repo_contract_path('governance.setup_failures').read_text(encoding='utf-8'))
        one_click_source = (ROOT_DIR / 'scripts' / 'setup' / 'one_click_deploy.sh').read_text(encoding='utf-8')
        control_plane_source = (
            ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'deploy_flow_control_plane_shell.sh'
        ).read_text(encoding='utf-8')
        getting_started_sections = json.loads(
            (ROOT_DIR / 'config' / 'governance' / 'docs' / 'getting_started_sections.json').read_text(encoding='utf-8')
        )
        deploy_flow_source = (ROOT_DIR / 'python' / 'openclaw' / 'setup' / 'flow' / 'deploy_flow.py').read_text(encoding='utf-8')

        order = deploy_flow['deploy_flow']['stage_order']
        canonical_stages: set[str] = set()
        for value in order.values():
            if isinstance(value, list):
                canonical_stages.update(str(item) for item in value)
            else:
                canonical_stages.add(str(value))

        current_stage_names = set(re.findall(r'CURRENT_STAGE_NAME="([A-Za-z0-9_]+)"', one_click_source))
        current_stage_names.update(
            re.findall(r'flow_set_var\s+CURRENT_STAGE_NAME\s+([A-Za-z0-9_]+)', control_plane_source)
        )
        current_stage_names.difference_update({'init', 'deploy_stage_runner'})

        manifest_stages = set(stage_flow['stages'])
        one_click_failure_stages: set[str] = set()
        one_click_scenarios = setup_failures['entries']['one_click_deploy']['scenarios']
        for scenario in one_click_scenarios.values():
            one_click_failure_stages.update(str(stage) for stage in scenario.get('stages', []))
        post_resume_stage_ids = set(
            str(stage)
            for stage in getting_started_sections['quickstart']['deploy_stage'].get('post_resume_stage_ids', [])
        )
        post_resume_source = deploy_flow_source.split('POST_DEPLOY_RESUME_STAGES', 1)[1].split('}', 1)[0]
        allowed_post_resume = set(re.findall(r"'([A-Za-z0-9_]+)'", post_resume_source))

        self.assertFalse(canonical_stages - manifest_stages)
        self.assertFalse(current_stage_names - manifest_stages)
        self.assertFalse(manifest_stages - one_click_failure_stages)
        self.assertEqual(post_resume_stage_ids, allowed_post_resume)
        self.assertFalse(post_resume_stage_ids - manifest_stages)

    def test_deploy_stage_summary_hints_do_not_repeat_reference_docs(self) -> None:
        stage_flow = json.loads(repo_contract_path('governance.deploy_stage_flow').read_text(encoding='utf-8'))

        for stage_id, info in stage_flow['stages'].items():
            with self.subTest(stage=stage_id):
                refs = [
                    str(line).removeprefix('- ').strip()
                    for line in list(info.get('summary_hint') or [])
                    if str(line).startswith('- docs/')
                ]
                self.assertEqual(len(refs), len(set(refs)))

    def test_post_deploy_recovery_surfaces_keep_resume_commands(self) -> None:
        stage_flow = json.loads(repo_contract_path('governance.deploy_stage_flow').read_text(encoding='utf-8'))
        setup_failures = json.loads(repo_contract_path('governance.setup_failures').read_text(encoding='utf-8'))
        setup_followups = json.loads(repo_contract_path('governance.setup_followups').read_text(encoding='utf-8'))

        self.assertEqual(
            stage_flow['stages']['post_deploy_acceptance']['next_commands'],
            ['bash ./scripts/setup/one_click_deploy.sh --resume-from post_deploy_acceptance'],
        )
        self.assertEqual(
            stage_flow['stages']['post_deploy_full_acceptance']['next_commands'],
            ['bash ./scripts/setup/one_click_deploy.sh --resume-from post_deploy_full_acceptance'],
        )
        self.assertEqual(
            stage_flow['stages']['export_runtime_acceptance_evidence']['next_commands'],
            [
                'bash ./scripts/setup/one_click_deploy.sh --resume-from post_deploy_acceptance',
                'bash ./scripts/setup/one_click_deploy.sh --resume-from post_deploy_full_acceptance',
            ],
        )

        post_failure = setup_failures['entries']['one_click_deploy']['scenarios']['post_deploy_acceptance_failed']
        self.assertEqual(
            post_failure['commands'],
            [
                'bash ./scripts/setup/one_click_deploy.sh --resume-from post_deploy_acceptance',
                'bash ./scripts/setup/one_click_deploy.sh --resume-from post_deploy_full_acceptance',
            ],
        )

        deploy_followups = setup_followups['entries']['one_click_deploy']['scenarios']
        self.assertIn(
            'bash ./scripts/setup/one_click_deploy.sh --resume-from post_deploy_acceptance',
            deploy_followups['success_prepare_only']['commands'],
        )
        self.assertNotIn('bash ./scripts/setup/one_click_test_full.sh', deploy_followups['success_prepare_only']['commands'])
        self.assertNotIn(
            'bash ./scripts/runtime/export_runtime_acceptance_evidence.sh',
            deploy_followups['success_prepare_only']['commands'],
        )
        self.assertIn(
            'bash ./scripts/setup/one_click_deploy.sh --resume-from post_deploy_full_acceptance',
            setup_followups['entries']['one_click_test_full']['scenarios']['success_default']['commands'],
        )

    def test_dispatch_runtime_audit_surface_exports_stay_available(self) -> None:
        self.assertTrue(callable(dispatch_runtime_audit.load_context))
        self.assertTrue(callable(dispatch_runtime_audit.target_acceptance_payload))
        self.assertTrue(callable(dispatch_runtime_audit.batch_acceptance_payload))
        self.assertTrue(callable(dispatch_runtime_audit.rotation_sequence_payload))
        self.assertTrue(callable(dispatch_runtime_audit.health_overview_payload))
        self.assertTrue(callable(dispatch_runtime_audit.render_text))
        self.assertEqual(dispatch_runtime_audit._exit_code_from_status('warn', fail_on_warn=True, fail_on_fail=False), 1)

    def test_deploy_success_build_summary_uses_contract_defaults(self) -> None:
        with isolated_test_root('repo-contract-deploy-success') as root:
            env_file = root / 'deploy.env'
            env_file.write_text(
                '\n'.join([
                    'OPENCLAW_TLS_CN=claw.local',
                    'OPENCLAW_INGRESS_LISTEN_IP=127.0.0.1',
                    'OPENCLAW_OFFICIAL_GATEWAY_IMAGE=ghcr.io/openclaw/gateway:1',
                    'OPENCLAW_CONTROL_PLANE_IMAGE=ghcr.io/openclaw/control-plane:1',
                    'OPENCLAW_RUNTIME_PYTHON_IMAGE=ghcr.io/openclaw/runtime-python:1',
                    'NGINX_IMAGE=nginx:1',
                    f'HOST_STATE_ROOT={root / "state" / "openclaw"}',
                ]) + '\n',
                encoding='utf-8',
            )
            config_summary = root / 'config_summary.json'
            config_summary.write_text(
                '{"status":"ready","required_manual_keys":[{"key":"TLS_CN","status":"filled"}]}\n',
                encoding='utf-8',
            )
            acceptance_state = root / 'acceptance.json'
            acceptance_state.write_text(
                '{"eligible": true, "accepted": true, "required_checks": [{"id":"compose_ps","status":"PASS"}]}\n',
                encoding='utf-8',
            )
            ingress_boundary = root / 'ingress_boundary.json'
            ingress_boundary.write_text(
                '{"accepted": true, "boundary_evidence": {"method": "private_ingress"}, "nginx_policy": {"required": true, "checked": true, "ok": true, "default_deny": true, "rewrite_phase_default_deny": true, "access_phase_default_deny": true, "source_cidrs": ["10.0.0.0/24"]}}\n',
                encoding='utf-8',
            )
            latest_json = root / 'latest.json'
            latest_md = root / 'latest.md'
            derived_release_root = root / 'state' / 'openclaw' / 'control_plane' / 'release'
            derived_release_root.mkdir(parents=True)

            def fake_default_path(key: str, profile_id: str = 'one_click_deploy') -> Path:
                del profile_id
                mapping = {
                    'config_summary': config_summary,
                    'deployment_acceptance_state': acceptance_state,
                    'ingress_boundary_evidence': ingress_boundary,
                    'latest_json': latest_json,
                    'latest_markdown': latest_md,
                }
                return mapping[key]

            with mock.patch('openclaw.setup.flow.deploy_success.default_path', side_effect=fake_default_path):
                with mock.patch(
                    'openclaw.setup.flow.deploy_success.build_private_ingress_plan',
                    return_value={
                        'ingress': {
                            'exposurePolicyPlane': 'nginx_allowlist_and_infra_boundary',
                            'networkBoundaryInRepo': True,
                            'tlsMode': 'self_signed',
                        },
                        'nginx': {
                            'hstsMaxAge': 31536000,
                            'outputPath': str(root / 'nginx.conf'),
                        },
                    },
                ):
                    with mock.patch(
                        'openclaw.setup.flow.deploy_success.build_runtime_evidence_status',
                        return_value={
                            'runtime_acceptance_exists': False,
                            'runtime_accepted': False,
                            'official_cli_summary_exists': False,
                        },
                    ) as runtime_evidence_mock:
                        summary = deploy_success.build_summary(
                            {
                                'env_file': env_file,
                                'config_summary': '',
                                'acceptance_state': '',
                                'release_root': root,
                                'out_json': root / 'out.json',
                                'out_md': root / 'out.md',
                                'format': 'text',
                                'mode': 'online',
                                'status': 'success',
                                'timestamp': '2026-04-21T12:00:00Z',
                                'resume_from': '',
                                'log_path': root / 'deploy.log',
                                'summary_json_path': root / 'summary.json',
                                'summary_md_path': root / 'summary.md',
                                'start_services': True,
                                'image_archive_path': '',
                            }
                        )

        self.assertTrue(summary['deploy_env_summary']['exists'])
        self.assertTrue(summary['deployment_acceptance']['accepted'])
        self.assertEqual(summary['fixed_latest_summary']['json_path'], str(latest_json))
        self.assertIn('bash ./scripts/runtime/export_runtime_acceptance_evidence.sh', summary['next_steps'])
        runtime_evidence_mock.assert_called_once_with(derived_release_root)

    def test_deploy_success_control_plane_image_falls_back_to_runtime_pin(self) -> None:
        with isolated_test_root('repo-contract-deploy-success-image-pin') as root:
            _write_image_pin_repo_contracts(root)
            env_file = root / 'deploy.env'
            env_file.write_text(
                '\n'.join([
                    'OPENCLAW_TLS_CN=claw.local',
                    'OPENCLAW_OFFICIAL_GATEWAY_IMAGE=gw:1',
                    'OPENCLAW_RUNTIME_PYTHON_IMAGE=runtime:1',
                    'NGINX_IMAGE=nginx:1',
                    f'HOST_STATE_ROOT={root / "state" / "openclaw"}',
                ]) + '\n',
                encoding='utf-8',
            )
            pin_file = root / 'config' / 'image_pins' / 'runtime.env'
            pin_file.parent.mkdir(parents=True)
            pin_file.write_text('OPENCLAW_CONTROL_PLANE_IMAGE=control-plane:1\n', encoding='utf-8')

            def parse_env(path: Path) -> dict[str, str]:
                rows: dict[str, str] = {}
                if not path.exists():
                    return rows
                for line in path.read_text(encoding='utf-8').splitlines():
                    if not line or line.startswith('#') or '=' not in line:
                        continue
                    key, value = line.split('=', 1)
                    rows[key] = value
                return rows

            def fake_default_path(key: str) -> Path:
                mapping = {
                    'config_summary': root / 'missing-config-summary.json',
                    'deployment_acceptance_state': root / 'missing-acceptance.json',
                    'ingress_boundary_evidence': root / 'missing-ingress.json',
                    'latest_json': root / 'latest.json',
                    'latest_markdown': root / 'latest.md',
                }
                return mapping[key]

            summary = deploy_success_summary.build_summary(
                {
                    'env_file': env_file,
                    'config_summary': '',
                    'acceptance_state': '',
                    'release_root': root,
                    'release_root_explicit': True,
                    'out_json': root / 'out.json',
                    'out_md': root / 'out.md',
                    'format': 'text',
                    'mode': 'online',
                    'status': 'success',
                    'timestamp': '2026-04-21T12:00:00Z',
                    'resume_from': '',
                    'log_path': root / 'deploy.log',
                    'summary_json_path': root / 'summary.json',
                    'summary_md_path': root / 'summary.md',
                    'start_services': True,
                    'image_archive_path': '',
                },
                root_dir=root,
                parse_env_file_fn=parse_env,
                read_json_fn=lambda path: None,
                build_private_ingress_plan_fn=lambda path: {},
                build_runtime_evidence_status_fn=lambda path: {},
                default_path_fn=fake_default_path,
                next_steps_fn=lambda payload: [],
                relative_or_self_fn=lambda path, *, root_dir: str(path),
                utc_now_iso_fn=lambda: '2026-04-21T12:00:00Z',
            )

        self.assertEqual(summary['selected_images']['control_plane_image'], 'control-plane:1')

    def test_deploy_success_skip_acceptance_does_not_reuse_stale_acceptance(self) -> None:
        with isolated_test_root('repo-contract-deploy-success-skip-acceptance') as root:
            _write_image_pin_repo_contracts(root)
            env_file = root / 'deploy.env'
            env_file.write_text(
                '\n'.join([
                    'OPENCLAW_TLS_CN=claw.local',
                    'OPENCLAW_OFFICIAL_GATEWAY_IMAGE=ghcr.io/openclaw/gateway:1',
                    'OPENCLAW_CONTROL_PLANE_IMAGE=ghcr.io/openclaw/control-plane:1',
                    'OPENCLAW_RUNTIME_PYTHON_IMAGE=ghcr.io/openclaw/runtime-python:1',
                    'NGINX_IMAGE=nginx:1',
                    f'HOST_STATE_ROOT={root / "state" / "openclaw"}',
                ]) + '\n',
                encoding='utf-8',
            )
            acceptance_state = root / 'acceptance.json'
            acceptance_state.write_text(
                '{"eligible": true, "accepted": true, "required_checks": [{"id":"compose_ps","status":"PASS"}]}\n',
                encoding='utf-8',
            )

            def fake_default_path(key: str) -> Path:
                mapping = {
                    'config_summary': root / 'missing-config-summary.json',
                    'deployment_acceptance_state': acceptance_state,
                    'ingress_boundary_evidence': root / 'missing-ingress.json',
                    'latest_json': root / 'latest.json',
                    'latest_markdown': root / 'latest.md',
                }
                return mapping[key]

            summary = deploy_success_summary.build_summary(
                {
                    'env_file': env_file,
                    'config_summary': '',
                    'acceptance_state': '',
                    'release_root': root,
                    'release_root_explicit': True,
                    'out_json': root / 'out.json',
                    'out_md': root / 'out.md',
                    'format': 'text',
                    'mode': 'online',
                    'status': 'success',
                    'timestamp': '2026-04-21T12:00:00Z',
                    'resume_from': '',
                    'log_path': root / 'deploy.log',
                    'summary_json_path': root / 'summary.json',
                    'summary_md_path': root / 'summary.md',
                    'start_services': True,
                    'post_acceptance': False,
                    'image_archive_path': '',
                },
                root_dir=root,
                parse_env_file_fn=lambda path: {
                    key: value
                    for key, value in (
                        line.split('=', 1)
                        for line in path.read_text(encoding='utf-8').splitlines()
                        if '=' in line
                    )
                },
                read_json_fn=lambda path: json.loads(path.read_text(encoding='utf-8')) if path.exists() else None,
                build_private_ingress_plan_fn=lambda path: {},
                build_runtime_evidence_status_fn=lambda path: {
                    'runtime_acceptance_exists': True,
                    'runtime_accepted': True,
                    'official_cli_summary_exists': True,
                },
                default_path_fn=fake_default_path,
                next_steps_fn=lambda payload: [],
                relative_or_self_fn=lambda path, *, root_dir: str(path),
                utc_now_iso_fn=lambda: '2026-04-21T12:00:00Z',
            )

        self.assertFalse(summary['deploy_run']['post_acceptance'])
        self.assertFalse(summary['deployment_acceptance']['exists'])
        self.assertFalse(summary['deployment_acceptance']['accepted'])
        self.assertTrue(summary['deployment_acceptance']['skipped_by_current_run'])
        self.assertFalse(summary['runtime_evidence']['runtime_acceptance_exists'])
        self.assertFalse(summary['runtime_evidence']['runtime_accepted'])
        self.assertTrue(summary['runtime_evidence']['skipped_by_current_run'])

    def test_deploy_success_explicit_release_root_is_not_overridden_by_host_state_root(self) -> None:
        with isolated_test_root('repo-contract-deploy-success-explicit-release-root') as root:
            env_file = root / 'deploy.env'
            env_file.write_text(
                '\n'.join([
                    'OPENCLAW_TLS_CN=claw.local',
                    f'HOST_STATE_ROOT={root / "state" / "openclaw"}',
                ]) + '\n',
                encoding='utf-8',
            )
            explicit_release_root = root / 'custom-release'
            explicit_release_root.mkdir()
            derived_release_root = root / 'state' / 'openclaw' / 'control_plane' / 'release'
            derived_release_root.mkdir(parents=True)

            def fake_default_path(key: str, profile_id: str = 'one_click_deploy') -> Path:
                del profile_id
                mapping = {
                    'config_summary': root / 'config_summary.json',
                    'deployment_acceptance_state': root / 'acceptance.json',
                    'ingress_boundary_evidence': root / 'ingress_boundary.json',
                    'latest_json': root / 'latest.json',
                    'latest_markdown': root / 'latest.md',
                }
                return mapping[key]

            with mock.patch('openclaw.setup.flow.deploy_success.default_path', side_effect=fake_default_path):
                with mock.patch('openclaw.setup.flow.deploy_success.build_private_ingress_plan', return_value={}):
                    with mock.patch(
                        'openclaw.setup.flow.deploy_success.build_runtime_evidence_status',
                        return_value={
                            'runtime_acceptance_exists': False,
                            'runtime_accepted': False,
                            'official_cli_summary_exists': False,
                        },
                    ) as runtime_evidence_mock:
                        deploy_success.build_summary(
                            {
                                'env_file': env_file,
                                'config_summary': '',
                                'acceptance_state': '',
                                'release_root': explicit_release_root,
                                'release_root_explicit': True,
                                'out_json': root / 'out.json',
                                'out_md': root / 'out.md',
                                'format': 'text',
                                'mode': 'online',
                                'status': 'success',
                                'timestamp': '2026-04-21T12:00:00Z',
                                'resume_from': '',
                                'log_path': root / 'deploy.log',
                                'summary_json_path': root / 'summary.json',
                                'summary_md_path': root / 'summary.md',
                                'start_services': True,
                                'image_archive_path': '',
                            }
                        )

        runtime_evidence_mock.assert_called_once_with(explicit_release_root)

    def test_run_failure_build_summary_uses_contract_defaults(self) -> None:
        with isolated_test_root('repo-contract-run-failure') as root:
            summary = run_failure.build_summary(
                {
                    'flow': 'deploy',
                    'stage': 'bootstrap',
                    'status': 'failed',
                    'timestamp': '2026-04-21T12:00:00Z',
                    'log_path': root / 'deploy.log',
                    'summary_json_path': root / 'summary.json',
                    'summary_md_path': root / 'summary.md',
                    'out_json': root / 'out.json',
                    'out_md': root / 'out.md',
                    'exit_code': 2,
                    'format': 'text',
                    'mode': 'online',
                    'resume_from': '',
                    'image_archive_path': '',
                }
            )
            markdown = run_failure.render_markdown(summary)

        self.assertEqual(
            Path(summary['fixed_latest_summary']['json_path']).as_posix(),
            Path(governance_default_path('latest_json', profile_id='one_click_deploy')).as_posix(),
        )
        self.assertTrue(summary['setup_failure_bucket']['doc_path'])
        self.assertTrue(summary['next_steps'])
        self.assertIn('失败阶段', markdown)


if __name__ == '__main__':
    unittest.main()
