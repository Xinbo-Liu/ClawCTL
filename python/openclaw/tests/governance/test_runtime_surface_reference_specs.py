from __future__ import annotations

import contextlib
import io
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openclaw.docs.renderers import runtime_surface
from openclaw.docs.support import reference_specs
from openclaw.tests.support.managed_extensions import managed_extensions


class RuntimeSurfaceReferenceSpecsSurfaceTest(unittest.TestCase):
    def test_runtime_surface_generated_doc_is_synced(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(runtime_surface.render_entry(['--check']), 0)
        self.assertIn('已同步', stdout.getvalue())
        self.assertEqual(stderr.getvalue(), '')

    def test_runtime_surface_check_ignores_active_profile_env(self) -> None:
        extension_ids = sorted(row.id for row in managed_extensions(runtime_surface.ROOT_DIR))
        active_profile = extension_ids[0] if extension_ids else 'agent_synthetic_unused'
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.dict(os.environ, {'OPENCLAW_CONTROL_PLANE_PROFILE': active_profile}, clear=False):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(runtime_surface.render_entry(['--check']), 0)
        self.assertIn('已同步', stdout.getvalue())
        self.assertEqual(stderr.getvalue(), '')

    def test_runtime_surface_render_entry_check_and_write_contract(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = {
                'generated_doc': 'docs/runtime-service-reference.md',
                'title': 'Runtime Surface',
                'intro': [],
                'entrypoints': [],
                'targets': [],
                'runtime_contract': {},
                'source_strategy': {},
                'manual_post_deploy_checks': {},
                'acceptance_reference': {},
            }
            target_path = root / 'docs' / 'runtime-service-reference.md'
            rendered = '# Runtime Surface\n'

            with patch.object(runtime_surface, 'ROOT_DIR', root):
                with patch.object(runtime_surface, 'read_manifest', return_value=manifest):
                    with patch.object(runtime_surface, 'render_doc', return_value=rendered):
                        with patch.object(runtime_surface, 'resolve_selected_control_plane_config_path', return_value=root / 'service.json'):
                            with patch.object(runtime_surface, '_is_default_runtime_config_path', return_value=True):
                                stdout = io.StringIO()
                                stderr = io.StringIO()
                                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                                    self.assertEqual(runtime_surface.render_entry(['--check']), 1)
                                    self.assertEqual(runtime_surface.render_entry([]), 0)
                                self.assertEqual(target_path.read_text(encoding='utf-8'), rendered)
                                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                                    self.assertEqual(runtime_surface.render_entry(['--check']), 0)
                                captured_stderr = stderr.getvalue()
                                self.assertIn('文档未同步', captured_stderr)
                                self.assertIn('已写入', stdout.getvalue())

    def test_runtime_surface_check_rejects_non_default_profile_drift(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = runtime_surface.render_entry(['--check', '--control-plane-profile', 'base'])

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout.getvalue(), '')
        self.assertIn('canonical runtime-service-reference 只允许使用默认 runtime profile', stderr.getvalue())
        self.assertIn('--stdout', stderr.getvalue())

    def test_runtime_surface_read_manifest_uses_public_loader_seams(self) -> None:
        with patch.object(
            runtime_surface,
            'load_runtime_surface_manifest',
            return_value={'generated_doc': 'docs/runtime-service-reference.md', 'title': 'Patched Surface', 'intro': [], 'entrypoints': []},
        ):
            with patch.object(runtime_surface, 'load_testing_manifest', return_value={'acceptance_reference': {'artifacts': []}}):
                with patch('openclaw.docs.renderers.runtime_surface.service_registry_targets', return_value=[]):
                    with patch('openclaw.docs.renderers.runtime_surface.read_repo_contract_json', return_value={}):
                        manifest = runtime_surface.read_manifest(config_path=Path('service.json'))

        self.assertEqual(manifest['title'], 'Patched Surface')
        self.assertEqual(manifest['acceptance_reference'], {'artifacts': []})

    def test_reference_specs_workspace_and_script_helpers_stay_available(self) -> None:
        target = reference_specs.WORKSPACE_USER_TARGETS[0]
        content = '\n'.join([
            'before',
            reference_specs.begin_marker(target),
            'stale',
            reference_specs.end_marker(target),
            'after',
            '',
        ])

        rendered = reference_specs.replace_workspace_managed_block(content, target)
        docs = reference_specs.get_script_doc_targets()

        self.assertIn(reference_specs.begin_marker(target), rendered)
        self.assertIn('显式路由指令', rendered)
        self.assertEqual(reference_specs.validate_script_surface_manifest(), [])
        self.assertTrue(docs)

    def test_script_catalog_visibility_is_entry_local_truth(self) -> None:
        groups = reference_specs.script_groups()
        entries = reference_specs.get_all_script_entries()
        manifest = reference_specs.load_specs('script_surface_manifest.json')

        self.assertTrue(entries)
        self.assertEqual(set(manifest), set(reference_specs.SURFACE_LEVELS))
        for group in groups:
            for item in group.get('files') or []:
                self.assertIn(item.get('visibility'), reference_specs.SURFACE_LEVELS)
        default_paths = set(manifest['default_entrypoint'])
        self.assertIn('scripts/runtime/run_openclaw_python_tool.sh', default_paths)
        self.assertNotIn('scripts/runtime/container_python', default_paths)

    def test_reference_specs_script_helpers_remain_patchable_from_public_surface(self) -> None:
        with patch.object(reference_specs, 'load_specs', return_value=[]):
            self.assertEqual(reference_specs.script_groups(), [])

        with patch.object(
            reference_specs,
            'script_groups',
            return_value=[{'id': 'patched', 'title': 'Patched', 'purpose': 'Only patched surface', 'files': []}],
        ):
            with patch.object(
                reference_specs,
                'get_script_catalog_doc_layout',
                return_value={'scripts_index_doc': 'README.md', 'group_readme_dir': '', 'group_readme_name': ''},
            ):
                docs = reference_specs.get_script_doc_targets()

        self.assertEqual(set(docs.keys()), {'README.md'})


if __name__ == '__main__':
    unittest.main()
