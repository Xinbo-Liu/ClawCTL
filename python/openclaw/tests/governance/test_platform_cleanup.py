from __future__ import annotations

import json
import unittest
from pathlib import Path

from openclaw.doctor.platform.architecture_import_guards import business_name_leak_tokens
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.tests.support.static_text_assertions import assert_static_text_absent
from openclaw.tests.support.managed_extensions import managed_extensions, current_managed_extension_domain_id


ROOT_DIR = resolve_repo_root(Path(__file__))


class PlatformSurfaceCleanupTest(unittest.TestCase):
    def _removed_hook_markers(self) -> tuple[str, ...]:
        return (
            ''.join(('audit', '-', 'hook')),
            '_'.join(('audit', 'hook', 'healthz')),
            '_'.join(('AUDIT', 'HOOK', 'SHARED', 'TOKEN')),
        )

    def test_removed_reference_paths_are_absent(self) -> None:
        extensions = managed_extensions(ROOT_DIR)
        for rel_path in (
            'config/control_plane/references',
            'python/openclaw/modules',
        ):
            self.assertFalse((ROOT_DIR / rel_path).exists(), msg=rel_path)
        for extension in extensions:
            domain_id = current_managed_extension_domain_id(ROOT_DIR, extension_id=extension.id)
            for rel_path in (
                f'config/control_plane/profiles/{extension.id}.service.json',
                f'python/openclaw/extensions/{extension.id}',
                f'python/openclaw/domains/{domain_id}',
            ):
                with self.subTest(extension=extension.id, path=rel_path):
                    self.assertFalse((ROOT_DIR / rel_path).exists(), msg=rel_path)

    def test_platform_runtime_paths_are_neutral(self) -> None:
        payload = json.loads((ROOT_DIR / 'config' / 'control_plane' / 'extensions.d' / 'agent_platform.runtime_paths.json').read_text(encoding='utf-8'))
        entries = payload.get('entries') if isinstance(payload.get('entries'), dict) else {}
        view_contract = json.loads((ROOT_DIR / 'config' / 'runtime' / 'paths.json').read_text(encoding='utf-8')).get('view_contract') or {}
        allowed_views = {str(item) for item in view_contract.get('internal_view_keys') or []}
        self.assertIn('dispatch_out_dir', entries)
        self.assertIn('dispatch_runs_dir', entries)
        serialized_entries = json.dumps(entries, ensure_ascii=False)
        leak_tokens = business_name_leak_tokens(ROOT_DIR)
        for token in leak_tokens:
            self.assertNotIn(token, serialized_entries)
        for entry_id, entry in entries.items():
            with self.subTest(entry=entry_id):
                owner = entry.get('owner') if isinstance(entry, dict) else None
                owner_keys = set(owner if isinstance(owner, list) else [owner]) - {None}
                self.assertFalse(owner_keys - allowed_views)
                for block_name in ('paths', 'env_names'):
                    block = entry.get(block_name)
                    if isinstance(block, dict):
                        self.assertFalse(set(block) - allowed_views)

    def test_removed_hook_surface_names_are_absent(self) -> None:
        extension_paths: list[Path] = []
        for extension in managed_extensions(ROOT_DIR):
            extension_paths.extend(sorted((extension.root_dir / 'config').glob('*.data_contract.json')))
            extension_paths.extend(sorted(extension.manifest_dir.glob('*.runtime_paths.json')))
        checked_paths = [
            *extension_paths,
            ROOT_DIR / 'config' / 'runtime' / 'service_registry.json',
            ROOT_DIR / 'config' / 'runtime' / 'testing_manifest.json',
            ROOT_DIR / 'docs' / 'operations' / 'troubleshooting.md',
            ROOT_DIR / 'python' / 'openclaw' / 'setup' / 'deploy_env' / 'docs.py',
            ROOT_DIR / 'python' / 'openclaw' / 'setup' / 'deploy_env' / 'render_validate.py',
            ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'full_test_group_runner.sh',
        ]
        for path in checked_paths:
            with self.subTest(path=path.relative_to(ROOT_DIR).as_posix()):
                content = path.read_text(encoding='utf-8')
                for marker in self._removed_hook_markers():
                    assert_static_text_absent(self, marker, content)


if __name__ == '__main__':
    unittest.main()
