from __future__ import annotations

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
