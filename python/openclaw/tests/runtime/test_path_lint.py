from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openclaw.doctor.agent_modules.managed_probe_fixture import materialize_managed_probe_extension
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.runtime.path_lint import _scan_structured_surface_refs
from openclaw.lib.runtime.path_resolver import PathResolver
from openclaw.tests.support.helpers import isolated_test_root
from openclaw.tests.support.managed_extensions import managed_extensions


ROOT_DIR = resolve_repo_root(Path(__file__))


class PathLintRulesTest(unittest.TestCase):
    def test_path_lint_structured_refs_pass_on_repo(self) -> None:
        resolver = PathResolver.from_repo_root(ROOT_DIR)

        self.assertEqual(_scan_structured_surface_refs(ROOT_DIR, resolver), [])

    def test_path_lint_flags_missing_repo_path_and_unknown_runtime_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / 'README.md').write_text(
                'see docs/missing.md* first, then docs/missing.md. and runtime.env\n',
                encoding='utf-8',
            )
            resolver = PathResolver(
                repo_root=repo_root,
                manifest={
                    'version': 1,
                    'roots': {},
                    'entries': {},
                    'view_contract': {
                        'internal_view_keys': ['host', 'gateway', 'scheduler'],
                        'public_view_names': {'gateway': 'gateway', 'scheduler': 'scheduler'},
                    },
                    'logical_groups': {},
                },
            )

            issues = _scan_structured_surface_refs(repo_root, resolver)

        self.assertIn('README.md references missing repository path: docs/missing.md', issues)
        self.assertIn('README.md references unknown runtime env file: runtime.env', issues)

    def test_path_lint_accepts_available_managed_extension_profile(self) -> None:
        resolver = PathResolver.from_repo_root(ROOT_DIR)
        issues = _scan_structured_surface_refs(ROOT_DIR, resolver)
        extensions = managed_extensions(ROOT_DIR)
        profile_issue_prefix = 'profile service config is not available as a control-plane profile: '

        if not extensions:
            self.assertFalse([issue for issue in issues if issue.startswith(profile_issue_prefix)])
            return
        for extension in extensions:
            with self.subTest(extension=extension.id):
                rel_config_path = extension.default_service_config_path.relative_to(ROOT_DIR).as_posix()
                self.assertNotIn(
                    f'{profile_issue_prefix}{rel_config_path}',
                    issues,
                )

    def test_path_lint_accepts_valid_discovered_extension_profile(self) -> None:
        with isolated_test_root('path-lint-discovered-profile') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            index_path = repo_root / 'agent' / 'extensions' / 'index.json'
            if index_path.exists():
                index_path.unlink()
            resolver = PathResolver.from_repo_root(repo_root)
            issues = _scan_structured_surface_refs(repo_root, resolver)
            rel_config_path = fixture.service_path.relative_to(repo_root).as_posix()

        self.assertNotIn(
            f'profile service config is not available as a control-plane profile: {rel_config_path}',
            issues,
        )


if __name__ == '__main__':
    unittest.main()
