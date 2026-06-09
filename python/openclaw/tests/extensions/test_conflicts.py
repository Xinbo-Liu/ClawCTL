from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from openclaw.control_plane.extensions.api import (
    discover_known_extension_ids,
    extension_cli_commands,
    extension_internal_api_routes,
    extension_ready_checks,
    known_extension_cli_commands,
    load_enabled_extensions,
    load_extension_manifests,
)
from openclaw.control_plane.extensions.normalization import ExtensionError
from openclaw.control_plane.extensions.policy import is_unauthenticated_extension_route_allowed
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.managed_extensions import ManagedExtensionError


ROOT_DIR = resolve_repo_root(Path(__file__))


class ExtensionConflictValidationTest(unittest.TestCase):
    def _repo_root_for_manifest_dirs(self, manifests_dirs: list[Path]) -> Path | None:
        for path in manifests_dirs:
            resolved = Path(path).resolve()
            parts = resolved.parts
            if 'agent' in parts and 'extensions' in parts:
                for parent in resolved.parents:
                    if (parent / 'config' / 'runtime' / 'paths.json').exists() and (parent / 'python' / 'openclaw').exists():
                        return parent.resolve()
            for parent in [resolved, *resolved.parents]:
                if (parent / 'config' / 'runtime' / 'paths.json').exists() and (parent / 'python' / 'openclaw').exists():
                    return parent.resolve()
        return None

    def _service_payload(self, manifests_dirs: list[Path], enabled_ids: list[str]) -> dict[str, object]:
        repo_root = self._repo_root_for_manifest_dirs(manifests_dirs)
        normalized_enabled_ids = list(enabled_ids)
        normalized_manifest_dirs = [str(path) for path in manifests_dirs]
        if repo_root is not None and any(extension_id != 'agent_platform' for extension_id in normalized_enabled_ids):
            platform_dir = self._contract_manifest_dir(repo_root, 'agent_platform')
            if platform_dir.exists() and str(platform_dir) not in normalized_manifest_dirs:
                normalized_manifest_dirs.insert(0, str(platform_dir))
            if 'agent_platform' not in normalized_enabled_ids:
                normalized_enabled_ids.insert(0, 'agent_platform')
        payload: dict[str, object] = {
            'extensions': {
                'manifestsDirs': normalized_manifest_dirs,
                'enabledExtensionIds': normalized_enabled_ids,
            }
        }
        return payload

    def _write_manifest(self, manifests_dir: Path, extension_id: str, payload: dict[str, object]) -> None:
        manifest = dict(payload)
        manifest.setdefault('id', extension_id)
        manifest.setdefault('title', extension_id)
        (manifests_dir / f'{extension_id}.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

    def _write_json(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    def _prepare_repo_root(self, root: Path) -> None:
        (root / 'python' / 'openclaw').mkdir(parents=True, exist_ok=True)
        (root / 'config' / 'runtime').mkdir(parents=True, exist_ok=True)
        (root / 'config' / 'control_plane').mkdir(parents=True, exist_ok=True)
        self._write_json(root / 'config' / 'runtime' / 'paths.json', {'entries': {}})
        if not (root / 'config' / 'control_plane' / 'service.json').exists():
            self._write_json(root / 'config' / 'control_plane' / 'service.json', {})
        platform_dir = self._contract_manifest_dir(root, 'agent_platform')
        platform_dir.mkdir(parents=True, exist_ok=True)
        platform_manifest = platform_dir / 'agent_platform.json'
        if not platform_manifest.exists():
            self._write_manifest(platform_dir, 'agent_platform', {'title': 'Agent Platform'})

    def _extension_root(self, root: Path, extension_id: str) -> Path:
        return (root / 'agent' / 'extensions' / extension_id).resolve()

    def _contract_manifest_dir(self, root: Path, extension_id: str) -> Path:
        if extension_id == 'agent_platform':
            return (root / 'config' / 'control_plane' / 'extensions.d').resolve()
        return (self._extension_root(root, extension_id) / 'config' / 'control_plane' / 'extensions.d').resolve()

    def _write_contract_manifest(self, root: Path, extension_id: str, payload: dict[str, object]) -> Path:
        self._prepare_repo_root(root)
        manifests_dir = self._contract_manifest_dir(root, extension_id)
        manifests_dir.mkdir(parents=True, exist_ok=True)
        if extension_id != 'agent_platform':
            package_dir = self._extension_root(root, extension_id) / 'python' / 'pkg'
            package_dir.mkdir(parents=True, exist_ok=True)
            (package_dir / '__init__.py').write_text('', encoding='utf-8')
        self._write_manifest(manifests_dir, extension_id, payload)
        return manifests_dir

    def _write_service_config(self, root: Path, manifests_dirs: list[Path], enabled_ids: list[str]) -> Path:
        self._prepare_repo_root(root)
        service_path = root / 'config' / 'control_plane' / 'service.json'
        service_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json(
            service_path,
            self._service_payload(manifests_dirs, enabled_ids),
        )
        return service_path

    def test_duplicate_cli_command_across_enabled_extensions_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            dir_a = self._write_contract_manifest(base, 'ext_a', {'cliCommands': [{'command': 'same', 'module': 'pkg.a'}]})
            dir_b = self._write_contract_manifest(base, 'ext_b', {'cliCommands': [{'command': 'same', 'module': 'pkg.b'}]})
            with self.assertRaises(ExtensionError) as ctx:
                load_enabled_extensions(self._service_payload([dir_a, dir_b], ['ext_a', 'ext_b']), service_base_dir=base)
        self.assertIn('extension CLI command', str(ctx.exception))

    def test_duplicate_internal_api_route_path_across_enabled_extensions_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            route = {'id': 'fixture_route', 'path': '/v1/fixture', 'module': 'pkg.route', 'callable': 'render'}
            dir_a = self._write_contract_manifest(base, 'ext_a', {'internalApiRoutes': [route]})
            dir_b = self._write_contract_manifest(base, 'ext_b', {'internalApiRoutes': [dict(route, id='fixture_route_b')]})
            with self.assertRaises(ExtensionError) as ctx:
                load_enabled_extensions(self._service_payload([dir_a, dir_b], ['ext_a', 'ext_b']), service_base_dir=base)
        self.assertIn('route path', str(ctx.exception))

    def test_multiple_dispatch_registries_across_enabled_extensions_are_allowed(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            registry_a = self._extension_root(base, 'ext_a') / 'a.json'
            registry_b = self._extension_root(base, 'ext_b') / 'b.json'
            self._write_json(registry_a, {})
            self._write_json(registry_b, {})
            dir_a = self._write_contract_manifest(base, 'ext_a', {'registry': {'dispatchTargetRegistryPaths': ['@extension/a.json']}})
            dir_b = self._write_contract_manifest(base, 'ext_b', {'registry': {'dispatchTargetRegistryPaths': ['@extension/b.json']}})
            manifests = load_enabled_extensions(self._service_payload([dir_a, dir_b], ['ext_a', 'ext_b']), service_base_dir=base)
        business_manifests = [row for row in manifests if row['id'] != 'agent_platform']
        self.assertEqual([row['id'] for row in business_manifests], ['ext_a', 'ext_b'])
        self.assertEqual([item.name for item in business_manifests[0]['registry']['dispatchTargetRegistryPaths']], ['a.json'])
        self.assertEqual([item.name for item in business_manifests[1]['registry']['dispatchTargetRegistryPaths']], ['b.json'])
        self.assertNotIn('dispatchTargetRegistryPath', business_manifests[0]['registry'])
        self.assertNotIn('dispatchTargetRegistryPath', business_manifests[1]['registry'])

    def test_singular_dispatch_registry_keys_are_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            registry_a = self._extension_root(base, 'ext_a') / 'a.json'
            self._write_json(registry_a, {})
            manifests_dir = self._write_contract_manifest(base, 'ext_a', {'registry': {'dispatchTargetRegistryPath': '@extension/a.json'}})
            with self.assertRaises(ExtensionError) as ctx:
                load_enabled_extensions(self._service_payload([manifests_dir], ['ext_a']), service_base_dir=base)
        self.assertIn('dispatchTargetRegistryPath', str(ctx.exception))

    def test_manifest_strict_shape_rejects_unknown_fields_and_compat_aliases(self) -> None:
        cases: list[tuple[dict[str, object], str]] = [
            ({'id': '../bad'}, 'lowercase extension id pattern'),
            ({'sampleContract': {}}, 'unsupported manifest field(s): sampleContract'),
            ({'registry': {'jobsDir': '@extension/jobs'}}, 'extension.registry contains unsupported manifest field(s): jobsDir'),
            ({'compat': {'control_plane': '>=1.0.0'}}, 'extension ext_a.compat contains unsupported manifest field(s): control_plane'),
            ({'dependencies': [{'id': 'ext_base', 'versionRange': '>=1.0.0'}]}, 'versionRange'),
            (
                {'readyChecks': [{'id': 'ready_a', 'module': 'pkg.ready', 'callable': 'probe', 'blocking': 'false'}]},
                'readyChecks[0].blocking must be a boolean',
            ),
        ]
        for payload, expected in cases:
            with self.subTest(expected=expected):
                with TemporaryDirectory() as tmp:
                    base = Path(tmp)
                    manifests_dir = self._write_contract_manifest(base, 'ext_a', payload)
                    enabled_id = str(payload.get('id') or 'ext_a')
                    with self.assertRaises(ExtensionError) as ctx:
                        load_enabled_extensions(self._service_payload([manifests_dir], [enabled_id]), service_base_dir=base)
                self.assertIn(expected, str(ctx.exception))

    def test_extension_manifest_must_be_loaded_from_contract_path(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._prepare_repo_root(base)
            wrong_dir = self._contract_manifest_dir(base, 'ext_wrong_path')
            wrong_dir.mkdir(parents=True)
            self._write_json(wrong_dir / 'renamed_manifest.json', {'id': 'ext_wrong_path', 'title': 'wrong'})
            with self.assertRaises(ExtensionError) as ctx:
                load_enabled_extensions(self._service_payload([wrong_dir], ['ext_wrong_path']), service_base_dir=base)
        self.assertIn('manifest must be loaded from repository contract path', str(ctx.exception))

    def test_extension_manifest_dirs_must_use_contract_paths_even_when_enabled_manifest_is_valid(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            valid_dir = self._write_contract_manifest(base, 'ext_a', {'title': 'A'})
            wrong_dir = base / 'config' / 'control_plane' / 'fixtures' / 'extensions.d'
            wrong_dir.mkdir(parents=True)
            with self.assertRaises(ExtensionError) as ctx:
                load_enabled_extensions(self._service_payload([valid_dir, wrong_dir], ['ext_a']), service_base_dir=base)
        self.assertIn('must use repository contract manifest dirs', str(ctx.exception))

    def test_extension_manifest_dir_extension_id_must_follow_project_pattern(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._prepare_repo_root(base)
            wrong_dir = base / 'agent' / 'extensions' / 'ExtA' / 'config' / 'control_plane' / 'extensions.d'
            wrong_dir.mkdir(parents=True)
            with self.assertRaises(ExtensionError) as ctx:
                load_extension_manifests(self._service_payload([wrong_dir], []), service_base_dir=base)
        self.assertIn('must use repository contract manifest dirs', str(ctx.exception))

    def test_disabled_manifest_with_id_must_still_follow_manifest_contract(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            enabled_dir = self._write_contract_manifest(base, 'ext_enabled', {'title': 'Enabled'})
            disabled_dir = self._write_contract_manifest(base, 'ext_disabled', {'sampleContract': {}})
            with self.assertRaises(ExtensionError) as ctx:
                load_enabled_extensions(self._service_payload([enabled_dir, disabled_dir], ['ext_enabled']), service_base_dir=base)
        self.assertIn('unsupported manifest field(s): sampleContract', str(ctx.exception))

    def test_extension_service_config_must_stay_inside_repo_root_even_with_env_root(self) -> None:
        with TemporaryDirectory() as tmp:
            parent = Path(tmp)
            repo_root = parent / 'repo'
            outside_service_dir = parent / 'outside_service'
            outside_service_dir.mkdir(parents=True)
            manifests_dir = self._write_contract_manifest(repo_root, 'ext_a', {'title': 'ext_a'})
            with mock.patch.dict(os.environ, {'OPENCLAW_REPO_ROOT': str(repo_root)}, clear=False):
                with self.assertRaises(ExtensionError) as ctx:
                    load_enabled_extensions(
                        self._service_payload([manifests_dir], ['ext_a']),
                        service_base_dir=outside_service_dir,
                    )
        self.assertIn('must stay inside the repository root', str(ctx.exception))

    def test_extension_manifest_registry_paths_must_stay_inside_extension_root(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifests_dir = self._write_contract_manifest(
                base,
                'ext_escape',
                {
                    'registry': {
                        'dispatchTargetRegistryPaths': ['@repo/config/control_plane/service.json'],
                    },
                },
            )
            with self.assertRaises(ExtensionError) as ctx:
                load_enabled_extensions(self._service_payload([manifests_dir], ['ext_escape']), service_base_dir=base)
        self.assertIn('escapes extension root', str(ctx.exception))

    def test_extension_manifest_schema_paths_must_stay_inside_repo_root(self) -> None:
        with TemporaryDirectory() as tmp:
            parent = Path(tmp)
            base = parent / 'repo'
            self._prepare_repo_root(base)
            manifests_dir = self._contract_manifest_dir(base, 'ext_schema_escape')
            manifests_dir.mkdir(parents=True)
            outside_schema = parent / 'outside.schema.json'
            self._write_json(outside_schema, {'type': 'object'})
            schema_rel = Path(os.path.relpath(outside_schema, manifests_dir)).as_posix()
            self._write_manifest(
                manifests_dir,
                'ext_schema_escape',
                {'schemas': {'agentGroupsSchema': schema_rel}},
            )
            with self.assertRaises(ExtensionError) as ctx:
                load_enabled_extensions(self._service_payload([manifests_dir], ['ext_schema_escape']), service_base_dir=base)
        self.assertIn('escapes repository root', str(ctx.exception))

    def test_job_runner_workspace_docs_testing_runtime_and_path_conflicts_are_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            root_a = self._extension_root(base, 'ext_a')
            root_b = self._extension_root(base, 'ext_b')
            workspace_a = root_a / 'workspace_a.json'
            workspace_b = root_b / 'workspace_b.json'
            docs_a = root_a / 'docs_a.json'
            docs_b = root_b / 'docs_b.json'
            testing_a = root_a / 'testing_a.json'
            testing_b = root_b / 'testing_b.json'
            runtime_a = root_a / 'runtime_a.json'
            runtime_b = root_b / 'runtime_b.json'
            path_a = root_a / 'path_a.json'
            path_b = root_b / 'path_b.json'
            self._write_json(workspace_a, {'control_plane': [{'template': 'same_template', 'target_entry': 'same_target'}], 'stale_dirs': []})
            self._write_json(workspace_b, {'control_plane': [{'template': 'same_template', 'target_entry': 'same_target'}], 'stale_dirs': []})
            self._write_json(docs_a, {'pages': [{'path': 'docs/same.md'}]})
            self._write_json(docs_b, {'pages': [{'path': 'docs/same.md'}]})
            self._write_json(testing_a, {'groups': [{'id': 'same_group'}], 'checks': [{'id': 'same_check'}]})
            self._write_json(testing_b, {'groups': [{'id': 'same_group'}], 'checks': [{'id': 'same_check'}]})
            self._write_json(runtime_a, {'targets': [{'target': 'same_target'}]})
            self._write_json(runtime_b, {'targets': [{'target': 'same_target'}]})
            self._write_json(path_a, {'entrypoints': {'same_entry': {'title': 'A'}}, 'common_entries': [{'entry_id': 'same_entry', 'title': 'A'}]})
            self._write_json(path_b, {'entrypoints': {'same_entry': {'title': 'B'}}, 'common_entries': [{'entry_id': 'same_entry', 'title': 'B'}]})
            dir_a = self._write_contract_manifest(
                base,
                'ext_a',
                {
                    'jobRunners': [{'id': 'same_runner', 'title': 'same_runner', 'module': 'pkg.a', 'callable': 'run'}],
                    'surfaceFragments': {
                        'workspaceTemplatesManifestPath': '@extension/workspace_a.json',
                        'testingManifestPath': '@extension/testing_a.json',
                        'runtimeServiceRegistryPath': '@extension/runtime_a.json',
                    },
                    'governanceSurfaces': {
                        'docsRegistryPath': '@extension/docs_a.json',
                        'pathEntrypointsSurfacePath': '@extension/path_a.json',
                    },
                },
            )
            dir_b = self._write_contract_manifest(
                base,
                'ext_b',
                {
                    'jobRunners': [{'id': 'same_runner', 'title': 'same_runner', 'module': 'pkg.b', 'callable': 'run'}],
                    'surfaceFragments': {
                        'workspaceTemplatesManifestPath': '@extension/workspace_b.json',
                        'testingManifestPath': '@extension/testing_b.json',
                        'runtimeServiceRegistryPath': '@extension/runtime_b.json',
                    },
                    'governanceSurfaces': {
                        'docsRegistryPath': '@extension/docs_b.json',
                        'pathEntrypointsSurfacePath': '@extension/path_b.json',
                    },
                },
            )
            with self.assertRaises(ExtensionError) as ctx:
                load_enabled_extensions(self._service_payload([dir_a, dir_b], ['ext_a', 'ext_b']), service_base_dir=base)
        self.assertTrue(
            any(token in str(ctx.exception) for token in (
                'extension job runner id',
                'extension workspace template',
                'extension docs registry page path',
                'extension testing manifest group id',
                'extension runtime service registry target',
                'extension path entrypoint id',
            ))
        )

    def test_unauthenticated_extension_route_manifest_is_environment_agnostic(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            route = {'id': 'public_route', 'path': '/v1/public', 'module': 'pkg.route', 'callable': 'render', 'authRequired': False}
            manifests_dir = self._write_contract_manifest(base, 'ext_a', {'internalApiRoutes': [route]})
            with mock.patch.dict(os.environ, {
                'OPENCLAW_INTERNAL_API_ENABLE_UNAUTH_EXTENSION_ROUTES': '',
                'OPENCLAW_INTERNAL_API_UNAUTH_EXTENSION_ROUTE_IDS': '',
            }, clear=False):
                manifests = load_enabled_extensions(self._service_payload([manifests_dir], ['ext_a']), service_base_dir=base)
        business_manifests = [row for row in manifests if row['id'] != 'agent_platform']
        self.assertEqual(len(business_manifests), 1)
        self.assertEqual(business_manifests[0]['internalApiRoutes'][0]['id'], 'public_route')
        with mock.patch.dict(os.environ, {
            'OPENCLAW_INTERNAL_API_ENABLE_UNAUTH_EXTENSION_ROUTES': '',
            'OPENCLAW_INTERNAL_API_UNAUTH_EXTENSION_ROUTE_IDS': '',
        }, clear=False):
            self.assertFalse(is_unauthenticated_extension_route_allowed('public_route'))
        with mock.patch.dict(os.environ, {
            'OPENCLAW_INTERNAL_API_ENABLE_UNAUTH_EXTENSION_ROUTES': '1',
            'OPENCLAW_INTERNAL_API_UNAUTH_EXTENSION_ROUTE_IDS': 'public_route',
        }, clear=False):
            self.assertTrue(is_unauthenticated_extension_route_allowed('public_route'))

    def test_load_extension_manifests_and_known_cli_commands_include_disabled_manifests(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            enabled_dir = self._write_contract_manifest(base, 'ext_enabled', {'cliCommands': [{'command': 'enabled', 'module': 'pkg.enabled'}]})
            disabled_dir = self._write_contract_manifest(base, 'ext_disabled', {'cliCommands': [{'command': 'disabled', 'module': 'pkg.disabled'}]})
            payload = self._service_payload([disabled_dir, enabled_dir], ['ext_enabled'])
            service_path = self._write_service_config(base, [disabled_dir, enabled_dir], ['ext_enabled'])

            manifests = load_extension_manifests(payload, service_base_dir=base)
            enabled_commands = extension_cli_commands(service_path)
            known_commands = known_extension_cli_commands(service_path)

        self.assertEqual([row['id'] for row in manifests if row['id'] != 'agent_platform'], ['ext_disabled', 'ext_enabled'])
        self.assertEqual(enabled_commands, {'enabled': 'pkg.enabled'})
        self.assertEqual(known_commands, {'disabled': 'pkg.disabled', 'enabled': 'pkg.enabled'})

    def test_extension_internal_api_routes_and_ready_checks_include_extension_identity(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifests_dir = self._write_contract_manifest(
                base,
                'ext_enabled',
                {
                    'internalApiRoutes': [
                        {'id': 'route_a', 'path': '/v1/a', 'module': 'pkg.route', 'callable': 'render', 'authRequired': False},
                    ],
                    'readyChecks': [
                        {'id': 'ready_a', 'module': 'pkg.ready', 'callable': 'probe', 'blocking': False},
                    ],
                },
            )
            service_path = self._write_service_config(base, [manifests_dir], ['ext_enabled'])

            routes = extension_internal_api_routes(service_path)
            ready_checks = extension_ready_checks(service_path)

        self.assertEqual(
            routes,
            [{
                'id': 'route_a',
                'path': '/v1/a',
                'module': 'pkg.route',
                'callable': 'render',
                'authRequired': False,
                'extensionId': 'ext_enabled',
            }],
        )
        self.assertEqual(
            ready_checks,
            [{
                'id': 'ready_a',
                'module': 'pkg.ready',
                'callable': 'probe',
                'blocking': False,
                'extensionId': 'ext_enabled',
            }],
        )

    def test_discover_known_extension_ids_reads_explicit_index_rows(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'python' / 'openclaw').mkdir(parents=True)
            (root / 'config' / 'runtime').mkdir(parents=True)
            (root / 'config' / 'control_plane').mkdir(parents=True)
            (root / 'agent' / 'extensions').mkdir(parents=True)
            (root / 'config' / 'runtime' / 'paths.json').write_text('{}', encoding='utf-8')
            self._write_json(root / 'config' / 'control_plane' / 'service.json', {})
            manifests_dir = root / 'config' / 'control_plane' / 'fixtures' / 'extensions.d'
            manifests_dir.mkdir(parents=True)
            self._write_manifest(manifests_dir, 'ext_alpha', {'title': 'Alpha'})
            self._write_manifest(manifests_dir, 'ext_beta', {'title': 'Beta'})
            self._write_json(
                root / 'agent' / 'extensions' / 'index.json',
                {
                    'extensions': [
                        {
                            'id': 'ext_managed',
                            'title': 'Managed Extension',
                            'rootDir': 'agent/extensions/ext_managed',
                            'defaultServiceConfigPath': 'agent/extensions/ext_managed/config/control_plane/profiles/ext_managed.service.json',
                            'manifestDir': 'agent/extensions/ext_managed/config/control_plane/extensions.d',
                            'pythonRoots': ['agent/extensions/ext_managed/python'],
                            'status': 'managed_explicit_extension',
                        }
                    ]
                },
            )

            discovered = discover_known_extension_ids(root / 'python' / 'openclaw')

        self.assertEqual(discovered, {'ext_managed'})

    def test_discover_known_extension_ids_rejects_malformed_managed_index(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'python' / 'openclaw').mkdir(parents=True)
            index_path = root / 'agent' / 'extensions' / 'index.json'
            index_path.parent.mkdir(parents=True)
            index_path.write_text('[]\n', encoding='utf-8')

            with self.assertRaisesRegex(ManagedExtensionError, 'root must be an object'):
                discover_known_extension_ids(root / 'python' / 'openclaw')


if __name__ == '__main__':
    unittest.main()
