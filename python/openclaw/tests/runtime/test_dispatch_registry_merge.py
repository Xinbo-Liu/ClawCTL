from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from openclaw.lib.channels.provider_registry import load_channel_provider_registry
from openclaw.lib.dispatch.target_registry import load_dispatch_registry


TEST_ROTATION_BATCH_ID = 'batch_default'


class DispatchRegistryMergeContractTest(unittest.TestCase):
    def _write_provider_module(self, base: Path, module_name: str) -> str:
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

    def test_provider_registry_merge_accepts_disjoint_adapters(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = base / 'provider_a.json'
            b = base / 'provider_b.json'
            sys.path.insert(0, str(base))
            a.write_text(json.dumps({
                'version': 1,
                'adapters': [{
                    'id': 'alpha',
                    'title': 'Alpha',
                    'description': 'Alpha adapter',
                    'transport': 'webhook',
                    'module': self._write_provider_module(base, 'alpha'),
                    'endpointValidator': 'validate',
                    'payloadBuilder': 'build',
                    'responseEvaluator': 'eval',
                }],
            }), encoding='utf-8')
            b.write_text(json.dumps({
                'version': 1,
                'adapters': [{
                    'id': 'beta',
                    'title': 'Beta',
                    'description': 'Beta adapter',
                    'transport': 'webhook',
                    'module': self._write_provider_module(base, 'beta'),
                    'endpointValidator': 'validate',
                    'payloadBuilder': 'build',
                    'responseEvaluator': 'eval',
                }],
            }), encoding='utf-8')
            try:
                payload = load_channel_provider_registry([a, b])
            finally:
                sys.path.remove(str(base))
                sys.modules.pop('pkg.alpha', None)
                sys.modules.pop('pkg.beta', None)
                sys.modules.pop('pkg', None)
        self.assertEqual(sorted((row['id'] for row in payload['adapters'])), ['alpha', 'beta'])

    def test_dispatch_target_registry_merge_accepts_disjoint_targets(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            providers = base / 'providers.json'
            sys.path.insert(0, str(base))
            providers.write_text(json.dumps({
                'version': 1,
                'adapters': [{
                    'id': 'alpha',
                    'title': 'Alpha',
                    'description': 'Alpha adapter',
                    'transport': 'webhook',
                    'module': self._write_provider_module(base, 'alpha'),
                    'endpointValidator': 'validate',
                    'payloadBuilder': 'build',
                    'responseEvaluator': 'eval',
                }],
            }), encoding='utf-8')

            shared_defaults = {
                'dedupeWindowHours': 12,
                'maxAttempts': 3,
                'backoffSeconds': [30, 120],
                'targetMinIntervalMs': 0,
                'targetMaxPerSecond': 5,
                'targetMaxPerMinute': 60,
                'targetRateLimitStateTtlSeconds': 3600,
            }
            release_policies = [{
                'id': 'review_only',
                'title': 'Review only',
                'description': 'Review-only release policy',
                'allowedReleaseLevels': ['review'],
            }]
            lifecycle_states = [{
                'id': 'active',
                'title': 'Active',
                'description': 'Active target',
                'enableAllowed': True,
                'decommissioned': False,
            }]
            shared_batch = {
                'id': TEST_ROTATION_BATCH_ID,
                'title': 'Default rotation',
                'description': 'Default rotation batch',
                'requiredForRelease': False,
                'requiredTargetGroups': ['test', 'ops'],
                'targetIds': ['target_alpha', 'target_beta'],
            }
            target_a = {
                'id': 'target_alpha',
                'transport': 'webhook',
                'provider': 'alpha',
                'targetGroup': 'test',
                'deliveryTier': 'validation',
                'messageProfile': 'test_detail',
                'enabledDefault': True,
                'silenceEnabledDefault': False,
                'silenceMinDeltaDefault': 0.0,
                'secretRequiredDefault': True,
                'endpointIsolationDefault': True,
                'atAllDefault': False,
                'formatDefault': 'text',
                'verificationOrderDefault': 1,
                'owner': {'team': 'ops', 'primary': 'owner-a', 'backup': 'owner-b'},
                'releasePolicyId': 'review_only',
                'lifecycleState': 'active',
                'verificationBatchIds': [TEST_ROTATION_BATCH_ID],
                'rotationClass': 'default',
                'allowedReleaseLevelsDefault': ['review'],
                'enabledEnv': 'ALPHA_ENABLED',
                'endpointEnv': 'ALPHA_ENDPOINT',
                'secretEnv': 'ALPHA_SECRET',
                'titleEnv': 'ALPHA_TITLE',
                'atAllEnv': 'ALPHA_AT_ALL',
                'formatEnv': 'ALPHA_FORMAT',
                'silenceEnabledEnv': 'ALPHA_SILENCE_ENABLED',
                'silenceMinDeltaEnv': 'ALPHA_SILENCE_MIN_DELTA',
                'allowedReleaseLevelsEnv': 'ALPHA_ALLOWED_LEVELS',
                'titleDefault': 'Alpha',
                'boundary': {
                    'dispatchLane': 'integration_validation',
                    'payloadScope': 'validation_digest',
                    'publishLatestDefault': False,
                    'description': 'Validation target',
                },
            }
            target_b = dict(
                target_a,
                id='target_beta',
                targetGroup='ops',
                deliveryTier='technical',
                messageProfile='ops_detail',
                verificationOrderDefault=2,
                enabledEnv='BETA_ENABLED',
                endpointEnv='BETA_ENDPOINT',
                secretEnv='BETA_SECRET',
                titleEnv='BETA_TITLE',
                atAllEnv='BETA_AT_ALL',
                formatEnv='BETA_FORMAT',
                silenceEnabledEnv='BETA_SILENCE_ENABLED',
                silenceMinDeltaEnv='BETA_SILENCE_MIN_DELTA',
                allowedReleaseLevelsEnv='BETA_ALLOWED_LEVELS',
                titleDefault='Beta',
                boundary={
                    'dispatchLane': 'operations_monitoring',
                    'payloadScope': 'ops_summary',
                    'publishLatestDefault': False,
                    'description': 'Ops monitoring target',
                },
            )

            registry_a = base / 'dispatch_a.json'
            registry_b = base / 'dispatch_b.json'
            registry_a.write_text(json.dumps({
                'version': 7,
                'defaults': shared_defaults,
                'releasePolicies': release_policies,
                'lifecycleStates': lifecycle_states,
                'verificationBatches': {'defaultRotationBatchId': TEST_ROTATION_BATCH_ID, 'batches': [shared_batch]},
                'targets': [target_a],
            }), encoding='utf-8')
            registry_b.write_text(json.dumps({
                'version': 7,
                'defaults': shared_defaults,
                'releasePolicies': release_policies,
                'lifecycleStates': lifecycle_states,
                'verificationBatches': {'defaultRotationBatchId': TEST_ROTATION_BATCH_ID, 'batches': [shared_batch]},
                'targets': [target_b],
            }), encoding='utf-8')

            try:
                payload = load_dispatch_registry([registry_a, registry_b], provider_registry_path=[providers])
            finally:
                sys.path.remove(str(base))
                sys.modules.pop('pkg.alpha', None)
                sys.modules.pop('pkg', None)

        self.assertEqual(sorted((row['id'] for row in payload['targets'])), ['target_alpha', 'target_beta'])
        source_paths = {row['id']: Path(str(row.get('sourceRegistryPath') or '')).name for row in payload['targets']}
        self.assertEqual(source_paths, {
            'target_alpha': 'dispatch_a.json',
            'target_beta': 'dispatch_b.json',
        })



if __name__ == '__main__':
    unittest.main()
