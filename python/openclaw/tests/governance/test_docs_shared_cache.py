"""验证共享文档读取缓存跟随文件版本，并对删除和无效正文即时失败。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openclaw.docs.support import doc_targets
from openclaw.docs.support.shared_cache import read_json, read_text


class DocumentationReadCacheTest(unittest.TestCase):
    """通过真实文件变更验证读取缓存，而不要求批次清理缓存。"""

    def test_unchanged_json_reuses_parsed_value_and_text_matches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'page.json'
            path.write_text('{"title":"页面"}', encoding='utf-8')
            original = read_json(path)
            self.assertIs(original, read_json(path))
            self.assertEqual(read_text(path), '{"title":"页面"}')

    def test_same_size_text_and_json_edits_refresh_without_external_cache_clear(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'page.json'
            path.write_text('{"value":1}', encoding='utf-8')
            self.assertEqual(read_json(path), {'value': 1})
            self.assertEqual(read_text(path), '{"value":1}')
            original_time = path.stat().st_mtime_ns
            path.write_text('{"value":2}', encoding='utf-8')
            os.utime(path, ns=(original_time + 1_000_000_000, original_time + 1_000_000_000))
            self.assertEqual(read_json(path), {'value': 2})
            self.assertEqual(read_text(path), '{"value":2}')

    def test_atomic_replacement_with_same_size_and_time_refreshes_by_inode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'page.json'
            replacement = Path(directory) / 'replacement.json'
            path.write_text('{"value":1}', encoding='utf-8')
            self.assertEqual(read_json(path), {'value': 1})
            original = path.stat()
            replacement.write_text('{"value":2}', encoding='utf-8')
            os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
            replacement.replace(path)
            self.assertEqual(read_json(path), {'value': 2})
            self.assertEqual(read_text(path), '{"value":2}')

    def test_deleted_text_and_json_fail_instead_of_reusing_old_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'page.json'
            path.write_text('{"value":1}', encoding='utf-8')
            read_text(path)
            read_json(path)
            path.unlink()
            with self.assertRaises(FileNotFoundError):
                read_text(path)
            with self.assertRaises(FileNotFoundError):
                read_json(path)

    def test_changed_invalid_json_fails_instead_of_reusing_valid_object(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'page.json'
            path.write_text('{"value":1}', encoding='utf-8')
            read_json(path)
            path.write_text('not-json', encoding='utf-8')
            with self.assertRaises(json.JSONDecodeError):
                read_json(path)

    def test_document_source_cannot_read_outside_root_or_follow_escaping_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / 'repository'
            root.mkdir()
            outside = parent / 'outside.json'
            outside.write_text('{"generated_artifacts":{"doc":"docs/page.md"}}', encoding='utf-8')
            linked = root / 'linked.json'
            linked.symlink_to(outside)
            for source in (outside, linked):
                with self.subTest(source=source), patch.object(doc_targets, 'read_json') as read:
                    with self.assertRaises(ValueError):
                        doc_targets.read_json_object(source, prefix='test', root_dir=root)
                    read.assert_not_called()


if __name__ == '__main__':
    unittest.main()
