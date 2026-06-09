from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from openclaw.doctor.agent_modules.support import repo_copy_ignore
from openclaw.doctor.platform.temp_workspace import project_tmp_root
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.tests.support.helpers import isolated_test_root
from openclaw.tests.support.managed_extensions import (
    current_managed_extension,
    current_managed_extension_domain_id,
    managed_extensions,
    representative_managed_extension,
)


ROOT_DIR = resolve_repo_root(Path(__file__))
MANAGED_EXTENSIONS = tuple(sorted(managed_extensions(ROOT_DIR), key=lambda row: row.id))


class TestSupportHelpers(unittest.TestCase):
    def test_isolated_test_root_uses_project_tmp_root_outside_repo(self) -> None:
        expected_root = project_tmp_root(ROOT_DIR)
        with isolated_test_root('support-helper') as temp_root:
            self.assertTrue(temp_root.is_dir())
            self.assertTrue(temp_root.is_relative_to(expected_root))
            self.assertFalse(temp_root.is_relative_to(ROOT_DIR))

    def test_repo_copy_ignore_filters_workspace_residue_and_caches(self) -> None:
        ignore = repo_copy_ignore(ROOT_DIR)

        self.assertEqual(
            ignore(
                str(ROOT_DIR),
                ['.git', '.idea', 'tmp', 'artifacts', 'state', 'release', 'keep', 'tmp-snapshot', 'module.pyc'],
            ),
            {'.git', '.idea', 'tmp', 'artifacts', 'tmp-snapshot', 'module.pyc'},
        )
        self.assertEqual(
            ignore(str(ROOT_DIR / 'state'), ['image_pull', 'openclaw', 'image_artifacts', 'remote_first_install', 'keep']),
            {'image_pull', 'openclaw', 'image_artifacts', 'remote_first_install'},
        )
        self.assertEqual(ignore(str(ROOT_DIR / 'release'), ['history', 'evidence', 'keep']), {'history'})
        self.assertEqual(ignore(str(ROOT_DIR / 'python' / 'openclaw'), ['__pycache__', 'module.py', 'module.pyc']), {'__pycache__', 'module.pyc'})

    def test_repo_copy_ignore_supports_policy_target_projection(self) -> None:
        fake_root = Path(r'C:\repo')

        with mock.patch(
            'openclaw.doctor.agent_modules.support.workspace_target_paths',
            return_value=('nested/cache', 'preserved/state'),
        ):
            ignore = repo_copy_ignore(fake_root)

        self.assertEqual(ignore(str(fake_root), ['nested', 'keep', 'tmp-shadow']), {'tmp-shadow'})
        self.assertEqual(ignore(str(fake_root / 'nested'), ['cache', 'keep']), {'cache'})
        self.assertEqual(ignore(str(fake_root / 'nested'), ['cache', 'keep', 'tmp-shadow']), {'cache'})
        self.assertEqual(ignore(str(fake_root / 'preserved'), ['state', 'keep']), {'state'})

    def test_managed_extension_helpers_assert_single_extension_layout(self) -> None:
        if not MANAGED_EXTENSIONS:
            self.skipTest('base release surface has no repo-managed extension')
        extension = representative_managed_extension(ROOT_DIR)

        self.assertTrue(extension.id)
        self.assertEqual(current_managed_extension_domain_id(ROOT_DIR, extension_id=extension.id), extension.id.removeprefix('agent_'))

    def test_representative_managed_extension_supports_multi_extension_index(self) -> None:
        with isolated_test_root('managed-extension-helper-multi') as repo_root:
            (repo_root / 'python' / 'openclaw').mkdir(parents=True)
            (repo_root / 'config' / 'runtime').mkdir(parents=True)
            (repo_root / 'config' / 'control_plane').mkdir(parents=True)
            (repo_root / 'config' / 'runtime' / 'paths.json').write_text('{}\n', encoding='utf-8')
            (repo_root / 'config' / 'control_plane' / 'service.json').write_text('{}\n', encoding='utf-8')
            (repo_root / 'agent' / 'extensions').mkdir(parents=True)
            (repo_root / 'agent' / 'extensions' / 'index.json').write_text(
                json.dumps(
                    {
                        'extensions': [
                            {
                                'id': 'agent_zeta',
                                'title': 'Zeta',
                                'rootDir': 'agent/extensions/agent_zeta',
                                'defaultServiceConfigPath': 'agent/extensions/agent_zeta/config/control_plane/profiles/agent_zeta.service.json',
                                'manifestDir': 'agent/extensions/agent_zeta/config/control_plane/extensions.d',
                                'pythonRoots': ['agent/extensions/agent_zeta/python'],
                                'status': 'managed_explicit_extension',
                            },
                            {
                                'id': 'agent_alpha',
                                'title': 'Alpha',
                                'rootDir': 'agent/extensions/agent_alpha',
                                'defaultServiceConfigPath': 'agent/extensions/agent_alpha/config/control_plane/profiles/agent_alpha.service.json',
                                'manifestDir': 'agent/extensions/agent_alpha/config/control_plane/extensions.d',
                                'pythonRoots': ['agent/extensions/agent_alpha/python'],
                                'status': 'managed_explicit_extension',
                            },
                        ]
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + '\n',
                encoding='utf-8',
            )

            representative = representative_managed_extension(repo_root)
            explicit = current_managed_extension(repo_root, extension_id='agent_zeta')
            with self.assertRaisesRegex(AssertionError, 'expected exactly one managed explicit extension'):
                current_managed_extension(repo_root)

        self.assertEqual(representative.id, 'agent_alpha')
        self.assertEqual(explicit.id, 'agent_zeta')


if __name__ == '__main__':
    unittest.main()
