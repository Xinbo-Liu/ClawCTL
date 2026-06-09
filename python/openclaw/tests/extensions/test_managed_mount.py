from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from openclaw.control_plane.extensions.api import enabled_extensions_from_config
from openclaw.control_plane.registry import load_registry
from openclaw.control_plane.registry_loader.config import load_registry_service_context
from openclaw.doctor.agent_modules.support import run_python_module
from openclaw.lib.control_plane import diagnostic_surface
from openclaw.doctor.agent_modules.managed_probe_fixture import (
    PROBE_CHANGE_CONTROL_DOC_PATHS,
    PROBE_DIAGNOSTIC_ACTION,
    PROBE_EXTENSION_ID,
    PROBE_GROUP_REF,
    PROBE_MODEL_REF,
    PROBE_PACKAGE_NAME,
    PROBE_PRIMARY_MODULE_REF,
    PROBE_SUPPORT_MODULE_REF,
    PROBE_TARGET_REF,
)
from openclaw.lib.repo.managed_extensions import load_managed_extensions_index, managed_extension_python_roots_for_config_path
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.tests.support.helpers import isolated_test_root
from openclaw.tests.support.lightweight_repo import materialize_managed_probe_repo
from openclaw.tests.support.managed_probe import managed_probe_repo


ROOT_DIR = resolve_repo_root(Path(__file__))


class ManagedExtensionMountTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._fixture_context = None
        cls._fixture = None
        cls._registry = None

    _fixture_context: Any
    _fixture: Any
    _registry: dict[str, Any] | None

    @classmethod
    def _ensure_fixture(cls) -> Any:
        if cls._fixture is None:
            cls._fixture_context = managed_probe_repo('managed-extension-mount-shared')
            cls._fixture = cls._fixture_context.__enter__()
            cls.addClassCleanup(cls._fixture_context.__exit__, None, None, None)
        return cls._fixture

    @classmethod
    def _ensure_registry(cls) -> dict[str, Any]:
        if cls._registry is None:
            cls._registry = load_registry(cls._ensure_fixture().service_path)
        return cls._registry

    @property
    def fixture(self) -> Any:
        return type(self)._ensure_fixture()

    @property
    def registry(self) -> dict[str, Any]:
        return type(self)._ensure_registry()

    def _read_json(self, path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding='utf-8'))

    def _write_json(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    def _write_minimal_dispatch_registry_extension(self, repo_root: Path, extension_id: str) -> Path:
        package_root = repo_root / 'agent' / 'extensions' / extension_id
        manifest_dir = package_root / 'config' / 'control_plane' / 'extensions.d'
        dispatch_registry_path = package_root / 'agent' / 'control_plane' / 'registries' / 'dispatch_targets.json'
        self._write_json(
            manifest_dir / f'{extension_id}.json',
            {
                'id': extension_id,
                'title': extension_id,
                'registry': {
                    'dispatchTargetRegistryPaths': ['@extension/agent/control_plane/registries/dispatch_targets.json'],
                },
            },
        )
        self._write_json(dispatch_registry_path, {'version': 7, 'targets': []})
        (package_root / 'python' / f'openclaw_ext_{extension_id.removeprefix("agent_")}' / '__init__.py').parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        (package_root / 'python' / f'openclaw_ext_{extension_id.removeprefix("agent_")}' / '__init__.py').write_text(
            '',
            encoding='utf-8',
        )
        return dispatch_registry_path.resolve()

    def test_managed_extension_index_can_resolve_package_paths(self) -> None:
        rows = {row.id: row for row in load_managed_extensions_index(self.fixture.repo_root)}
        self.assertIn(PROBE_EXTENSION_ID, rows)
        row = rows[PROBE_EXTENSION_ID]
        self.assertEqual(row.root_dir, self.fixture.package_root)
        self.assertEqual(row.default_service_config_path, self.fixture.service_path)
        self.assertEqual(managed_extension_python_roots_for_config_path(self.fixture.service_path, start_path=self.fixture.repo_root), row.python_roots)

    def test_managed_extension_python_roots_only_attach_to_default_service_path(self) -> None:
        alias_path = self.fixture.service_path.with_name('alias.service.json')
        self._write_json(alias_path, self._read_json(self.fixture.service_path))
        try:
            roots = managed_extension_python_roots_for_config_path(alias_path, start_path=self.fixture.repo_root)
        finally:
            alias_path.unlink(missing_ok=True)

        self.assertEqual(roots, ())

    def test_managed_probe_package_can_be_loaded_by_explicit_config_path(self) -> None:
        extensions = enabled_extensions_from_config(self.fixture.service_path)
        modules = {row['id']: row for row in self.registry.get('agentModules') or []}
        agents = {row['id']: row for row in self.registry.get('agents') or []}
        groups = {row['id']: row for row in self.registry.get('agentGroups') or []}
        jobs = {row['id']: row for row in self.registry.get('jobs') or []}
        models = {row['id']: row for row in self.registry.get('models') or []}
        targets = {row['id']: row for row in self.registry.get('targets') or []}

        self.assertEqual([row.get('id') for row in extensions], ['agent_platform', PROBE_EXTENSION_ID])
        self.assertEqual(sorted(modules), [PROBE_PRIMARY_MODULE_REF, PROBE_SUPPORT_MODULE_REF])
        self.assertEqual(sorted(agents), [PROBE_PRIMARY_MODULE_REF, PROBE_SUPPORT_MODULE_REF])
        self.assertEqual(list(groups), [PROBE_GROUP_REF])
        self.assertEqual(list(jobs), ['probe_dispatch_weekday'])
        self.assertEqual(list(models), [PROBE_MODEL_REF])
        self.assertEqual(list(targets), [PROBE_TARGET_REF])

        primary = modules[PROBE_PRIMARY_MODULE_REF]
        support = modules[PROBE_SUPPORT_MODULE_REF]
        group = groups[PROBE_GROUP_REF]

        self.assertEqual(primary.get('extensionId'), PROBE_EXTENSION_ID)
        self.assertEqual(primary.get('resolvedActiveExtensionIds'), [PROBE_EXTENSION_ID])
        self.assertEqual(agents[PROBE_PRIMARY_MODULE_REF].get('extensionId'), PROBE_EXTENSION_ID)
        self.assertEqual(primary.get('controlPlane', {}).get('agent', {}).get('defaultModelProfileRef'), PROBE_MODEL_REF)
        self.assertEqual(
            primary.get('operations', {}).get('send_default', {}).get('jobBindings', {}).get('probe_dispatch_weekday', {}).get('targetBindingRef'),
            PROBE_TARGET_REF,
        )
        self.assertEqual(support.get('resolvedActiveExtensionIds'), [PROBE_EXTENSION_ID])
        self.assertEqual(group.get('dependencyPolicy', {}).get('orderedJobRefs'), ['probe_dispatch_weekday'])
        rendered = diagnostic_surface.render_action(PROBE_DIAGNOSTIC_ACTION, config_path=self.fixture.service_path, extension_id=PROBE_EXTENSION_ID)
        self.assertIn(f'extension: {PROBE_EXTENSION_ID}', rendered)
        self.assertIn(f'action: {PROBE_DIAGNOSTIC_ACTION}', rendered)

    def test_managed_probe_module_help_works_without_separate_install(self) -> None:
        result = run_python_module(
            self.fixture.repo_root,
            f'{PROBE_PACKAGE_NAME}.modules.{PROBE_PRIMARY_MODULE_REF}.main',
            ['--help'],
            extra_env={'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH': str(self.fixture.service_path)},
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn(PROBE_PRIMARY_MODULE_REF, result.stdout)
        self.assertEqual(result.stderr, '')

    def test_managed_probe_package_defaults_can_be_overridden_without_rewriting_logic(self) -> None:
        model_path = self.fixture.models_dir / f'{PROBE_MODEL_REF}.json'
        target_path = self.fixture.targets_dir / f'{PROBE_TARGET_REF}.json'
        original_model_payload = self._read_json(model_path)
        original_target_payload = self._read_json(target_path)
        try:
            model_payload = dict(original_model_payload)
            model_payload['provider'] = 'anthropic'
            model_payload['modelRef'] = 'anthropic/claude-sonnet-4.5'
            self._write_json(model_path, model_payload)

            target_payload = dict(original_target_payload)
            target_payload['provider'] = 'slack_webhook'
            target_payload['transport'] = 'webhook'
            self._write_json(target_path, target_payload)

            registry = load_registry(self.fixture.service_path)
            modules = {row['id']: row for row in registry.get('agentModules') or []}
            models = {row['id']: row for row in registry.get('models') or []}
            targets = {row['id']: row for row in registry.get('targets') or []}

            self.assertEqual(models[PROBE_MODEL_REF].get('provider'), 'anthropic')
            self.assertEqual(models[PROBE_MODEL_REF].get('modelRef'), 'anthropic/claude-sonnet-4.5')
            self.assertEqual(targets[PROBE_TARGET_REF].get('provider'), 'slack_webhook')
            self.assertEqual(targets[PROBE_TARGET_REF].get('transport'), 'webhook')
            self.assertEqual(
                modules[PROBE_PRIMARY_MODULE_REF].get('operations', {}).get('send_default', {}).get('jobBindings', {}).get('probe_dispatch_weekday', {}).get('targetBindingRef'),
                PROBE_TARGET_REF,
            )
        finally:
            self._write_json(model_path, original_model_payload)
            self._write_json(target_path, original_target_payload)

    def test_managed_module_readmes_point_to_real_package_paths(self) -> None:
        group_rel = f'agent/extensions/{PROBE_EXTENSION_ID}/agent/control_plane/groups/{PROBE_GROUP_REF}.json'
        shared_rel = f'agent/extensions/{PROBE_EXTENSION_ID}/python/{PROBE_PACKAGE_NAME}/domains/probe/shared/'

        self.assertTrue((self.fixture.groups_dir / f'{PROBE_GROUP_REF}.json').is_file())
        self.assertTrue((self.fixture.python_package_dir / 'domains' / 'probe' / 'shared').is_dir())

        for module_ref in (PROBE_PRIMARY_MODULE_REF, PROBE_SUPPORT_MODULE_REF):
            readme_path = self.fixture.modules_dir / module_ref / 'README.md'
            text = readme_path.read_text(encoding='utf-8')
            module_payload = self._read_json(self.fixture.modules_dir / module_ref / 'module.json')

            module_dir_rel = f'agent/extensions/{PROBE_EXTENSION_ID}/agent/modules/{module_ref}/'
            module_manifest_rel = f'agent/extensions/{PROBE_EXTENSION_ID}/agent/modules/{module_ref}/module.json'
            module_main_rel = f'agent/extensions/{PROBE_EXTENSION_ID}/python/{PROBE_PACKAGE_NAME}/modules/{module_ref}/main.py'

            self.assertTrue((self.fixture.modules_dir / module_ref).is_dir())
            self.assertTrue((self.fixture.python_package_dir / 'modules' / module_ref / 'main.py').is_file())
            self.assertIn(module_dir_rel, text)
            self.assertIn(module_manifest_rel, text)
            self.assertIn(module_main_rel, text)
            self.assertIn(group_rel, text)
            self.assertIn(shared_rel, text)
            self.assertEqual(module_payload.get('governance', {}).get('changeControlDocPaths'), list(PROBE_CHANGE_CONTROL_DOC_PATHS))
            self.assertNotIn(f'`agent/modules/{module_ref}/`', text)
            self.assertNotIn(f'`python/openclaw/modules/{module_ref}/main.py`', text)

    def test_two_managed_extensions_can_own_extension_local_dispatch_registries(self) -> None:
        with isolated_test_root('managed-extension-dual-registry') as repo_root:
            materialize_managed_probe_repo(repo_root)
            alpha_registry_path = self._write_minimal_dispatch_registry_extension(repo_root, PROBE_EXTENSION_ID)
            beta_registry_path = self._write_minimal_dispatch_registry_extension(repo_root, 'agent_probe_beta')
            service_path = repo_root / 'dual_managed.service.json'
            self._write_json(
                service_path,
                {
                    'extends': '@repo/config/control_plane/service.json',
                    'extensions': {
                        'manifestsDirs': [
                            '@repo/config/control_plane/extensions.d',
                            f'agent/extensions/{PROBE_EXTENSION_ID}/config/control_plane/extensions.d',
                            'agent/extensions/agent_probe_beta/config/control_plane/extensions.d',
                        ],
                        'enabledExtensionIds': [
                            'agent_platform',
                            PROBE_EXTENSION_ID,
                            'agent_probe_beta',
                        ],
                    },
                },
            )

            context = load_registry_service_context(service_path)

            extensions = [str(row.get('id') or '') for row in context.get('extensions') or []]
            dispatch_registry_paths = [
                Path(str(item)).resolve()
                for item in (context.get('registryInputs') or {}).get('dispatch_target_registry_paths') or []
            ]
            self.assertEqual(context.get('serviceScope'), {'kind': 'custom', 'profileId': 'custom', 'extensionId': ''})
            self.assertEqual(extensions, ['agent_platform', PROBE_EXTENSION_ID, 'agent_probe_beta'])
            self.assertEqual(dispatch_registry_paths, [alpha_registry_path.resolve(), beta_registry_path.resolve()])
            self.assertFalse((repo_root / 'agent' / 'control_plane' / 'registries' / 'dispatch_targets.json').exists())


if __name__ == '__main__':
    unittest.main()
