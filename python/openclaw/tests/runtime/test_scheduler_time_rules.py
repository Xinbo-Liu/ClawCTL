from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from openclaw.control_plane.registry_validation.runtime_policy import _normalize_job_schedule
from openclaw.lib.cli.common import CliError
from openclaw.scheduler import engine
from openclaw.scheduler.cron import cron_matches, resolve_timezone, validate_cron_expr


class SchedulerTimeRulesTest(unittest.TestCase):
    def test_timezone_resolution_is_strict(self) -> None:
        with self.assertRaises(CliError):
            resolve_timezone('Asia/Shangahi')

    def test_cron_validation_rejects_bad_tokens_and_ranges(self) -> None:
        invalid_exprs = [
            'bad 8 * * 1-5',
            '60 8 * * 1-5',
            '35 24 * * 1-5',
            '35 8 * * 8',
        ]
        for expr in invalid_exprs:
            with self.subTest(expr=expr):
                with self.assertRaises(CliError):
                    validate_cron_expr(expr)

    def test_registry_schedule_normalization_rejects_bad_cron_and_timezone(self) -> None:
        with self.assertRaises(CliError):
            _normalize_job_schedule({'id': 'bad_cron', 'schedule': {'expr': '99 8 * * 1-5'}}, default_timezone='Asia/Shanghai')
        with self.assertRaises(CliError):
            _normalize_job_schedule({'id': 'bad_tz', 'schedule': {'expr': '35 8 * * 1-5', 'tz': 'Asia/Shangahi'}}, default_timezone='Asia/Shanghai')

    def test_pending_retry_uses_supplied_current_time(self) -> None:
        job = {'id': 'demo_job'}
        job_state = {'pendingRetry': {'attempt': 1, 'nextRunAt': '2099-01-01T00:00:00Z'}}

        due, due_key, trigger = engine.candidate_due(
            job,
            job_state,
            datetime(2100, 1, 1, 0, 1, tzinfo=timezone.utc),
            False,
        )

        self.assertTrue(due)
        self.assertEqual(trigger, 'retry')
        self.assertEqual(due_key, 'demo_job@retry1@2100-01-01T00:01')

    def test_execute_due_jobs_uses_single_tick_time_for_all_jobs(self) -> None:
        config = {
            'defaults': {'timezone': 'Asia/Shanghai'},
            'jobs': [
                {'id': 'first_job', 'title': 'First', 'schedule': {'kind': 'cron', 'expr': '30 8 * * 1-5'}},
                {'id': 'second_job', 'title': 'Second', 'schedule': {'kind': 'cron', 'expr': '30 8 * * 1-5'}},
            ],
        }
        captured: list[tuple[str, datetime, str]] = []

        def fake_run_job(**kwargs):
            captured.append((kwargs['job']['id'], kwargs['current'], kwargs['due_key']))
            return {
                'status': engine.STATUS_SUCCEEDED,
                'runId': kwargs['due_key'],
                'finished_at': '2026-04-27T00:30:00Z',
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            files = SimpleNamespace(history_path=Path(tmpdir) / 'history.jsonl')
            with mock.patch.object(engine, 'run_job', side_effect=fake_run_job):
                result = engine.execute_due_jobs(
                    config=config,
                    files=files,
                    state={'schemaVersion': 1, 'jobs': {}},
                    tick_started_at=datetime(2026, 4, 27, 0, 30, 59, tzinfo=timezone.utc),
                )

        self.assertEqual(result['executed_count'], 2)
        self.assertEqual([row[0] for row in captured], ['first_job', 'second_job'])
        self.assertTrue(all(row[1].isoformat() == '2026-04-27T08:30:00+08:00' for row in captured))
        self.assertEqual([row[2] for row in captured], ['first_job@schedule@2026-04-27T08:30', 'second_job@schedule@2026-04-27T08:30'])

    def test_valid_weekday_cron_still_matches(self) -> None:
        validate_cron_expr('35 8 * * 1-5')
        self.assertTrue(cron_matches('35 8 * * 1-5', datetime(2026, 4, 27, 8, 35, tzinfo=resolve_timezone('Asia/Shanghai'))))


if __name__ == '__main__':
    unittest.main()
