from __future__ import annotations

import unittest
from typing import Any

from openclaw.control_plane.modules.scaffold import scaffold_agent_module
from openclaw.control_plane.registry import load_registry
from openclaw.control_plane.surfaces import load_testing_manifest
from openclaw.doctor.agent_modules.managed_probe_fixture import (
    PROBE_CHECK_ID,
    PROBE_EXTENSION_ID,
    PROBE_OWNER_DOMAIN,
    PROBE_PRIMARY_MODULE_REF,
    PROBE_TEST_GROUP_ID,
)
from openclaw.doctor.agent_modules.managed_probe_fixture_scaffold import module_main
from openclaw.lib.repo.managed_extensions import load_managed_extensions_index, managed_extension_layout_for_config_path
from openclaw.tests.support.managed_probe import managed_probe_repo


class ManagedProbeFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._fixture_context = managed_probe_repo('managed-probe-fixture-shared')
        cls.fixture = cls._fixture_context.__enter__()
        cls.addClassCleanup(cls._fixture_context.__exit__, None, None, None)

    fixture: Any

    def test_materialized_probe_extension_registers_index_and_layout(self) -> None:
        fixture = self.fixture

        rows = {row.id: row for row in load_managed_extensions_index(fixture.repo_root)}
        self.assertIn(PROBE_EXTENSION_ID, rows)
        self.assertEqual(rows[PROBE_EXTENSION_ID].default_service_config_path, fixture.service_path)

        layout = managed_extension_layout_for_config_path(fixture.service_path, start_path=fixture.repo_root)
        self.assertIsNotNone(layout)
        assert layout is not None
        self.assertEqual(layout.row.id, PROBE_EXTENSION_ID)
        self.assertEqual(layout.module_root, fixture.package_root / 'agent' / 'modules')
        self.assertEqual(layout.python_root, fixture.python_root)
        self.assertEqual(layout.python_package_dir, fixture.python_package_dir)

    def test_materialized_probe_extension_exposes_testing_manifest_contract(self) -> None:
        payload = load_testing_manifest(config_path=self.fixture.service_path)
        groups = {
            str(row.get('id') or '').strip(): row
            for row in payload.get('groups') or []
            if isinstance(row, dict)
        }
        checks = {
            str(row.get('id') or '').strip(): row
            for row in payload.get('checks') or []
            if isinstance(row, dict)
        }

        self.assertEqual(groups[PROBE_TEST_GROUP_ID]['extensionId'], PROBE_EXTENSION_ID)
        self.assertEqual(checks[PROBE_CHECK_ID]['extensionId'], PROBE_EXTENSION_ID)
        self.assertIn(PROBE_CHECK_ID, (payload.get('acceptance_reference') or {}).get('required_checks') or [])

    def test_materialized_probe_extension_supports_registry_load_and_scaffold(self) -> None:
        fixture = self.fixture
        payload = scaffold_agent_module(
            repo_root=fixture.repo_root,
            config_path=fixture.service_path,
            module_ref='alpha_probe',
            title='Alpha Probe',
            owner_domain=PROBE_OWNER_DOMAIN,
            operation_ref='probe_default',
        )

        self.assertIn('agent/extensions/agent_probe/agent/modules/alpha_probe', payload['moduleDir'].replace('\\', '/'))
        updated_registry = load_registry(fixture.service_path)
        self.assertIn(PROBE_PRIMARY_MODULE_REF, updated_registry.get('agentModulesById') or {})
        self.assertIn('alpha_probe', updated_registry.get('agentModulesById') or {})

    def test_managed_probe_repo_returns_isolated_snapshot_copies(self) -> None:
        fixture_a = self.fixture
        original_text = fixture_a.primary_main_path.read_text(encoding='utf-8')
        try:
            with managed_probe_repo('managed-probe-snapshot-b') as fixture_b:
                fixture_a.primary_main_path.write_text('mutated\n', encoding='utf-8')

                self.assertNotEqual(fixture_a.repo_root, fixture_b.repo_root)
                self.assertEqual(
                    fixture_b.primary_main_path.read_text(encoding='utf-8'),
                    module_main(PROBE_PRIMARY_MODULE_REF, 'probe_dispatcher'),
                )
        finally:
            fixture_a.primary_main_path.write_text(original_text, encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
