from __future__ import annotations

import unittest

from openclaw.control_plane.registry import CliError
from openclaw.control_plane.registry_validation.runtime_policy import _normalize_job_runtime_policy
from openclaw.scheduler import engine


class DeliveryRetryOwnershipTest(unittest.TestCase):
    def _job(self) -> dict[str, object]:
        return {
            'id': 'dispatch_job',
            'artifactPolicy': {'latestAlias': 'latest_dispatch'},
            'failureClassPolicy': {
                'retryableClasses': ['transient_delivery'],
                'terminalClasses': ['delivery_ack_unknown', 'provider_rejected'],
            },
        }

    def _agent(self) -> dict[str, object]:
        return {
            'capabilities': {
                'network': True,
                'externalDispatch': True,
                'filesystemWrite': ['delivery_artifact_root'],
            }
        }

    def test_group_owned_jobs_never_receive_scheduler_auto_retry(self) -> None:
        job = self._job()

        _normalize_job_runtime_policy(
            job,
            agent=self._agent(),
            module={'moduleKind': 'worker'},
            is_recovery_job=False,
            retry_ownership='group_owned',
        )

        self.assertEqual(job['retryOwnership'], 'group_owned')
        self.assertEqual(job['retryPolicy'], {'enabled': False, 'maxAttempts': 0, 'backoffSeconds': []})

    def test_group_owned_explicit_retry_conflict_fails_registry_validation(self) -> None:
        job = self._job()
        job['retryPolicy'] = {'enabled': True, 'maxAttempts': 1, 'backoffSeconds': [60]}

        with self.assertRaises(CliError):
            _normalize_job_runtime_policy(
                job,
                agent=self._agent(),
                module={'moduleKind': 'worker'},
                is_recovery_job=False,
                retry_ownership='group_owned',
            )

    def test_stage_owned_retries_only_declared_failure_classes(self) -> None:
        job = self._job()
        job['retryOwnership'] = 'stage_owned'
        job['retryPolicy'] = {'enabled': True, 'maxAttempts': 1, 'backoffSeconds': [1]}
        transient_state: dict[str, object] = {'pendingRetry': None}
        terminal_state: dict[str, object] = {'pendingRetry': None}

        engine._retry_metadata(
            job,
            transient_state,
            {'failure_class': 'transient_delivery', 'reason': 'temporary'},
        )
        engine._retry_metadata(
            job,
            terminal_state,
            {'failure_class': 'delivery_ack_unknown', 'reason': 'unknown delivery'},
        )

        self.assertEqual(transient_state['currentStatus'], engine.STATUS_RETRY_PENDING)
        self.assertEqual(transient_state['pendingRetry']['failureClass'], 'transient_delivery')
        self.assertIsNone(terminal_state['pendingRetry'])

if __name__ == '__main__':
    unittest.main()
