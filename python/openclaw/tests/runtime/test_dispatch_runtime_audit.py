from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from openclaw.control_plane.dispatch import dispatch_runtime_audit


TEST_ROTATION_BATCH_ID = 'batch_default'


def _target(**kwargs):
    defaults = {
        'target_id': 'target_alpha',
        'transport': 'webhook',
        'provider': 'alpha',
        'target_group': 'ops',
        'delivery_tier': 'validation',
        'message_profile': 'default',
        'enabled': True,
        'enabled_default': True,
        'configured': True,
        'endpoint_url': 'https://alpha.example.test/hook',
        'endpoint_present': True,
        'secret': 'secret',
        'secret_required': True,
        'secret_present': True,
        'title': 'Alpha',
        'msg_format': 'text',
        'at_all': False,
        'silence_enabled': False,
        'silence_min_delta': 0.0,
        'allowed_release_levels': ['review'],
        'max_attempts': 3,
        'dedupe_window_hours': 12,
        'backoff_seconds': [30, 120],
        'env': {},
        'source_registry_path': '',
        'extension_id': 'ext_alpha',
        'display_name': 'Alpha target',
        'role_description': 'Audit target',
        'audience_description': 'Audit audience',
        'dispatch_lane': 'operations_monitoring',
        'payload_scope': 'ops_summary',
        'publish_latest': False,
        'boundary_description': 'Audit boundary',
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class DispatchRuntimeAuditSurfaceTest(unittest.TestCase):
    def _context(self) -> dispatch_runtime_audit.DispatchAuditContext:
        target_alpha = _target(target_id='target_alpha', target_group='ops', extension_id='ext_alpha')
        target_beta = _target(
            target_id='target_beta',
            target_group='test',
            delivery_tier='validation',
            message_profile='test_detail',
            enabled=False,
            secret_present=False,
            endpoint_present=False,
            endpoint_url='',
            extension_id='ext_beta',
            display_name='Beta target',
            role_description='Validation target',
            audience_description='Validation audience',
            dispatch_lane='integration_validation',
            payload_scope='validation_digest',
            publish_latest=False,
        )
        return dispatch_runtime_audit.DispatchAuditContext(
            config_path=Path('service.json'),
            target_registry_paths=(),
            target_rows_by_id={
                'target_alpha': {
                    'id': 'target_alpha',
                    'targetGroup': 'ops',
                    'deliveryTier': 'technical',
                    'messageProfile': 'ops_detail',
                    'boundary': {
                        'dispatchLane': 'operations_monitoring',
                        'payloadScope': 'ops_summary',
                        'publishLatestDefault': False,
                        'description': 'Audit boundary',
                    },
                    'verificationOrderDefault': 2,
                    'verificationBatchIds': [TEST_ROTATION_BATCH_ID],
                    'releasePolicyId': 'review_only',
                    'lifecycleState': 'active',
                    'sourceRegistryPath': 'registry_a.json',
                    'extensionId': 'ext_alpha',
                },
                'target_beta': {
                    'id': 'target_beta',
                    'targetGroup': 'test',
                    'deliveryTier': 'validation',
                    'messageProfile': 'test_detail',
                    'boundary': {
                        'dispatchLane': 'integration_validation',
                        'payloadScope': 'validation_digest',
                        'publishLatestDefault': False,
                        'description': 'Audit boundary',
                    },
                    'verificationOrderDefault': 1,
                    'verificationBatchIds': [TEST_ROTATION_BATCH_ID],
                    'releasePolicyId': 'review_only',
                    'lifecycleState': 'active',
                    'sourceRegistryPath': 'registry_b.json',
                    'extensionId': 'ext_beta',
                },
            },
            targets_by_id={
                'target_alpha': target_alpha,
                'target_beta': target_beta,
            },
            policies_by_id={
                'target_alpha': {
                    'blocking_issues': [],
                    'security_warnings': [],
                    'endpoint_validation': {'ok': True},
                },
                'target_beta': {
                    'blocking_issues': [],
                    'security_warnings': ['missing_secret'],
                    'endpoint_validation': {'ok': False},
                },
            },
            registry_payload={
                'verificationBatches': {
                    'defaultRotationBatchId': TEST_ROTATION_BATCH_ID,
                    'batches': [
                        {
                            'id': TEST_ROTATION_BATCH_ID,
                            'requiredTargetGroups': ['ops', 'test'],
                            'targetIds': ['target_alpha', 'target_beta'],
                        }
                    ],
                }
            },
        )

    def test_batch_rotation_health_payloads_remain_available_from_public_surface(self) -> None:
        with patch.object(dispatch_runtime_audit, 'load_context', return_value=self._context()):
            batch = dispatch_runtime_audit.batch_acceptance_payload()
            rotation = dispatch_runtime_audit.rotation_sequence_payload()
            health = dispatch_runtime_audit.health_overview_payload()

        self.assertEqual(batch['batch_id'], TEST_ROTATION_BATCH_ID)
        self.assertEqual(batch['overall_status'], 'warn')
        self.assertEqual(rotation['target_ids'], ['target_beta', 'target_alpha'])
        self.assertEqual(health['overall_status'], 'pass')
        self.assertIn('target_count: 2', dispatch_runtime_audit.render_text(batch))
        self.assertEqual(dispatch_runtime_audit._exit_code_from_status('warn', fail_on_warn=True, fail_on_fail=False), 1)

    def test_target_acceptance_audit_writer_stays_available(self) -> None:
        payload = {
            'kind': 'dispatch_target_acceptance',
            'target_id': 'target_alpha',
            'extensionId': 'ext_alpha',
            'status': 'pass',
        }
        with TemporaryDirectory() as tmp:
            audit_dir = Path(tmp) / 'audits'
            with patch('openclaw.control_plane.dispatch.audit.writers.get_entry', return_value={'resolved_path': str(audit_dir)}):
                path = dispatch_runtime_audit.maybe_write_target_acceptance_audit(
                    payload,
                    config_path=Path(tmp) / 'service.json',
                )

            saved = json.loads(path.read_text(encoding='utf-8'))

        self.assertTrue(path.name.startswith('target_target_alpha.'))
        self.assertEqual(saved['target_id'], 'target_alpha')


if __name__ == '__main__':
    unittest.main()
