from __future__ import annotations

import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from openclaw.control_plane.stack.release import _should_hash_base_file
from openclaw.release import bundle_governance


class BundleGovernanceSourceClosureTest(unittest.TestCase):
    def test_full_source_preserves_all_hashable_base_material(self) -> None:
        """完整源码包保留来源摘要覆盖的全部基座文件，使无 Git 的解包目录也能校验溯源。"""
        root = bundle_governance.ROOT_DIR
        source_paths = {
            path.relative_to(root).as_posix()
            for path in root.rglob('*')
            if path.is_file() and _should_hash_base_file(path.relative_to(root))
        }
        bundle_paths = set(bundle_governance.resolve_bundle_files('full-source-governance'))
        self.assertTrue(source_paths)
        self.assertFalse(source_paths - bundle_paths, f'完整源码包遗漏基座来源文件：{sorted(source_paths - bundle_paths)}')


class BundleGovernanceZipModeTest(unittest.TestCase):
    def test_git_index_modes_read_null_delimited_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.git').mkdir()
            path = '目录/含制表\t和换行\n的脚本.sh'
            output = b'100755 ' + b'a' * 40 + b' 0\t' + path.encode('utf-8') + b'\0'
            with (
                patch.object(bundle_governance, 'ROOT_DIR', root),
                patch.object(bundle_governance.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output)) as run,
            ):
                self.assertEqual(bundle_governance._git_index_file_modes(), {path: '100755'})
                self.assertEqual(run.call_args.args[0], ['git', 'ls-files', '--stage', '-z'])

    def test_git_worktree_requires_readable_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.git').mkdir()
            for failure in (FileNotFoundError('git'), subprocess.CompletedProcess([], 128, b'')):
                with self.subTest(failure=type(failure).__name__):
                    with (
                        patch.object(bundle_governance, 'ROOT_DIR', root),
                        patch.object(bundle_governance.subprocess, 'run') as run,
                    ):
                        if isinstance(failure, Exception):
                            run.side_effect = failure
                        else:
                            run.return_value = failure
                        with self.assertRaises(bundle_governance.BundleGovernanceError):
                            bundle_governance._git_index_file_modes()

    def test_git_index_modes_reject_unmerged_or_malformed_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.git').mkdir()
            for output in (b'100644 hash 2\tREADME.md\0', b'invalid-record\0'):
                with self.subTest(output=output):
                    with (
                        patch.object(bundle_governance, 'ROOT_DIR', root),
                        patch.object(bundle_governance.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output)),
                    ):
                        with self.assertRaises(bundle_governance.BundleGovernanceError):
                            bundle_governance._git_index_file_modes()

    def test_unreadable_index_preserves_previous_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.git').mkdir()
            zip_path = root / 'artifact.zip'
            zip_path.write_bytes(b'previous-archive')
            with (
                patch.object(bundle_governance, 'ROOT_DIR', root),
                patch.object(bundle_governance.subprocess, 'run', side_effect=FileNotFoundError('git')),
            ):
                with self.assertRaises(bundle_governance.BundleGovernanceError):
                    bundle_governance._write_zip('probe', [], zip_path)
            self.assertEqual(zip_path.read_bytes(), b'previous-archive')

    def test_source_package_without_git_uses_file_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = root / 'README.md'
            payload.write_text('content\n', encoding='utf-8')
            with (
                patch.object(bundle_governance, 'ROOT_DIR', root),
                patch.object(bundle_governance.subprocess, 'run') as run,
            ):
                modes = bundle_governance._git_index_file_modes()
                self.assertEqual(modes, {})
                self.assertEqual(bundle_governance._zip_external_mode('README.md', modes), payload.stat().st_mode & 0xFFFF)
                run.assert_not_called()

    def test_write_zip_uses_git_index_executable_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / 'tool.sh'
            script.write_text('#!/usr/bin/env sh\nexit 0\n', encoding='utf-8')
            script.chmod(0o644)
            zip_path = root / 'artifact.zip'

            with (
                patch.object(bundle_governance, 'ROOT_DIR', root),
                patch.object(bundle_governance, '_git_index_file_modes', return_value={'tool.sh': '100755'}),
            ):
                bundle_governance._write_zip('probe', ['tool.sh'], zip_path)

            with zipfile.ZipFile(zip_path) as archive:
                mode = (archive.getinfo('tool.sh').external_attr >> 16) & 0o7777

        self.assertEqual(mode, 0o755)

    def test_compute_bom_uses_git_index_regular_file_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = root / 'README.md'
            payload.write_text('content\n', encoding='utf-8')
            payload.chmod(0o666)

            with (
                patch.object(bundle_governance, 'ROOT_DIR', root),
                patch.object(bundle_governance, '_git_index_file_modes', return_value={'README.md': '100644'}),
            ):
                rows = bundle_governance.compute_bom(['README.md'])

        self.assertEqual(rows[0]['mode'], '0o644')


if __name__ == '__main__':
    unittest.main()
