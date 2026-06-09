from __future__ import annotations

import json
import unittest
from pathlib import Path

from openclaw.lib.repo.layout import resolve_repo_root


ROOT_DIR = resolve_repo_root(Path(__file__))
SURFACE_PATH = ROOT_DIR / 'config' / 'control_plane' / 'extensions.d' / 'agent_platform.dispatch_operations_surface.json'
OBSERVABILITY_SURFACE_PATH = ROOT_DIR / 'config' / 'governance' / 'docs' / 'dispatch_observability_surface.json'
MANAGED_DISPATCH_LITERALS = (
    'dispatch_validation',
    'dispatch_primary',
    'dispatch_ops',
    'validation_only',
    'rotation_default',
    'production_required',
)


class DispatchOperationsSurfaceExamplesTest(unittest.TestCase):
    def test_default_dry_run_examples_are_explicit_commands(self) -> None:
        payload = json.loads(SURFACE_PATH.read_text(encoding='utf-8'))
        entry = payload['entries']['dispatch_target_default_dry_run']
        commands = [str(item.get('command') or '') for item in list(entry.get('example_commands') or []) if isinstance(item, dict)]

        self.assertTrue(commands)
        self.assertTrue(all('| bash' not in command for command in commands))
        self.assertIn('bash ./scripts/runtime/run_openclaw_python_tool.sh dispatch ops run-target-operation', commands[0])
        self.assertIn('--operation send --target <target_id> --env-file deploy/.env --control-plane-profile <profile_id> --ensure-running strict -- --dry-run true', commands[0])
        self.assertIn('--operation retry --target <target_id> --env-file deploy/.env --control-plane-profile <profile_id> --ensure-running strict -- --dry-run true', commands[0])
        self.assertIn('--operation resend --target <target_id> --env-file deploy/.env --control-plane-profile <profile_id> --ensure-running strict -- --dry-run true', commands[0])
        self.assertNotIn('scripts/control_plane/run_registered_target_operation.sh', commands[0])

    def test_step_commands_are_not_adjacent_duplicates(self) -> None:
        payload = json.loads(SURFACE_PATH.read_text(encoding='utf-8'))
        for entry_id, entry in payload['entries'].items():
            steps = [str(item) for item in list(entry.get('steps') or [])]
            with self.subTest(entry=entry_id):
                for left, right in zip(steps, steps[1:]):
                    self.assertNotEqual(left, right)

    def test_generic_dispatch_surfaces_do_not_embed_managed_targets(self) -> None:
        for surface_path in (SURFACE_PATH, OBSERVABILITY_SURFACE_PATH):
            with self.subTest(path=str(surface_path.relative_to(ROOT_DIR))):
                text = surface_path.read_text(encoding='utf-8')
                for literal in MANAGED_DISPATCH_LITERALS:
                    self.assertNotIn(literal, text)


if __name__ == '__main__':
    unittest.main()
