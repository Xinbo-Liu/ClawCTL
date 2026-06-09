from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openclaw.control_plane.dispatch import dispatch_runtime_audit
from openclaw.lib.dispatch import operations_surface
from openclaw.setup.deploy_env.dispatch_registry import load as deploy_dispatch_load


TEST_ROTATION_BATCH_ID = 'batch_default'


def _provider_registry_payload() -> dict[str, object]:
    return {
        'version': 1,
        'adapters': [{
            'id': 'alpha',
            'title': 'Alpha',
            'description': 'Alpha adapter',
            'transport': 'webhook',
            'module': 'pkg.alpha',
            'endpointValidator': 'validate',
            'payloadBuilder': 'build',
            'responseEvaluator': 'eval',
        }],
    }


def _write_provider_module(base: Path, module_name: str = 'alpha') -> str:
    package_dir = base / 'pkg'
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / '__init__.py').write_text('', encoding='utf-8')
    (package_dir / f'{module_name}.py').write_text(
        '\n'.join([
            'def validate(*_args, **_kwargs):',
            '    return True',
            '',
            'def build(*_args, **_kwargs):',
            '    return {}',
            '',
            'def eval(*_args, **_kwargs):',
            '    return {"ok": True}',
            '',
        ]),
        encoding='utf-8',
    )
    return f'pkg.{module_name}'


def _dispatch_defaults() -> dict[str, object]:
    return {
        'dedupeWindowHours': 12,
        'maxAttempts': 3,
        'backoffSeconds': [30, 120],
        'targetMinIntervalMs': 0,
        'targetMaxPerSecond': 5,
        'targetMaxPerMinute': 60,
        'targetRateLimitStateTtlSeconds': 3600,
    }


def _release_policies() -> list[dict[str, object]]:
    return [{
        'id': 'review_only',
        'title': 'Review only',
        'description': 'Review-only release policy',
        'allowedReleaseLevels': ['review'],
    }]


def _lifecycle_states() -> list[dict[str, object]]:
    return [{
        'id': 'active',
        'title': 'Active',
        'description': 'Active target',
        'enableAllowed': True,
        'decommissioned': False,
    }]


def _verification_batch() -> dict[str, object]:
    return {
        'id': TEST_ROTATION_BATCH_ID,
        'title': 'Default rotation',
        'description': 'Default rotation batch',
        'requiredForRelease': False,
        'requiredTargetGroups': ['test', 'ops'],
        'targetIds': ['target_alpha', 'target_beta'],
    }


def _target_payload(
    *,
    target_id: str,
    target_group: str,
    verification_order: int,
    env_prefix: str,
    title: str,
) -> dict[str, object]:
    if target_group == 'ops':
        delivery_tier = 'technical'
        message_profile = 'ops_detail'
        boundary = {
            'dispatchLane': 'operations_monitoring',
            'payloadScope': 'ops_summary',
            'publishLatestDefault': False,
            'description': 'Ops monitoring target',
        }
    else:
        delivery_tier = 'validation'
        message_profile = 'test_detail'
        boundary = {
            'dispatchLane': 'integration_validation',
            'payloadScope': 'validation_digest',
            'publishLatestDefault': False,
            'description': 'Validation target',
        }
    return {
        'id': target_id,
        'transport': 'webhook',
        'provider': 'alpha',
        'targetGroup': target_group,
        'deliveryTier': delivery_tier,
        'messageProfile': message_profile,
        'enabledDefault': False,
        'silenceEnabledDefault': False,
        'silenceMinDeltaDefault': 0.0,
        'secretRequiredDefault': True,
        'endpointIsolationDefault': True,
        'atAllDefault': False,
        'formatDefault': 'text',
        'verificationOrderDefault': verification_order,
        'owner': {'team': 'ops', 'primary': 'owner-a', 'backup': 'owner-b'},
        'releasePolicyId': 'review_only',
        'lifecycleState': 'active',
        'verificationBatchIds': [TEST_ROTATION_BATCH_ID],
        'rotationClass': 'default',
        'allowedReleaseLevelsDefault': ['review'],
        'enabledEnv': f'{env_prefix}_ENABLED',
        'endpointEnv': f'{env_prefix}_ENDPOINT',
        'secretEnv': f'{env_prefix}_SECRET',
        'titleEnv': f'{env_prefix}_TITLE',
        'atAllEnv': f'{env_prefix}_AT_ALL',
        'formatEnv': f'{env_prefix}_FORMAT',
        'silenceEnabledEnv': f'{env_prefix}_SILENCE_ENABLED',
        'silenceMinDeltaEnv': f'{env_prefix}_SILENCE_MIN_DELTA',
        'allowedReleaseLevelsEnv': f'{env_prefix}_ALLOWED_LEVELS',
        'titleDefault': title,
        'boundary': boundary,
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _registry_fixture(base: Path) -> tuple[Path, Path, Path]:
    providers = base / 'providers.json'
    registry_a = base / 'dispatch_a.json'
    registry_b = base / 'dispatch_b.json'
    payload = _provider_registry_payload()
    payload['adapters'][0]['module'] = _write_provider_module(base)
    _write_json(providers, payload)
    _write_json(registry_a, {
        'version': 7,
        'defaults': _dispatch_defaults(),
        'releasePolicies': _release_policies(),
        'lifecycleStates': _lifecycle_states(),
        'verificationBatches': {
            'defaultRotationBatchId': TEST_ROTATION_BATCH_ID,
            'batches': [_verification_batch()],
        },
        'targets': [
            _target_payload(
                target_id='target_alpha',
                target_group='test',
                verification_order=1,
                env_prefix='ALPHA',
                title='Alpha',
            ),
        ],
    })
    _write_json(registry_b, {
        'version': 7,
        'defaults': _dispatch_defaults(),
        'releasePolicies': _release_policies(),
        'lifecycleStates': _lifecycle_states(),
        'verificationBatches': {
            'defaultRotationBatchId': TEST_ROTATION_BATCH_ID,
            'batches': [_verification_batch()],
        },
        'targets': [
            _target_payload(
                target_id='target_beta',
                target_group='ops',
                verification_order=2,
                env_prefix='BETA',
                title='Beta',
            ),
        ],
    })
    return providers, registry_a, registry_b


def _registry_index_payload(providers: Path, registry_a: Path, registry_b: Path) -> dict[str, object]:
    return {
        'registryPaths': {
            'dispatchTargetRegistryPaths': [str(registry_a.resolve()), str(registry_b.resolve())],
            'dispatchProviderRegistryPaths': [str(providers.resolve())],
        },
        'extensions': [
            {
                'id': 'ext_alpha',
                'registry': {
                    'dispatchTargetRegistryPaths': [str(registry_a.resolve())],
                },
            },
            {
                'id': 'ext_beta',
                'registry': {
                    'dispatchTargetRegistryPaths': [str(registry_b.resolve())],
                },
            },
        ],
    }


class DispatchMultiRegistryConsumersTest(unittest.TestCase):
    def test_collect_targets_accepts_merged_dispatch_registries(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            sys.path.insert(0, str(base))
            providers, registry_a, registry_b = _registry_fixture(base)
            env_file = base / 'deploy.env'
            env_file.write_text('\n'.join([
                'ALPHA_ENABLED=true',
                'ALPHA_ENDPOINT=https://alpha.example.test/hook',
                'ALPHA_SECRET=alpha-secret',
                'BETA_ENABLED=true',
                'BETA_ENDPOINT=https://beta.example.test/hook',
                'BETA_SECRET=beta-secret',
            ]) + '\n', encoding='utf-8')
            opts = {
                'gate_env_file': str(env_file),
                'batch': TEST_ROTATION_BATCH_ID,
                'config_path': '',
            }
            try:
                with patch.object(operations_surface, 'load_registry', return_value=_registry_index_payload(providers, registry_a, registry_b)):
                    collected = operations_surface._collect_targets(opts)
            finally:
                sys.path.remove(str(base))
                sys.modules.pop('pkg.alpha', None)
                sys.modules.pop('pkg', None)

        self.assertEqual(collected, 'target_alpha,target_beta')

    def test_target_acceptance_payload_derives_extension_from_registry_owner(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            sys.path.insert(0, str(base))
            providers, registry_a, registry_b = _registry_fixture(base)
            service_path = base / 'service.json'
            service_path.write_text('{}\n', encoding='utf-8')
            env = {
                'ALPHA_ENABLED': 'true',
                'ALPHA_ENDPOINT': 'https://alpha.example.test/hook',
                'ALPHA_SECRET': 'alpha-secret',
                'BETA_ENABLED': 'true',
                'BETA_ENDPOINT': 'https://beta.example.test/hook',
                'BETA_SECRET': 'beta-secret',
            }
            try:
                with patch.object(dispatch_runtime_audit, 'load_registry', return_value=_registry_index_payload(providers, registry_a, registry_b)):
                    with patch.dict(os.environ, env, clear=False):
                        payload = dispatch_runtime_audit.target_acceptance_payload('target_beta', config_path=service_path)
            finally:
                sys.path.remove(str(base))
                sys.modules.pop('pkg.alpha', None)
                sys.modules.pop('pkg', None)

        self.assertEqual(payload['extensionId'], 'ext_beta')
        self.assertEqual(payload['target']['source_registry_path'], str(registry_b.resolve()))

    def test_load_dispatch_targets_merges_multiple_paths(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            sys.path.insert(0, str(base))
            providers, registry_a, registry_b = _registry_fixture(base)
            service_path = base / 'service.json'
            service_path.write_text('{}\n', encoding='utf-8')
            try:
                with patch.object(deploy_dispatch_load, 'require_runtime_dependencies', return_value=None):
                    with patch.object(deploy_dispatch_load, 'load_registry', return_value=_registry_index_payload(providers, registry_a, registry_b)):
                        payload = deploy_dispatch_load.load_dispatch_targets(service_path)
                        primary_path = deploy_dispatch_load.resolve_dispatch_targets_path(service_path)
            finally:
                sys.path.remove(str(base))
                sys.modules.pop('pkg.alpha', None)
                sys.modules.pop('pkg', None)

        self.assertEqual(sorted(row['id'] for row in payload['targets']), ['target_alpha', 'target_beta'])
        self.assertEqual(primary_path, registry_a.resolve())


if __name__ == '__main__':
    unittest.main()
