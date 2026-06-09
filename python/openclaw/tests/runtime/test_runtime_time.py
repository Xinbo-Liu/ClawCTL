from __future__ import annotations

import unittest
from datetime import datetime, timezone

from openclaw.lib.runtime import time as runtime_time


class RuntimeTimeTest(unittest.TestCase):
    def test_now_in_app_tz_returns_aware_datetime(self) -> None:
        value = runtime_time.now_in_app_tz()
        self.assertIsNotNone(value.tzinfo)
        self.assertIsNotNone(value.utcoffset())

    def test_make_run_id_uses_app_tz_timestamp_format(self) -> None:
        value = runtime_time.make_run_id('digest', timestamp=datetime(2026, 4, 20, 10, 0, 0))
        self.assertTrue(value.startswith('20260420-100000-digest-'))

    def test_resolve_timezone_rejects_unknown_timezone(self) -> None:
        with self.assertRaises(runtime_time.TimePolicyError):
            runtime_time.resolve_timezone('Asia/Shangahi')

    def test_parse_iso_datetime_defaults_naive_values_to_utc(self) -> None:
        value = runtime_time.parse_iso_datetime('2026-04-20T10:00:00')

        self.assertIsNotNone(value)
        assert value is not None
        self.assertEqual(value.tzinfo, timezone.utc)

    def test_align_datetime_converts_aware_values_to_reference_timezone(self) -> None:
        app_tz = runtime_time.resolve_timezone('Asia/Shanghai')
        value = datetime(2026, 4, 20, 2, 0, 0, tzinfo=timezone.utc)
        reference = datetime(2026, 4, 20, 10, 0, 0, tzinfo=app_tz)

        aligned = runtime_time.align_datetime_for_compare(value, reference)

        self.assertIsNotNone(aligned.tzinfo)
        self.assertEqual(aligned.isoformat(), '2026-04-20T10:00:00+08:00')

    def test_align_datetime_converts_aware_values_before_dropping_timezone(self) -> None:
        app_tz = runtime_time.resolve_timezone('Asia/Shanghai')
        value = datetime(2026, 4, 20, 2, 0, 0, tzinfo=timezone.utc)
        reference = datetime(2026, 4, 20, 10, 0, 0)

        aligned = runtime_time.align_datetime_for_compare(value, reference, assume_tz=app_tz)

        self.assertIsNone(aligned.tzinfo)
        self.assertEqual(aligned, datetime(2026, 4, 20, 10, 0, 0))

    def test_align_datetime_interprets_naive_values_with_assume_timezone(self) -> None:
        app_tz = runtime_time.resolve_timezone('Asia/Shanghai')
        value = datetime(2026, 4, 20, 10, 0, 0)
        reference = datetime(2026, 4, 20, 2, 0, 0, tzinfo=timezone.utc)

        aligned = runtime_time.align_datetime_for_compare(value, reference, assume_tz=app_tz)

        self.assertIsNotNone(aligned.tzinfo)
        self.assertEqual(aligned.isoformat(), '2026-04-20T02:00:00+00:00')


if __name__ == '__main__':
    unittest.main()
