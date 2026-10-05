from __future__ import annotations

import json
import tempfile
import threading
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

    def test_heartbeat_start_failure_releases_the_owned_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_dir = Path(tmpdir) / 'events.lock'
            with mock.patch.object(state, '_start_lock_heartbeat', side_effect=RuntimeError('thread startup failed')):
                with self.assertRaisesRegex(RuntimeError, 'thread startup failed'):
                    with state.with_lock_dir(lock_dir):
                        self.fail('心跳启动失败时不能进入保护块')
            self.assertFalse(lock_dir.exists())

    def test_lock_age_treats_metadata_permission_error_as_active_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_dir = Path(tmpdir) / 'events.lock'
            lock_dir.mkdir()

            with mock.patch.object(Path, 'exists', side_effect=PermissionError('busy')):
                self.assertEqual(state._lock_age_seconds(lock_dir), 0.0)

    def test_heartbeat_never_reclaims_another_owner_or_removes_their_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_dir = Path(tmpdir) / 'events.lock'
            with state.with_lock_dir(lock_dir):
                metadata_path = lock_dir / state.LOCK_METADATA_NAME
                original_owner = json.loads(metadata_path.read_text(encoding='utf-8'))['ownerToken']
                replacement = json.dumps({'ownerToken': 'replacement', 'createdAtEpoch': 123})
                metadata_path.write_text(replacement, encoding='utf-8')

                self.assertFalse(state._refresh_lock_metadata(lock_dir, original_owner))
                self.assertEqual(metadata_path.read_text(encoding='utf-8'), replacement)

            self.assertEqual(metadata_path.read_text(encoding='utf-8'), replacement)

    def test_heartbeat_does_not_recreate_a_missing_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_dir = Path(tmpdir) / 'missing.lock'
            self.assertFalse(state._refresh_lock_metadata(lock_dir, 'expired-owner'))
            self.assertFalse(lock_dir.exists())

    def test_heartbeat_leaves_metadata_unchanged_without_directory_fd_support(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_dir = Path(tmpdir) / 'events.lock'
            lock_dir.mkdir()
            metadata_path = lock_dir / state.LOCK_METADATA_NAME
            original = json.dumps({'ownerToken': 'owner', 'updatedAtEpoch': 123})
            metadata_path.write_text(original, encoding='utf-8')

            with mock.patch.object(state.os, 'supports_dir_fd', set()):
                self.assertFalse(state._refresh_lock_metadata(lock_dir, 'owner'))

            self.assertEqual(metadata_path.read_text(encoding='utf-8'), original)

    @unittest.skipUnless(state.os.name == 'posix', '心跳刷新依赖目录描述符')
    def test_heartbeat_keeps_creation_time_when_refreshing_its_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_dir = Path(tmpdir) / 'events.lock'
            lock_dir.mkdir()
            metadata_path = lock_dir / state.LOCK_METADATA_NAME
            metadata_path.write_text(json.dumps({'ownerToken': 'owner', 'createdAtEpoch': 123, 'updatedAtEpoch': 123}), encoding='utf-8')

            with mock.patch.object(state.time, 'time', return_value=456):
                self.assertTrue(state._refresh_lock_metadata(lock_dir, 'owner'))

            refreshed = json.loads(metadata_path.read_text(encoding='utf-8'))
            self.assertEqual(refreshed['ownerToken'], 'owner')
            self.assertEqual(refreshed['createdAtEpoch'], 123)
            self.assertEqual(refreshed['updatedAtEpoch'], 456)

    @unittest.skipUnless(state.os.name == 'posix', '目录替换竞态依赖目录描述符')
    def test_heartbeat_staging_cannot_overwrite_a_replacement_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            lock_dir = root / 'events.lock'
            detached = root / 'detached.lock'
            lock_dir.mkdir()
            (lock_dir / state.LOCK_METADATA_NAME).write_text(json.dumps({'ownerToken': 'old-owner'}), encoding='utf-8')
            new_metadata = json.dumps({'ownerToken': 'new-owner', 'createdAtEpoch': 789})
            intervened = False
            fsync = state.os.fsync

            def replace_after_staging(descriptor: int) -> None:
                nonlocal intervened
                fsync(descriptor)
                if not intervened and list(lock_dir.glob('.*.tmp')):
                    intervened = True
                    lock_dir.rename(detached)
                    lock_dir.mkdir()
                    (lock_dir / state.LOCK_METADATA_NAME).write_text(new_metadata, encoding='utf-8')

            with mock.patch.object(state.os, 'fsync', side_effect=replace_after_staging):
                self.assertFalse(state._refresh_lock_metadata(lock_dir, 'old-owner'))

            self.assertTrue(intervened)
            self.assertEqual((lock_dir / state.LOCK_METADATA_NAME).read_text(encoding='utf-8'), new_metadata)
            self.assertEqual(json.loads((detached / state.LOCK_METADATA_NAME).read_text(encoding='utf-8'))['ownerToken'], 'old-owner')
            self.assertEqual(list(detached.glob('.*.tmp')), [])

    @unittest.skipUnless(state.os.name == 'posix', '心跳暂存依赖目录描述符')
    def test_slow_heartbeat_releases_its_lock_after_context_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_dir = Path(tmpdir) / 'events.lock'
            staged = threading.Event()
            resume = threading.Event()
            heartbeat_threads: list[threading.Thread] = []
            start_heartbeat = state._start_lock_heartbeat
            fsync = state.os.fsync

            def capture_heartbeat(path: Path, owner_token: str):
                # 仅缩短测试线程的唤醒间隔；实际锁元数据仍采用正式配置。
                with mock.patch.object(state, 'LOCK_STALE_SECONDS', 3):
                    stop, thread = start_heartbeat(path, owner_token)
                heartbeat_threads.append(thread)
                return stop, thread

            def hold_staged_heartbeat(descriptor: int) -> None:
                fsync(descriptor)
                if threading.current_thread().name.startswith('openclaw-lock-heartbeat-') and not staged.is_set():
                    staged.set()
                    resume.wait(10)

            with mock.patch.object(state, '_start_lock_heartbeat', side_effect=capture_heartbeat), mock.patch.object(state.os, 'fsync', side_effect=hold_staged_heartbeat):
                try:
                    with state.with_lock_dir(lock_dir):
                        self.assertTrue(staged.wait(5), '心跳必须到达暂存后的阻塞点')
                    self.assertTrue((lock_dir / state.LOCK_METADATA_NAME).exists(), '未结束的心跳仍须持有锁')
                finally:
                    resume.set()
                    for thread in heartbeat_threads:
                        thread.join(timeout=5)

            self.assertFalse(any(thread.is_alive() for thread in heartbeat_threads))
            self.assertFalse(lock_dir.exists(), '心跳退出后不能留下空锁目录')
            with state.with_lock_dir(lock_dir):
                pass


if __name__ == '__main__':
    unittest.main()
