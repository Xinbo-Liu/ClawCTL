from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from openclaw.docs.renderers import getting_started
from openclaw.docs.renderers.getting_started import render_step_sections
from openclaw.docs.renderers.getting_started_support.fragments import quickstart_step2_note_lines
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.tests.support.managed_extensions import managed_extensions
from openclaw.tests.support.static_text_assertions import assert_static_text_absent


ROOT_DIR = resolve_repo_root(Path(__file__))
MANAGED_EXTENSIONS = tuple(sorted(managed_extensions(ROOT_DIR), key=lambda row: row.id))


class GettingStartedRendererTest(unittest.TestCase):
    def test_generated_docs_are_synced(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(getting_started.render_entry(['--check']), 0)
        self.assertIn('已同步', stdout.getvalue())
        self.assertEqual(stderr.getvalue(), '')

    def test_generated_docs_ignore_active_profile_env(self) -> None:
        if not MANAGED_EXTENSIONS:
            self.skipTest('base release surface has no repo-managed extension profile')
        extension = MANAGED_EXTENSIONS[0]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.dict(os.environ, {'OPENCLAW_CONTROL_PLANE_PROFILE': extension.id}, clear=False):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(getting_started.render_entry(['--check']), 0)
        self.assertIn('已同步', stdout.getvalue())
        self.assertEqual(stderr.getvalue(), '')

    def test_render_step_sections_keeps_commands_and_notes_compact(self) -> None:
        rendered = render_step_sections(
            [
                {
                    'title': '准备环境',
                    'commands': ['echo step-one', ''],
                    'notes': ['保持顺序执行', ''],
                }
            ]
        )

        self.assertEqual(
            rendered,
            [
                '## 准备环境',
                '',
                '```bash',
                'echo step-one',
                '```',
                '',
                '- 保持顺序执行',
                '',
            ],
        )

    def test_quickstart_notes_do_not_mark_optional_model_secret_as_required(self) -> None:
        fields = [
            {'key': key, 'required': True, 'manual_required': True}
            for key in (
                'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS',
                'OPENCLAW_INGRESS_LISTEN_IP',
                'OPENCLAW_TLS_CN',
                'OPENCLAW_TLS_MODE',
                'OPENCLAW_INGRESS_BOUNDARY_MODE',
            )
        ]
        fields.append(
            {
                'key': 'PROBE_EXTERNAL_MODEL_API_KEY',
                'group': 'model_providers',
                'secret': True,
                'manual_required': False,
                'doc_summary': '探针扩展外部模型 API key',
            }
        )

        rendered = '\n'.join(quickstart_step2_note_lines({'fields': fields}))

        assert_static_text_absent(self, 'PROBE_EXTERNAL_MODEL_API_KEY', rendered)

    def test_quickstart_renders_strict_https_verification_commands(self) -> None:
        rendered = (ROOT_DIR / 'docs' / 'getting-started' / 'quickstart.md').read_text(encoding='utf-8')

        self.assertIn('访问端 HTTPS 验证命令：', rendered)
        self.assertIn('#### `self_signed`', rendered)
        self.assertIn('curl --cacert <openclaw-self-signed.crt>', rendered)
        self.assertIn('#### `provided_files`', rendered)
        self.assertIn('curl --resolve ${OPENCLAW_TLS_CN}:443:${OPENCLAW_CURL_RESOLVE_IP}', rendered)
        assert_static_text_absent(self, 'curl -k', rendered)
        assert_static_text_absent(self, 'provided_files_custom_ca', rendered)


if __name__ == '__main__':
    unittest.main()
