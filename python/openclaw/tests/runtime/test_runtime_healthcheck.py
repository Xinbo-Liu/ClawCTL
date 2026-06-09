from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from unittest import mock

from openclaw.runtime import healthcheck
from openclaw.tests.support.helpers import isolated_test_root


class RuntimeHealthcheckTest(unittest.TestCase):
    def _write_json(self, path: Path, payload: dict[str, object]) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False) + '\n', encoding='utf-8')

    def test_dispatch_summary_accepts_current_dispatch_preflight_contract(self) -> None:
        with isolated_test_root('runtime-healthcheck-dispatch') as root:
            preflight_path = root / 'preflight.json'
            status_path = root / 'status.json'
            self._write_json(
                preflight_path,
                {
                    'schemaVersion': 1,
                    'stage': 'dispatch',
                    'status': 'ok',
                    'ready': True,
                    'validation': {'ok': True, 'blocking_issue': None},
                    'target_summary': {'enabled_target_count': 3},
                    'runtime_state': {'queue': {'total': 0}},
                },
            )
            self._write_json(
                status_path,
                {
                    'schemaVersion': 1,
                    'stage': 'dispatch',
                    'validation': {'ok': True},
                    'formal_dispatch': {
                        'ready': True,
                        'status': 'ok',
                        'issues': [],
                        'queue_total_count': 0,
                        'queue_due_count': 0,
                        'missing_target_count': 0,
                        'failed_count': 0,
                        'latest_run_id': 'run-1',
                        'current_push_run_id': 'push-1',
                    },
                },
            )

            stdout = io.StringIO()
            with mock.patch('sys.stdout', stdout):
                exit_code = healthcheck.main([
                    'dispatch-summary',
                    '--preflight',
                    str(preflight_path),
                    '--status',
                    str(status_path),
                ])

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload['preflight_ok'])
        self.assertTrue(payload['dispatch_ready'])
        self.assertTrue(payload['validation_ok'])
        self.assertEqual(payload['latest_run_id'], 'run-1')


if __name__ == '__main__':
    unittest.main()
