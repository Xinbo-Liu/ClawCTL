from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from openclaw.control_plane.dispatch import delivery_outcome


class DeliveryOutcomeContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.artifact = self.root / 'runs' / 'business-1' / 'dispatch.primary.json'
        self.artifact.parent.mkdir(parents=True)
        self.artifact.write_text(json.dumps({
            'schemaVersion': 8,
            'runId': 'business-1',
            'schedulerRunId': 'scheduler-1',
            'targetId': 'dispatch_primary',
            'result': {
                'status': 'sent',
                'proofType': 'provider_ack',
                'contentSha256': 'a' * 64,
                'providerAck': {'httpStatus': 200, 'businessCode': 0},
            },
            'source': {'contentSha256': 'a' * 64},
        }) + '\n', encoding='utf-8')
        self.started_at = datetime.now(timezone.utc).isoformat()
        generated_at = datetime.now(timezone.utc).isoformat()
        self.evidence = {
            'artifactId': 'delivery_artifact_root',
            'relativePath': self.artifact.relative_to(self.root).as_posix(),
            'sha256': hashlib.sha256(self.artifact.read_bytes()).hexdigest(),
            'generatedAt': generated_at,
        }
        self.job = {
            'id': 'dispatch_job',
            'qualifiedId': 'agent_probe:dispatch_job',
            'resolvedRuntimeJobKey': 'agent_probe:dispatch_job',
            'artifactPolicy': {'runArtifactRoot': 'delivery_artifact_root'},
            'resolvedOutputs': {
                'statusSignals': [
                    'dispatch_sent',
                    'dispatch_noop',
                    'dispatch_retry_pending',
                    'dispatch_failed',
                    'dispatch_blocked',
                    'dispatch_advisory',
                    'dispatch_manual_verified',
                ]
            },
            'resolvedDeliveryContract': {
                'successStatuses': ['sent', 'noop'],
                'retryableStatuses': ['retry_pending', 'rate_limited'],
                'terminalStatuses': ['failed', 'blocked'],
            },
            'failureClassPolicy': {
                'retryableClasses': ['target_rate_limited', 'transient_delivery'],
            },
        }

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _sent_manifest(self) -> dict[str, object]:
        return {
            'schemaVersion': 1,
            'jobId': 'agent_probe:dispatch_job',
            'schedulerRunId': 'scheduler-1',
            'businessRunId': 'business-1',
            'operation': 'send',
            'status': 'sent',
            'failureClass': None,
            'statusSignals': ['dispatch_sent'],
            'evidence': [dict(self.evidence)],
            'recoveries': [],
            'details': {
                'targetOutcomes': [
                    {
                        'targetId': 'dispatch_primary',
                        'completionRole': 'required',
                        'status': 'sent',
                        'reasonCode': 'provider_acknowledged',
                        'proofType': 'provider_ack',
                        'evidenceRefs': [self.evidence['relativePath']],
                        'providerAck': {'httpStatus': 200, 'businessCode': 0},
                    }
                ]
            },
        }

    def _validate(self, payload: object, *, expected_business_run_id: str | None = None) -> dict[str, object]:
        with mock.patch.object(delivery_outcome, 'resolve_artifact_root', return_value=self.root):
            return delivery_outcome.validate_outcome_manifest(
                payload,
                job=self.job,
                scheduler_run_id='scheduler-1',
                started_at=self.started_at,
                env={},
                expected_business_run_id=expected_business_run_id,
            )

    def test_valid_provider_ack_is_accepted(self) -> None:
        result = self._validate(self._sent_manifest(), expected_business_run_id='business-1')

        self.assertTrue(result['manifestValid'])
        self.assertTrue(result['contractAccepted'])
        self.assertTrue(result['artifactAccepted'])
        self.assertEqual(result['schedulerStatus'], 'succeeded')

    def test_provider_ack_requires_http_2xx_and_business_code_zero(self) -> None:
        for http_status, business_code in ((500, 0), (200, 19001), (204, None)):
            with self.subTest(http_status=http_status, business_code=business_code):
                payload = self._sent_manifest()
                payload['details']['targetOutcomes'][0]['providerAck'] = {
                    'httpStatus': http_status,
                    'businessCode': business_code,
                }
                result = self._validate(payload)
                self.assertFalse(result['manifestValid'])
                self.assertIn('provider_ack_not_accepted', ';'.join(result['reasons']))

    def test_provider_ack_cannot_be_proved_by_a_different_run_artifact(self) -> None:
        artifact_payload = json.loads(self.artifact.read_text(encoding='utf-8'))
        artifact_payload['schedulerRunId'] = 'different-scheduler-run'
        self.artifact.write_text(json.dumps(artifact_payload) + '\n', encoding='utf-8')
        self.evidence['sha256'] = hashlib.sha256(self.artifact.read_bytes()).hexdigest()

        result = self._validate(self._sent_manifest())

        self.assertFalse(result['manifestValid'])
        self.assertIn('provider_ack_evidence_invalid', ';'.join(result['reasons']))

    def test_advisory_failure_does_not_block_required_acceptance(self) -> None:
        payload = self._sent_manifest()
        payload['statusSignals'].append('dispatch_advisory')
        payload['details']['targetOutcomes'].append({
            'targetId': 'dispatch_validation',
            'completionRole': 'advisory',
            'status': 'failed',
            'reasonCode': 'provider_rejected',
            'proofType': 'attempt_record',
            'evidenceRefs': [self.evidence['relativePath']],
            'providerAck': {'httpStatus': 400, 'businessCode': 19001},
        })

        result = self._validate(payload)

        self.assertTrue(result['manifestValid'])
        self.assertTrue(result['contractAccepted'])

    def test_manifest_identity_schema_and_evidence_fail_closed(self) -> None:
        mutations = {
            'job_mismatch': lambda payload: payload.__setitem__('jobId', 'other'),
            'run_mismatch': lambda payload: payload.__setitem__('schedulerRunId', 'other'),
            'business_mismatch': lambda payload: payload.__setitem__('businessRunId', 'other'),
            'unknown_status': lambda payload: payload.__setitem__('status', 'skipped'),
            'unknown_field': lambda payload: payload.__setitem__('endpoint', 'secret'),
            'path_traversal': lambda payload: payload['evidence'][0].__setitem__('relativePath', '../secret'),
            'hash_mismatch': lambda payload: payload['evidence'][0].__setitem__('sha256', '0' * 64),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                payload = self._sent_manifest()
                mutate(payload)
                result = self._validate(payload, expected_business_run_id='business-1')
                self.assertFalse(result['manifestValid'])
                self.assertFalse(result['contractAccepted'])
                self.assertEqual(result['failureClass'], 'target_contract_violation')

    def test_stale_2000_artifact_is_rejected(self) -> None:
        stale_epoch = datetime(2000, 1, 1, tzinfo=timezone.utc).timestamp()
        os.utime(self.artifact, (stale_epoch, stale_epoch))

        result = self._validate(self._sent_manifest())

        self.assertFalse(result['artifactAccepted'])
        self.assertIn('file_stale', ';'.join(result['reasons']))

    def test_exact_prior_sent_proof_may_predate_current_noop_run(self) -> None:
        stale = datetime(2000, 1, 1, tzinfo=timezone.utc)
        stale_epoch = stale.timestamp()
        os.utime(self.artifact, (stale_epoch, stale_epoch))
        self.evidence['generatedAt'] = stale.isoformat()
        payload = self._sent_manifest()
        payload['status'] = 'noop'
        payload['statusSignals'] = ['dispatch_noop']
        payload['details']['targetOutcomes'] = [{
            'targetId': 'dispatch_primary',
            'completionRole': 'required',
            'status': 'noop',
            'reasonCode': 'same_run_already_sent',
            'proofType': 'prior_sent',
            'evidenceRefs': [self.evidence['relativePath']],
            'priorSentProof': {
                'schedulerRunId': 'scheduler-1',
                'businessRunId': 'business-1',
                'targetId': 'dispatch_primary',
                'contentSha256': 'a' * 64,
                'artifactPath': self.evidence['relativePath'],
            },
        }]

        result = self._validate(payload)

        self.assertTrue(result['manifestValid'], result['reasons'])
        self.assertTrue(result['artifactAccepted'])

    def test_operator_attestation_requires_audited_unknown_delivery_origin(self) -> None:
        confirmed_at = datetime.now(timezone.utc).isoformat()
        attestation = {
            'schemaVersion': 1,
            'proofType': 'operator_attestation',
            'businessRunId': 'business-1',
            'schedulerRunId': 'scheduler-1',
            'targetId': 'dispatch_primary',
            'contentSha256': 'a' * 64,
            'operatorId': 'operator-1',
            'operatorReason': 'receiver confirmed the message is visible',
            'confirmedVisible': True,
            'confirmedAt': confirmed_at,
            'verificationOf': {
                'schedulerRunId': 'attempt-unknown-1',
                'artifactPath': 'runs/business-1/dispatch.primary.unknown.json',
                'failureClass': 'delivery_ack_unknown',
            },
            'statusSignals': ['dispatch_manual_verified', 'dispatch_advisory'],
        }

        def validate(current: dict[str, object]) -> dict[str, object]:
            self.artifact.write_text(json.dumps(current) + '\n', encoding='utf-8')
            observed_mtime = datetime.fromtimestamp(self.artifact.stat().st_mtime, timezone.utc).isoformat()
            self.evidence['sha256'] = hashlib.sha256(self.artifact.read_bytes()).hexdigest()
            self.evidence['generatedAt'] = observed_mtime
            payload = self._sent_manifest()
            payload['operation'] = 'operator_verify'
            payload['statusSignals'] = ['dispatch_manual_verified', 'dispatch_advisory']
            payload['details']['targetOutcomes'] = [{
                'targetId': 'dispatch_primary',
                'completionRole': 'required',
                'status': 'sent',
                'reasonCode': 'operator_verified_delivery',
                'proofType': 'operator_attestation',
                'evidenceRefs': [self.evidence['relativePath']],
                'operatorAttestation': {
                    'attemptSchedulerRunId': 'attempt-unknown-1',
                    'contentSha256': 'a' * 64,
                    'artifactPath': self.evidence['relativePath'],
                },
            }]
            return self._validate(payload)

        accepted = validate(attestation)
        self.assertTrue(accepted['manifestValid'], accepted['reasons'])

        mutations = {
            'operator_identity_missing': lambda row: row.__setitem__('operatorId', ''),
            'operator_reason_missing': lambda row: row.__setitem__('operatorReason', ''),
            'origin_failure_class_wrong': lambda row: row['verificationOf'].__setitem__('failureClass', 'transient_delivery'),
            'manual_signal_missing': lambda row: row.__setitem__('statusSignals', ['dispatch_advisory']),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                candidate = json.loads(json.dumps(attestation))
                mutate(candidate)
                rejected = validate(candidate)
                self.assertFalse(rejected['manifestValid'])
                self.assertIn('operator_attestation_evidence_invalid', ';'.join(rejected['reasons']))

    def test_empty_retry_noop_cannot_claim_recovery(self) -> None:
        payload = self._sent_manifest()
        payload.update({
            'operation': 'retry',
            'status': 'noop',
            'statusSignals': ['dispatch_noop'],
            'evidence': [],
            'recoveries': [{
                'ofJobId': 'agent_probe:source_dispatch_job',
                'ofSchedulerRunId': 'failed-run-1',
                'businessRunId': 'business-1',
            }],
            'details': {'targetOutcomes': []},
        })
        self.job['resolvedRecoveryStep'] = {
            'recoveryOfJobRef': 'agent_probe:source_dispatch_job',
        }

        result = self._validate(payload)

        self.assertFalse(result['manifestValid'])
        self.assertIn('recovery_without_accepted_required_targets', result['reasons'])

    def test_recovery_must_reference_configured_origin_job(self) -> None:
        payload = self._sent_manifest()
        payload['operation'] = 'retry'
        payload['recoveries'] = [{
            'ofJobId': 'agent_probe:unrelated_job',
            'ofSchedulerRunId': 'failed-run-1',
            'businessRunId': 'business-1',
        }]
        self.job['resolvedRecoveryStep'] = {
            'recoveryOfJobRef': 'agent_probe:source_dispatch_job',
        }

        result = self._validate(payload)

        self.assertFalse(result['manifestValid'])
        self.assertIn('recoveries[0]_origin_job_mismatch', result['reasons'])

    def test_missing_and_corrupt_outcome_files_fail_closed(self) -> None:
        missing = self.root / 'missing.json'
        result = delivery_outcome.load_and_validate_outcome_manifest(
            missing,
            job=self.job,
            scheduler_run_id='scheduler-1',
            started_at=self.started_at,
        )
        self.assertEqual(result['reasons'], ['outcome_missing'])

        corrupt = self.root / 'corrupt.json'
        corrupt.write_text('{broken', encoding='utf-8')
        result = delivery_outcome.load_and_validate_outcome_manifest(
            corrupt,
            job=self.job,
            scheduler_run_id='scheduler-1',
            started_at=self.started_at,
        )
        self.assertFalse(result['manifestValid'])
        self.assertIn('outcome_unreadable', result['reasons'][0])


if __name__ == '__main__':
    unittest.main()
