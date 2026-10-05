from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from openclaw.docs.renderers import maintenance_map


class MaintenanceMapRendererTest(unittest.TestCase):
    def test_render_doc_uses_lightweight_profile_overview(self) -> None:
        def overview(*, config_path: Path, **kwargs):
            profile = 'agent_platform' if config_path.name == 'agent_platform.service.json' else 'base'
            return {
                'selected_config': {'profile_id': profile, 'config_relpath': config_path.name},
                'extensions': {'enabled_extension_ids': [profile] if profile != 'base' else [], 'known_extension_ids': [profile] if profile != 'base' else [], 'registry_inputs': {}, 'managed_explicit': []},
                'generated_artifacts': [{'source': 'selected/source.json'}],
                'evidence': [],
            }

        with (
            patch.object(maintenance_map, 'build_overview_payload', side_effect=overview) as build_payload,
            patch.object(maintenance_map, 'render_overview_markdown', return_value='rendered') as render_markdown,
            patch.object(maintenance_map, 'verification_tier_rows', return_value=[]),
        ):
            self.assertEqual(maintenance_map.render_doc(), 'rendered')

        self.assertEqual(build_payload.call_count, 2)
        for call in build_payload.call_args_list:
            self.assertFalse(call.kwargs['probe_local'])
            self.assertFalse(call.kwargs['include_all_profiles'])
            self.assertFalse(call.kwargs['include_profile_runtime_services'])
            self.assertFalse(call.kwargs['include_profile_evidence_paths'])
            self.assertEqual(call.kwargs['scope'], 'selected_config')
            self.assertEqual(call.kwargs['path_environment'], {})
        self.assertFalse(render_markdown.call_args.kwargs['redact_managed_extensions'])
        payload = render_markdown.call_args.args[0]
        self.assertEqual([row['id'] for row in payload['profile_overviews']], ['base', 'agent_platform'])
        self.assertEqual(payload['extensions']['known_extension_ids'], ['agent_platform'])
        self.assertEqual(payload['extensions']['managed_explicit'], [])
        self.assertEqual(payload['generated_artifacts'], [{'source': 'selected/source.json'}])

    def test_business_profile_cannot_replace_canonical_map(self) -> None:
        with self.assertRaisesRegex(ValueError, '固定使用 agent_platform'):
            maintenance_map.render_doc(config_path=maintenance_map.ROOT_DIR / 'business.service.json')

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
