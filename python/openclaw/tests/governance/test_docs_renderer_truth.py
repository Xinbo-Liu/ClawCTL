from __future__ import annotations

import json
import unittest
from pathlib import Path

from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.static_truth import repo_contract_path
from openclaw.tests.support.static_text_assertions import assert_static_text_absent


ROOT_DIR = resolve_repo_root(Path(__file__))
GETTING_STARTED_RENDERER = ROOT_DIR / 'python' / 'openclaw' / 'docs' / 'renderers' / 'getting_started.py'
GETTING_STARTED_SECTIONS = ROOT_DIR / 'config' / 'governance' / 'docs' / 'getting_started_sections.json'
DEPLOYMENT_INPUTS_RENDERER = ROOT_DIR / 'python' / 'openclaw' / 'setup' / 'deploy_env' / 'docs.py'
DEPLOYMENT_INPUTS_SECTIONS = ROOT_DIR / 'config' / 'deploy_env' / 'doc_sections.json'
RUNTIME_SURFACE_RENDERER = ROOT_DIR / 'python' / 'openclaw' / 'docs' / 'renderers' / 'runtime_surface.py'
REFERENCE_SPECS_RENDERER = ROOT_DIR / 'python' / 'openclaw' / 'docs' / 'support' / 'reference_specs.py'


class DocsRendererTruthTest(unittest.TestCase):
    def test_getting_started_renderer_uses_sections_truth_for_problem_intro(self) -> None:
        source = GETTING_STARTED_RENDERER.read_text(encoding='utf-8')
        payload = json.loads(GETTING_STARTED_SECTIONS.read_text(encoding='utf-8'))
        truth_line = payload['quickstart']['problem_paragraphs'][0]

        self.assertIn(truth_line, GETTING_STARTED_SECTIONS.read_text(encoding='utf-8'))
        assert_static_text_absent(self, truth_line, source)

    def test_deployment_inputs_renderer_uses_doc_sections_truth_for_cross_instance_guidance(self) -> None:
        source = DEPLOYMENT_INPUTS_RENDERER.read_text(encoding='utf-8')
        payload = json.loads(DEPLOYMENT_INPUTS_SECTIONS.read_text(encoding='utf-8'))
        truth_line = payload['cross_instance_access_scenario'][0]

        self.assertIn(truth_line, DEPLOYMENT_INPUTS_SECTIONS.read_text(encoding='utf-8'))
        assert_static_text_absent(self, truth_line, source)

    def test_runtime_surface_module_does_not_embed_runtime_entrypoints_intro_truth(self) -> None:
        source = RUNTIME_SURFACE_RENDERER.read_text(encoding='utf-8')
        payload = json.loads(repo_contract_path('governance.runtime_entrypoints').read_text(encoding='utf-8'))
        truth_line = payload['intro'][0]

        self.assertIn(truth_line, repo_contract_path('governance.runtime_entrypoints').read_text(encoding='utf-8'))
        assert_static_text_absent(self, truth_line, source)

    def test_reference_specs_module_does_not_embed_router_surface_truth(self) -> None:
        source = REFERENCE_SPECS_RENDERER.read_text(encoding='utf-8')
        payload = json.loads(repo_contract_path('governance.router_route_surface').read_text(encoding='utf-8'))
        truth_line = payload['description']

        self.assertIn(truth_line, repo_contract_path('governance.router_route_surface').read_text(encoding='utf-8'))
        assert_static_text_absent(self, truth_line, source)


if __name__ == '__main__':
    unittest.main()
