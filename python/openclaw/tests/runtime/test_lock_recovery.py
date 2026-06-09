from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from openclaw.scheduler import engine
from openclaw.scheduler import locking as scheduler_locking


class SchedulerLockRecoveryTest(unittest.TestCase):
    def test_acquire_lock_reclaims_stale_lock_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / 'job.lock'
            lock_path.write_text(json.dumps({
                'pid': 999999,
                'hostname': os.uname().nodename if hasattr(os, 'uname') else '',
                'createdAtEpoch': int(time.time()) - 4000,
                'staleAfterSeconds': 300,
            }), encoding='utf-8')
            with mock.patch.object(scheduler_locking, 'pid_is_running', return_value=False):
                acquired = scheduler_locking.acquire_lock(lock_path, {'jobId': 'job-a'}, stale_after_seconds=300)
        self.assertTrue(acquired)

    def test_acquire_lock_keeps_fresh_active_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / 'job.lock'
            lock_path.write_text(json.dumps({
                'pid': os.getpid(),
                'hostname': os.uname().nodename if hasattr(os, 'uname') else '',
                'createdAtEpoch': int(time.time()),
                'staleAfterSeconds': 3600,
            }), encoding='utf-8')
            with mock.patch.object(scheduler_locking, 'pid_is_running', return_value=True):
                acquired = scheduler_locking.acquire_lock(lock_path, {'jobId': 'job-a'}, stale_after_seconds=3600)
        self.assertFalse(acquired)

    def test_scheduler_cycle_lock_caps_legacy_long_stale_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / '.scheduler-cycle.lock'
            lock_path.write_text(json.dumps({
                'kind': 'scheduler_cycle',
                'pid': os.getpid(),
                'hostname': 'previous-container-hostname',
                'createdAtEpoch': int(time.time()) - 180,
                'staleAfterSeconds': 3600,
            }), encoding='utf-8')
            acquired = scheduler_locking.acquire_lock(
                lock_path,
                {'kind': 'scheduler_cycle'},
                stale_after_seconds=120,
            )
        self.assertTrue(acquired)

    def test_job_lock_keeps_legacy_long_stale_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / 'job.lock'
            lock_path.write_text(json.dumps({
                'pid': os.getpid(),
                'hostname': 'previous-container-hostname',
                'createdAtEpoch': int(time.time()) - 180,
                'staleAfterSeconds': 3600,
            }), encoding='utf-8')
            acquired = scheduler_locking.acquire_lock(
                lock_path,
                {'jobId': 'job-a'},
                stale_after_seconds=120,
            )
        self.assertFalse(acquired)

    def test_retry_metadata_materializes_pending_retry_window(self) -> None:
        job = {
            'retryPolicy': {
                'enabled': True,
                'maxAttempts': 2,
                'backoffSeconds': [30, 60],
            }
        }
        job_state: dict[str, object] = {}
        engine._retry_metadata(job, job_state, {'reason': 'failed_once', 'return_code': 1})
        pending = job_state.get('pendingRetry')
        self.assertEqual(job_state.get('currentStatus'), engine.STATUS_RETRY_PENDING)
        self.assertIsInstance(pending, dict)
        assert isinstance(pending, dict)
        self.assertEqual(pending.get('attempt'), 1)
        self.assertEqual(pending.get('reason'), 'failed_once')
        self.assertTrue(str(pending.get('nextRunAt') or '').endswith('Z'))


if __name__ == '__main__':
    unittest.main()
