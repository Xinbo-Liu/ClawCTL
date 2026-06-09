from __future__ import annotations

import unittest

from openclaw.doctor.agent_governance.repo_baseline import load_object_fact_drift_rules, validate_object_fact_surfaces


class RepoBaselineRulesTest(unittest.TestCase):
    def test_object_fact_checks_are_structured_sources(self) -> None:
        rules = {row['id']: row['source'] for row in load_object_fact_drift_rules()}

        self.assertEqual(rules['docs_registry_pages_exist'], 'config/governance/docs/docs_registry.json')
        self.assertEqual(rules['profile_registry_paths_exist'], 'config/control_plane/profile_registry.tsv')
        self.assertEqual(rules['managed_extension_index_layout'], 'agent/extensions/index.json')

    def test_validate_object_fact_surfaces_passes_on_repo(self) -> None:
        errors: list[str] = []
        validate_object_fact_surfaces(errors)
        self.assertEqual(errors, [], msg='\n'.join(errors))


if __name__ == '__main__':
    unittest.main()
