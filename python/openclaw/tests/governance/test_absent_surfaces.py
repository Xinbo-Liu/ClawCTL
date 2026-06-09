from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.absent_surfaces import (
    ABSENT_SURFACES_PATH,
    AbsentSurfaceError,
    load_absent_surfaces,
    validate_absent_surfaces,
)
from openclaw.tests.support.managed_extensions import managed_extensions
from openclaw.tests.support.static_text_assertions import assert_static_text_absent


ROOT_DIR = resolve_repo_root(Path(__file__))


class AbsentSurfacesTest(unittest.TestCase):
    def test_catalog_loads_structured_absent_paths(self) -> None:
        surfaces = {row.id: row for row in load_absent_surfaces(ABSENT_SURFACES_PATH)}

        self.assertIn('control_plane_cli_support_parser', surfaces)
        self.assertIn('doctor_platform_config_selection', surfaces)
        self.assertIn('python/openclaw/control_plane/cli_support/parser.py', surfaces['control_plane_cli_support_parser'].paths)

    def test_catalog_does_not_embed_current_managed_extension_names(self) -> None:
        catalog_text = ABSENT_SURFACES_PATH.read_text(encoding='utf-8')

        for extension in managed_extensions(ROOT_DIR):
            with self.subTest(extension=extension.id):
                assert_static_text_absent(self, extension.id, catalog_text)
                assert_static_text_absent(self, extension.root_dir.name, catalog_text)

    def test_catalog_rejects_duplicate_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'absent_surfaces.json'
            path.write_text(
                json.dumps(
                    {
                        'schemaVersion': 1,
                        'surfaces': [
                            {'id': 'a', 'reason': 'a', 'paths': ['docs/old.md']},
                            {'id': 'b', 'reason': 'b', 'paths': ['docs/old.md']},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding='utf-8',
            )

            with self.assertRaises(AbsentSurfaceError):
                load_absent_surfaces(path)

    def test_validate_absent_surfaces_flags_existing_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / 'docs').mkdir()
            (repo_root / 'docs' / 'blocked.md').write_text('blocked\n', encoding='utf-8')
            manifest = Path(tmpdir) / 'absent_surfaces.json'
            manifest.write_text(
                json.dumps(
                    {
                        'schemaVersion': 1,
                        'surfaces': [
                            {'id': 'blocked_doc', 'reason': 'blocked doc path', 'paths': ['docs/blocked.md']},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding='utf-8',
            )

            violations = validate_absent_surfaces(repo_root, load_absent_surfaces(manifest))

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].kind, 'unexpected_path')
        self.assertEqual(violations[0].target, 'docs/blocked.md')

    def test_keyword_gate_source_is_absent(self) -> None:
        filename = '_'.join(('platform', 'surface', 'rules')) + '.json'
        self.assertFalse((ROOT_DIR / 'config' / 'governance' / 'validation' / filename).exists())


if __name__ == '__main__':
    unittest.main()
