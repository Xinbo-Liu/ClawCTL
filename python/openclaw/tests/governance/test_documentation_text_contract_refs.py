from __future__ import annotations

import json
import unittest
from pathlib import Path

from openclaw.docs.support.text_contracts import check_text_contract, resolve_text_ref
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.contracts import repo_contract_relpath
from openclaw.tests.support.static_text_assertions import assert_static_text_absent


ROOT_DIR = resolve_repo_root(Path(__file__))


class DocumentationTextContractRefsTest(unittest.TestCase):
    def test_repo_contract_ref_resolves_to_current_relpath(self) -> None:
        token = resolve_text_ref({'kind': 'repo_contract', 'id': 'governance.docs_registry'})[0]

        self.assertEqual(token, repo_contract_relpath('governance.docs_registry'))

    def test_release_gate_check_ref_resolves_to_command_text(self) -> None:
        token = resolve_text_ref({'kind': 'release_gate_check', 'id': 'host_python_governance'})[0]

        self.assertEqual(token, 'bash ./scripts/doctor/check_host_python_governance.sh')

    def test_text_contract_checks_object_refs_without_literal_policy(self) -> None:
        content = 'See config/governance/docs/docs_registry.json and bash ./scripts/doctor/check_host_python_governance.sh.'
        errors = check_text_contract(
            rel_path='docs/example.md',
            content=content,
            contract={
                'requiredRefs': [
                    {'kind': 'repo_contract', 'id': 'governance.docs_registry'},
                    {'kind': 'release_gate_check', 'id': 'host_python_governance'},
                ],
                'forbiddenRefs': [
                    {'kind': 'script', 'path': 'scripts/doctor/run_repo_release_gate.sh'},
                ],
            },
            missing_label='missing',
            forbidden_label='forbidden',
        )

        self.assertEqual(errors, [])

    def test_text_contract_checks_multiple_primary_refs(self) -> None:
        errors = check_text_contract(
            rel_path='docs/example.md',
            content='alpha beta',
            contract={
                'requiredRefs': [
                    {'kind': 'literal', 'value': 'alpha'},
                    {'kind': 'literal', 'value': 'beta'},
                ],
            },
            missing_label='missing',
            forbidden_label='forbidden',
        )

        self.assertEqual(errors, [])

    def test_base_docs_registry_uses_current_text_contract_keys(self) -> None:
        registry = json.loads((ROOT_DIR / 'config' / 'governance' / 'docs' / 'docs_registry.json').read_text(encoding='utf-8'))
        serialized = json.dumps(registry, ensure_ascii=False)
        required_key = '"must' + 'Contain"'
        forbidden_key = '"must' + 'NotContain"'

        assert_static_text_absent(self, required_key, serialized)
        assert_static_text_absent(self, forbidden_key, serialized)
        self.assertIn('"requiredRefs"', serialized)
        self.assertIn('"forbiddenRefs"', serialized)


if __name__ == '__main__':
    unittest.main()
