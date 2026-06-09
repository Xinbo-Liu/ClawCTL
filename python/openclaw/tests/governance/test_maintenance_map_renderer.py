from __future__ import annotations

import contextlib
import io
import unittest
from unittest.mock import patch

from openclaw.docs.renderers import maintenance_map


class MaintenanceMapRendererTest(unittest.TestCase):
    def test_render_doc_uses_lightweight_profile_overview(self) -> None:
        with (
            patch.object(maintenance_map, 'build_overview_payload', return_value={}) as build_payload,
            patch.object(maintenance_map, 'render_overview_markdown', return_value='rendered') as render_markdown,
        ):
            self.assertEqual(maintenance_map.render_doc(), 'rendered')

        self.assertFalse(build_payload.call_args.kwargs['include_profile_runtime_services'])
        self.assertFalse(build_payload.call_args.kwargs['include_profile_evidence_paths'])
        self.assertTrue(render_markdown.call_args.kwargs['redact_managed_extensions'])

    def test_generated_doc_is_synced(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        existing = (maintenance_map.ROOT_DIR / maintenance_map.MAINTENANCE_MAP_DOC).read_text(encoding='utf-8')
        with (
            patch.object(maintenance_map, 'render_doc', return_value=existing),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(maintenance_map.render_entry(['--check']), 0)
        self.assertIn('已同步', stdout.getvalue())
        self.assertEqual(stderr.getvalue(), '')


if __name__ == '__main__':
    unittest.main()
