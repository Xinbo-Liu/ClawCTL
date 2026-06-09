from __future__ import annotations

import json
import tempfile
import tomllib
import unittest
from pathlib import Path

from openclaw.doctor.platform.architecture_import_guards import (
    ALLOWED_TOP_LEVEL_PACKAGE_DIRS,
    ALLOWED_TOP_LEVEL_PACKAGE_FILES,
    PACKAGE_LAYOUT_RULES,
    agent_authoring_package_marker_offenders,
    build_report,
    business_name_leak_offenders,
    business_name_leak_tokens,
)
from openclaw.lib.repo.layout import resolve_repo_root


ROOT_DIR = resolve_repo_root(Path(__file__))


class PythonPackageLayoutTest(unittest.TestCase):
    def test_tests_fixtures_package_marker_is_tracked(self) -> None:
        self.assertTrue((ROOT_DIR / 'python' / 'openclaw' / 'tests' / 'fixtures' / '__init__.py').is_file())

    def test_clean_tree_truth_keeps_required_tests_fixtures_subpackage(self) -> None:
        with tempfile.TemporaryDirectory(prefix='openclaw-layout-minimal-') as temp_dir:
            export_root = Path(temp_dir).resolve()
            package_root = export_root / 'python' / 'openclaw'
            for name in ALLOWED_TOP_LEVEL_PACKAGE_DIRS:
                (package_root / name).mkdir(parents=True, exist_ok=True)
            for name in ALLOWED_TOP_LEVEL_PACKAGE_FILES:
                (package_root / name).write_text('', encoding='utf-8')
            for rule in PACKAGE_LAYOUT_RULES:
                base = export_root / str(rule['rel_path'])
                base.mkdir(parents=True, exist_ok=True)
                (base / '__init__.py').write_text('', encoding='utf-8')
                for name in rule['required_dirs']:
                    target = base / str(name)
                    target.mkdir(parents=True, exist_ok=True)
                    (target / '__init__.py').write_text('', encoding='utf-8')
            payload = build_report(export_root)

        missing_fixtures = [
            offender
            for offender in payload.get('layoutOffenders', [])
            if str(offender).startswith('tests: missing required subpackages')
            and 'fixtures' in str(offender).split('->', 1)[-1]
        ]
        self.assertEqual(missing_fixtures, [])

    def test_package_layout_guard_passes_on_repo(self) -> None:
        payload = build_report(ROOT_DIR)
        self.assertEqual(payload['topLevelPackageLayoutOffenders'], [])
        self.assertEqual(payload['layoutOffenders'], [])
        self.assertEqual(payload['moduleImportOffenders'], [])
        self.assertEqual(payload['libReverseDependencyOffenders'], [])
        self.assertEqual(payload['forbiddenTopLevelPackageOffenders'], [])
        self.assertEqual(payload['sysPathMutationOffenders'], [])
        self.assertEqual(payload['repoRootResolverOffenders'], [])
        self.assertEqual(payload['registryValidationImportOffenders'], [])
        self.assertEqual(payload['agentAuthoringPackageMarkerOffenders'], [])
        self.assertEqual(payload['businessNameLeakOffenders'], [])

    def test_business_name_leak_guard_scans_core_surfaces(self) -> None:
        with tempfile.TemporaryDirectory(prefix='openclaw-business-leak-') as temp_dir:
            repo_root = Path(temp_dir)
            index_path = repo_root / 'agent' / 'extensions' / 'index.json'
            package_dir = repo_root / 'agent' / 'extensions' / 'agent_marketprobe' / 'python' / 'openclaw_ext_marketprobe'
            leaked = repo_root / 'python' / 'openclaw' / 'lib' / 'leak.py'
            package_dir.mkdir(parents=True)
            index_path.parent.mkdir(parents=True, exist_ok=True)
            leaked.parent.mkdir(parents=True)
            (package_dir / '__init__.py').write_text('', encoding='utf-8')
            index_path.write_text(
                json.dumps(
                    {
                        'extensions': [
                            {
                                'id': 'agent_marketprobe',
                                'title': 'Market Probe',
                                'rootDir': 'agent/extensions/agent_marketprobe',
                                'defaultServiceConfigPath': (
                                    'agent/extensions/agent_marketprobe/config/control_plane/profiles/agent_marketprobe.service.json'
                                ),
                                'manifestDir': 'agent/extensions/agent_marketprobe/config/control_plane/extensions.d',
                                'pythonRoots': ['agent/extensions/agent_marketprobe/python'],
                                'status': 'managed_explicit_extension',
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding='utf-8',
            )
            leaked.write_text("TOKENS = 'agent_marketprobe openclaw_ext_marketprobe marketprobe'\n", encoding='utf-8')

            tokens = business_name_leak_tokens(repo_root)
            offenders = business_name_leak_offenders(repo_root)

        self.assertEqual(tokens, ('agent_marketprobe', 'openclaw_ext_marketprobe', 'marketprobe'))
        self.assertEqual(
            offenders,
            ['python/openclaw/lib/leak.py: agent_marketprobe, openclaw_ext_marketprobe, marketprobe'],
        )

    def test_agent_authoring_surfaces_are_not_python_packages(self) -> None:
        with tempfile.TemporaryDirectory(prefix='openclaw-agent-authoring-') as temp_dir:
            repo_root = Path(temp_dir)
            blocked_agent_marker = repo_root / 'agent' / '__init__.py'
            blocked_module_marker = repo_root / 'agent' / 'extensions' / 'agent_probe' / 'agent' / 'modules' / 'alpha_probe' / '__init__.py'
            allowed_python_marker = repo_root / 'agent' / 'extensions' / 'agent_probe' / 'python' / 'openclaw_ext_probe' / '__init__.py'
            allowed_tests_marker = repo_root / 'agent' / 'extensions' / 'agent_probe' / 'tests' / '__init__.py'
            for marker in (blocked_agent_marker, blocked_module_marker, allowed_python_marker, allowed_tests_marker):
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text('', encoding='utf-8')

            offenders = agent_authoring_package_marker_offenders(repo_root)

        self.assertEqual(
            offenders,
            [
                'agent authoring surface must not be a Python package -> agent/__init__.py',
                'agent authoring surface must not be a Python package -> agent/extensions/agent_probe/agent/modules/alpha_probe/__init__.py',
            ],
        )

    def test_placeholder_top_level_packages_are_absent(self) -> None:
        for rel_path in (
            'python/openclaw/domains',
            'python/openclaw/extensions',
            'python/openclaw/modules',
        ):
            self.assertFalse((ROOT_DIR / rel_path).exists(), msg=rel_path)

    def test_distribution_excludes_test_packages(self) -> None:
        payload = tomllib.loads((ROOT_DIR / 'pyproject.toml').read_text(encoding='utf-8'))
        excludes = (
            payload.get('tool', {})
            .get('setuptools', {})
            .get('packages', {})
            .get('find', {})
            .get('exclude', [])
        )

        self.assertIn('openclaw.tests', excludes)
        self.assertIn('openclaw.tests.*', excludes)

    def test_architecture_docs_register_package_layout_contract(self) -> None:
        architecture_readme = (ROOT_DIR / 'docs' / 'architecture' / 'README.md').read_text(encoding='utf-8')
        docs_registry = json.loads((ROOT_DIR / 'config' / 'governance' / 'docs' / 'docs_registry.json').read_text(encoding='utf-8'))
        page_paths = {str(page.get('path') or '') for page in docs_registry.get('pages') or []}

        self.assertIn('python-package-layout.md', architecture_readme)
        self.assertIn('docs/architecture/python-package-layout.md', page_paths)


if __name__ == '__main__':
    unittest.main()
