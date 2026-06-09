from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
import unittest

from openclaw.control_plane.config_loader import ControlPlaneConfigError, load_control_plane_service_payload
from openclaw.control_plane.registry_loader import load_registry_from_path
from openclaw.control_plane.registry_loader.activation import resolve_object_activation
from openclaw.control_plane.registry_loader.collections import (
    _ensure_agent_control_plane_registry,
    _ensure_agent_internal_assembly_registry,
    _load_agent_modules,
    _load_collection,
    _load_registry_collections,
    _merge_job_runners,
)
from openclaw.control_plane.registry_loader.config import load_registry_service_context
from openclaw.control_plane.schema import load_schema
from openclaw.lib.cli.common import CliError
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.doctor.agent_modules.managed_probe_fixture import PROBE_PRIMARY_MODULE_REF, PROBE_SUPPORT_MODULE_REF, PROBE_TARGET_REF
from openclaw.doctor.agent_modules.managed_probe_fixture import PROBE_GROUP_REF
from openclaw.tests.support.managed_probe import managed_probe_repo
from openclaw.tests.support.managed_extensions import managed_extensions, representative_managed_extension


ROOT_DIR = resolve_repo_root(Path(__file__))
MANAGED_EXTENSIONS = tuple(sorted(managed_extensions(ROOT_DIR), key=lambda row: row.id))


def _repo_combination_profile() -> tuple[str | None, Path | None, list[str]]:
    managed_ids = {extension.id for extension in MANAGED_EXTENSIONS}
    for path in sorted((ROOT_DIR / 'config' / 'control_plane' / 'profiles').glob('*.service.json')):
        context = load_registry_service_context(path)
        extension_ids = [item for item in context['enabledExtensionIds'] if item in managed_ids]
        if len(extension_ids) >= 2:
            return path.name.removesuffix('.service.json'), path, extension_ids
    return None, None, []


COMBO_PROFILE_ID, COMBO_CONFIG_PATH, COMBO_EXTENSION_IDS = _repo_combination_profile()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


class RegistryLoaderCollectionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._fixture_context = managed_probe_repo('registry-loader-shared')
        cls.fixture = cls._fixture_context.__enter__()
        cls.addClassCleanup(cls._fixture_context.__exit__, None, None, None)
        cls.context = load_registry_service_context(cls.fixture.service_path)
        cls.collections = _load_registry_collections(cls.context)
        cls.registry_payload = load_registry_from_path(cls.fixture.service_path)

    fixture: Any
    context: dict[str, Any]
    collections: dict[str, Any]
    registry_payload: dict[str, Any]

    def test_registry_validation_uses_canonical_package_only(self) -> None:
        self.assertFalse((ROOT_DIR / 'python/openclaw/control_plane/registry/rules.py').exists())

    def test_virtualized_registry_checks_allow_profiles_without_agent_modules(self) -> None:
        config_path = ROOT_DIR / 'config' / 'control_plane' / 'profiles' / 'agent_platform.service.json'

        internal = _ensure_agent_internal_assembly_registry(config_path)
        control = _ensure_agent_control_plane_registry(config_path)

        self.assertEqual(internal['status'], 'ok')
        self.assertEqual(control['status'], 'ok')
        self.assertEqual(internal['counts'], {'agentModules': 0, 'skillSets': 0, 'permissionPolicies': 0, 'toolsets': 0})
        self.assertEqual(control['counts'], {'agentModules': 0, 'agents': 0, 'implementations': 0})

    def test_agent_platform_profile_does_not_load_business_dispatch_targets(self) -> None:
        payload = load_registry_from_path(ROOT_DIR / 'config' / 'control_plane' / 'profiles' / 'agent_platform.service.json')
        registry_paths = payload['registryPaths']

        self.assertEqual(registry_paths['dispatchTargetRegistryPaths'], [])
        self.assertEqual(
            [Path(item).name for item in registry_paths['dispatchProviderRegistryPaths']],
            ['dispatch_provider_adapters.json'],
        )

    def test_representative_managed_profile_owns_dispatch_target_registry(self) -> None:
        if not MANAGED_EXTENSIONS:
            self.skipTest('base release surface has no repo-managed extension')
        extension = representative_managed_extension(ROOT_DIR)
        payload = load_registry_from_path(extension.default_service_config_path)
        registry_paths = payload['registryPaths']

        self.assertEqual(
            [Path(item).name for item in registry_paths['dispatchTargetRegistryPaths']],
            ['dispatch_targets.json'],
        )
        registry_path = Path(registry_paths['dispatchTargetRegistryPaths'][0]).resolve()
        registry_path.relative_to(extension.root_dir)
        self.assertFalse((ROOT_DIR / 'agent' / 'control_plane' / 'registries' / 'dispatch_targets.json').exists())
        self.assertEqual(
            [Path(item).name for item in registry_paths['dispatchProviderRegistryPaths']],
            ['dispatch_provider_adapters.json'],
        )

    def test_service_scope_classifies_base_platform_and_extension_services(self) -> None:
        base_context = load_registry_service_context(ROOT_DIR / 'config' / 'control_plane' / 'service.json')
        platform_context = load_registry_service_context(
            ROOT_DIR / 'config' / 'control_plane' / 'profiles' / 'agent_platform.service.json'
        )
        extension_context = load_registry_service_context(self.fixture.service_path)

        self.assertEqual(base_context['serviceScope'], {'kind': 'base', 'profileId': 'base', 'extensionId': ''})
        self.assertEqual(
            platform_context['serviceScope'],
            {'kind': 'platform', 'profileId': 'agent_platform', 'extensionId': ''},
        )
        self.assertEqual(
            extension_context['serviceScope'],
            {'kind': 'managed_extension', 'profileId': self.fixture.extension_id, 'extensionId': self.fixture.extension_id},
        )
        registry_payload = load_registry_from_path(self.fixture.service_path)
        self.assertEqual(registry_payload['serviceScope'], extension_context['serviceScope'])

    def test_repo_combination_profile_loads_managed_extensions(self) -> None:
        if COMBO_CONFIG_PATH is None:
            self.skipTest('base release surface has no repo combination profile')
        context = load_registry_service_context(COMBO_CONFIG_PATH)
        payload = load_registry_from_path(COMBO_CONFIG_PATH)

        self.assertEqual(
            context['serviceScope'],
            {'kind': 'repo_combination', 'profileId': COMBO_PROFILE_ID, 'extensionId': ''},
        )
        self.assertEqual(
            context['enabledExtensionIds'],
            ['agent_platform', *COMBO_EXTENSION_IDS],
        )
        for extension_id in COMBO_EXTENSION_IDS:
            with self.subTest(extension_id=extension_id):
                self.assertTrue(any(key.startswith(f'{extension_id}:') for key in payload['modelsByQualifiedId']))
                self.assertTrue(any(key.startswith(f'{extension_id}:') for key in payload['jobsByQualifiedId']))
        self.assertEqual(
            [Path(item).name for item in payload['registryPaths']['dispatchTargetRegistryPaths']],
            ['dispatch_targets.json'],
        )

    def test_base_service_rejects_enabled_extensions_at_runtime(self) -> None:
        with managed_probe_repo('base-service-boundary') as fixture:
            base_path = fixture.repo_root / 'config' / 'control_plane' / 'service.json'
            payload = json.loads(base_path.read_text(encoding='utf-8'))
            payload['extensions']['enabledExtensionIds'] = ['agent_platform']
            _write_json(base_path, payload)

            with self.assertRaisesRegex(CliError, 'base service.*enabledExtensionIds'):
                load_registry_service_context(base_path)

    def test_platform_service_rejects_business_extensions_at_runtime(self) -> None:
        with managed_probe_repo('platform-service-boundary') as fixture:
            platform_path = fixture.repo_root / 'config' / 'control_plane' / 'profiles' / 'agent_platform.service.json'
            payload = json.loads(platform_path.read_text(encoding='utf-8'))
            payload['extensions']['enabledExtensionIds'] = ['agent_platform', fixture.extension_id]
            _write_json(platform_path, payload)

            with self.assertRaisesRegex(CliError, 'agent_platform service.*enabledExtensionIds'):
                load_registry_service_context(platform_path)

    def test_extension_default_service_rejects_extra_enabled_extension_at_runtime(self) -> None:
        with managed_probe_repo('extension-service-extra-enabled') as fixture:
            payload = json.loads(fixture.service_path.read_text(encoding='utf-8'))
            payload['extensions']['enabledExtensionIds'] = ['agent_platform', fixture.extension_id, 'shadow_extension']
            _write_json(fixture.service_path, payload)

            with self.assertRaisesRegex(CliError, 'extension service.*enabledExtensionIds'):
                load_registry_service_context(fixture.service_path)

    def test_extension_default_service_rejects_extra_manifest_dir_at_runtime(self) -> None:
        with managed_probe_repo('extension-service-extra-manifest-dir') as fixture:
            payload = json.loads(fixture.service_path.read_text(encoding='utf-8'))
            payload['extensions']['manifestsDirs'] = [
                '@repo/config/control_plane/extensions.d',
                '@extension/config/control_plane/extensions.d',
                '@repo/config/runtime',
            ]
            _write_json(fixture.service_path, payload)

            with self.assertRaisesRegex(CliError, 'extension service.*manifestsDirs'):
                load_registry_service_context(fixture.service_path)

    def test_custom_composite_service_remains_supported_as_custom_scope(self) -> None:
        with managed_probe_repo('custom-composite-service') as fixture:
            service_path = fixture.repo_root / 'composite.service.json'
            _write_json(
                service_path,
                {
                    'extends': '@repo/config/control_plane/service.json',
                    'extensions': {
                        'manifestsDirs': [
                            '@repo/config/control_plane/extensions.d',
                            f'agent/extensions/{fixture.extension_id}/config/control_plane/extensions.d',
                        ],
                        'enabledExtensionIds': ['agent_platform', fixture.extension_id],
                    },
                },
            )

            context = load_registry_service_context(service_path)

        self.assertEqual(context['serviceScope'], {'kind': 'custom', 'profileId': 'custom', 'extensionId': ''})
        self.assertEqual(context['enabledExtensionIds'], ['agent_platform', fixture.extension_id])

    def test_reserved_root_profile_directory_rejects_custom_service_at_runtime(self) -> None:
        with managed_probe_repo('root-profile-boundary') as fixture:
            profile_path = fixture.repo_root / 'config' / 'control_plane' / 'profiles' / 'business.service.json'
            _write_json(
                profile_path,
                {
                    'extends': '@repo/config/control_plane/service.json',
                    'extensions': {
                        'manifestsDirs': ['@repo/config/control_plane/extensions.d'],
                        'enabledExtensionIds': ['agent_platform'],
                    },
                },
            )

            with self.assertRaisesRegex(CliError, '基座 profile 目录只保留受控 profile'):
                load_registry_service_context(profile_path)

    def test_extension_profile_alias_rejected_at_runtime(self) -> None:
        with managed_probe_repo('extension-profile-alias-boundary') as fixture:
            alias_path = fixture.service_path.with_name('alias.service.json')
            payload = json.loads(fixture.service_path.read_text(encoding='utf-8'))
            _write_json(alias_path, payload)

            with self.assertRaisesRegex(CliError, '扩展 service 配置必须使用自身合同路径'):
                load_registry_service_context(alias_path)

    def test_extension_service_rejects_invalid_extension_id_in_path(self) -> None:
        with managed_probe_repo('extension-profile-invalid-id-boundary') as fixture:
            service_path = (
                fixture.repo_root
                / 'agent'
                / 'extensions'
                / 'AgentProbe'
                / 'config'
                / 'control_plane'
                / 'profiles'
                / 'AgentProbe.service.json'
            )
            _write_json(
                service_path,
                {
                    'extends': '@repo/config/control_plane/service.json',
                    'extensions': {
                        'manifestsDirs': ['@repo/config/control_plane/extensions.d'],
                        'enabledExtensionIds': ['agent_platform'],
                    },
                },
            )

            with self.assertRaisesRegex(CliError, 'extension id 非法'):
                load_registry_service_context(service_path)

    def test_control_plane_config_rejects_extends_outside_repo(self) -> None:
        with managed_probe_repo('config-extends-boundary') as fixture:
            outside_parent = fixture.repo_root.parent / 'outside_parent.service.json'
            _write_json(outside_parent, {'schemaVersion': 1})
            profile_path = fixture.repo_root / 'config' / 'control_plane' / 'profiles' / 'escape.service.json'
            _write_json(
                profile_path,
                {
                    'extends': Path(os.path.relpath(outside_parent.resolve(), start=profile_path.parent.resolve())).as_posix(),
                },
            )

            with self.assertRaisesRegex(ControlPlaneConfigError, 'extends.*仓库内'):
                load_control_plane_service_payload(profile_path)

    def test_control_plane_config_rejects_registry_path_escape(self) -> None:
        with managed_probe_repo('config-registry-boundary') as fixture:
            profile_path = fixture.repo_root / 'config' / 'control_plane' / 'profiles' / 'registry_escape.service.json'
            _write_json(
                profile_path,
                {
                    'extends': '@repo/config/control_plane/service.json',
                    'registry': {
                        'jobsDir': str((fixture.repo_root.parent / 'outside_jobs').resolve()),
                    },
                },
            )

            with self.assertRaisesRegex(ControlPlaneConfigError, 'registry.jobsDir.*仓库内'):
                load_control_plane_service_payload(profile_path)

    def test_non_platform_extension_requires_agent_platform_baseline(self) -> None:
        with managed_probe_repo('extension-platform-baseline') as fixture:
            profile_path = fixture.repo_root / 'without_platform.service.json'
            _write_json(
                profile_path,
                {
                    'extends': '@repo/config/control_plane/service.json',
                    'extensions': {
                        'manifestsDirs': [
                            Path(os.path.relpath(fixture.manifest_dir.resolve(), start=profile_path.parent.resolve())).as_posix(),
                        ],
                        'enabledExtensionIds': [fixture.extension_id],
                    },
                },
            )

            with self.assertRaisesRegex(CliError, 'agent_platform'):
                load_registry_service_context(profile_path)

    def test_extension_manifest_callable_must_use_own_package(self) -> None:
        with managed_probe_repo('extension-callable-boundary') as fixture:
            manifest = json.loads(fixture.manifest_path.read_text(encoding='utf-8'))
            manifest['jobRunners'] = [
                {
                    'id': 'borrowed_platform_runner',
                    'title': 'Borrowed Platform Runner',
                    'module': 'openclaw.control_plane.agent.runtime_runner',
                    'callable': 'run_job',
                    'handlesAgentBindings': False,
                }
            ]
            _write_json(fixture.manifest_path, manifest)

            with self.assertRaisesRegex(CliError, 'own extension python package'):
                load_registry_service_context(fixture.service_path)

    def test_agent_module_assets_must_stay_inside_module_dir(self) -> None:
        with managed_probe_repo('module-asset-boundary') as fixture:
            shared_readme = fixture.primary_module_dir.parent / 'shared_README.md'
            shared_readme.write_text('# shared\n', encoding='utf-8')
            module_payload = json.loads(fixture.primary_module_path.read_text(encoding='utf-8'))
            module_payload['assets']['readmePath'] = '../shared_README.md'
            _write_json(fixture.primary_module_path, module_payload)

            with self.assertRaisesRegex(CliError, 'assets.readmePath.*模块目录'):
                load_registry_from_path(fixture.service_path)

    def test_agent_module_logic_sources_must_stay_inside_extension_root(self) -> None:
        with managed_probe_repo('module-source-boundary') as fixture:
            outside_source = fixture.package_root.parent / 'shared_escape.py'
            outside_source.write_text('from __future__ import annotations\n', encoding='utf-8')
            module_payload = json.loads(fixture.primary_module_path.read_text(encoding='utf-8'))
            module_payload['logic']['sourcePaths'] = [
                Path(os.path.relpath(outside_source.resolve(), start=fixture.primary_module_dir.resolve())).as_posix(),
            ]
            _write_json(fixture.primary_module_path, module_payload)

            with self.assertRaisesRegex(CliError, 'logic.sourcePaths.*extension 根目录'):
                load_registry_from_path(fixture.service_path)

    def test_agent_group_single_source_docs_must_stay_inside_repo(self) -> None:
        with managed_probe_repo('group-doc-boundary') as fixture:
            outside_doc = fixture.repo_root.parent / 'outside_group_doc.md'
            outside_doc.write_text('# outside\n', encoding='utf-8')
            group_path = fixture.groups_dir / f'{PROBE_GROUP_REF}.json'
            group_payload = json.loads(group_path.read_text(encoding='utf-8'))
            group_payload['releasePolicy']['singleSourceDocs'] = [
                Path(os.path.relpath(outside_doc.resolve(), start=fixture.repo_root.resolve())).as_posix(),
            ]
            _write_json(group_path, group_payload)

            with self.assertRaisesRegex(CliError, 'singleSourceDocs.*repository'):
                load_registry_from_path(fixture.service_path)

    def test_control_plane_default_timezone_is_validated_at_registry_root(self) -> None:
        with managed_probe_repo('service-default-timezone-validation') as fixture:
            service_payload = json.loads(fixture.service_path.read_text(encoding='utf-8'))
            service_payload['defaults'] = {'timezone': 'Asia/Shangahi'}
            _write_json(fixture.service_path, service_payload)

            with self.assertRaisesRegex(CliError, 'defaults.timezone'):
                load_registry_from_path(fixture.service_path)

    def test_agent_group_schedule_timezone_is_validated(self) -> None:
        with managed_probe_repo('group-timezone-validation') as fixture:
            group_path = fixture.groups_dir / f'{PROBE_GROUP_REF}.json'
            group_payload = json.loads(group_path.read_text(encoding='utf-8'))
            group_payload['schedulePolicy']['timezone'] = 'Asia/Shangahi'
            _write_json(group_path, group_payload)

            with self.assertRaisesRegex(CliError, 'schedulePolicy.timezone'):
                load_registry_from_path(fixture.service_path)

    def test_resolve_object_activation_keeps_existing_visibility_contract(self) -> None:
        visible_without_activation = resolve_object_activation(
            {},
            label='target fixture.json',
            enabled_extension_ids=['agent_probe'],
            known_extension_ids=['agent_probe'],
            require_activation=False,
        )
        self.assertEqual(visible_without_activation['configuredExtensionIds'], [])
        self.assertEqual(visible_without_activation['activeExtensionIds'], [])
        self.assertEqual(visible_without_activation['primaryActiveExtensionId'], '')
        self.assertTrue(visible_without_activation['visible'])

        with self.assertRaises(CliError):
            resolve_object_activation(
                {},
                label='target fixture.json',
                enabled_extension_ids=['agent_probe'],
                known_extension_ids=['agent_probe'],
                require_activation=True,
            )

        with self.assertRaises(CliError):
            resolve_object_activation(
                {'activation': {'enabledExtensionIds': ['missing_extension']}},
                label='target fixture.json',
                enabled_extension_ids=['agent_probe'],
                known_extension_ids=['agent_probe'],
                require_activation=True,
            )

        with self.assertRaises(CliError):
            resolve_object_activation(
                {'activation': {'enabledExtensionIds': ['agent_probe', 'agent_probe']}},
                label='target fixture.json',
                enabled_extension_ids=['agent_probe'],
                known_extension_ids=['agent_probe'],
                require_activation=True,
            )

    def test_merge_job_runners_preserves_extension_identity_and_binding_ids(self) -> None:
        rows, rows_by_id, binding_runner_ids = _merge_job_runners([
            {
                'id': 'ext_alpha',
                'sourcePath': '/tmp/ext_alpha.json',
                'jobRunners': [
                    {'id': 'alpha', 'title': 'Alpha', 'module': 'pkg.alpha', 'callable': 'run', 'handlesAgentBindings': True},
                    {'id': 'beta', 'title': 'Beta', 'module': 'pkg.beta', 'callable': 'run', 'handlesAgentBindings': False},
                ],
            },
            {
                'id': 'ext_beta',
                'sourcePath': '/tmp/ext_beta.json',
                'jobRunners': [
                    {'id': 'gamma', 'title': 'Gamma', 'module': 'pkg.gamma', 'callable': 'run', 'handlesAgentBindings': True},
                ],
            },
        ])

        self.assertEqual([row['id'] for row in rows], ['alpha', 'beta', 'gamma'])
        self.assertEqual(rows_by_id['alpha']['extensionId'], 'ext_alpha')
        self.assertEqual(rows_by_id['gamma']['sourcePath'], '/tmp/ext_beta.json')
        self.assertEqual(binding_runner_ids, ['alpha', 'gamma'])

    def test_load_schema_cache_returns_independent_payloads(self) -> None:
        with TemporaryDirectory() as tmpdir:
            schema_path = Path(tmpdir) / 'demo.schema.json'
            schema_path.write_text(
                json.dumps({'type': 'object', 'properties': {'name': {'type': 'string'}}}, ensure_ascii=False),
                encoding='utf-8',
            )
            first = load_schema(schema_path)
            first['properties']['name']['type'] = 'integer'
            second = load_schema(schema_path)

        self.assertEqual(second['properties']['name']['type'], 'string')

    def test_extension_owned_schemas_require_activation_and_group_topology_truth(self) -> None:
        for schema_key in ('jobs', 'models', 'targets'):
            schema = load_schema(self.context['schemaPaths'][schema_key])
            self.assertIn('activation', schema['required'], msg=schema_key)

        module_schema = load_schema(self.context['schemaPaths']['agentModules'])
        group_schema = load_schema(self.context['schemaPaths']['agentGroups'])

        self.assertIn('activation', module_schema['required'])
        self.assertIn('activation', group_schema['required'])
        self.assertEqual(
            set(group_schema['properties']['dependencyPolicy']['required']),
            {'haltOnMemberFailure', 'retryMode', 'orderedJobRefs'},
        )
        self.assertEqual(
            set(group_schema['properties']['schedulePolicy']['required']),
            {'timezone', 'windowRef', 'orderBase', 'orderStep', 'jobRefs'},
        )
        for static_member_field in ('memberAgentRefs', 'entryAgentRefs', 'exitAgentRefs'):
            self.assertNotIn(static_member_field, group_schema['properties'])
        self.assertNotIn('orderedMembers', group_schema['properties']['dependencyPolicy']['properties'])

    def test_load_collection_filters_shared_rows_without_activation(self) -> None:
        context = self.context
        target_schema = load_schema(context['schemaPaths']['targets'])
        hidden_target_path = self.fixture.targets_dir / 'dispatch_target_hidden.json'
        hidden_target_payload = json.loads(
            (self.fixture.targets_dir / f'{PROBE_TARGET_REF}.json').read_text(encoding='utf-8')
        )
        hidden_target_payload['id'] = 'dispatch_target_hidden'
        hidden_target_payload['title'] = 'Hidden Dispatch Target'
        hidden_target_payload['activation'] = {'enabledExtensionIds': ['other_extension']}
        hidden_target_path.write_text(
            json.dumps(hidden_target_payload, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8',
        )
        try:
            rows, rows_by_id = _load_collection(
                self.fixture.targets_dir,
                'control-plane targets',
                target_schema,
                enabled_extension_ids=list(context['enabledExtensionIds']),
                known_extension_ids=set(context['knownExtensionIds']) | {'other_extension'},
                require_activation=True,
            )
        finally:
            hidden_target_path.unlink(missing_ok=True)

        self.assertEqual([row['id'] for row in rows], [PROBE_TARGET_REF])
        self.assertIn(PROBE_TARGET_REF, rows_by_id)
        self.assertNotIn('dispatch_target_hidden', rows_by_id)

    def test_extension_owned_collection_rejects_activation_owner_drift(self) -> None:
        context = self.context
        target_schema = load_schema(context['schemaPaths']['targets'])
        drift_target_path = self.fixture.targets_dir / 'dispatch_target_owner_drift.json'
        drift_target_payload = json.loads(
            (self.fixture.targets_dir / f'{PROBE_TARGET_REF}.json').read_text(encoding='utf-8')
        )
        drift_target_payload['id'] = 'dispatch_target_owner_drift'
        drift_target_payload['title'] = 'Owner Drift Dispatch Target'
        drift_target_payload['activation'] = {'enabledExtensionIds': ['agent_platform']}
        drift_target_path.write_text(
            json.dumps(drift_target_payload, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8',
        )
        try:
            with self.assertRaisesRegex(CliError, 'directory owner'):
                _load_collection(
                    self.fixture.targets_dir,
                    'control-plane targets',
                    target_schema,
                    enabled_extension_ids=list(context['enabledExtensionIds']),
                    known_extension_ids=set(context['knownExtensionIds']),
                    require_activation=True,
                    owner_id=self.fixture.extension_id,
                )
        finally:
            drift_target_path.unlink(missing_ok=True)

    def test_load_agent_modules_filters_shared_rows_without_activation(self) -> None:
        context = self.context
        module_schema = load_schema(context['schemaPaths']['agentModules'])
        hidden_module_dir = self.fixture.modules_dir / 'probe_hidden'
        hidden_module_dir.mkdir(parents=True, exist_ok=True)
        hidden_module_payload = json.loads(self.fixture.primary_module_path.read_text(encoding='utf-8'))
        hidden_module_payload['id'] = 'probe_hidden'
        hidden_module_payload['agentRef'] = 'probe_hidden'
        hidden_module_payload['title'] = 'Probe Hidden'
        hidden_module_payload['activation'] = {'enabledExtensionIds': ['other_extension']}
        (hidden_module_dir / 'module.json').write_text(
            json.dumps(hidden_module_payload, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8',
        )
        try:
            rows, rows_by_id = _load_agent_modules(
                self.fixture.modules_dir,
                'control-plane agent modules',
                module_schema,
                enabled_extension_ids=list(context['enabledExtensionIds']),
                known_extension_ids=set(context['knownExtensionIds']) | {'other_extension'},
                require_activation=True,
            )
        finally:
            shutil.rmtree(hidden_module_dir, ignore_errors=True)

        self.assertEqual([row['id'] for row in rows], [PROBE_PRIMARY_MODULE_REF, PROBE_SUPPORT_MODULE_REF])
        self.assertIn(PROBE_PRIMARY_MODULE_REF, rows_by_id)
        self.assertIn(PROBE_SUPPORT_MODULE_REF, rows_by_id)
        self.assertNotIn('probe_hidden', rows_by_id)

    def test_extension_owned_agent_module_rejects_activation_owner_drift(self) -> None:
        context = self.context
        module_schema = load_schema(context['schemaPaths']['agentModules'])
        drift_module_dir = self.fixture.modules_dir / 'probe_owner_drift'
        drift_module_dir.mkdir(parents=True, exist_ok=True)
        drift_module_payload = json.loads(self.fixture.primary_module_path.read_text(encoding='utf-8'))
        drift_module_payload['id'] = 'probe_owner_drift'
        drift_module_payload['agentRef'] = 'probe_owner_drift'
        drift_module_payload['title'] = 'Probe Owner Drift'
        drift_module_payload['activation'] = {'enabledExtensionIds': ['agent_platform']}
        (drift_module_dir / 'module.json').write_text(
            json.dumps(drift_module_payload, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8',
        )
        try:
            with self.assertRaisesRegex(CliError, 'directory owner'):
                _load_agent_modules(
                    self.fixture.modules_dir,
                    'control-plane agent modules',
                    module_schema,
                    enabled_extension_ids=list(context['enabledExtensionIds']),
                    known_extension_ids=set(context['knownExtensionIds']),
                    require_activation=True,
                    owner_id=self.fixture.extension_id,
                )
        finally:
            shutil.rmtree(drift_module_dir, ignore_errors=True)

    def test_load_registry_collections_materializes_virtualized_managed_surfaces(self) -> None:
        collections = self.collections
        self.assertEqual([row['id'] for row in collections['agentModules']], [PROBE_PRIMARY_MODULE_REF, PROBE_SUPPORT_MODULE_REF])
        self.assertEqual([row['id'] for row in collections['agents']], [PROBE_PRIMARY_MODULE_REF, PROBE_SUPPORT_MODULE_REF])
        self.assertEqual([row['id'] for row in collections['implementations']], ['probe_dispatcher_impl', 'probe_helper_impl'])
        self.assertEqual([row['id'] for row in collections['skillSets']], [PROBE_PRIMARY_MODULE_REF, PROBE_SUPPORT_MODULE_REF])
        self.assertEqual([row['id'] for row in collections['permissionPolicies']], [PROBE_PRIMARY_MODULE_REF, PROBE_SUPPORT_MODULE_REF])
        self.assertEqual([row['id'] for row in collections['toolsets']], [PROBE_PRIMARY_MODULE_REF, PROBE_SUPPORT_MODULE_REF])
        self.assertEqual(sorted(collections['runtimeAdaptersById']), ['python_module'])
        self.assertIn('dispatch_target_default', collections['targetsById'])
        self.assertTrue(all(str(row.get('sourcePath') or '').endswith('.json') for row in collections['agents']))

    def test_virtualized_registry_checks_still_match_managed_module_truth(self) -> None:
        internal = _ensure_agent_internal_assembly_registry(self.fixture.service_path)
        control = _ensure_agent_control_plane_registry(self.fixture.service_path)

        self.assertEqual(internal['status'], 'ok')
        self.assertEqual(control['status'], 'ok')
        self.assertEqual(internal['counts']['agentModules'], 2)
        self.assertEqual(internal['counts']['skillSets'], 2)
        self.assertEqual(internal['counts']['permissionPolicies'], 2)
        self.assertEqual(internal['counts']['toolsets'], 2)
        self.assertEqual(control['counts']['agentModules'], 2)
        self.assertEqual(control['counts']['agents'], 2)
        self.assertEqual(control['counts']['implementations'], 2)

    def test_load_registry_from_path_still_materializes_registry_payload(self) -> None:
        payload = self.registry_payload

        self.assertEqual(Path(payload['configPath']).resolve(), self.fixture.service_path)
        self.assertIn(PROBE_PRIMARY_MODULE_REF, payload['agentsById'])
        self.assertIn(PROBE_SUPPORT_MODULE_REF, payload['agentModulesById'])
        self.assertIn(PROBE_TARGET_REF, payload['targetsById'])


if __name__ == '__main__':
    unittest.main()
