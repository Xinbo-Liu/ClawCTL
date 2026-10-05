from __future__ import annotations

import json
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from openclaw.doctor.platform.architecture_import_guards import (
    ALLOWED_TOP_LEVEL_PACKAGE_DIRS,
    ALLOWED_TOP_LEVEL_PACKAGE_FILES,
    PACKAGE_LAYOUT_RULES,
    agent_authoring_package_marker_offenders,
    build_report,
    business_name_leak_offenders,
    business_name_leak_tokens,
    extension_import_boundary_offenders,
    public_provider_import_prefixes,
)
from openclaw.lib.repo.layout import relative_path_within_root, resolve_repo_root


ROOT_DIR = resolve_repo_root(Path(__file__))


def _write_extension_fixture(
    repo_root: Path,
    *,
    extension_id: str,
    package_name: str,
    provider_registry: bool,
    dependencies: tuple[str, ...] = (),
    source: str = '',
) -> None:
    root_dir = repo_root / 'agent' / 'extensions' / extension_id
    package_dir = root_dir / 'python' / package_name
    manifest_dir = root_dir / 'config' / 'control_plane' / 'extensions.d'
    profile_dir = root_dir / 'config' / 'control_plane' / 'profiles'
    package_dir.mkdir(parents=True, exist_ok=True)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / '__init__.py').write_text('', encoding='utf-8')
    (package_dir / 'module.py').write_text(source or 'VALUE = 1\n', encoding='utf-8')
    registry: dict[str, object] = {}
    if provider_registry:
        provider_dir = package_dir / 'providers'
        provider_dir.mkdir(parents=True, exist_ok=True)
        (provider_dir / '__init__.py').write_text('', encoding='utf-8')
        registry['dispatchProviderRegistryPaths'] = [
            '@extension/agent/control_plane/registries/dispatch_provider_adapters.json',
        ]
    manifest = {
        'id': extension_id,
        'dependencies': [{'id': dependency_id} for dependency_id in dependencies],
        'registry': registry,
    }
    (manifest_dir / f'{extension_id}.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    (profile_dir / f'{extension_id}.service.json').write_text('{}\n', encoding='utf-8')


def _write_extension_index(repo_root: Path, extension_ids: tuple[str, ...]) -> None:
    rows = []
    for extension_id in extension_ids:
        rows.append(
            {
                'id': extension_id,
                'title': extension_id,
                'rootDir': f'agent/extensions/{extension_id}',
                'defaultServiceConfigPath': f'agent/extensions/{extension_id}/config/control_plane/profiles/{extension_id}.service.json',
                'manifestDir': f'agent/extensions/{extension_id}/config/control_plane/extensions.d',
                'pythonRoots': [f'agent/extensions/{extension_id}/python'],
                'status': 'managed_explicit_extension',
            }
        )
    index_path = repo_root / 'agent' / 'extensions' / 'index.json'
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps({'extensions': rows}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


class PythonPackageLayoutTest(unittest.TestCase):
    def test_repo_relative_path_fallback_uses_filesystem_identity(self) -> None:
        with tempfile.TemporaryDirectory(prefix='openclaw-relative-alias-') as temp_dir:
            root = Path(temp_dir)
            target = root / 'agent' / 'module.py'
            target.parent.mkdir(parents=True)
            target.write_text('', encoding='utf-8')
            with mock.patch.object(Path, 'relative_to', side_effect=ValueError('synthetic path alias')):
                relative = relative_path_within_root(target, root)

        self.assertEqual(relative.as_posix(), 'agent/module.py')

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

    def test_public_provider_import_allowance_is_manifest_derived(self) -> None:
        with tempfile.TemporaryDirectory(prefix='openclaw-provider-import-') as temp_dir:
            repo_root = Path(temp_dir)
            _write_extension_index(repo_root, ('agent_provider', 'agent_consumer'))
            _write_extension_fixture(
                repo_root,
                extension_id='agent_provider',
                package_name='openclaw_ext_provider',
                provider_registry=True,
            )
            _write_extension_fixture(
                repo_root,
                extension_id='agent_consumer',
                package_name='openclaw_ext_consumer',
                provider_registry=False,
                dependencies=('agent_provider',),
                source='from openclaw_ext_provider.providers import feishu\n',
            )

            prefixes = public_provider_import_prefixes(repo_root)
            offenders = extension_import_boundary_offenders(repo_root)

        self.assertEqual(prefixes, {'agent_provider': ('openclaw_ext_provider.providers',)})
        self.assertEqual(offenders, [])

    def test_cross_extension_import_rejects_undeclared_or_internal_packages(self) -> None:
        with tempfile.TemporaryDirectory(prefix='openclaw-provider-import-blocked-') as temp_dir:
            repo_root = Path(temp_dir)
            _write_extension_index(repo_root, ('agent_plain', 'agent_provider', 'agent_consumer'))
            _write_extension_fixture(
                repo_root,
                extension_id='agent_plain',
                package_name='openclaw_ext_plain',
                provider_registry=False,
            )
            _write_extension_fixture(
                repo_root,
                extension_id='agent_provider',
                package_name='openclaw_ext_provider',
                provider_registry=True,
            )
            _write_extension_fixture(
                repo_root,
                extension_id='agent_consumer',
                package_name='openclaw_ext_consumer',
                provider_registry=False,
                dependencies=('agent_provider',),
                source='\n'.join([
                    'from openclaw_ext_plain.providers import webhook',
                    'from openclaw_ext_provider.modules import internal',
                    '',
                ]),
            )

            offenders = extension_import_boundary_offenders(repo_root)

        self.assertEqual(
            offenders,
            [
                'agent/extensions/agent_consumer/python/openclaw_ext_consumer/module.py: imports openclaw_ext_plain.providers from agent_plain',
                'agent/extensions/agent_consumer/python/openclaw_ext_consumer/module.py: imports openclaw_ext_provider.modules from agent_provider',
            ],
        )

    def test_public_provider_import_requires_consumer_dependency_and_checks_each_alias(self) -> None:
        with tempfile.TemporaryDirectory(prefix='openclaw-provider-import-aliases-') as temp_dir:
            repo_root = Path(temp_dir)
            _write_extension_index(repo_root, ('agent_provider', 'agent_consumer'))
            _write_extension_fixture(
                repo_root,
                extension_id='agent_provider',
                package_name='openclaw_ext_provider',
                provider_registry=True,
            )
            _write_extension_fixture(
                repo_root,
                extension_id='agent_consumer',
                package_name='openclaw_ext_consumer',
                provider_registry=False,
                source='import openclaw_ext_provider.providers.feishu, openclaw_ext_provider.modules.internal\n',
            )

            offenders = extension_import_boundary_offenders(repo_root)

        self.assertEqual(
            offenders,
            [
                (
                    'agent/extensions/agent_consumer/python/openclaw_ext_consumer/module.py: '
                    'imports openclaw_ext_provider.providers.feishu from agent_provider without required dependency'
                ),
                (
                    'agent/extensions/agent_consumer/python/openclaw_ext_consumer/module.py: '
                    'imports openclaw_ext_provider.modules.internal from agent_provider'
                ),
            ],
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
