from __future__ import annotations

import unittest

from openclaw.doctor.agent_governance.registry_alignment import _registry_views, validate_resolved_registry_views


class RegistryAlignmentTest(unittest.TestCase):
    def test_registry_views_accept_qualified_indexes_for_owned_rows(self) -> None:
        views = _registry_views(
            {
                'agentsById': {},
                'agentsByQualifiedId': {
                    'agent_demo:agent.demo': {
                        'id': 'agent.demo',
                        'qualifiedId': 'agent_demo:agent.demo',
                        'resolvedModuleRef': 'agent_demo:module.demo',
                    },
                },
                'agentGroupsById': {},
                'agentGroupsByQualifiedId': {
                    'agent_demo:group.demo': {
                        'id': 'group.demo',
                        'qualifiedId': 'agent_demo:group.demo',
                        'resolvedMembers': ['agent_demo:agent.demo'],
                    },
                },
                'agentModulesById': {},
                'agentModulesByQualifiedId': {
                    'agent_demo:module.demo': {
                        'id': 'module.demo',
                        'qualifiedId': 'agent_demo:module.demo',
                        'sourcePath': '/tmp/module.demo/module.json',
                    },
                },
            }
        )

        self.assertIn('agent_demo:agent.demo', views['agentsById'])
        self.assertIn('agent_demo:group.demo', views['groupsById'])
        self.assertIn('agent_demo:module.demo', views['modulesById'])

    def test_agent_module_dir_resolution_error_is_readable(self) -> None:
        errors: list[str] = []

        checked_modules = validate_resolved_registry_views(
            {
                'agents': [{'id': 'agent.demo', 'resolvedModuleRef': 'module.demo'}],
                'agentsById': {},
                'groupsById': {},
                'modulesById': {'module.demo': {}},
            },
            errors,
        )

        self.assertEqual(checked_modules, ['module.demo'])
        self.assertEqual(
            errors,
            ['agent agent.demo 模块目录解析失败：module module.demo is missing resolved sourcePath'],
        )

    def test_group_member_module_dir_resolution_error_is_readable(self) -> None:
        errors: list[str] = []

        checked_modules = validate_resolved_registry_views(
            {
                'agents': [],
                'agentsById': {'agent.demo': {'id': 'agent.demo', 'resolvedModuleRef': 'module.demo'}},
                'groupsById': {
                    'group.demo': {
                        'extensionId': 'agent_demo',
                        'resolvedMembers': ['agent.demo'],
                    }
                },
                'modulesById': {'module.demo': {}},
            },
            errors,
        )

        self.assertEqual(checked_modules, [])
        self.assertEqual(
            errors,
            ['group group.demo 成员模块目录解析失败：module module.demo is missing resolved sourcePath'],
        )


if __name__ == '__main__':
    unittest.main()
