from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from openclaw.lib.io import state
from openclaw.lib.io.state import append_jsonl, write_json_atomic, write_text_atomic


class StateIoTest(unittest.TestCase):
    def test_write_text_atomic_uses_unique_temp_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / 'state.json'

            def write_item(index: int) -> None:
                write_text_atomic(target, json.dumps({'index': index}, ensure_ascii=False))

            with ThreadPoolExecutor(max_workers=6) as executor:
                list(executor.map(write_item, range(24)))

            payload = json.loads(target.read_text(encoding='utf-8'))
            self.assertIn(payload['index'], range(24))
            self.assertFalse((root / '.state.json.tmp').exists())
            self.assertEqual(list(root.glob('.state.json.*.tmp')), [])

    def test_append_jsonl_serializes_complete_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / 'events.jsonl'

            def append_item(index: int) -> None:
                append_jsonl(target, {'index': index, 'value': f'row-{index}'})

            with ThreadPoolExecutor(max_workers=6) as executor:
                list(executor.map(append_item, range(12)))

            rows = [json.loads(line) for line in target.read_text(encoding='utf-8').splitlines() if line.strip()]
            self.assertEqual(sorted(row['index'] for row in rows), list(range(12)))
            self.assertFalse((root / '.events.jsonl.lock').exists())

    def test_atomic_writers_preserve_lf_newlines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            text_target = root / 'state.txt'
            json_target = root / 'state.json'
            events_target = root / 'events.jsonl'

            write_text_atomic(text_target, 'alpha\nbeta\n')
            write_json_atomic(json_target, {'alpha': ['beta', 'gamma']})
            append_jsonl(events_target, {'alpha': 'beta'})

            self.assertNotIn(b'\r\n', text_target.read_bytes())
            self.assertNotIn(b'\r\n', json_target.read_bytes())
            self.assertNotIn(b'\r\n', events_target.read_bytes())

    def test_lock_metadata_write_failure_preserves_original_exception(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_dir = Path(tmpdir) / 'events.lock'

            with mock.patch.object(state, 'write_json_atomic', side_effect=RuntimeError('metadata boom')):
                with self.assertRaisesRegex(RuntimeError, 'metadata boom'):
                    with state.with_lock_dir(lock_dir):
                        pass

            self.assertFalse(lock_dir.exists())

    def test_lock_age_treats_metadata_permission_error_as_active_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_dir = Path(tmpdir) / 'events.lock'
            lock_dir.mkdir()

            with mock.patch.object(Path, 'exists', side_effect=PermissionError('busy')):
                self.assertEqual(state._lock_age_seconds(lock_dir), 0.0)


if __name__ == '__main__':
    unittest.main()
