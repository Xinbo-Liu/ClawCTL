from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import shlex
import tempfile
import unittest
from collections import OrderedDict
from pathlib import Path
from typing import Iterator
from unittest.mock import patch
from openclaw.control_plane.registry_loader.config import load_registry_service_context
from openclaw.doctor.agent_modules.managed_probe_fixture import ManagedProbeExtensionFixture, materialize_managed_probe_extension
from openclaw.doctor.agent_modules.managed_probe_fixture_repo_markers import write_json
from openclaw.lib.models.env import ModelEnvSpec
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.profiles import CONTROL_PLANE_PROFILE_REGISTRY_ENV

from openclaw.control_plane.dispatch.targets import TargetConfigError, load_targets_config, load_targets_payload
from openclaw.setup.deploy_env import bootstrap_runtime
from openclaw.setup.deploy_env import control_plane
from openclaw.setup.deploy_env import input_values as deploy_input_values
from openclaw.setup.deploy_env import render_validate as deploy_render_validate
from openclaw.setup.deploy_env.dispatch_registry import render as dispatch_registry_render
from openclaw.setup.deploy_env.dispatch_registry.render import render_dispatch_runtime
from openclaw.setup.deploy_env.query import parse_env_file, render_shell_assignment
from openclaw.setup.deploy_env.render_validate import (
    build_default_values,
    build_summary,
    dispatch_target_env_key_map,
    ensure_site_env_example,
    ensure_targets_env_dir,
    merge_target_env_overrides,
    render_env,
    render_blocking_required_keys,
    site_allowed_model_env_keys,
    validate_deploy_env_values,
    validate_local_ingress_acceptance_source,
)
from openclaw.setup.deploy_env.support import detect_first_private_ipv4_from_hostname_i, format_schema_lines, load_schema, validate_value
from openclaw.tests.support.managed_extensions import managed_extensions

ROOT_DIR = resolve_repo_root(Path(__file__))
MANAGED_EXTENSIONS = tuple(sorted(managed_extensions(ROOT_DIR), key=lambda row: row.id))


def _managed_extension_with_dispatch_targets():
    for extension in MANAGED_EXTENSIONS:
        path = extension.root_dir / 'agent' / 'control_plane' / 'registries' / 'dispatch_targets.json'
        if path.is_file():
            return extension
    return None


MANAGED_EXTENSION = _managed_extension_with_dispatch_targets()
MANAGED_EXTENSION_PROFILE_ID = MANAGED_EXTENSION.id if MANAGED_EXTENSION is not None else 'agent_probe'
MANAGED_EXTENSION_RUNTIME_CONFIG_PATH = (
    f'/opt/openclaw-tools/{MANAGED_EXTENSION.default_service_config_path.relative_to(ROOT_DIR).as_posix()}'
    if MANAGED_EXTENSION is not None
    else '/opt/openclaw-tools/agent/extensions/agent_probe/config/control_plane/profiles/agent_probe.service.json'
)


def _managed_extension_with_deploy_env_key(key: str):
    for extension in MANAGED_EXTENSIONS:
        schema_paths = sorted(extension.manifest_dir.glob('*.deploy_env_schema.json'))
        for schema_path in schema_paths:
            if key in schema_path.read_text(encoding='utf-8'):
                return extension
    return None


DEPLOY_ENV_EXTENSION = _managed_extension_with_deploy_env_key('PROBE_NOTIFY_APP_ID')
RELATED_PARTY_EXTENSION = next((extension for extension in MANAGED_EXTENSIONS if extension.id == 'agent_related_party_compare'), None)


def _repo_combination_profile() -> tuple[str | None, Path | None]:
    managed_ids = {extension.id for extension in MANAGED_EXTENSIONS}
    for path in sorted((ROOT_DIR / 'config' / 'control_plane' / 'profiles').glob('*.service.json')):
        context = load_registry_service_context(path)
        extension_ids = [item for item in context['enabledExtensionIds'] if item in managed_ids]
        if len(extension_ids) >= 2:
            return path.name.removesuffix('.service.json'), path
    return None, None


COMBO_PROFILE_ID, COMBO_CONFIG_PATH = _repo_combination_profile()
COMBO_RUNTIME_CONFIG_PATH = f'/opt/openclaw-tools/config/control_plane/profiles/{COMBO_PROFILE_ID}.service.json'


def _combo_extension_owned_site_env_rejected_key() -> str:
    if COMBO_CONFIG_PATH is None:
        return 'EXTENSION_OWNED_KEY'
    schema = load_schema(config_path=COMBO_CONFIG_PATH)
    for field in schema.get('fields', []):
        key = str(field.get('key') or '').strip()
        extension_id = str(field.get('extensionId') or '').strip()
        doc_location = str(field.get('doc_location') or '').strip()
        if key and extension_id and doc_location != 'deploy/site.env':
            return key
    raise AssertionError('combo profile has no extension-owned deploy env key outside deploy/site.env')


def dispatch_registry_path() -> Path:
    if MANAGED_EXTENSION is None:
        raise AssertionError('base release surface has no managed extension dispatch registry')
    path = MANAGED_EXTENSION.root_dir / 'agent' / 'control_plane' / 'registries' / 'dispatch_targets.json'
    if not path.is_file():
        raise AssertionError(f'expected managed dispatch target registry path to exist: {path}')
    return path.resolve()


class DeployEnvSupportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        """构造类内复用的探针 profile，隔离通用部署输入契约与业务包装配。

        返回值：无；保存真实受管扩展 fixture 及其 dispatch registry。
        副作用：在临时仓库写入探针及最小 provider env schema，类结束时清理。
        异常：fixture 构造或声明文件读取错误直接传播，避免掩盖测试前置条件。
        """
        super().setUpClass()
        temporary_repo = tempfile.TemporaryDirectory(prefix='deploy-env-contract-')
        cls.addClassCleanup(temporary_repo.cleanup)
        cls._probe_fixture = materialize_managed_probe_extension(Path(temporary_repo.name), base_repo_root=ROOT_DIR)
        fixture = cls._probe_fixture
        schema_name = 'probe.deploy_env_schema.json'
        manifest = json.loads(fixture.manifest_path.read_text(encoding='utf-8'))
        manifest.setdefault('surfaceFragments', {})['deployEnvSchemaPath'] = schema_name
        write_json(fixture.manifest_path, manifest)
        write_json(fixture.manifest_dir / schema_name, {
            'schema_version': 1,
            'groups': [{'id': 'probe_provider', 'title': '探针 provider 输入'}],
            'fields': [{
                'key': 'PROBE_PROVIDER_WEBHOOK_URL',
                'group': 'probe_provider',
                'required': False,
                'manual_required': False,
                'default_kind': 'literal',
                'default': '',
                'doc_summary': '探针扩展 provider endpoint',
                'doc_location': f'agent/extensions/{fixture.extension_id}/deploy/extension.env',
                'validator': {'type': 'non_empty'},
            }],
        })
        registry_path = fixture.package_root / 'agent' / 'control_plane' / 'registries' / 'dispatch_targets.json'
        cls._probe_dispatch_registry = json.loads(registry_path.read_text(encoding='utf-8'))

    @contextlib.contextmanager
    def _probe_profile_context(self) -> Iterator[ManagedProbeExtensionFixture]:
        """将部署输入解析定位到真实探针仓库，并在退出时恢复模块和环境。

        返回值：上下文提供 ManagedProbeExtensionFixture，供测试选择 profile 与 env 根目录。
        副作用：临时替换部署模块的仓库根及 profile registry 环境覆盖；省略无关的默认模板写入。
        异常：保留 profile、schema、dispatch 加载及输入校验产生的原始异常。
        """
        with (
            patch.object(deploy_render_validate, 'ROOT_DIR', self._probe_fixture.repo_root),
            patch.object(deploy_render_validate, 'ensure_site_env_example', return_value=None),
            patch.dict(os.environ, {CONTROL_PLANE_PROFILE_REGISTRY_ENV: ''}),
        ):
            yield self._probe_fixture

    _EXTENSION_REQUIRED_TESTS = {
        'test_render_env_redacts_target_secrets_from_terminal_output',
        'test_render_env_rejects_extension_keys_in_site_env',
        'test_render_env_loads_active_extension_env_from_extension_root',
        'test_render_env_rejects_undeclared_extension_env_key',
        'test_render_env_does_not_preserve_extension_values_from_existing_output',
        'test_render_env_maps_profile_id_to_runtime_path_and_dispatch_registry',
        'test_render_env_infers_profile_from_runtime_config_path',
        'test_target_env_examples_are_reconciled_to_active_profile_registry',
        'test_dispatch_registry_commands_resolve_active_profile_from_env_file',
        'test_dispatch_runtime_render_writes_registry_loader_snapshot',
        'test_dispatch_runtime_v7_requires_explicit_boundary',
        'test_dispatch_runtime_v7_requires_boolean_publish_latest_boundary',
        'test_dispatch_runtime_local_id_payload_requires_explicit_boundary',
    }
    _COMBO_REQUIRED_TESTS = {
        'test_combo_profile_allows_shared_ollama_values_in_site_env',
        'test_combo_profile_still_rejects_extension_owned_keys_in_site_env',
        'test_combo_profile_input_routes_shared_ollama_to_site_env',
    }
    _DEPLOY_ENV_EXTENSION_REQUIRED_TESTS = {
        'test_deploy_env_requires_truthy_probe_cards_when_live_is_required',
        'test_render_env_rejects_synthetic_notify_keys_in_site_env',
        'test_render_env_rejects_undeclared_synthetic_notify_extension_keys',
        'test_runtime_service_env_render_includes_active_extension_runtime_env',
        'test_runtime_service_env_render_enforces_live_card_truthy_required',
    }

    def setUp(self) -> None:
        if self._testMethodName in self._EXTENSION_REQUIRED_TESTS and MANAGED_EXTENSION is None:
            self.skipTest('base release surface has no repo-managed extension; synthetic probe fixture tests cover extension contracts')
        if self._testMethodName in self._COMBO_REQUIRED_TESTS and COMBO_PROFILE_ID is None:
            self.skipTest('base release surface has no repo combination profile')
        if self._testMethodName in self._DEPLOY_ENV_EXTENSION_REQUIRED_TESTS and DEPLOY_ENV_EXTENSION is None:
            self.skipTest('base release surface has no repo extension deploy-env schema')

    def test_extension_env_examples_only_declare_keys_owned_by_same_extension(self) -> None:
        offenders: list[str] = []
        for extension in MANAGED_EXTENSIONS:
            schema = load_schema(config_path=extension.default_service_config_path)
            key_owners = {
                str(field.get('key') or '').strip(): str(field.get('extensionId') or '').strip()
                for field in schema.get('fields') or []
                if isinstance(field, dict) and str(field.get('key') or '').strip() and str(field.get('extensionId') or '').strip()
            }
            owned_keys = {key for key, owner in key_owners.items() if owner == extension.id}
            example_path = extension.root_dir / 'deploy' / 'extension.env.example'
            if owned_keys:
                self.assertTrue(example_path.is_file(), msg=f'missing extension env example for {extension.id}')
            if not example_path.exists():
                continue
            for key in parse_env_file(example_path):
                owner = key_owners.get(key)
                if owner != extension.id:
                    offenders.append(f'{example_path.relative_to(ROOT_DIR).as_posix()} declares {key} owned by {owner or "<undeclared>"}')

        self.assertEqual([], offenders)

    def test_format_schema_lines_accepts_single_string(self) -> None:
        self.assertEqual(
            format_schema_lines('OPENCLAW_TLS_CN=openclaw.internal.example'),
            ['OPENCLAW_TLS_CN=openclaw.internal.example'],
        )

    def test_format_schema_lines_formats_iterables_and_placeholders(self) -> None:
        self.assertEqual(
            format_schema_lines(['ip={DETECTED_INGRESS_IP}', 'static'], detected_ingress_ip='10.0.0.8'),
            ['ip=10.0.0.8', 'static'],
        )

    def test_control_plane_config_env_is_generated_from_profile_id(self) -> None:
        payload = json.loads((ROOT_DIR / 'config' / 'deploy_env' / 'schema.json').read_text(encoding='utf-8'))
        fields = payload.get('fields') if isinstance(payload, dict) else []
        profile_row = next(item for item in fields if isinstance(item, dict) and str(item.get('key') or '') == 'OPENCLAW_CONTROL_PLANE_PROFILE')
        path_row = next(item for item in fields if isinstance(item, dict) and str(item.get('key') or '') == 'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH')
        self.assertEqual(profile_row.get('default'), 'agent_platform')
        self.assertEqual((profile_row.get('validator') or {}).get('type'), 'control_plane_profile_id')
        self.assertEqual(path_row.get('generated'), 'control_plane_service_config_path')
        self.assertTrue(path_row.get('site_env_hidden'))
        self.assertNotIn('default', path_row)

    def test_tls_hostname_validator_rejects_injection_and_ip_literals(self) -> None:
        validator = {'type': 'tls_hostname'}
        self.assertEqual(validate_value('openclaw.internal.example', validator), '')
        for value in (
            'good.internal; return 200 injected;',
            'bad_name.internal',
            '*.internal.example',
            'openclaw.internal.',
            '192.168.0.10',
            '999.999.999.999',
            'fd00::10',
        ):
            with self.subTest(value=value):
                self.assertTrue(validate_value(value, validator))

    def test_single_token_validator_rejects_multiple_values(self) -> None:
        validator = {'type': 'single_token'}
        self.assertEqual(validate_value('channel_xxx', validator), '')
        for value in ('channel_a,channel_b', 'channel_a；channel_b', 'channel_a channel_b'):
            with self.subTest(value=value):
                self.assertIn('只填写一个值', validate_value(value, validator))
        text_validator = {'type': 'single_text'}
        self.assertEqual(validate_value('探针 校验 通知', text_validator), '')
        self.assertIn('只填写一个值', validate_value('机器人A,机器人B', text_validator))

    def test_ingress_source_cidr_csv_accepts_private_and_precise_public_sources(self) -> None:
        validator = {'type': 'ingress_source_cidr_csv'}
        self.assertEqual(validate_value('10.0.0.0/24,127.0.0.1/32,fd00::/8,::1/128', validator), '')
        self.assertEqual(validate_value('8.8.8.8/32', validator), '')
        self.assertIn('必须提供至少一个 CIDR', validate_value('', validator))
        self.assertIn('只允许私网、loopback 或精确公网主机 CIDR', validate_value('2001:db8::/32', validator))

    def test_deploy_env_requires_exact_local_full_test_source(self) -> None:
        values = OrderedDict([
            ('OPENCLAW_INGRESS_LISTEN_IP', '192.168.91.128'),
            ('OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS', '192.168.91.1/32,192.168.91.0/24'),
        ])
        errors = validate_local_ingress_acceptance_source(values, allow_placeholders=True)
        self.assertIn('192.168.91.128/32', '\n'.join(errors))

        values['OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS'] = '192.168.91.1/32,192.168.91.128/32'
        self.assertEqual(validate_local_ingress_acceptance_source(values, allow_placeholders=True), [])

    def test_deploy_env_conditional_required_tls_paths_are_enforced(self) -> None:
        schema = json.loads((ROOT_DIR / 'config' / 'deploy_env' / 'schema.json').read_text(encoding='utf-8'))
        values = build_default_values(schema=schema, dispatch_registry={'targets': []})
        values.update({
            'OPENCLAW_INGRESS_LISTEN_IP': '192.168.91.128',
            'OPENCLAW_TLS_CN': 'openclaw.internal.example',
            'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS': '192.168.91.128/32',
            'OPENCLAW_TLS_MODE': 'provided_files',
            'OPENCLAW_TLS_CERT_SOURCE_PATH': '',
            'OPENCLAW_TLS_KEY_SOURCE_PATH': '',
        })

        errors = validate_deploy_env_values(values, schema, model_specs={})

        self.assertIn('OPENCLAW_TLS_CERT_SOURCE_PATH: 不能为空', errors)
        self.assertIn('OPENCLAW_TLS_KEY_SOURCE_PATH: 不能为空', errors)

    def test_deploy_env_conditional_required_external_acl_evidence_is_enforced(self) -> None:
        schema = json.loads((ROOT_DIR / 'config' / 'deploy_env' / 'schema.json').read_text(encoding='utf-8'))
        values = build_default_values(schema=schema, dispatch_registry={'targets': []})
        values.update({
            'OPENCLAW_INGRESS_LISTEN_IP': '192.168.91.128',
            'OPENCLAW_TLS_CN': 'openclaw.internal.example',
            'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS': '192.168.91.128/32',
            'OPENCLAW_INGRESS_BOUNDARY_MODE': 'external_acl',
            'OPENCLAW_INGRESS_BOUNDARY_EVIDENCE_PATH': '',
        })

        errors = validate_deploy_env_values(values, schema, model_specs={})

        self.assertIn('OPENCLAW_INGRESS_BOUNDARY_EVIDENCE_PATH: 不能为空', errors)

    def test_deploy_env_requires_truthy_probe_cards_when_live_is_required(self) -> None:
        schema = load_schema(config_path=DEPLOY_ENV_EXTENSION.default_service_config_path)
        values = build_default_values(
            schema=schema,
            control_plane_profile=DEPLOY_ENV_EXTENSION.id,
            dispatch_registry={'targets': []},
        )
        self.assertEqual(values['PROBE_NOTIFY_CARD_ACTION_TRIGGER_ENABLED'], '1')
        for live_value in ('1', 'True', 'YES', 'On'):
            with self.subTest(live_value=live_value):
                values.update({
                    'OPENCLAW_INGRESS_LISTEN_IP': '192.168.91.128',
                    'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS': '192.168.91.128/32',
                    'PROBE_NOTIFY_LIVE_REQUIRED': live_value,
                    'PROBE_NOTIFY_CARD_ENABLED': '0',
                    'PROBE_NOTIFY_CARD_UPDATE_ENABLED': '',
                    'PROBE_NOTIFY_CARD_ACTION_TRIGGER_ENABLED': '0',
                })
                errors = validate_deploy_env_values(values, schema, allow_placeholders=True, model_specs={})
                joined = '\n'.join(errors)
                self.assertIn('PROBE_NOTIFY_CARD_ENABLED: 必须启用', joined)
                self.assertIn('PROBE_NOTIFY_CARD_UPDATE_ENABLED: 必须启用', joined)
                self.assertNotIn('PROBE_NOTIFY_CARD_ACTION_TRIGGER_ENABLED: 必须启用', joined)

        values['PROBE_NOTIFY_LIVE_REQUIRED'] = 'On'
        values['PROBE_NOTIFY_CARD_ENABLED'] = '1'
        values['PROBE_NOTIFY_CARD_UPDATE_ENABLED'] = '1'
        values['PROBE_NOTIFY_CARD_ACTION_TRIGGER_ENABLED'] = '0'
        errors = validate_deploy_env_values(values, schema, allow_placeholders=True, model_specs={})
        self.assertFalse([item for item in errors if 'PROBE_NOTIFY_CARD' in item])

    @unittest.skipIf(RELATED_PARTY_EXTENSION is None, 'agent_related_party_compare extension not installed')
    def test_related_party_live_requires_feishu_identity_and_acceptance_task(self) -> None:
        schema = load_schema(config_path=RELATED_PARTY_EXTENSION.default_service_config_path)
        values = build_default_values(
            schema=schema,
            control_plane_profile=RELATED_PARTY_EXTENSION.id,
        )
        values.update({
            'OPENCLAW_INGRESS_LISTEN_IP': '192.168.91.128',
            'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS': '192.168.91.128/32',
            'RELATED_PARTY_COMPARE_LIVE_REQUIRED': '1',
            'RELATED_PARTY_COMPARE_FEISHU_CARD_ENABLED': '1',
            'RELATED_PARTY_COMPARE_FEISHU_CARD_UPDATE_ENABLED': '1',
            'RELATED_PARTY_COMPARE_FEISHU_CARD_ACTION_TRIGGER_ENABLED': '1',
            'CHANNEL_GATEWAY_FEISHU_RELATED_PARTY_COMPARE_APP_ID': '',
            'CHANNEL_GATEWAY_FEISHU_RELATED_PARTY_COMPARE_APP_SECRET': '',
            'CHANNEL_GATEWAY_FEISHU_RELATED_PARTY_COMPARE_CHAT_ID': '',
            'CHANNEL_GATEWAY_FEISHU_RELATED_PARTY_COMPARE_ROBOT_ID': '',
            'RELATED_PARTY_COMPARE_LIVE_ACCEPTANCE_TASK_ID': '',
        })

        summary = build_summary(values, Path('deploy/.env'), schema, model_specs={})
        pending_by_key = {
            str(row['key']): row
            for row in summary['required_manual_keys']
            if row['status'] != 'filled'
        }

        self.assertEqual(pending_by_key['CHANNEL_GATEWAY_FEISHU_RELATED_PARTY_COMPARE_APP_ID']['error'], '不能为空')
        self.assertEqual(pending_by_key['CHANNEL_GATEWAY_FEISHU_RELATED_PARTY_COMPARE_APP_SECRET']['error'], '长度不能小于 8')
        self.assertEqual(pending_by_key['CHANNEL_GATEWAY_FEISHU_RELATED_PARTY_COMPARE_CHAT_ID']['error'], '不能为空')
        self.assertEqual(pending_by_key['CHANNEL_GATEWAY_FEISHU_RELATED_PARTY_COMPARE_ROBOT_ID']['error'], '不能为空')
        self.assertEqual(pending_by_key['RELATED_PARTY_COMPARE_LIVE_ACCEPTANCE_TASK_ID']['error'], '不能为空')
        self.assertEqual(
            render_blocking_required_keys(summary, schema),
            [
                'CHANNEL_GATEWAY_FEISHU_RELATED_PARTY_COMPARE_APP_ID',
                'CHANNEL_GATEWAY_FEISHU_RELATED_PARTY_COMPARE_APP_SECRET',
                'CHANNEL_GATEWAY_FEISHU_RELATED_PARTY_COMPARE_CHAT_ID',
                'CHANNEL_GATEWAY_FEISHU_RELATED_PARTY_COMPARE_ROBOT_ID',
                'RELATED_PARTY_COMPARE_LIVE_ACCEPTANCE_TASK_ID',
            ],
        )

    @unittest.skipIf(RELATED_PARTY_EXTENSION is None, 'agent_related_party_compare extension not installed')
    def test_render_env_rejects_related_party_live_without_feishu_identity(self) -> None:
        def fail(_: str, message: str, code: int) -> None:
            raise AssertionError(f'{code}: {message}')

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            site_env = tmp / 'site.env'
            extension_root = tmp / 'extensions'
            extension_deploy = extension_root / RELATED_PARTY_EXTENSION.id / 'deploy'
            output = tmp / '.env'
            extension_deploy.mkdir(parents=True)
            site_env.write_text(
                '\n'.join([
                    f'OPENCLAW_CONTROL_PLANE_PROFILE={RELATED_PARTY_EXTENSION.id}',
                    'OPENCLAW_INGRESS_LISTEN_IP=192.168.91.128',
                    'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS=192.168.91.1/32,192.168.91.128/32',
                ]) + '\n',
                encoding='utf-8',
            )
            (extension_deploy / 'extension.env').write_text(
                '\n'.join([
                    'RELATED_PARTY_COMPARE_MODEL_PROFILE_REF=ollama_default',
                    'OLLAMA_BASE_URL=http://ollama.internal:11434',
                    'OLLAMA_MODEL_REF=qwen-test',
                    'RELATED_PARTY_COMPARE_LIVE_REQUIRED=1',
                    'RELATED_PARTY_COMPARE_FEISHU_CARD_ENABLED=1',
                    'RELATED_PARTY_COMPARE_FEISHU_CARD_UPDATE_ENABLED=1',
                    'RELATED_PARTY_COMPARE_FEISHU_CARD_ACTION_TRIGGER_ENABLED=1',
                    'RELATED_PARTY_COMPARE_LIVE_ACCEPTANCE_TASK_ID=feishu-task',
                ]) + '\n',
                encoding='utf-8',
            )

            with self.assertRaisesRegex(AssertionError, '部署输入仍有运行必填项未补齐|deploy env 配置校验未通过'):
                render_env(
                    [
                        '--site-env', str(site_env),
                        '--targets-env-dir', str(tmp / 'targets.d'),
                        '--extension-env-root', str(extension_root),
                        '--output', str(output),
                        '--summary-json', str(tmp / 'summary.json'),
                    ],
                    fail=fail,
                    note=lambda *_: None,
                )

            self.assertFalse(output.exists())

    def test_hostname_i_detection_returns_first_private_ipv4(self) -> None:
        with patch('openclaw.setup.deploy_env.support.subprocess.check_output', return_value='203.0.113.9 10.2.3.4 192.168.1.8\n'):
            self.assertEqual(detect_first_private_ipv4_from_hostname_i(), '10.2.3.4')

    def test_query_env_shell_assignment_is_safe_and_round_trips(self) -> None:
        def fail(_: str, message: str, code: int) -> None:
            raise AssertionError(f'{code}: {message}')

        value = "C:/Program Files/OpenClaw/cert $(not-executed) 'quoted'"
        assignment = render_shell_assignment('OPENCLAW_TLS_CERT_SOURCE_PATH', value, fail=fail)
        self.assertEqual(assignment, f'OPENCLAW_TLS_CERT_SOURCE_PATH={shlex.quote(value)}')
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / 'deploy.env'
            env_path.write_text(f'{assignment}\nEMPTY={shlex.quote("")}\n', encoding='utf-8')
            parsed = parse_env_file(env_path)
        self.assertEqual(parsed['OPENCLAW_TLS_CERT_SOURCE_PATH'], value)
        self.assertEqual(parsed['EMPTY'], '')

    def test_render_env_redacts_target_secrets_from_terminal_output(self) -> None:
        def fail(_: str, message: str, code: int) -> None:
            raise AssertionError(f'{code}: {message}')

        def note(prefix: str, message: str) -> None:
            print(f'[{prefix}] {message}')

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            site_env = tmp / 'site.env'
            output = tmp / '.env'
            summary = tmp / 'config_summary.json'
            targets_dir = tmp / 'targets.d'
            targets_dir.mkdir()
            site_env.write_text(
                '\n'.join([
                    f'OPENCLAW_CONTROL_PLANE_PROFILE={MANAGED_EXTENSION_PROFILE_ID}',
                    'OPENCLAW_INGRESS_LISTEN_IP=192.168.91.128',
                    'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS=192.168.91.1/32,192.168.91.128/32',
                ]) + '\n',
                encoding='utf-8',
            )
            (targets_dir / 'dispatch_primary.env').write_text(
                '\n'.join([
                    'CHANNEL_GATEWAY_FEISHU_PREMARKET_PRIMARY_WEBHOOK_URL=https://example.invalid/hook-secret',
                    'CHANNEL_GATEWAY_FEISHU_PREMARKET_PRIMARY_SECRET=bot-secret-value',
                ]) + '\n',
                encoding='utf-8',
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = render_env(
                    [
                        '--site-env', str(site_env),
                        '--targets-env-dir', str(targets_dir),
                        '--output', str(output),
                        '--summary-json', str(summary),
                    ],
                    fail=fail,
                    note=note,
                )

            self.assertEqual(exit_code, 0)
            rendered = stdout.getvalue()
            self.assertIn('CHANNEL_GATEWAY_FEISHU_PREMARKET_PRIMARY_WEBHOOK_URL=<redacted>', rendered)
            self.assertIn('CHANNEL_GATEWAY_FEISHU_PREMARKET_PRIMARY_SECRET=<redacted>', rendered)
            self.assertNotIn('hook-secret', rendered)
            self.assertNotIn('bot-secret-value', rendered)
            written = output.read_text(encoding='utf-8')
            self.assertIn('CHANNEL_GATEWAY_FEISHU_PREMARKET_PRIMARY_WEBHOOK_URL=https://example.invalid/hook-secret', written)
            self.assertIn('CHANNEL_GATEWAY_FEISHU_PREMARKET_PRIMARY_SECRET=bot-secret-value', written)

    def test_render_env_rejects_target_env_outside_active_profile(self) -> None:
        def fail(_: str, message: str, code: int) -> None:
            raise AssertionError(f'{code}: {message}')

        with self._probe_profile_context() as fixture, tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            site_env = tmp / 'site.env'
            output = tmp / '.env'
            summary = tmp / 'config_summary.json'
            targets_dir = tmp / 'targets.d'
            targets_dir.mkdir()
            site_env.write_text(
                '\n'.join([
                    f'OPENCLAW_CONTROL_PLANE_PROFILE={fixture.extension_id}',
                    'OPENCLAW_INGRESS_LISTEN_IP=192.168.91.128',
                    'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS=192.168.91.1/32,192.168.91.128/32',
                ]) + '\n',
                encoding='utf-8',
            )
            (targets_dir / 'unregistered_target.env').write_text('UNREGISTERED_WEBHOOK_URL=https://example.invalid/hook\n', encoding='utf-8')

            with self.assertRaisesRegex(AssertionError, '不属于当前 active profile'):
                render_env(
                    [
                        '--site-env', str(site_env), '--targets-env-dir', str(targets_dir),
                        '--extension-env-root', str(fixture.repo_root / 'agent' / 'extensions'),
                        '--output', str(output), '--summary-json', str(summary),
                    ],
                    fail=fail,
                    note=lambda *_: None,
                )

    def test_render_env_rejects_dispatch_keys_in_site_env(self) -> None:
        def fail(_: str, message: str, code: int) -> None:
            raise AssertionError(f'{code}: {message}')

        with self._probe_profile_context() as fixture, tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            site_env = tmp / 'site.env'
            site_env.write_text(
                '\n'.join([
                    f'OPENCLAW_CONTROL_PLANE_PROFILE={fixture.extension_id}',
                    'PROBE_PROVIDER_WEBHOOK_URL=https://example.invalid/hook',
                ]) + '\n',
                encoding='utf-8',
            )
            with self.assertRaisesRegex(AssertionError, '不允许填写扩展变量'):
                render_env(
                    [
                        '--site-env', str(site_env),
                        '--targets-env-dir', str(tmp / 'targets.d'),
                        '--extension-env-root', str(fixture.repo_root / 'agent' / 'extensions'),
                        '--output', str(tmp / '.env'),
                        '--summary-json', str(tmp / 'summary.json'),
                    ],
                    fail=fail,
                    note=lambda *_: None,
                )

    def test_render_env_rejects_canonical_target_keys_in_site_env(self) -> None:
        def fail(_: str, message: str, code: int) -> None:
            raise AssertionError(f'{code}: {message}')

        with self._probe_profile_context() as fixture, tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            site_env = tmp / 'site.env'
            target = self._probe_dispatch_registry['targets'][0]
            site_env.write_text(
                '\n'.join([
                    f'OPENCLAW_CONTROL_PLANE_PROFILE={fixture.extension_id}',
                    f"{target['enabledEnv']}=true",
                ]) + '\n',
                encoding='utf-8',
            )
            with self.assertRaisesRegex(AssertionError, '不允许填写 target 级变量'):
                render_env(
                    [
                        '--site-env', str(site_env),
                        '--targets-env-dir', str(tmp / 'targets.d'),
                        '--extension-env-root', str(fixture.repo_root / 'agent' / 'extensions'),
                        '--output', str(tmp / '.env'),
                        '--summary-json', str(tmp / 'summary.json'),
                    ],
                    fail=fail,
                    note=lambda *_: None,
                )

    def test_render_env_rejects_extension_keys_in_site_env(self) -> None:
        def fail(_: str, message: str, code: int) -> None:
            raise AssertionError(f'{code}: {message}')

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            site_env = tmp / 'site.env'
            site_env.write_text(
                '\n'.join([
                    f'OPENCLAW_CONTROL_PLANE_PROFILE={MANAGED_EXTENSION_PROFILE_ID}',
                    'OLLAMA_BASE_URL=http://ollama.invalid:11434',
                ]) + '\n',
                encoding='utf-8',
            )
            with self.assertRaisesRegex(AssertionError, '不允许填写扩展变量'):
                render_env(
                    [
                        '--site-env', str(site_env),
                        '--targets-env-dir', str(tmp / 'targets.d'),
                        '--extension-env-root', str(tmp / 'extensions'),
                        '--output', str(tmp / '.env'),
                        '--summary-json', str(tmp / 'summary.json'),
                    ],
                    fail=fail,
                    note=lambda *_: None,
                )

    def test_combo_profile_allows_shared_ollama_values_in_site_env(self) -> None:
        def fail(_: str, message: str, code: int) -> None:
            raise AssertionError(f'{code}: {message}')

        schema = load_schema(config_path=COMBO_CONFIG_PATH)
        ollama_fields = [field for field in schema.get('fields') or [] if field.get('key') in {'OLLAMA_BASE_URL', 'OLLAMA_MODEL_REF'}]
        self.assertEqual({field.get('key') for field in ollama_fields}, {'OLLAMA_BASE_URL', 'OLLAMA_MODEL_REF'})
        for field in ollama_fields:
            self.assertEqual(field.get('doc_location'), 'deploy/site.env')
            self.assertFalse(field.get('extensionId'))
            self.assertTrue(field.get('required'))
            self.assertTrue(field.get('manual_required'))

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            site_env = tmp / 'site.env'
            output = tmp / '.env'
            summary = tmp / 'config_summary.json'
            site_env.write_text(
                '\n'.join([
                    f'OPENCLAW_CONTROL_PLANE_PROFILE={COMBO_PROFILE_ID}',
                    'OPENCLAW_INGRESS_LISTEN_IP=192.168.91.128',
                    'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS=192.168.91.1/32,192.168.91.128/32',
                    'OLLAMA_BASE_URL=http://ollama.internal:11434',
                    'OLLAMA_MODEL_REF=qwen-test',
                ]) + '\n',
                encoding='utf-8',
            )

            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = render_env(
                    [
                        '--site-env', str(site_env),
                        '--targets-env-dir', str(tmp / 'targets.d'),
                        '--extension-env-root', str(tmp / 'extensions'),
                        '--output', str(output),
                        '--summary-json', str(summary),
                    ],
                    fail=fail,
                    note=lambda *_: None,
                )

            self.assertEqual(exit_code, 0)
            parsed = parse_env_file(output)
            self.assertEqual(parsed['OPENCLAW_CONTROL_PLANE_PROFILE'], COMBO_PROFILE_ID)
            self.assertEqual(parsed['OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH'], COMBO_RUNTIME_CONFIG_PATH)
            self.assertEqual(parsed['OLLAMA_BASE_URL'], 'http://ollama.internal:11434')
            self.assertEqual(parsed['OLLAMA_MODEL_REF'], 'qwen-test')
            required_keys = [row['key'] for row in json.loads(summary.read_text(encoding='utf-8'))['required_manual_keys']]
            self.assertEqual(required_keys.count('OLLAMA_BASE_URL'), 1)
            self.assertEqual(required_keys.count('OLLAMA_MODEL_REF'), 1)

    def test_combo_profile_still_rejects_extension_owned_keys_in_site_env(self) -> None:
        def fail(_: str, message: str, code: int) -> None:
            raise AssertionError(f'{code}: {message}')

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            site_env = tmp / 'site.env'
            rejected_key = _combo_extension_owned_site_env_rejected_key()
            site_env.write_text(
                '\n'.join([
                    f'OPENCLAW_CONTROL_PLANE_PROFILE={COMBO_PROFILE_ID}',
                    'OPENCLAW_INGRESS_LISTEN_IP=192.168.91.128',
                    'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS=192.168.91.1/32,192.168.91.128/32',
                    f'{rejected_key}=app_test',
                ]) + '\n',
                encoding='utf-8',
            )
            with self.assertRaisesRegex(AssertionError, f'不允许填写扩展变量：{rejected_key}'):
                render_env(
                    [
                        '--site-env', str(site_env),
                        '--targets-env-dir', str(tmp / 'targets.d'),
                        '--extension-env-root', str(tmp / 'extensions'),
                        '--output', str(tmp / '.env'),
                        '--summary-json', str(tmp / 'summary.json'),
                    ],
                    fail=fail,
                    note=lambda *_: None,
                )

    def test_combo_profile_input_routes_shared_ollama_to_site_env(self) -> None:
        routes = deploy_input_values.build_input_routes(COMBO_PROFILE_ID)

        for key in ('OLLAMA_BASE_URL', 'OLLAMA_MODEL_REF'):
            with self.subTest(key=key):
                route = routes[key]
                self.assertEqual(route.scope, 'site')
                self.assertEqual(route.owner, '')
                self.assertEqual(route.rel_path, 'deploy/site.env')

    def test_site_allowed_model_env_keys_only_include_shared_or_unowned_specs(self) -> None:
        specs = {
            'SHARED_MODEL_ENV': ModelEnvSpec('SHARED_MODEL_ENV', True, False, 'non_empty', 'ext_a:model', 'model_ref'),
            'EXTENSION_ONLY_ENV': ModelEnvSpec('EXTENSION_ONLY_ENV', True, False, 'non_empty', 'ext_a:model', 'model_ref'),
            'PLATFORM_MODEL_ENV': ModelEnvSpec('PLATFORM_MODEL_ENV', True, False, 'non_empty', 'platform_model', 'model_ref'),
        }

        with patch(
            'openclaw.setup.deploy_env.render_validate.control_plane_repo_combination_profile',
            return_value={'sharedDeployEnvFields': [{'keys': ['SHARED_MODEL_ENV']}]},
        ):
            allowed = site_allowed_model_env_keys('combo_profile', specs)

        self.assertIn('SHARED_MODEL_ENV', allowed)
        self.assertIn('PLATFORM_MODEL_ENV', allowed)
        self.assertNotIn('EXTENSION_ONLY_ENV', allowed)

    def test_deploy_input_validate_only_routes_without_writing(self) -> None:
        routes = {
            'SECRET_TOKEN': deploy_input_values.DeployInputRoute(
                key='SECRET_TOKEN',
                scope='extension',
                owner='agent_premarket',
                rel_path='agent/extensions/agent_premarket/deploy/extension.env',
                secret=True,
            ),
            'MISSING_REQUIRED': deploy_input_values.DeployInputRoute(
                key='MISSING_REQUIRED',
                scope='site',
                owner='',
                rel_path='deploy/site.env',
                required=True,
                manual_required=True,
            ),
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            input_file = Path(tmpdir) / 'input.env'
            input_file.write_text('SECRET_TOKEN=super-secret\n', encoding='utf-8')
            stdout = io.StringIO()
            with (
                patch.object(deploy_input_values, '_assert_owner_only_input_file'),
                patch.object(deploy_input_values, 'build_input_routes', return_value=routes),
                patch.object(deploy_input_values, '_dispatch_target_routes_by_key', return_value={}),
                patch.object(deploy_input_values, '_write_env_file') as write_env_file,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = deploy_input_values.apply_input_values(
                    'combo_profile',
                    input_file,
                    init=True,
                    validate_only=True,
                )

        self.assertEqual(exit_code, 0)
        write_env_file.assert_not_called()
        output = stdout.getvalue()
        self.assertIn('validate_only=true', output)
        self.assertIn('SECRET_TOKEN=<redacted>', output)
        self.assertNotIn('super-secret', output)
        self.assertIn('MISSING_REQUIRED -> deploy/site.env', output)

    def test_input_routes_reject_schema_key_owner_conflict(self) -> None:
        schema = {
            'groups': [{'id': 'g', 'title': 'group'}],
            'fields': [
                {'key': 'DUP_KEY', 'group': 'g', 'extensionId': 'ext_a'},
                {'key': 'DUP_KEY', 'group': 'g', 'extensionId': 'ext_b'},
            ],
        }
        stderr = io.StringIO()

        with (
            patch.object(deploy_input_values, 'host_config_path_for_profile', return_value=ROOT_DIR / 'dummy.json'),
            patch.object(deploy_input_values, 'load_schema', return_value=schema),
            patch.object(deploy_input_values, '_shared_keys_for_profile', return_value=set()),
            patch.object(deploy_input_values, 'active_model_env_specs', return_value={}),
            patch.object(deploy_input_values.deploy_env_dispatch_registry_lib, 'load_dispatch_targets', return_value={'targets': []}),
            patch.object(deploy_input_values, 'dispatch_target_env_key_map', return_value={}),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as ctx,
        ):
            deploy_input_values.build_input_routes('combo_profile')

        self.assertEqual(ctx.exception.code, 2)
        self.assertIn('部署输入键归属冲突', stderr.getvalue())

    def test_extension_env_check_reads_shared_model_values_from_site_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            site_env = tmp / 'site.env'
            extension_env = tmp / 'extension.env'
            schema_path = tmp / 'schema.json'
            site_env.write_text(
                'OLLAMA_BASE_URL=http://ollama.internal:11434\nOLLAMA_MODEL_REF=qwen-test\n',
                encoding='utf-8',
            )
            extension_env.write_text('', encoding='utf-8')
            schema_path.write_text(
                json.dumps(
                    {
                        'groups': [{'id': 'model_providers', 'title': '模型 provider'}],
                        'fields': [
                            {'key': 'OLLAMA_BASE_URL', 'group': 'model_providers', 'required': True, 'manual_required': True},
                            {'key': 'OLLAMA_MODEL_REF', 'group': 'model_providers', 'required': True, 'manual_required': True},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding='utf-8',
            )
            routes = {
                key: deploy_input_values.DeployInputRoute(
                    key=key,
                    scope='site',
                    owner='',
                    rel_path='deploy/site.env',
                    required=True,
                    manual_required=True,
                    group='model_providers',
                    group_title='模型 provider',
                )
                for key in ('OLLAMA_BASE_URL', 'OLLAMA_MODEL_REF')
            }

            with (
                patch.object(deploy_input_values, 'DEFAULT_SITE_ENV_PATH', site_env),
                patch.object(deploy_input_values, 'build_input_routes', return_value=routes),
                patch.object(deploy_input_values, '_enabled_managed_extension_ids', return_value=['agent_premarket']),
                patch.object(deploy_input_values, '_extension_env_path', return_value=extension_env),
                patch.object(deploy_input_values, '_extension_schema_path', return_value=schema_path),
                contextlib.redirect_stdout(io.StringIO()) as stdout,
            ):
                exit_code = deploy_input_values.check_extension_env_values('combo_profile')

        self.assertEqual(exit_code, 0)
        self.assertIn('扩展 env 必填项已闭合', stdout.getvalue())

    def test_extension_env_check_reports_missing_shared_model_values_in_site_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            site_env = tmp / 'site.env'
            extension_env = tmp / 'extension.env'
            schema_path = tmp / 'schema.json'
            site_env.write_text('', encoding='utf-8')
            extension_env.write_text('OLLAMA_BASE_URL=http://wrong-location.invalid:11434\n', encoding='utf-8')
            schema_path.write_text(
                json.dumps(
                    {
                        'groups': [{'id': 'model_providers', 'title': '模型 provider'}],
                        'fields': [{'key': 'OLLAMA_BASE_URL', 'group': 'model_providers', 'required': True, 'manual_required': True}],
                    },
                    ensure_ascii=False,
                ),
                encoding='utf-8',
            )
            routes = {
                'OLLAMA_BASE_URL': deploy_input_values.DeployInputRoute(
                    key='OLLAMA_BASE_URL',
                    scope='site',
                    owner='',
                    rel_path='deploy/site.env',
                    required=True,
                    manual_required=True,
                    group='model_providers',
                    group_title='模型 provider',
                )
            }

            stdout = io.StringIO()
            with (
                patch.object(deploy_input_values, 'DEFAULT_SITE_ENV_PATH', site_env),
                patch.object(deploy_input_values, 'build_input_routes', return_value=routes),
                patch.object(deploy_input_values, '_enabled_managed_extension_ids', return_value=['agent_premarket']),
                patch.object(deploy_input_values, '_extension_env_path', return_value=extension_env),
                patch.object(deploy_input_values, '_extension_schema_path', return_value=schema_path),
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = deploy_input_values.check_extension_env_values('combo_profile', format_name='json')

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(payload['missing'][0]['scope'], 'site')
        self.assertEqual(payload['missing'][0]['envPath'], 'deploy/site.env')
        self.assertIn('apply_site_env_values.sh', payload['missing'][0]['fixCommand'])

    def test_extension_env_check_extension_option_keeps_extension_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            extension_env = tmp / 'extension.env'
            schema_path = tmp / 'schema.json'
            extension_env.write_text('', encoding='utf-8')
            schema_path.write_text(
                json.dumps(
                    {
                        'groups': [{'id': 'model_providers', 'title': '模型 provider'}],
                        'fields': [{'key': 'OLLAMA_BASE_URL', 'group': 'model_providers', 'required': True, 'manual_required': True}],
                    },
                    ensure_ascii=False,
                ),
                encoding='utf-8',
            )
            stdout = io.StringIO()
            with (
                patch.object(deploy_input_values, '_extension_env_path', return_value=extension_env),
                patch.object(deploy_input_values, '_extension_schema_path', return_value=schema_path),
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = deploy_input_values.check_single_extension_env_values('agent_premarket', format_name='json')

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(payload['missing'][0]['scope'], 'extension')
        self.assertEqual(payload['missing'][0]['owner'], 'agent_premarket')
        self.assertEqual(payload['missing'][0]['envPath'], 'agent/extensions/agent_premarket/deploy/extension.env')
        self.assertIn('apply_extension_env_values.sh', payload['missing'][0]['fixCommand'])
        self.assertNotIn('apply_site_env_values.sh', payload['missing'][0]['fixCommand'])

    def test_apply_deploy_input_values_routes_site_extension_and_target_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            input_env = tmp / 'owner-input.env'
            site_env = tmp / 'deploy' / 'site.env'
            extension_env = tmp / 'extensions' / 'ext_a' / 'deploy' / 'extension.env'
            targets_dir = tmp / 'targets.d'
            input_env.write_text(
                '\n'.join(
                    [
                        'SITE_KEY=site-value',
                        'EXT_SECRET=extension-secret',
                        'TARGET_KEY=target-value',
                    ]
                )
                + '\n',
                encoding='utf-8',
            )
            input_env.chmod(0o600)
            routes = {
                'SITE_KEY': deploy_input_values.DeployInputRoute('SITE_KEY', 'site', '', 'deploy/site.env'),
                'EXT_SECRET': deploy_input_values.DeployInputRoute(
                    'EXT_SECRET',
                    'extension',
                    'ext_a',
                    'agent/extensions/ext_a/deploy/extension.env',
                    secret=True,
                ),
                'TARGET_KEY': deploy_input_values.DeployInputRoute('TARGET_KEY', 'target', 'dispatch_primary', 'deploy/targets.d/dispatch_primary.env'),
            }
            stdout = io.StringIO()

            with (
                patch.object(deploy_input_values, 'DEFAULT_SITE_ENV_PATH', site_env),
                patch.object(deploy_input_values, 'DEFAULT_TARGETS_ENV_DIR', targets_dir),
                patch.object(deploy_input_values, 'build_input_routes', return_value=routes),
                patch.object(deploy_input_values, '_dispatch_target_routes_by_key', return_value={}),
                patch.object(deploy_input_values, '_extension_env_path', return_value=extension_env),
                patch.object(deploy_input_values, '_target_example_lines', return_value=['TARGET_ENABLE=true', 'TARGET_KEY=__TARGET_KEY__']),
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = deploy_input_values.apply_input_values('combo_profile', input_env, init=True)

            self.assertEqual(exit_code, 0)
            self.assertEqual(parse_env_file(site_env)['SITE_KEY'], 'site-value')
            self.assertEqual(parse_env_file(extension_env)['EXT_SECRET'], 'extension-secret')
            parsed_target = parse_env_file(targets_dir / 'dispatch_primary.env')
            self.assertEqual(parsed_target['TARGET_ENABLE'], 'true')
            self.assertEqual(parsed_target['TARGET_KEY'], 'target-value')
            self.assertIn('EXT_SECRET=<redacted>', stdout.getvalue())

    def test_apply_deploy_input_values_filters_shared_keys_from_extension_example(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            input_env = tmp / 'owner-input.env'
            site_env = tmp / 'deploy' / 'site.env'
            extension_env = tmp / 'extensions' / 'ext_a' / 'deploy' / 'extension.env'
            extension_env.parent.mkdir(parents=True)
            extension_env.with_name('extension.env.example').write_text(
                '\n'.join(
                    [
                        'OLLAMA_BASE_URL=http://stale.invalid:11434',
                        'EXT_KEY=__EXT_KEY__',
                    ]
                )
                + '\n',
                encoding='utf-8',
            )
            input_env.write_text(
                '\n'.join(
                    [
                        'OLLAMA_BASE_URL=http://ollama.internal:11434',
                        'EXT_KEY=extension-value',
                    ]
                )
                + '\n',
                encoding='utf-8',
            )
            input_env.chmod(0o600)
            routes = {
                'OLLAMA_BASE_URL': deploy_input_values.DeployInputRoute('OLLAMA_BASE_URL', 'site', '', 'deploy/site.env'),
                'EXT_KEY': deploy_input_values.DeployInputRoute('EXT_KEY', 'extension', 'ext_a', 'agent/extensions/ext_a/deploy/extension.env'),
            }

            with (
                patch.object(deploy_input_values, 'DEFAULT_SITE_ENV_PATH', site_env),
                patch.object(deploy_input_values, 'build_input_routes', return_value=routes),
                patch.object(deploy_input_values, '_dispatch_target_routes_by_key', return_value={}),
                patch.object(deploy_input_values, '_extension_env_path', return_value=extension_env),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                exit_code = deploy_input_values.apply_input_values('combo_profile', input_env, init=True)

            self.assertEqual(exit_code, 0)
            self.assertEqual(parse_env_file(site_env)['OLLAMA_BASE_URL'], 'http://ollama.internal:11434')
            self.assertEqual(parse_env_file(extension_env)['EXT_KEY'], 'extension-value')
            self.assertNotIn('OLLAMA_BASE_URL=', extension_env.read_text(encoding='utf-8'))

    def test_apply_deploy_input_values_mirrors_extension_owned_dispatch_key_to_target_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            input_env = tmp / 'owner-input.env'
            extension_env = tmp / 'extensions' / 'ext_a' / 'deploy' / 'extension.env'
            targets_dir = tmp / 'targets.d'
            input_env.write_text('DISPATCH_WEBHOOK_URL=https://example.invalid/webhook\n', encoding='utf-8')
            input_env.chmod(0o600)
            routes = {
                'DISPATCH_WEBHOOK_URL': deploy_input_values.DeployInputRoute(
                    'DISPATCH_WEBHOOK_URL',
                    'extension',
                    'ext_a',
                    'agent/extensions/ext_a/deploy/extension.env',
                    secret=True,
                ),
            }
            target_route = deploy_input_values.DeployInputRoute(
                'DISPATCH_WEBHOOK_URL',
                'target',
                'dispatch_primary',
                'deploy/targets.d/dispatch_primary.env',
                secret=True,
            )

            with (
                patch.object(deploy_input_values, 'DEFAULT_TARGETS_ENV_DIR', targets_dir),
                patch.object(deploy_input_values, 'build_input_routes', return_value=routes),
                patch.object(deploy_input_values, '_dispatch_target_routes_by_key', return_value={'DISPATCH_WEBHOOK_URL': [target_route]}),
                patch.object(deploy_input_values, '_extension_env_path', return_value=extension_env),
                patch.object(deploy_input_values, '_target_example_lines', return_value=['TARGET_ENABLE=true', 'DISPATCH_WEBHOOK_URL=__REQUIRED__']),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                exit_code = deploy_input_values.apply_input_values('combo_profile', input_env, init=True)

            self.assertEqual(exit_code, 0)
            self.assertEqual(parse_env_file(extension_env)['DISPATCH_WEBHOOK_URL'], 'https://example.invalid/webhook')
            parsed_target = parse_env_file(targets_dir / 'dispatch_primary.env')
            self.assertEqual(parsed_target['TARGET_ENABLE'], 'true')
            self.assertEqual(parsed_target['DISPATCH_WEBHOOK_URL'], 'https://example.invalid/webhook')

    def test_apply_deploy_input_values_validate_only_prints_mirrored_target_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_env = Path(tmpdir) / 'owner-input.env'
            input_env.write_text('DISPATCH_WEBHOOK_URL=https://example.invalid/webhook\n', encoding='utf-8')
            input_env.chmod(0o600)
            routes = {
                'DISPATCH_WEBHOOK_URL': deploy_input_values.DeployInputRoute(
                    'DISPATCH_WEBHOOK_URL',
                    'extension',
                    'ext_a',
                    'agent/extensions/ext_a/deploy/extension.env',
                    secret=True,
                ),
            }
            target_route = deploy_input_values.DeployInputRoute(
                'DISPATCH_WEBHOOK_URL',
                'target',
                'dispatch_primary',
                'deploy/targets.d/dispatch_primary.env',
                secret=True,
            )
            stdout = io.StringIO()

            with (
                patch.object(deploy_input_values, 'build_input_routes', return_value=routes),
                patch.object(deploy_input_values, '_dispatch_target_routes_by_key', return_value={'DISPATCH_WEBHOOK_URL': [target_route]}),
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = deploy_input_values.apply_input_values('combo_profile', input_env, init=False, validate_only=True)

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn('将写入 agent/extensions/ext_a/deploy/extension.env', output)
            self.assertIn('将写入 deploy/targets.d/dispatch_primary.env', output)
            self.assertEqual(output.count('DISPATCH_WEBHOOK_URL=<redacted>'), 2)

    def test_dispatch_target_routes_reject_duplicate_key_within_same_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            stderr = io.StringIO()
            with (
                patch.object(deploy_input_values, 'host_config_path_for_profile', return_value=Path(tmpdir) / 'profile.service.json'),
                patch.object(deploy_input_values.deploy_env_dispatch_registry_lib, 'load_dispatch_targets', return_value={}),
                patch.object(deploy_input_values, 'dispatch_target_env_key_map', return_value={'dispatch_primary': ['TARGET_KEY', 'TARGET_KEY']}),
                contextlib.redirect_stderr(stderr),
                self.assertRaises(SystemExit) as ctx,
            ):
                deploy_input_values._dispatch_target_routes_by_key('combo_profile')

            self.assertEqual(ctx.exception.code, 2)
            self.assertIn('同一 dispatch target registry 重复', stderr.getvalue())

    def test_apply_deploy_input_values_cli_accepts_input_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_env = Path(tmpdir) / 'owner-input.env'
            input_env.write_text('SITE_KEY=value\n', encoding='utf-8')

            with patch.object(deploy_input_values, 'apply_input_values', return_value=0) as mocked_apply:
                exit_code = deploy_input_values.main([
                    'apply',
                    '--profile',
                    'combo_profile',
                    '--input',
                    str(input_env),
                    '--validate-only',
                ])

        self.assertEqual(exit_code, 0)
        mocked_apply.assert_called_once()
        self.assertEqual(mocked_apply.call_args.args[0], 'combo_profile')
        self.assertEqual(mocked_apply.call_args.args[1], input_env.resolve())
        self.assertTrue(mocked_apply.call_args.kwargs['validate_only'])

    def test_apply_deploy_input_values_rejects_duplicate_input_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            input_env = tmp / 'owner-input.env'
            input_env.write_text('SITE_KEY=first\nSITE_KEY=second\n', encoding='utf-8')
            input_env.chmod(0o600)
            routes = {
                'SITE_KEY': deploy_input_values.DeployInputRoute('SITE_KEY', 'site', '', 'deploy/site.env'),
            }
            stderr = io.StringIO()

            with (
                patch.object(deploy_input_values, 'build_input_routes', return_value=routes),
                patch.object(deploy_input_values, '_dispatch_target_routes_by_key', return_value={}),
                contextlib.redirect_stderr(stderr),
                self.assertRaises(SystemExit) as ctx,
            ):
                deploy_input_values.apply_input_values('combo_profile', input_env, init=True)

        self.assertEqual(ctx.exception.code, 2)
        self.assertIn('重复声明部署输入键：SITE_KEY', stderr.getvalue())

    def test_extension_only_apply_rejects_shared_site_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            input_env = tmp / 'owner-input.env'
            input_env.write_text('OLLAMA_BASE_URL=http://ollama.internal:11434\n', encoding='utf-8')
            input_env.chmod(0o600)
            routes = {
                'OLLAMA_BASE_URL': deploy_input_values.DeployInputRoute(
                    'OLLAMA_BASE_URL',
                    'site',
                    '',
                    'deploy/site.env',
                )
            }
            stderr = io.StringIO()
            with (
                patch.object(deploy_input_values, 'build_input_routes', return_value=routes),
                patch.object(deploy_input_values, '_dispatch_target_routes_by_key', return_value={}),
                contextlib.redirect_stderr(stderr),
                self.assertRaises(SystemExit) as ctx,
            ):
                deploy_input_values.apply_input_values('combo_profile', input_env, init=True, allowed_scopes={'extension'})

        self.assertEqual(ctx.exception.code, 2)
        self.assertIn('apply_site_env_values.sh', stderr.getvalue())

    def test_write_target_env_initializes_from_dispatch_registry_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            targets_dir = tmp / 'targets.d'
            routes = {
                'TARGET_KEY': deploy_input_values.DeployInputRoute('TARGET_KEY', 'target', 'dispatch_primary', 'deploy/targets.d/dispatch_primary.env'),
            }

            with (
                patch.object(deploy_input_values, 'DEFAULT_TARGETS_ENV_DIR', targets_dir),
                patch.object(deploy_input_values, 'build_input_routes', return_value=routes),
                patch.object(deploy_input_values, '_dispatch_target_routes_by_key', return_value={}),
                patch.object(deploy_input_values, '_target_example_lines', return_value=['TARGET_ENABLE=true', 'TARGET_KEY=__TARGET_KEY__']),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                exit_code = deploy_input_values.write_target_env('combo_profile', 'dispatch_primary', ['TARGET_KEY=target-value'], init=True)

            self.assertEqual(exit_code, 0)
            parsed = parse_env_file(targets_dir / 'dispatch_primary.env')
            self.assertEqual(parsed['TARGET_ENABLE'], 'true')
            self.assertEqual(parsed['TARGET_KEY'], 'target-value')

    def test_write_target_env_accepts_extension_owned_dispatch_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            targets_dir = Path(tmpdir) / 'targets.d'
            routes = {
                'DISPATCH_WEBHOOK_URL': deploy_input_values.DeployInputRoute(
                    'DISPATCH_WEBHOOK_URL',
                    'extension',
                    'ext_a',
                    'agent/extensions/ext_a/deploy/extension.env',
                    secret=True,
                ),
            }
            target_route = deploy_input_values.DeployInputRoute(
                'DISPATCH_WEBHOOK_URL',
                'target',
                'dispatch_primary',
                'deploy/targets.d/dispatch_primary.env',
                secret=True,
            )

            with (
                patch.object(deploy_input_values, 'DEFAULT_TARGETS_ENV_DIR', targets_dir),
                patch.object(deploy_input_values, 'build_input_routes', return_value=routes),
                patch.object(deploy_input_values, '_dispatch_target_routes_by_key', return_value={'DISPATCH_WEBHOOK_URL': [target_route]}),
                patch.object(deploy_input_values, '_target_example_lines', return_value=['DISPATCH_WEBHOOK_URL=__REQUIRED__']),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                exit_code = deploy_input_values.write_target_env(
                    'combo_profile',
                    'dispatch_primary',
                    ['DISPATCH_WEBHOOK_URL=https://example.invalid/webhook'],
                    init=True,
                )

            self.assertEqual(exit_code, 0)
            parsed = parse_env_file(targets_dir / 'dispatch_primary.env')
            self.assertEqual(parsed['DISPATCH_WEBHOOK_URL'], 'https://example.invalid/webhook')

    def test_write_target_env_accepts_owner_only_input_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            targets_dir = tmp / 'targets.d'
            input_env = tmp / 'target-input.env'
            input_env.write_text('TARGET_KEY=target-value\nTARGET_SECRET=target-secret-value\n', encoding='utf-8')
            input_env.chmod(0o600)
            target_env = targets_dir / 'dispatch_primary.env'
            target_env.parent.mkdir(parents=True)
            target_env.write_text('TARGET_KEY=old-value\nTARGET_SECRET=old-secret\n', encoding='utf-8')
            routes = {
                'TARGET_KEY': deploy_input_values.DeployInputRoute('TARGET_KEY', 'target', 'dispatch_primary', 'deploy/targets.d/dispatch_primary.env'),
                'TARGET_SECRET': deploy_input_values.DeployInputRoute(
                    'TARGET_SECRET',
                    'target',
                    'dispatch_primary',
                    'deploy/targets.d/dispatch_primary.env',
                    secret=True,
                ),
            }
            stdout = io.StringIO()

            with (
                patch.object(deploy_input_values, 'DEFAULT_TARGETS_ENV_DIR', targets_dir),
                patch.object(deploy_input_values, 'build_input_routes', return_value=routes),
                patch.object(deploy_input_values, '_dispatch_target_routes_by_key', return_value={}),
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = deploy_input_values.write_target_env('combo_profile', 'dispatch_primary', [], init=False, input_env_file=input_env)

            self.assertEqual(exit_code, 0)
            parsed = parse_env_file(target_env)
            self.assertEqual(parsed['TARGET_KEY'], 'target-value')
            self.assertEqual(parsed['TARGET_SECRET'], 'target-secret-value')
            self.assertIn('TARGET_SECRET=<redacted>', stdout.getvalue())
            self.assertNotIn('target-secret-value', stdout.getvalue())

    def test_render_env_loads_active_extension_env_from_extension_root(self) -> None:
        def fail(_: str, message: str, code: int) -> None:
            raise AssertionError(f'{code}: {message}')

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            site_env = tmp / 'site.env'
            output = tmp / '.env'
            summary = tmp / 'config_summary.json'
            targets_dir = tmp / 'targets.d'
            extension_root = tmp / 'extensions'
            extension_deploy = extension_root / MANAGED_EXTENSION_PROFILE_ID / 'deploy'
            targets_dir.mkdir()
            extension_deploy.mkdir(parents=True)
            site_env.write_text(
                '\n'.join([
                    f'OPENCLAW_CONTROL_PLANE_PROFILE={MANAGED_EXTENSION_PROFILE_ID}',
                    'OPENCLAW_INGRESS_LISTEN_IP=192.168.91.128',
                    'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS=192.168.91.1/32,192.168.91.128/32',
                ]) + '\n',
                encoding='utf-8',
            )
            (extension_deploy / 'extension.env').write_text(
                '\n'.join([
                    'OLLAMA_BASE_URL=http://ollama.internal:11434',
                    'OLLAMA_MODEL_REF=qwen-test',
                ]) + '\n',
                encoding='utf-8',
            )

            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = render_env(
                    [
                        '--site-env', str(site_env),
                        '--targets-env-dir', str(targets_dir),
                        '--extension-env-root', str(extension_root),
                        '--output', str(output),
                        '--summary-json', str(summary),
                    ],
                    fail=fail,
                    note=lambda *_: None,
                )

            self.assertEqual(exit_code, 0)
            parsed = parse_env_file(output)
            self.assertEqual(parsed['OLLAMA_BASE_URL'], 'http://ollama.internal:11434')
            self.assertEqual(parsed['OLLAMA_MODEL_REF'], 'qwen-test')

    def test_render_env_rejects_undeclared_extension_env_key(self) -> None:
        def fail(_: str, message: str, code: int) -> None:
            raise AssertionError(f'{code}: {message}')

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            site_env = tmp / 'site.env'
            extension_root = tmp / 'extensions'
            extension_deploy = extension_root / MANAGED_EXTENSION_PROFILE_ID / 'deploy'
            extension_deploy.mkdir(parents=True)
            site_env.write_text(
                '\n'.join([
                    f'OPENCLAW_CONTROL_PLANE_PROFILE={MANAGED_EXTENSION_PROFILE_ID}',
                    'OPENCLAW_INGRESS_LISTEN_IP=192.168.91.128',
                    'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS=192.168.91.1/32,192.168.91.128/32',
                ]) + '\n',
                encoding='utf-8',
            )
            (extension_deploy / 'extension.env').write_text('OPENCLAW_GATEWAY_TOKEN=not-allowed\n', encoding='utf-8')

            with self.assertRaisesRegex(AssertionError, '当前扩展未声明的 env 键'):
                render_env(
                    [
                        '--site-env', str(site_env),
                        '--targets-env-dir', str(tmp / 'targets.d'),
                        '--extension-env-root', str(extension_root),
                        '--output', str(tmp / '.env'),
                        '--summary-json', str(tmp / 'summary.json'),
                    ],
                    fail=fail,
                    note=lambda *_: None,
                )

    def test_render_env_rejects_unknown_extension_env_key(self) -> None:
        if RELATED_PARTY_EXTENSION is None:
            self.skipTest('agent_related_party_compare extension is unavailable')

        def fail(_: str, message: str, code: int) -> None:
            raise AssertionError(f'{code}: {message}')

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            site_env = tmp / 'site.env'
            extension_root = tmp / 'extensions'
            extension_deploy = extension_root / RELATED_PARTY_EXTENSION.id / 'deploy'
            extension_deploy.mkdir(parents=True)
            site_env.write_text(
                '\n'.join([
                    f'OPENCLAW_CONTROL_PLANE_PROFILE={RELATED_PARTY_EXTENSION.id}',
                    'OPENCLAW_INGRESS_LISTEN_IP=192.168.91.128',
                    'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS=192.168.91.1/32,192.168.91.128/32',
                ]) + '\n',
                encoding='utf-8',
            )
            unknown_key = 'RELATED_PARTY_COMPARE_NOT_DECLARED_KEY'
            (extension_deploy / 'extension.env').write_text(f'{unknown_key}=value\n', encoding='utf-8')

            with self.assertRaisesRegex(AssertionError, f'当前扩展未声明的 env 键：{unknown_key}'):
                render_env(
                    [
                        '--site-env', str(site_env),
                        '--targets-env-dir', str(tmp / 'targets.d'),
                        '--extension-env-root', str(extension_root),
                        '--output', str(tmp / '.env'),
                        '--summary-json', str(tmp / 'summary.json'),
                    ],
                    fail=fail,
                    note=lambda *_: None,
                )

    def test_render_env_rejects_unknown_site_env_key(self) -> None:
        def fail(_: str, message: str, code: int) -> None:
            raise AssertionError(f'{code}: {message}')

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            site_env = tmp / 'site.env'
            site_env.write_text(
                '\n'.join([
                    'OPENCLAW_CONTROL_PLANE_PROFILE=agent_platform',
                    'OPENCLAW_INGRESS_LISTEN_IP=192.168.91.128',
                    'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS=192.168.91.1/32,192.168.91.128/32',
                    'UNDECLARED_SITE_KEY=value',
                ]) + '\n',
                encoding='utf-8',
            )

            with self.assertRaisesRegex(AssertionError, '未声明的部署输入键：UNDECLARED_SITE_KEY'):
                render_env(
                    [
                        '--site-env', str(site_env),
                        '--targets-env-dir', str(tmp / 'targets.d'),
                        '--extension-env-root', str(tmp / 'extensions'),
                        '--output', str(tmp / '.env'),
                        '--summary-json', str(tmp / 'summary.json'),
                    ],
                    fail=fail,
                    note=lambda *_: None,
                )

    def test_render_env_rejects_synthetic_notify_keys_in_site_env(self) -> None:
        def fail(_: str, message: str, code: int) -> None:
            raise AssertionError(f'{code}: {message}')

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            site_env = tmp / 'site.env'
            site_env.write_text(
                '\n'.join([
                    f'OPENCLAW_CONTROL_PLANE_PROFILE={DEPLOY_ENV_EXTENSION.id}',
                    'OPENCLAW_INGRESS_LISTEN_IP=192.168.91.128',
                    'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS=192.168.91.1/32,192.168.91.128/32',
                    'PROBE_NOTIFY_APP_ID=app_site_scope_is_invalid',
                ]) + '\n',
                encoding='utf-8',
            )

            with self.assertRaisesRegex(AssertionError, '不允许填写扩展变量：PROBE_NOTIFY_APP_ID'):
                render_env(
                    [
                        '--site-env', str(site_env),
                        '--targets-env-dir', str(tmp / 'targets.d'),
                        '--extension-env-root', str(tmp / 'extensions'),
                        '--output', str(tmp / '.env'),
                        '--summary-json', str(tmp / 'summary.json'),
                    ],
                    fail=fail,
                    note=lambda *_: None,
                )

    def test_render_env_rejects_undeclared_synthetic_notify_extension_keys(self) -> None:
        def fail(_: str, message: str, code: int) -> None:
            raise AssertionError(f'{code}: {message}')

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            site_env = tmp / 'site.env'
            extension_root = tmp / 'extensions'
            extension_deploy = extension_root / DEPLOY_ENV_EXTENSION.id / 'deploy'
            extension_deploy.mkdir(parents=True)
            site_env.write_text(
                '\n'.join([
                    f'OPENCLAW_CONTROL_PLANE_PROFILE={DEPLOY_ENV_EXTENSION.id}',
                    'OPENCLAW_INGRESS_LISTEN_IP=192.168.91.128',
                    'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS=192.168.91.1/32,192.168.91.128/32',
                ]) + '\n',
                encoding='utf-8',
            )
            undeclared_key = 'PROBE_NOTIFY_USER_REF'
            (extension_deploy / 'extension.env').write_text(
                f'{undeclared_key}=user_unregistered\n',
                encoding='utf-8',
            )

            with self.assertRaisesRegex(AssertionError, f'当前扩展未声明的 env 键：{undeclared_key}'):
                render_env(
                    [
                        '--site-env', str(site_env),
                        '--targets-env-dir', str(tmp / 'targets.d'),
                        '--extension-env-root', str(extension_root),
                        '--output', str(tmp / '.env'),
                        '--summary-json', str(tmp / 'summary.json'),
                    ],
                    fail=fail,
                    note=lambda *_: None,
                )

    def test_render_env_does_not_preserve_extension_values_from_existing_output(self) -> None:
        def fail(_: str, message: str, code: int) -> None:
            raise AssertionError(f'{code}: {message}')

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            site_env = tmp / 'site.env'
            output = tmp / '.env'
            site_env.write_text(
                '\n'.join([
                    f'OPENCLAW_CONTROL_PLANE_PROFILE={MANAGED_EXTENSION_PROFILE_ID}',
                    'OPENCLAW_INGRESS_LISTEN_IP=192.168.91.128',
                    'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS=192.168.91.1/32,192.168.91.128/32',
                ]) + '\n',
                encoding='utf-8',
            )
            output.write_text(
                '\n'.join([
                    f'OPENCLAW_CONTROL_PLANE_PROFILE={MANAGED_EXTENSION_PROFILE_ID}',
                    'OLLAMA_BASE_URL=http://stale.invalid:11434',
                ]) + '\n',
                encoding='utf-8',
            )

            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = render_env(
                    [
                        '--site-env', str(site_env),
                        '--targets-env-dir', str(tmp / 'targets.d'),
                        '--extension-env-root', str(tmp / 'extensions'),
                        '--output', str(output),
                        '--summary-json', str(tmp / 'summary.json'),
                    ],
                    fail=fail,
                    note=lambda *_: None,
                )

            self.assertEqual(exit_code, 0)
            parsed = parse_env_file(output)
            self.assertNotEqual(parsed.get('OLLAMA_BASE_URL'), 'http://stale.invalid:11434')

    def test_render_env_maps_profile_id_to_runtime_path_and_dispatch_registry(self) -> None:
        def fail(_: str, message: str, code: int) -> None:
            raise AssertionError(f'{code}: {message}')

        registry_source = json.loads(dispatch_registry_path().read_text(encoding='utf-8'))
        target = next(item for item in registry_source['targets'] if item['enabledDefault'])
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            site_env = tmp / 'site.env'
            output = tmp / '.env'
            summary = tmp / 'config_summary.json'
            targets_dir = tmp / 'targets.d'
            targets_dir.mkdir()
            site_env.write_text(
                '\n'.join([
                    f'OPENCLAW_CONTROL_PLANE_PROFILE={MANAGED_EXTENSION_PROFILE_ID}',
                    'OPENCLAW_INGRESS_LISTEN_IP=192.168.91.128',
                    'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS=192.168.91.1/32,192.168.91.128/32',
                ]) + '\n',
                encoding='utf-8',
            )
            (targets_dir / f"{target['id']}.env").write_text(
                '\n'.join([
                    f"{target['enabledEnv']}=true",
                    f"{target['endpointEnv']}=https://example.invalid/hook",
                    f"{target['secretEnv']}=target-secret-value",
                ]) + '\n',
                encoding='utf-8',
            )

            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = render_env(
                    ['--site-env', str(site_env), '--targets-env-dir', str(targets_dir), '--output', str(output), '--summary-json', str(summary)],
                    fail=fail,
                    note=lambda *_: None,
                )

            self.assertEqual(exit_code, 0)
            parsed = parse_env_file(output)
            self.assertEqual(parsed['OPENCLAW_CONTROL_PLANE_PROFILE'], MANAGED_EXTENSION_PROFILE_ID)
            self.assertEqual(parsed['OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH'], MANAGED_EXTENSION_RUNTIME_CONFIG_PATH)
            self.assertEqual(parsed[str(target['endpointEnv'])], 'https://example.invalid/hook')

    def test_render_env_infers_profile_from_runtime_config_path(self) -> None:
        def fail(_: str, message: str, code: int) -> None:
            raise AssertionError(f'{code}: {message}')

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            site_env = tmp / 'site.env'
            output = tmp / '.env'
            summary = tmp / 'config_summary.json'
            targets_dir = tmp / 'targets.d'
            targets_dir.mkdir()
            site_env.write_text(
                '\n'.join([
                    f'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH={MANAGED_EXTENSION_RUNTIME_CONFIG_PATH}',
                    'OPENCLAW_INGRESS_LISTEN_IP=192.168.91.128',
                    'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS=192.168.91.1/32,192.168.91.128/32',
                ]) + '\n',
                encoding='utf-8',
            )

            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = render_env(
                    ['--site-env', str(site_env), '--targets-env-dir', str(targets_dir), '--output', str(output), '--summary-json', str(summary)],
                    fail=fail,
                    note=lambda *_: None,
                )

            self.assertEqual(exit_code, 0)
            parsed = parse_env_file(output)
            self.assertEqual(parsed['OPENCLAW_CONTROL_PLANE_PROFILE'], MANAGED_EXTENSION_PROFILE_ID)
            self.assertEqual(parsed['OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH'], MANAGED_EXTENSION_RUNTIME_CONFIG_PATH)

    def test_site_env_example_renderer_writes_lf_newlines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            site_env_example = Path(tmpdir) / 'site.env.example'

            ensure_site_env_example(site_env_example)

            content = site_env_example.read_bytes()
            self.assertIn(b'\n', content)
            self.assertNotIn(b'\r\n', content)
            rendered_text = content.decode('utf-8')
            self.assertIn('OPENCLAW_CONTROL_PLANE_PROFILE=agent_platform', rendered_text)
            self.assertNotIn('OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH=', rendered_text)
            self.assertNotIn(f'OPENCLAW_CONTROL_PLANE_PROFILE={MANAGED_EXTENSION_PROFILE_ID}', rendered_text)
            self.assertNotIn(f'OPENCLAW_CONTROL_PLANE_PROFILE={COMBO_PROFILE_ID}', rendered_text)
            self.assertNotIn('OLLAMA_BASE_URL=', rendered_text)
            self.assertNotIn('OLLAMA_MODEL_REF=', rendered_text)

    def test_site_env_example_renderer_ignores_active_extension_profile_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            site_env_example = Path(tmpdir) / 'site.env.example'

            with patch.dict(os.environ, {'OPENCLAW_CONTROL_PLANE_PROFILE': self._probe_fixture.extension_id}):
                ensure_site_env_example(site_env_example)

            rendered_text = site_env_example.read_text(encoding='utf-8')
            self.assertIn('OPENCLAW_CONTROL_PLANE_PROFILE=agent_platform', rendered_text)
            self.assertNotIn(f'OPENCLAW_CONTROL_PLANE_PROFILE={self._probe_fixture.extension_id}', rendered_text)
            self.assertNotIn('OLLAMA_BASE_URL=', rendered_text)
            self.assertNotIn('OLLAMA_MODEL_REF=', rendered_text)

    def test_repo_site_env_example_matches_renderer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rendered_path = Path(tmpdir) / 'site.env.example'

            ensure_site_env_example(rendered_path)

            self.assertEqual(
                (ROOT_DIR / 'deploy' / 'site.env.example').read_text(encoding='utf-8'),
                rendered_path.read_text(encoding='utf-8'),
            )

    def test_target_env_examples_are_reconciled_to_active_profile_registry(self) -> None:
        registry_source = json.loads(dispatch_registry_path().read_text(encoding='utf-8'))
        target_id = str(registry_source['targets'][0]['id'])
        with tempfile.TemporaryDirectory() as tmpdir:
            targets_dir = Path(tmpdir) / 'targets.d'
            targets_dir.mkdir()
            (targets_dir / 'unregistered_target.env.example').write_text('UNREGISTERED=true\n', encoding='utf-8')

            ensure_targets_env_dir(targets_dir, registry_source)

            self.assertTrue((targets_dir / f'{target_id}.env.example').is_file())
            self.assertFalse((targets_dir / 'unregistered_target.env.example').exists())

            ensure_targets_env_dir(targets_dir, {'targets': []})

            self.assertFalse((targets_dir / f'{target_id}.env.example').exists())

    def test_target_env_examples_use_registry_declared_names_only(self) -> None:
        registry_source = copy.deepcopy(self._probe_dispatch_registry)
        primary = registry_source['targets'][0]
        primary['enabledDefault'] = False
        target_id = primary['id']
        enabled_key = primary['enabledEnv']

        key_map = dispatch_target_env_key_map(registry_source)
        self.assertIn(enabled_key, key_map[target_id])
        self.assertNotIn('DISPATCH_PRIMARY_ENABLE', key_map[target_id])

        with tempfile.TemporaryDirectory() as tmpdir:
            targets_dir = Path(tmpdir) / 'targets.d'
            ensure_targets_env_dir(targets_dir, registry_source)
            rendered = (targets_dir / f'{target_id}.env.example').read_text(encoding='utf-8')

        # target_env_example_lines 为人工启用模板写入 true；enabledDefault 控制无部署覆盖时的运行默认值。
        self.assertIn(f'{enabled_key}=true', rendered)
        self.assertNotIn('\nDISPATCH_PRIMARY_ENABLE=', f'\n{rendered}')

    def test_targets_d_rejects_unregistered_old_dispatch_keys(self) -> None:
        registry_source = self._probe_dispatch_registry
        primary = registry_source['targets'][0]
        errors: list[str] = []

        def fail(_: str, message: str, code: int) -> None:
            errors.append(f'{code}: {message}')
            raise RuntimeError(message)

        with tempfile.TemporaryDirectory() as tmpdir:
            targets_dir = Path(tmpdir) / 'targets.d'
            targets_dir.mkdir()
            (targets_dir / f"{primary['id']}.env").write_text(
                '\n'.join([
                    'DISPATCH_PRIMARY_ENABLE=false',
                    'DISPATCH_PRIMARY_TITLE=旧标题',
                    f"{primary['endpointEnv']}=https://example.invalid/hook",
                    f"{primary['secretEnv']}=target-secret-value",
                ]) + '\n',
                encoding='utf-8',
            )
            values: OrderedDict[str, str] = OrderedDict()

            with self.assertRaises(RuntimeError):
                merge_target_env_overrides(values, targets_dir, set(), registry=registry_source, fail=fail)

        self.assertTrue(any('未声明的 env 键：DISPATCH_PRIMARY_ENABLE' in item for item in errors))
        self.assertNotIn(primary['enabledEnv'], values)
        self.assertNotIn('DISPATCH_PRIMARY_ENABLE', values)

    def test_target_runtime_ignores_old_dispatch_keys(self) -> None:
        for enabled_default in (False, True):
            with self.subTest(enabled_default=enabled_default):
                registry_source = copy.deepcopy(self._probe_dispatch_registry)
                primary = registry_source['targets'][0]
                primary['enabledDefault'] = enabled_default
                override_value = str(not enabled_default).lower()

                _, ignored_targets = load_targets_payload(registry_source, env={'DISPATCH_PRIMARY_ENABLE': override_value})
                ignored_primary = next(target for target in ignored_targets if target.target_id == primary['id'])
                self.assertEqual(enabled_default, ignored_primary.enabled)

                _, canonical_targets = load_targets_payload(
                    registry_source,
                    env={
                        'DISPATCH_PRIMARY_ENABLE': str(enabled_default).lower(),
                        primary['enabledEnv']: override_value,
                    },
                )
                canonical_primary = next(target for target in canonical_targets if target.target_id == primary['id'])
                self.assertEqual(not enabled_default, canonical_primary.enabled)

    def test_dispatch_runtime_default_output_uses_runtime_path_truth(self) -> None:
        self.assertEqual(
            control_plane.DEFAULT_DISPATCH_RUNTIME_OUTPUT_PATH,
            ROOT_DIR / 'state/openclaw/control_plane/dispatch/targets.json',
        )
        self.assertNotIn('dispatch_config', str(control_plane.DEFAULT_DISPATCH_RUNTIME_OUTPUT_PATH))

    def test_local_ro_mirror_incrementally_reconciles_to_manifest(self) -> None:
        class FakeResolver:
            def __init__(self, gateway_root: Path) -> None:
                self.gateway_root = gateway_root

            def absolute_host_path(self, entry_id: str) -> Path:
                if entry_id != 'gateway_host_state_dir':
                    raise AssertionError(entry_id)
                return self.gateway_root

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir).resolve()
            repo_root = tmp / 'repo'
            source_dir = repo_root / 'config'
            source_dir.mkdir(parents=True)
            (source_dir / 'a.txt').write_text('alpha\n', encoding='utf-8')
            nested = source_dir / 'nested'
            nested.mkdir()
            (nested / 'b.txt').write_text('beta\n', encoding='utf-8')
            manifest_path = repo_root / 'manifest.json'
            manifest_path.write_text(
                json.dumps(
                    {
                        'entries': [
                            {'type': 'file', 'source': 'config/a.txt', 'target': 'a.txt'},
                            {'type': 'dir', 'source': 'config/nested', 'target': 'nested'},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding='utf-8',
            )
            gateway_root = tmp / 'state' / 'gateway'
            output_dir = gateway_root / 'local_ro_gateway'
            output_dir.mkdir(parents=True)
            (output_dir / 'stale.txt').write_text('stale\n', encoding='utf-8')

            with patch('openclaw.setup.deploy_env.bootstrap_runtime.require_path_resolver', return_value=FakeResolver(gateway_root)):
                self.assertEqual(
                    bootstrap_runtime.render_local_ro_mirror(
                        manifest_path=manifest_path,
                        output_dir=output_dir,
                        label='test_local_ro',
                        repo_root=repo_root,
                    ),
                    0,
                )
                self.assertEqual(
                    bootstrap_runtime.render_local_ro_mirror(
                        manifest_path=manifest_path,
                        output_dir=output_dir,
                        label='test_local_ro',
                        repo_root=repo_root,
                        check_only=True,
                    ),
                    0,
                )

            self.assertEqual((output_dir / 'a.txt').read_text(encoding='utf-8'), 'alpha\n')
            self.assertEqual((output_dir / 'nested' / 'b.txt').read_text(encoding='utf-8'), 'beta\n')
            self.assertFalse((output_dir / 'stale.txt').exists())

    def test_local_ro_mirror_rejects_duplicate_targets(self) -> None:
        class FakeResolver:
            def __init__(self, gateway_root: Path) -> None:
                self.gateway_root = gateway_root

            def absolute_host_path(self, entry_id: str) -> Path:
                if entry_id != 'gateway_host_state_dir':
                    raise AssertionError(entry_id)
                return self.gateway_root

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir).resolve()
            repo_root = tmp / 'repo'
            source_dir = repo_root / 'config'
            source_dir.mkdir(parents=True)
            (source_dir / 'a.txt').write_text('alpha\n', encoding='utf-8')
            (source_dir / 'b.txt').write_text('beta\n', encoding='utf-8')
            manifest_path = repo_root / 'manifest.json'
            manifest_path.write_text(
                json.dumps(
                    {
                        'entries': [
                            {'type': 'file', 'source': 'config/a.txt', 'target': 'same.txt'},
                            {'type': 'file', 'source': 'config/b.txt', 'target': 'same.txt'},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding='utf-8',
            )

            with (
                patch(
                    'openclaw.setup.deploy_env.bootstrap_runtime.require_path_resolver',
                    return_value=FakeResolver(tmp / 'state' / 'gateway'),
                ),
                self.assertRaisesRegex(SystemExit, 'manifest target 重复'),
            ):
                bootstrap_runtime.render_local_ro_mirror(
                    manifest_path=manifest_path,
                    output_dir=tmp / 'state' / 'gateway' / 'local_ro_gateway',
                    label='test_local_ro',
                    repo_root=repo_root,
                )

    def test_dispatch_registry_commands_resolve_active_profile_from_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / 'deploy.env'
            env_file.write_text(
                '\n'.join([
                    f'OPENCLAW_CONTROL_PLANE_PROFILE={MANAGED_EXTENSION_PROFILE_ID}',
                    f'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH={MANAGED_EXTENSION_RUNTIME_CONFIG_PATH}',
                ]) + '\n',
                encoding='utf-8',
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                validate_exit = control_plane.main(['validate-dispatch-registry', '--env-file', str(env_file), '--json'])
            self.assertEqual(validate_exit, 0)
            validate_payload = json.loads(stdout.getvalue())
            self.assertTrue(validate_payload['registry_enabled'])
            self.assertIn('dispatch_primary', validate_payload['target_ids'])

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                query_exit = control_plane.main(['query-dispatch-registry', 'summary', '--env-file', str(env_file)])
            self.assertEqual(query_exit, 0)
            query_payload = json.loads(stdout.getvalue())
            self.assertTrue(query_payload['registry_enabled'])
            self.assertIn('dispatch_primary', query_payload['target_ids'])

    def test_runtime_internal_api_bind_default_is_container_reachable(self) -> None:
        self.assertEqual(control_plane.DEFAULT_RUNTIME_INTERNAL_API_BIND, '0.0.0.0')

    def test_runtime_service_env_render_uses_profile_provider_registry_paths(self) -> None:
        class FakeResolver:
            def resolve_path(self, entry_id: str, view: str = 'host') -> str:
                return f'/state/{view}/{entry_id}'

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            env_path = tmp / 'deploy.env'
            scheduler_output = tmp / 'scheduler.env'
            internal_api_output = tmp / 'internal-api.env'
            target_registry = tmp / 'dispatch_targets.json'
            provider_registry = tmp / 'dispatch_provider_adapters.json'
            observed: dict[str, object] = {}
            env_path.write_text('OPENCLAW_INTERNAL_API_TOKEN=test-token\n', encoding='utf-8')

            def fake_load_dispatch_registry(registry_paths: list[Path], schema_path: Path, provider_registry_path: list[Path] | None = None) -> dict[str, object]:
                observed['registry_paths'] = list(registry_paths)
                observed['provider_registry_path'] = provider_registry_path
                return {'targets': []}

            with (
                patch.object(dispatch_registry_render, 'require_runtime_dependencies'),
                patch.object(dispatch_registry_render, 'resolve_dispatch_targets_paths', return_value=[target_registry]),
                patch.object(dispatch_registry_render, 'resolve_dispatch_provider_paths', return_value=[provider_registry]),
                patch.object(dispatch_registry_render, 'load_dispatch_registry', side_effect=fake_load_dispatch_registry),
                patch.object(dispatch_registry_render, 'load_registry', return_value={}),
                patch.object(dispatch_registry_render, 'require_path_resolver', return_value=FakeResolver()),
            ):
                exit_code = dispatch_registry_render.render_runtime_service_envs(
                    [],
                    default_env_file=env_path,
                    default_scheduler_output=scheduler_output,
                    default_internal_api_output=internal_api_output,
                    default_config_path=ROOT_DIR / 'config/control_plane/profiles/agent_platform.service.json',
                    default_internal_api_bind='0.0.0.0',
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(observed['registry_paths'], [target_registry])
        self.assertEqual(observed['provider_registry_path'], [provider_registry])

    def test_runtime_service_env_render_includes_active_extension_runtime_env(self) -> None:
        class FakeResolver:
            def resolve_path(self, entry_id: str, view: str = 'host') -> str:
                return f'/state/{view}/{entry_id}'

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            env_path = tmp / 'deploy.env'
            scheduler_output = tmp / 'scheduler.env'
            internal_api_output = tmp / 'internal-api.env'
            env_path.write_text(
                '\n'.join(
                    [
                        'OPENCLAW_INTERNAL_API_TOKEN=test-token',
                        'OLLAMA_BASE_URL=http://ollama.invalid:11434',
                        'OLLAMA_MODEL_REF=qwen-test',
                        'PROBE_NOTIFY_APP_NAME=OpenClaw Probe',
                        'PROBE_NOTIFY_APP_ID=app_test',
                        'PROBE_NOTIFY_CHANNEL_ID=channel_test',
                        'PROBE_NOTIFY_ACTOR_ID=user_bot',
                        'PROBE_NOTIFY_BOT_NAME=Probe Notify',
                        'PROBE_ADMIN_USERS_JSON=[{"user_ref":"user_admin"}]',
                    ]
                )
                + '\n',
                encoding='utf-8',
            )

            with (
                patch.object(dispatch_registry_render, 'require_runtime_dependencies'),
                patch.object(dispatch_registry_render, 'resolve_dispatch_targets_paths', return_value=[]),
                patch.object(dispatch_registry_render, 'resolve_dispatch_provider_paths', return_value=[]),
                patch.object(dispatch_registry_render, 'require_path_resolver', return_value=FakeResolver()),
            ):
                exit_code = dispatch_registry_render.render_runtime_service_envs(
                    [],
                    default_env_file=env_path,
                    default_scheduler_output=scheduler_output,
                    default_internal_api_output=internal_api_output,
                    default_config_path=DEPLOY_ENV_EXTENSION.default_service_config_path,
                    default_internal_api_bind='0.0.0.0',
                )

            self.assertEqual(exit_code, 0)
            scheduler_env = parse_env_file(scheduler_output)
            internal_api_env = parse_env_file(internal_api_output)
            self.assertEqual(scheduler_env['PROBE_NOTIFY_APP_ID'], 'app_test')
            self.assertEqual(scheduler_env['PROBE_NOTIFY_CHANNEL_ID'], 'channel_test')
            self.assertEqual(scheduler_env['PROBE_NOTIFY_ACTOR_ID'], 'user_bot')
            self.assertEqual(scheduler_env['PROBE_NOTIFY_APP_NAME'], 'OpenClaw Probe')
            self.assertEqual(scheduler_env['PROBE_NOTIFY_BOT_NAME'], 'Probe Notify')
            self.assertIn('PROBE_ADMIN_USERS_JSON', scheduler_env)
            self.assertNotIn('PROBE_NOTIFY_APP_ID', internal_api_env)

    def test_runtime_service_env_render_enforces_live_card_truthy_required(self) -> None:
        class FakeResolver:
            def resolve_path(self, entry_id: str, view: str = 'host') -> str:
                return f'/state/{view}/{entry_id}'

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            env_path = tmp / 'deploy.env'
            scheduler_output = tmp / 'scheduler.env'
            internal_api_output = tmp / 'internal-api.env'
            env_path.write_text(
                '\n'.join(
                    [
                        'OPENCLAW_INTERNAL_API_TOKEN=test-token',
                        'OLLAMA_BASE_URL=http://ollama.invalid:11434',
                        'OLLAMA_MODEL_REF=qwen-test',
                        'PROBE_NOTIFY_LIVE_REQUIRED=True',
                        'PROBE_NOTIFY_CARD_ENABLED=0',
                        'PROBE_NOTIFY_CARD_UPDATE_ENABLED=1',
                        'PROBE_NOTIFY_CARD_ACTION_TRIGGER_ENABLED=0',
                    ]
                )
                + '\n',
                encoding='utf-8',
            )

            with (
                patch.object(dispatch_registry_render, 'require_runtime_dependencies'),
                patch.object(dispatch_registry_render, 'resolve_dispatch_targets_paths', return_value=[]),
                patch.object(dispatch_registry_render, 'resolve_dispatch_provider_paths', return_value=[]),
                patch.object(dispatch_registry_render, 'require_path_resolver', return_value=FakeResolver()),
                contextlib.redirect_stderr(io.StringIO()) as stderr,
                self.assertRaises(SystemExit) as raised,
            ):
                dispatch_registry_render.render_runtime_service_envs(
                    [],
                    default_env_file=env_path,
                    default_scheduler_output=scheduler_output,
                    default_internal_api_output=internal_api_output,
                    default_config_path=DEPLOY_ENV_EXTENSION.default_service_config_path,
                    default_internal_api_bind='0.0.0.0',
                )

            self.assertEqual(raised.exception.code, 2)
            self.assertIn('PROBE_NOTIFY_CARD_ENABLED', stderr.getvalue())
            self.assertNotIn('PROBE_NOTIFY_CARD_ACTION_TRIGGER_ENABLED', stderr.getvalue())

    def test_dispatch_runtime_render_writes_registry_loader_snapshot(self) -> None:
        registry_source = json.loads(dispatch_registry_path().read_text(encoding='utf-8'))
        enabled_target = next(item for item in registry_source['targets'] if item['enabledDefault'])
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            env_path = tmp / 'deploy.env'
            output = tmp / 'targets.json'
            summary = tmp / 'summary.json'
            env_path.write_text(f"{enabled_target['enabledEnv']}=true\n", encoding='utf-8')

            stdout = io.StringIO()
            with (
                contextlib.redirect_stdout(stdout),
                patch.object(dispatch_registry_render, 'load_dispatch_targets', return_value=registry_source),
            ):
                exit_code = render_dispatch_runtime(
                    [
                        '--env-file',
                        str(env_path),
                        '--output',
                        str(output),
                        '--summary-json',
                        str(summary),
                        '--config-path',
                        str(MANAGED_EXTENSION.default_service_config_path),
                    ],
                    default_env_file=env_path,
                    default_output=output,
                    default_summary_json=summary,
                )

            self.assertEqual(exit_code, 0)
            self.assertIn('[deploy_env_control_plane] 已生成 dispatch runtime', stdout.getvalue())
            payload = json.loads(output.read_text(encoding='utf-8'))
            self.assertIn('maxAttempts', payload['defaults'])
            self.assertIn('enabledEnv', payload['targets'][0])
            self.assertIn('boundary', payload['targets'][0])
            source_by_id = {str(item['id']): item for item in registry_source['targets']}
            for rendered_target in payload['targets']:
                source_target = source_by_id[str(rendered_target['id'])]
                self.assertEqual(rendered_target['boundary'], source_target['boundary'])
            self.assertNotIn('endpoint_url', payload['targets'][0])
            _, targets = load_targets_config(output, env={str(enabled_target['enabledEnv']): 'true'})
            target_by_id = {target.target_id: target for target in targets}
            self.assertIn(enabled_target['id'], target_by_id)
            self.assertEqual(target_by_id[enabled_target['id']].dispatch_lane, enabled_target['boundary']['dispatchLane'])
            self.assertEqual(target_by_id[enabled_target['id']].publish_latest, enabled_target['boundary']['publishLatestDefault'])

    def test_dispatch_runtime_v7_requires_explicit_boundary(self) -> None:
        payload = json.loads(dispatch_registry_path().read_text(encoding='utf-8'))
        payload['registry_version'] = 7
        payload['targets'][0] = dict(payload['targets'][0])
        payload['targets'][0].pop('boundary', None)

        with self.assertRaisesRegex(TargetConfigError, 'missing boundary'):
            load_targets_payload(payload, env={}, source_label='test_registry_v7')

    def test_dispatch_runtime_v7_requires_boolean_publish_latest_boundary(self) -> None:
        payload = json.loads(dispatch_registry_path().read_text(encoding='utf-8'))
        payload['registry_version'] = 7
        payload['targets'][0] = dict(payload['targets'][0])
        payload['targets'][0]['boundary'] = dict(payload['targets'][0]['boundary'])
        payload['targets'][0]['boundary']['publishLatestDefault'] = 'false'

        with self.assertRaisesRegex(TargetConfigError, 'publishLatestDefault must be boolean'):
            load_targets_payload(payload, env={}, source_label='test_registry_v7')

    def test_dispatch_runtime_local_id_payload_requires_explicit_boundary(self) -> None:
        payload = json.loads(dispatch_registry_path().read_text(encoding='utf-8'))
        payload.pop('version', None)
        payload['registry_version'] = 6
        target_index = next(index for index, row in enumerate(payload['targets']) if row['targetGroup'] == 'ops')
        payload['targets'][target_index] = dict(payload['targets'][target_index])
        payload['targets'][target_index].pop('boundary', None)

        with self.assertRaisesRegex(TargetConfigError, 'missing boundary'):
            load_targets_payload(payload, env={}, source_label='test_registry_v6')


if __name__ == '__main__':
    unittest.main()
