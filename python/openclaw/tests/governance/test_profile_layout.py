from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from openclaw.cli_registry import supported_root_commands
from openclaw.lib.repo.layout import (
    available_control_plane_profile_ids,
    control_plane_profile_config_rel_paths,
    resolve_control_plane_profile_service_config_path,
    resolve_repo_root,
)
from openclaw.lib.repo.profiles import control_plane_repo_combination_profile_rel_paths
from openclaw.release import artifact_smoke
from openclaw.lib.repo.managed_extensions import load_managed_extensions_index


ROOT_DIR = resolve_repo_root(Path(__file__))


class ProfileSurfaceLayoutTest(unittest.TestCase):
    def test_available_profiles_include_managed_extension_profiles(self) -> None:
        repo_combination_profile_ids = tuple(control_plane_repo_combination_profile_rel_paths(ROOT_DIR))
        managed_profile_ids = tuple(row.id for row in load_managed_extensions_index(ROOT_DIR))
        self.assertEqual(
            available_control_plane_profile_ids(ROOT_DIR),
            ('base', 'agent_platform', *repo_combination_profile_ids, *managed_profile_ids),
        )

    def test_profile_registry_matches_repo_profiles_and_managed_extension_index(self) -> None:
        expected = {
            'base': 'config/control_plane/service.json',
            'agent_platform': 'config/control_plane/profiles/agent_platform.service.json',
            **control_plane_repo_combination_profile_rel_paths(ROOT_DIR),
        }
        for row in load_managed_extensions_index(ROOT_DIR):
            expected[row.id] = row.default_service_config_path.resolve().relative_to(ROOT_DIR).as_posix()

        self.assertEqual(control_plane_profile_config_rel_paths(ROOT_DIR), expected)

    def test_managed_extension_profile_can_be_resolved(self) -> None:
        extensions = load_managed_extensions_index(ROOT_DIR)
        for extension in extensions:
            with self.subTest(extension=extension.id):
                self.assertEqual(
                    resolve_control_plane_profile_service_config_path(extension.id, start_path=ROOT_DIR),
                    extension.default_service_config_path,
                )

    def test_cli_registry_exposes_supported_root_commands(self) -> None:
        commands = supported_root_commands()
        self.assertEqual(commands, ['control-plane', 'dispatch', 'docs', 'guards', 'images', 'runtime', 'setup'])
        self.assertNotIn('extension-view', commands)
        self.assertNotIn('release-flow', commands)

    def test_artifact_smoke_exposes_supported_lanes(self) -> None:
        parser = artifact_smoke.build_parser()
        subparsers = next(action for action in parser._actions if getattr(action, 'choices', None))
        self.assertEqual(sorted(subparsers.choices), ['base-kernel', 'platform-profile'])

    def test_platform_artifact_smoke_accepts_provider_only_registry_surface(self) -> None:
        stdout = io.StringIO()
        args = SimpleNamespace(
            config_path=ROOT_DIR / 'config' / 'control_plane' / 'profiles' / 'agent_platform.service.json',
            extension_id='agent_platform',
        )
        with redirect_stdout(stdout):
            exit_code = artifact_smoke.cmd_platform_profile(args)

        self.assertEqual(exit_code, 0, msg=stdout.getvalue())
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload['dispatchTargetRegistryPaths'], [])
        self.assertGreater(len(payload['dispatchProviderRegistryPaths']), 0)


if __name__ == '__main__':
    unittest.main()
