from __future__ import annotations

import json
import unittest
from pathlib import Path

from openclaw.control_plane.artifact_policies import build_summary as build_artifact_policy_summary
from openclaw.control_plane.registry.owners import owned_index_bundle, resolve_owned_ref
from openclaw.control_plane.registry.commands import resolve_job_command
from openclaw.control_plane.registry_loader import load_registry_from_path
from openclaw.doctor.agent_modules.managed_probe_fixture import materialize_managed_probe_extension
from openclaw.lib.cli.common import CliError
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.runtime.generated_paths.gateway.config import build_gateway_model_projection
from openclaw.tests.support.helpers import isolated_test_root


ROOT_DIR = resolve_repo_root(Path(__file__))


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _prepare_dual_probe_manifest(fixture, extension_id: str) -> None:
    manifest = json.loads(fixture.manifest_path.read_text(encoding='utf-8'))
    manifest['version'] = '1.0.0'
    manifest['compat'] = {'controlPlane': '>=1.0.0'}
    manifest['dependencies'] = []
    manifest['migrations'] = []
    manifest['governanceSurfaces'] = {}
    _write_json(fixture.manifest_path, manifest)

    testing_manifest = json.loads(fixture.testing_manifest_path.read_text(encoding='utf-8'))
    group_id = f'probe_pipeline_checks_{extension_id}'
    testing_manifest['valid_groups'] = [group_id]
    testing_manifest['execution_order'] = [group_id]
    for group in testing_manifest.get('groups') or []:
        group['id'] = group_id
    for check in testing_manifest.get('checks') or []:
        check['id'] = f"{check['id']}_{extension_id}"
        check['group'] = group_id
    for check in testing_manifest.get('release_gate_checks') or []:
        check['id'] = f"{check['id']}_{extension_id}"
    testing_manifest['acceptance_reference']['required_checks'] = [
        check['id'] for check in testing_manifest.get('checks') or []
    ]
    _write_json(fixture.testing_manifest_path, testing_manifest)
    for job_path in fixture.jobs_dir.glob('*.json'):
        job_payload = json.loads(job_path.read_text(encoding='utf-8'))
        artifact_policy = job_payload.get('artifactPolicy') if isinstance(job_payload.get('artifactPolicy'), dict) else {}
        artifact_policy['runArtifactRoot'] = f'control_plane/{extension_id}/probe_dispatcher'
        job_payload['artifactPolicy'] = artifact_policy
        _write_json(job_path, job_payload)


class RegistryOwnershipTest(unittest.TestCase):
    def test_owner_resolver_prefers_current_owner_then_rejects_global_ambiguity(self) -> None:
        rows = [
            {'id': 'same', 'ownerId': 'alpha', 'qualifiedId': 'alpha:same'},
            {'id': 'same', 'ownerId': 'beta', 'qualifiedId': 'beta:same'},
            {'id': 'unique', 'ownerId': 'beta', 'qualifiedId': 'beta:unique'},
        ]
        bundle = owned_index_bundle(rows, label='fixture')

        self.assertEqual(
            resolve_owned_ref(
                'same',
                by_id=bundle['byId'],
                by_qualified_id=bundle['byQualifiedId'],
                ambiguous_ids=bundle['ambiguousIds'],
                owner_id='alpha',
                label='fixture',
            )['qualifiedId'],
            'alpha:same',
        )
        self.assertEqual(
            resolve_owned_ref(
                'unique',
                by_id=bundle['byId'],
                by_qualified_id=bundle['byQualifiedId'],
                ambiguous_ids=bundle['ambiguousIds'],
                owner_id='alpha',
                label='fixture',
            )['qualifiedId'],
            'beta:unique',
        )
        with self.assertRaises(CliError):
            resolve_owned_ref(
                'same',
                by_id=bundle['byId'],
                by_qualified_id=bundle['byQualifiedId'],
                ambiguous_ids=bundle['ambiguousIds'],
                label='fixture',
            )

    def test_dual_extensions_can_share_core_local_ids(self) -> None:
        with isolated_test_root('registry-owner-dual') as repo_root:
            for extension_id in ('agent_probe_a', 'agent_probe_b'):
                fixture = materialize_managed_probe_extension(
                    repo_root,
                    base_repo_root=ROOT_DIR,
                    extension_id=extension_id,
                    use_snapshot_cache=False,
                )
                _prepare_dual_probe_manifest(fixture, extension_id)
            service_path = repo_root / 'config' / 'control_plane' / 'dual.service.json'
            _write_json(
                service_path,
                {
                    'extends': '@repo/config/control_plane/service.json',
                    'extensions': {
                        'manifestsDirs': [
                            '@repo/config/control_plane/extensions.d',
                            '@repo/agent/extensions/agent_probe_a/config/control_plane/extensions.d',
                            '@repo/agent/extensions/agent_probe_b/config/control_plane/extensions.d',
                        ],
                        'enabledExtensionIds': ['agent_platform', 'agent_probe_a', 'agent_probe_b'],
                    },
                },
            )

            payload = load_registry_from_path(service_path)
            command_a = resolve_job_command(payload, 'agent_probe_a:probe_dispatch_weekday')
            command_b = resolve_job_command(payload, 'agent_probe_b:probe_dispatch_weekday')

        self.assertEqual(payload['jobsAmbiguousIds']['probe_dispatch_weekday'], ['agent_probe_a', 'agent_probe_b'])
        self.assertNotIn('probe_dispatch_weekday', payload['jobsById'])
        self.assertEqual(
            sorted(payload['jobsByQualifiedId']),
            ['agent_probe_a:probe_dispatch_weekday', 'agent_probe_b:probe_dispatch_weekday'],
        )
        self.assertEqual(
            {row['resolvedRuntimeJobKey'] for row in payload['jobs']},
            {'agent_probe_a:probe_dispatch_weekday', 'agent_probe_b:probe_dispatch_weekday'},
        )
        self.assertIn('agent_probe_a:probe_dispatcher', command_a)
        self.assertIn('agent_probe_b:probe_dispatcher', command_b)

    def test_artifact_summary_uses_qualified_runtime_job_key_paths(self) -> None:
        registry = {
            'jobs': [
                {
                    'id': 'probe_dispatch_weekday',
                    'qualifiedId': 'agent_probe_a:probe_dispatch_weekday',
                    'resolvedRuntimeJobKey': 'agent_probe_a:probe_dispatch_weekday',
                    'artifactPolicy': {'runArtifactRoot': 'control_plane/agent_probe_a/probe_dispatcher'},
                },
                {
                    'id': 'probe_dispatch_weekday',
                    'qualifiedId': 'agent_probe_b:probe_dispatch_weekday',
                    'resolvedRuntimeJobKey': 'agent_probe_b:probe_dispatch_weekday',
                    'artifactPolicy': {'runArtifactRoot': 'control_plane/agent_probe_b/probe_dispatcher'},
                },
            ]
        }

        artifact_summary = build_artifact_policy_summary(
            config_path=ROOT_DIR / 'config' / 'control_plane' / 'profiles' / 'agent_platform.service.json',
            base_root=ROOT_DIR,
            registry=registry,
        )
        run_patterns = {
            str(item.get('runtimeJobKey') or ''): str(item.get('schedulerRunDirPattern') or '').replace('\\', '/')
            for item in artifact_summary.get('items') or []
            if isinstance(item, dict)
        }

        self.assertIn('agent_probe_a:probe_dispatch_weekday', run_patterns)
        self.assertIn('agent_probe_b:probe_dispatch_weekday', run_patterns)
        self.assertIn('agent_probe_a_probe_dispatch_weekday', run_patterns['agent_probe_a:probe_dispatch_weekday'])
        self.assertIn('agent_probe_b_probe_dispatch_weekday', run_patterns['agent_probe_b:probe_dispatch_weekday'])

    def test_gateway_model_projection_uses_qualified_model_refs(self) -> None:
        with isolated_test_root('registry-owner-gateway-models') as repo_root:
            deploy_dir = repo_root / 'deploy'
            deploy_dir.mkdir(parents=True, exist_ok=True)
            (deploy_dir / '.env').write_text(
                'OLLAMA_BASE_URL=http://127.0.0.1:11434\n'
                'MODEL_ALPHA=llama-alpha\n'
                'MODEL_BETA=llama-beta\n',
                encoding='utf-8',
            )
            model_alpha = {
                'id': 'default_model',
                'ownerId': 'alpha',
                'qualifiedId': 'alpha:default_model',
                'provider': 'ollama',
                'modelRefEnv': 'MODEL_ALPHA',
                'channel': {'api': 'ollama', 'baseUrlEnv': 'OLLAMA_BASE_URL'},
                'capabilities': {},
                'costPolicy': {},
            }
            model_beta = {
                **model_alpha,
                'ownerId': 'beta',
                'qualifiedId': 'beta:default_model',
                'modelRefEnv': 'MODEL_BETA',
            }
            registry = {
                'models': [model_alpha, model_beta],
                'modelsById': {},
                'modelsByQualifiedId': {
                    'alpha:default_model': model_alpha,
                    'beta:default_model': model_beta,
                },
                'modelsAmbiguousIds': {'default_model': ['alpha', 'beta']},
                'agents': [
                    {
                        'id': 'worker',
                        'ownerId': 'alpha',
                        'qualifiedId': 'alpha:worker',
                        'defaultModelProfileRef': 'default_model',
                        'resolvedDefaultModelProfileRef': 'alpha:default_model',
                    },
                    {
                        'id': 'worker',
                        'ownerId': 'beta',
                        'qualifiedId': 'beta:worker',
                        'defaultModelProfileRef': 'default_model',
                        'resolvedDefaultModelProfileRef': 'beta:default_model',
                    },
                ],
                'jobs': [
                    {
                        'id': 'job',
                        'ownerId': 'alpha',
                        'qualifiedId': 'alpha:job',
                        'agentRef': 'worker',
                        'resolvedAgentQualifiedRef': 'alpha:worker',
                        'modelProfileRef': 'default_model',
                        'resolvedModelProfileRef': 'default_model',
                        'resolvedModelProfileQualifiedRef': 'alpha:default_model',
                    },
                    {
                        'id': 'job',
                        'ownerId': 'beta',
                        'qualifiedId': 'beta:job',
                        'agentRef': 'worker',
                        'resolvedAgentQualifiedRef': 'beta:worker',
                        'modelProfileRef': 'default_model',
                        'resolvedModelProfileRef': 'default_model',
                        'resolvedModelProfileQualifiedRef': 'beta:default_model',
                    },
                ],
            }

            projection = build_gateway_model_projection(registry, repo_root)

        ollama_models = projection['models']['providers']['ollama']['models']
        self.assertEqual(
            sorted(row['id'] for row in ollama_models),
            ['llama-alpha', 'llama-beta'],
        )


if __name__ == '__main__':
    unittest.main()
