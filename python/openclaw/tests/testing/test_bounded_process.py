from __future__ import annotations

import io
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from openclaw.lib.runtime.bounded_process import run_bounded_process, truncate_process_output


class BoundedProcessTest(unittest.TestCase):
    def test_timeout_returns_machine_state_and_recovers_process(self) -> None:
        outcome = run_bounded_process(
            [sys.executable, '-c', 'import time; time.sleep(60)'],
            cwd=Path.cwd(),
            timeout_seconds=0.1,
            heartbeat_seconds=0,
            term_grace_seconds=0.1,
        )

        self.assertTrue(outcome.timed_out)
        self.assertIsNone(outcome.exit_code)
        self.assertLess(outcome.duration_seconds, 2.0)

    def test_timeout_kills_descendant_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            marker = Path(tmpdir) / 'descendant-survived'
            child_code = f'import time; time.sleep(0.8); open({str(marker)!r}, "w").write("survived")'
            parent_code = (
                'import subprocess, sys, time; '
                f'subprocess.Popen([sys.executable, "-c", {child_code!r}]); '
                'time.sleep(60)'
            )
            outcome = run_bounded_process(
                [sys.executable, '-c', parent_code],
                cwd=Path(tmpdir),
                timeout_seconds=0.1,
                heartbeat_seconds=0,
                term_grace_seconds=0.1,
            )
            time.sleep(1.0)

            self.assertTrue(outcome.timed_out)
            self.assertFalse(marker.exists())

    def test_heartbeat_stays_out_of_json_stdout(self) -> None:
        heartbeat = io.StringIO()
        outcome = run_bounded_process(
            [sys.executable, '-c', 'import time; time.sleep(0.12); print("{\\"ok\\": true}")'],
            cwd=Path.cwd(),
            timeout_seconds=1.0,
            heartbeat_seconds=0.05,
            heartbeat_label='json-probe',
            heartbeat_stream=heartbeat,
        )

        self.assertEqual(outcome.exit_code, 0)
        self.assertEqual(outcome.stdout.strip(), '{"ok": true}')
        self.assertIn('[heartbeat] json-probe', heartbeat.getvalue())

    def test_failure_output_truncation_keeps_head_and_tail(self) -> None:
        source = 'head-' + ('x' * 500) + '-tail'
        truncated = truncate_process_output(source, limit_chars=80)

        self.assertLess(len(truncated), len(source))
        self.assertTrue(truncated.startswith('head-'))
        self.assertTrue(truncated.endswith('-tail'))
        self.assertIn('truncated', truncated)


if __name__ == '__main__':
    unittest.main()
