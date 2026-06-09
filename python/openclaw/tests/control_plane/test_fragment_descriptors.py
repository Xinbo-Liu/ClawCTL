from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openclaw.control_plane.agent.cli_surface import load_agent_cli_surface
from openclaw.control_plane.extensions.normalization import ExtensionError
from openclaw.control_plane.governance_surfaces import (
    load_diagnostic_surface,
    load_dispatch_operations_surface,
    load_docs_registry,
    load_full_test_group_registry,
)
from openclaw.control_plane.surfaces import load_runtime_paths_manifest, load_testing_manifest
from openclaw.control_plane.registry_loader import load_registry_from_context
from openclaw.control_plane.registry_loader.config import load_registry_service_context
from openclaw.setup.deploy_env.support import load_schema
from openclaw.doctor.agent_modules.managed_probe_fixture import (
    PROBE_CHECK_ID,
    PROBE_DIAGNOSTIC_ACTION,
    PROBE_EXTENSION_ID,
    PROBE_GROUP_REF,
    PROBE_JOB_REF,
    PROBE_MODEL_REF,
    PROBE_PRIMARY_MODULE_REF,
    PROBE_RUNTIME_ENTRY_ID,
    PROBE_TARGET_REF,
    PROBE_TEST_GROUP_ID,
)
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.tests.support.managed_probe import managed_probe_repo


ROOT_DIR = resolve_repo_root(Path(__file__))
SHARED_MODEL_EXTENSION_IDS = ('ext_shared_model_a', 'ext_shared_model_b')


class FragmentDescriptorIntegrationTest(unittest.TestCase):
    maxDiff = None

    def _write_json(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    def _prepare_repo_root(self, root: Path) -> None:
        (root / 'python' / 'openclaw').mkdir(parents=True, exist_ok=True)
        self._write_json(root / 'config' / 'runtime' / 'paths.json', {'entries': {}})
        if not (root / 'config' / 'control_plane' / 'service.json').exists():
            self._write_json(root / 'config' / 'control_plane' / 'service.json', {})
        platform_manifest_path = root / 'config' / 'control_plane' / 'extensions.d' / 'agent_platform.json'
        if not platform_manifest_path.exists():
            self._write_json(
                platform_manifest_path,
                {
                    'id': 'agent_platform',
                    'title': 'Agent Platform',
                },
            )

    def _write_shared_model_owner_set(self, root: Path, extension_ids: tuple[str, str]) -> None:
        self._write_json(
            root / 'config' / 'control_plane' / 'repo_combination_profiles.json',
            {
                'profiles': [
                    {
                        'id': 'synthetic_shared_model',
                        'configPath': 'config/control_plane/profiles/agent_platform.service.json',
                        'enabledExtensionIds': list(extension_ids),
                        'sharedDeployEnvFields': [
                            {
                                'keys': ['OLLAMA_BASE_URL'],
                                'extensionIds': list(extension_ids),
                            }
                        ],
                    }
                ]
            },
        )

    def _extension_root(self, root: Path, extension_id: str) -> Path:
        return root / 'agent' / 'extensions' / extension_id

    def _manifest_dir(self, root: Path, extension_id: str) -> Path:
        return self._extension_root(root, extension_id) / 'config' / 'control_plane' / 'extensions.d'

    def _write_manifest(self, root: Path, extension_id: str, payload: dict[str, object]) -> Path:
        manifests_dir = self._manifest_dir(root, extension_id)
        manifest = dict(payload)
        manifest.setdefault('id', extension_id)
        manifest.setdefault('title', extension_id)
        self._write_json(manifests_dir / f'{extension_id}.json', manifest)
        return manifests_dir

    def _write_service(self, root: Path, manifests_dirs: list[Path], enabled_ids: list[str]) -> Path:
        self._prepare_repo_root(root)
        service_path = root / 'service.json'
        self._write_json(
            service_path,
            {
                'extends': '@repo/config/control_plane/service.json',
                'extensions': {
                    'manifestsDirs': [
                        str(root / 'config' / 'control_plane' / 'extensions.d'),
                        *[str(path) for path in manifests_dirs],
                    ],
                    'enabledExtensionIds': ['agent_platform', *enabled_ids],
                },
            },
        )
        return service_path

    def test_managed_probe_fragments_merge_with_ownership(self) -> None:
        with managed_probe_repo('fragment-descriptors-probe') as fixture:
            context = load_registry_service_context(fixture.service_path)
            extensions = context['extensions']
            runtime_paths = load_runtime_paths_manifest(config_path=fixture.service_path, extensions=extensions)
            testing_manifest = load_testing_manifest(config_path=fixture.service_path, extensions=extensions)
            diagnostic_surface = load_diagnostic_surface(config_path=fixture.service_path, extensions=extensions)
            registry = load_registry_from_context(context)

        self.assertEqual(runtime_paths['entries'][PROBE_RUNTIME_ENTRY_ID]['extensionId'], PROBE_EXTENSION_ID)
        self.assertEqual(runtime_paths['logical_groups']['probe_outputs']['extensionId'], PROBE_EXTENSION_ID)
        probe_entry = runtime_paths['entries'][PROBE_RUNTIME_ENTRY_ID]
        self.assertIn('scheduler', probe_entry.get('paths') or {})
        self.assertIn('scheduler', probe_entry.get('env_names') or {})

        owned_groups = {
            str(row.get('id') or '').strip()
            for row in testing_manifest.get('groups') or []
            if isinstance(row, dict) and row.get('extensionId') == PROBE_EXTENSION_ID
        }
        self.assertIn(PROBE_TEST_GROUP_ID, owned_groups)
        owned_checks = {
            str(row.get('id') or '').strip()
            for row in testing_manifest.get('checks') or []
            if isinstance(row, dict) and row.get('extensionId') == PROBE_EXTENSION_ID
        }
        self.assertIn(PROBE_CHECK_ID, owned_checks)
        owned_release_gate_checks = {
            str(row.get('id') or '').strip()
            for row in testing_manifest.get('release_gate_checks') or []
            if isinstance(row, dict) and row.get('extensionId') == PROBE_EXTENSION_ID
        }
        self.assertIn('agent_module_smoke_tests_agent_probe', owned_release_gate_checks)

        owned_actions = {
            str(row.get('action') or '').strip()
            for row in diagnostic_surface.get('actions', {}).get('actions') or []
            if isinstance(row, dict) and row.get('extensionId') == PROBE_EXTENSION_ID
        }
        self.assertIn(PROBE_DIAGNOSTIC_ACTION, owned_actions)

        self.assertIn(PROBE_GROUP_REF, registry.get('agentGroupsById') or {})
        self.assertIn(PROBE_JOB_REF, registry.get('jobsById') or {})
        self.assertIn(PROBE_MODEL_REF, registry.get('modelsById') or {})
        self.assertIn(PROBE_TARGET_REF, registry.get('targetsById') or {})
        self.assertIn(PROBE_PRIMARY_MODULE_REF, registry.get('agentModulesById') or {})

    def test_agent_cli_descriptor_merges_generated_artifacts_and_mapping_values(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ext_a_dir = self._extension_root(root, 'ext_a')
            ext_b_dir = self._extension_root(root, 'ext_b')
            ext_a_dir.mkdir(parents=True)
            ext_b_dir.mkdir(parents=True)

            self._write_json(
                root / 'agent_cli_surface.json',
                {
                    'generated_artifacts': {
                        'base_agents_md': 'docs/base-agents.md',
                    },
                    'agents': {
                        'base_agent': {
                            'heading': 'Base agent',
                        }
                    },
                },
            )
            self._write_json(
                ext_a_dir / 'agent_cli_surface.json',
                {
                    'generated_artifacts': {
                        'ext_a_agents_md': 'docs/ext-a-agents.md',
                    },
                    'agents': {
                        'agent_a': {
                            'heading': 'Agent A',
                        }
                    },
                },
            )
            self._write_json(
                ext_b_dir / 'agent_cli_surface.json',
                {
                    'generated_artifacts': {
                        'ext_b_agents_md': 'docs/ext-b-agents.md',
                    },
                    'agents': {
                        'agent_b': {
                            'heading': 'Agent B',
                        }
                    },
                },
            )
            dir_a = self._write_manifest(root, 'ext_a', {'surfaceFragments': {'agentCliSurfacePath': '@extension/agent_cli_surface.json'}})
            dir_b = self._write_manifest(root, 'ext_b', {'surfaceFragments': {'agentCliSurfacePath': '@extension/agent_cli_surface.json'}})
            service_path = self._write_service(root, [dir_a, dir_b], ['ext_a', 'ext_b'])

            payload = load_agent_cli_surface(root / 'agent_cli_surface.json', config_path=service_path)

        self.assertEqual(
            payload['generated_artifacts'],
            {
                'base_agents_md': 'docs/base-agents.md',
                'ext_a_agents_md': 'docs/ext-a-agents.md',
                'ext_b_agents_md': 'docs/ext-b-agents.md',
            },
        )
        self.assertNotIn('extensionId', payload['agents']['base_agent'])
        self.assertEqual(payload['agents']['agent_a']['extensionId'], 'ext_a')
        self.assertEqual(payload['agents']['agent_b']['extensionId'], 'ext_b')

    def test_governance_descriptor_merges_owned_rows_unique_values_and_pages(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ext_a_dir = self._extension_root(root, 'ext_a')
            ext_b_dir = self._extension_root(root, 'ext_b')
            ext_a_dir.mkdir(parents=True)
            ext_b_dir.mkdir(parents=True)

            self._write_json(
                root / 'dispatch_surface.json',
                {
                    'generated_artifacts': {
                        'base_dispatch_doc': 'docs/base-dispatch.md',
                    },
                    'entries': {
                        'base_dispatch': {
                            'title': 'Base dispatch',
                            'steps': ['echo base'],
                        }
                    },
                },
            )
            self._write_json(
                root / 'full_test_groups.json',
                {
                    'generated_artifacts': {
                        'base_full_test_doc': 'docs/base-full-test.md',
                    },
                    'dispatch_recovery_actions': ['retry', 'reset'],
                    'groups': {
                        'base_group': {
                            'title': 'Base group',
                        }
                    },
                },
            )
            self._write_json(
                root / 'docs_registry.json',
                {
                    'checker': {
                        'script': 'scripts/check_docs.py',
                    },
                    'pages': [
                        {
                            'path': 'docs/shared.md',
                            'title': 'Shared doc',
                            'sections': ['base'],
                        }
                    ],
                },
            )
            self._write_json(
                ext_a_dir / 'dispatch_surface.json',
                {
                    'generated_artifacts': {
                        'ext_a_dispatch_doc': 'docs/ext-a-dispatch.md',
                    },
                    'entries': {
                        'dispatch_a': {
                            'title': 'Dispatch A',
                            'steps': ['echo a'],
                        }
                    },
                },
            )
            self._write_json(
                ext_b_dir / 'dispatch_surface.json',
                {
                    'generated_artifacts': {
                        'ext_b_dispatch_doc': 'docs/ext-b-dispatch.md',
                    },
                    'entries': {
                        'dispatch_b': {
                            'title': 'Dispatch B',
                            'steps': ['echo b'],
                        }
                    },
                },
            )
            self._write_json(
                ext_a_dir / 'full_test_groups.json',
                {
                    'generated_artifacts': {
                        'ext_a_full_test_doc': 'docs/ext-a-full-test.md',
                    },
                    'dispatch_recovery_actions': ['reset', 'repair_a'],
                    'groups': {
                        'group_a': {
                            'title': 'Group A',
                        }
                    },
                },
            )
            self._write_json(
                ext_b_dir / 'full_test_groups.json',
                {
                    'generated_artifacts': {
                        'ext_b_full_test_doc': 'docs/ext-b-full-test.md',
                    },
                    'dispatch_recovery_actions': ['repair_b', 'retry'],
                    'groups': {
                        'group_b': {
                            'title': 'Group B',
                        }
                    },
                },
            )
            self._write_json(
                ext_a_dir / 'docs_registry.json',
                {
                    'checker': {
                        'baseline': 'docs/shared.md',
                    },
                    'pages': [
                        {
                            'path': 'docs/shared.md',
                            'title': 'Shared doc',
                            'sections': ['ext_a'],
                        },
                        {
                            'path': 'docs/ext-a.md',
                            'title': 'Ext A doc',
                        },
                    ],
                },
            )
            self._write_json(
                ext_b_dir / 'docs_registry.json',
                {
                    'pages': [
                        {
                            'path': 'docs/ext-b.md',
                            'title': 'Ext B doc',
                        }
                    ],
                },
            )
            dir_a = self._write_manifest(
                root,
                'ext_a',
                {
                    'governanceSurfaces': {
                        'dispatchOperationsSurfacePath': '@extension/dispatch_surface.json',
                        'fullTestGroupRegistryPath': '@extension/full_test_groups.json',
                        'docsRegistryPath': '@extension/docs_registry.json',
                    }
                },
            )
            dir_b = self._write_manifest(
                root,
                'ext_b',
                {
                    'governanceSurfaces': {
                        'dispatchOperationsSurfacePath': '@extension/dispatch_surface.json',
                        'fullTestGroupRegistryPath': '@extension/full_test_groups.json',
                        'docsRegistryPath': '@extension/docs_registry.json',
                    }
                },
            )
            service_path = self._write_service(root, [dir_a, dir_b], ['ext_a', 'ext_b'])

            dispatch_payload = load_dispatch_operations_surface(root / 'dispatch_surface.json', config_path=service_path)
            full_test_payload = load_full_test_group_registry(root / 'full_test_groups.json', config_path=service_path)
            docs_payload = load_docs_registry(root / 'docs_registry.json', config_path=service_path)

        dispatch_entries = {
            str(row.get('id') or '').strip(): row
            for row in dispatch_payload.get('entries') or []
            if isinstance(row, dict)
        }
        self.assertEqual(dispatch_entries['dispatch_a']['extensionId'], 'ext_a')
        self.assertEqual(dispatch_entries['dispatch_b']['extensionId'], 'ext_b')
        self.assertEqual(
            dispatch_payload['generated_artifacts'],
            {
                'base_dispatch_doc': 'docs/base-dispatch.md',
                'ext_a_dispatch_doc': 'docs/ext-a-dispatch.md',
                'ext_b_dispatch_doc': 'docs/ext-b-dispatch.md',
            },
        )

        self.assertEqual(full_test_payload['groups']['group_a']['extensionId'], 'ext_a')
        self.assertEqual(full_test_payload['groups']['group_b']['extensionId'], 'ext_b')
        self.assertEqual(
            full_test_payload['dispatch_recovery_actions'],
            ['retry', 'reset', 'repair_a', 'repair_b'],
        )
        self.assertEqual(
            full_test_payload['generated_artifacts'],
            {
                'base_full_test_doc': 'docs/base-full-test.md',
                'ext_a_full_test_doc': 'docs/ext-a-full-test.md',
                'ext_b_full_test_doc': 'docs/ext-b-full-test.md',
            },
        )

        shared_page = next(
            row for row in docs_payload.get('pages') or []
            if isinstance(row, dict) and row.get('path') == 'docs/shared.md'
        )
        self.assertEqual(shared_page['sections'], ['base', 'ext_a'])
        self.assertEqual(shared_page['extensionId'], 'ext_a')
        self.assertEqual(docs_payload['checker']['baseline'], 'docs/shared.md')
        ext_page_paths = {
            str(row.get('path') or '').strip()
            for row in docs_payload.get('pages') or []
            if isinstance(row, dict) and row.get('extensionId')
        }
        self.assertTrue({'docs/shared.md', 'docs/ext-a.md', 'docs/ext-b.md'}.issubset(ext_page_paths))

    def test_testing_manifest_conflict_still_raises_on_duplicate_group_id(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ext_a_dir = self._extension_root(root, 'ext_a')
            ext_b_dir = self._extension_root(root, 'ext_b')
            ext_a_dir.mkdir(parents=True)
            ext_b_dir.mkdir(parents=True)

            self._write_json(
                root / 'testing_manifest.json',
                {
                    'groups': [
                        {
                            'id': 'base_group',
                        }
                    ],
                    'checks': [],
                    'valid_groups': ['base_group'],
                },
            )
            self._write_json(
                ext_a_dir / 'testing_manifest.json',
                {
                    'groups': [
                        {
                            'id': 'duplicate_group',
                        }
                    ],
                    'checks': [],
                },
            )
            self._write_json(
                ext_b_dir / 'testing_manifest.json',
                {
                    'groups': [
                        {
                            'id': 'duplicate_group',
                        }
                    ],
                    'checks': [],
                },
            )
            dir_a = self._write_manifest(root, 'ext_a', {'surfaceFragments': {'testingManifestPath': '@extension/testing_manifest.json'}})
            dir_b = self._write_manifest(root, 'ext_b', {'surfaceFragments': {'testingManifestPath': '@extension/testing_manifest.json'}})
            service_path = self._write_service(root, [dir_a, dir_b], ['ext_a', 'ext_b'])

            with self.assertRaises((ExtensionError, ValueError)) as ctx:
                load_testing_manifest(root / 'testing_manifest.json', config_path=service_path)

        self.assertIn('duplicate_group', str(ctx.exception))

    def test_deploy_env_shared_ollama_merge_is_limited_to_controlled_extension_pair(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared_field = {
                'key': 'OLLAMA_BASE_URL',
                'group': 'model_providers',
                'required': True,
                'manual_required': True,
                'default_kind': 'placeholder',
                'placeholder': '__REQUIRED__',
                'doc_summary': 'Ollama base URL',
                'doc_location': f'agent/extensions/{SHARED_MODEL_EXTENSION_IDS[0]}/deploy/extension.env',
                'validator': {'type': 'http_url'},
            }
            for extension_id in SHARED_MODEL_EXTENSION_IDS[:2]:
                ext_root = self._extension_root(root, extension_id)
                self._write_json(
                    ext_root / 'deploy_env_schema.json',
                    {
                        'groups': [{'id': 'model_providers', 'title': 'Model providers'}],
                        'fields': [dict(shared_field)],
                    },
                )
            dir_a = self._write_manifest(
                root,
                SHARED_MODEL_EXTENSION_IDS[0],
                {'surfaceFragments': {'deployEnvSchemaPath': '@extension/deploy_env_schema.json'}},
            )
            dir_b = self._write_manifest(
                root,
                SHARED_MODEL_EXTENSION_IDS[1],
                {'surfaceFragments': {'deployEnvSchemaPath': '@extension/deploy_env_schema.json'}},
            )
            self._write_shared_model_owner_set(root, SHARED_MODEL_EXTENSION_IDS)
            service_path = self._write_service(root, [dir_a, dir_b], SHARED_MODEL_EXTENSION_IDS[:2])

            with patch.dict(os.environ, {'OPENCLAW_REPO_ROOT': str(root)}):
                schema = load_schema(config_path=service_path)

        ollama_fields = [
            row for row in schema.get('fields') or []
            if isinstance(row, dict) and row.get('key') == 'OLLAMA_BASE_URL'
        ]
        self.assertEqual(len(ollama_fields), 1)
        self.assertEqual(ollama_fields[0].get('doc_location'), 'deploy/site.env')
        self.assertNotIn('extensionId', ollama_fields[0])

    def test_deploy_env_shared_ollama_merge_rejects_uncontrolled_extension_owner(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared_field = {
                'key': 'OLLAMA_BASE_URL',
                'group': 'model_providers',
                'required': True,
                'manual_required': True,
                'default_kind': 'placeholder',
                'placeholder': '__REQUIRED__',
                'doc_summary': 'Ollama base URL',
                'doc_location': 'agent/extensions/ext/deploy/extension.env',
                'validator': {'type': 'http_url'},
            }
            for extension_id in (SHARED_MODEL_EXTENSION_IDS[0], 'ext_third'):
                ext_root = self._extension_root(root, extension_id)
                self._write_json(
                    ext_root / 'deploy_env_schema.json',
                    {
                        'groups': [{'id': 'model_providers', 'title': 'Model providers'}],
                        'fields': [dict(shared_field)],
                    },
                )
            dir_a = self._write_manifest(
                root,
                SHARED_MODEL_EXTENSION_IDS[0],
                {'surfaceFragments': {'deployEnvSchemaPath': '@extension/deploy_env_schema.json'}},
            )
            dir_b = self._write_manifest(
                root,
                'ext_third',
                {'surfaceFragments': {'deployEnvSchemaPath': '@extension/deploy_env_schema.json'}},
            )
            self._write_shared_model_owner_set(root, SHARED_MODEL_EXTENSION_IDS)
            service_path = self._write_service(root, [dir_a, dir_b], [SHARED_MODEL_EXTENSION_IDS[0], 'ext_third'])

            with (
                patch.dict(os.environ, {'OPENCLAW_REPO_ROOT': str(root)}),
                self.assertRaisesRegex(ValueError, 'shared model owner conflict: model_providers'),
            ):
                load_schema(config_path=service_path)


if __name__ == '__main__':
    unittest.main()
