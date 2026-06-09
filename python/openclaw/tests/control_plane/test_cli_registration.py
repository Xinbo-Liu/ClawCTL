from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr

from openclaw import cli as openclaw_cli
from openclaw.cli_registry import supported_control_plane_group_namespaces
from openclaw.control_plane.cli import PUBLIC_GROUP_COMMANDS, build_group_parser, build_parser


def _subparser_choices(parser) -> dict[str, object]:
    action = next(item for item in parser._actions if getattr(item, 'choices', None))
    return dict(action.choices)


class ControlPlaneCliRegistrationTest(unittest.TestCase):
    def test_control_plane_public_parser_only_exposes_group_namespaces(self) -> None:
        parser = build_parser()
        self.assertEqual(sorted(_subparser_choices(parser)), sorted(supported_control_plane_group_namespaces()))

    def test_validate_namespace_exposes_public_commands(self) -> None:
        parser = build_parser()
        validate_parser = _subparser_choices(parser)['validate']
        self.assertEqual(
            sorted(_subparser_choices(validate_parser)),
            ['agent-assembly', 'agent-control-plane', 'registry'],
        )

    def test_summary_namespace_exposes_readonly_tree(self) -> None:
        self.assertEqual(
            sorted(PUBLIC_GROUP_COMMANDS['summary']),
            [
                'agent-groups',
                'agent-module-pluggability',
                'agent-modules',
                'agents',
                'implementations',
                'job',
                'jobs',
                'models',
                'overview',
                'permission-policies',
                'runtime-adapters',
                'skill-sets',
                'targets',
                'toolsets',
            ],
        )

    def test_module_group_parser_keeps_profile_aware_scaffold_surface(self) -> None:
        parser = build_group_parser('module')
        scaffold_parser = _subparser_choices(parser)['scaffold-agent-module']
        control_plane_profile = next(action for action in scaffold_parser._actions if action.dest == 'control_plane_profile')
        self.assertIsNone(control_plane_profile.choices)

    def test_flat_runtime_command_path_is_invalid(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as exc:
                openclaw_cli.main(['control-plane', 'run-agent-runtime'])
        self.assertEqual(exc.exception.code, 2)
        self.assertIn('未知命令：control-plane run-agent-runtime', stderr.getvalue())


if __name__ == '__main__':
    unittest.main()
