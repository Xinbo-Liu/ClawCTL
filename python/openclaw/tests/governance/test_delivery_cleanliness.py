from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.managed_extensions import ManagedExtensionError
from openclaw.lib.repo.absent_surfaces import ABSENT_SURFACES_PATH, AbsentSurface
from openclaw.doctor.agent_modules.managed_probe_fixture import PROBE_OWNER_DOMAIN
from openclaw.doctor.platform.architecture_import_guards import business_name_leak_tokens
from openclaw.tests.support.helpers import isolated_test_root
from openclaw.tests.support.managed_extensions import managed_extensions, current_managed_extension_domain_id
from openclaw.tests.support.managed_probe import managed_probe_repo

from openclaw.doctor.release.delivery_cleanliness import (
    RULES_PATH,
    load_managed_extension_rules,
    load_rules,
    managed_extension_boundary_absent_paths,
    scan_managed_extensions,
    scan_repo,
)
from openclaw.doctor.release.repo_release_gate import base_checks
from openclaw.tests.support.static_text_assertions import assert_static_text_absent

ROOT_DIR = resolve_repo_root(Path(__file__))


class DeliveryCleanlinessTest(unittest.TestCase):
    _rules: tuple[AbsentSurface, ...] | None = None
    _repo_violations: object | None = None
    _managed_violations: object | None = None
    _test_source_rows: tuple[tuple[str, str], ...] | None = None

    @classmethod
    def _rules_for_repo(cls) -> tuple[AbsentSurface, ...]:
        if cls._rules is None:
            cls._rules = load_rules(RULES_PATH)
        return cls._rules

    @classmethod
    def _repo_scan_violations(cls):
        if cls._repo_violations is None:
            cls._repo_violations = scan_repo(ROOT_DIR, cls._rules_for_repo())
        return cls._repo_violations

    @classmethod
    def _managed_scan_violations(cls):
        if cls._managed_violations is None:
            if cls._repo_violations == []:
                cls._managed_violations = []
                return cls._managed_violations
            cls._managed_violations = scan_managed_extensions(ROOT_DIR, cls._rules_for_repo())
        return cls._managed_violations

    @classmethod
    def _repo_test_sources(cls, *, exclude: Path | None = None) -> tuple[tuple[str, str], ...]:
        if cls._test_source_rows is None:
            rows: list[tuple[str, str]] = []
            for path in sorted((ROOT_DIR / 'python' / 'openclaw' / 'tests').rglob('test_*.py')):
                rows.append((
                    path.resolve().as_posix(),
                    path.read_text(encoding='utf-8'),
                ))
            cls._test_source_rows = tuple(rows)
        exclude_key = exclude.resolve().as_posix() if exclude is not None else ''
        return tuple(
            (path_text, source)
            for path_text, source in cls._test_source_rows
            if path_text != exclude_key
        )

    def _write_minimal_managed_extension_index(self, repo_root: Path) -> None:
        extension_root = repo_root / 'agent' / 'extensions' / 'agent_probe'
        manifest_dir = extension_root / 'config' / 'control_plane' / 'extensions.d'
        python_package = extension_root / 'python' / 'openclaw_ext_probe'
        (extension_root / 'agent' / 'modules').mkdir(parents=True)
        manifest_dir.mkdir(parents=True)
        python_package.mkdir(parents=True)
        (python_package / '__init__.py').write_text('', encoding='utf-8')
        (repo_root / 'agent' / 'extensions').mkdir(parents=True, exist_ok=True)
        (repo_root / 'agent' / 'extensions' / 'index.json').write_text(
            json.dumps(
                {
                    'extensions': [
                        {
                            'id': 'agent_probe',
                            'title': 'Probe Managed Extension',
                            'rootDir': 'agent/extensions/agent_probe',
                            'defaultServiceConfigPath': 'agent/extensions/agent_probe/config/control_plane/profiles/agent_probe.service.json',
                            'manifestDir': 'agent/extensions/agent_probe/config/control_plane/extensions.d',
                            'pythonRoots': ['agent/extensions/agent_probe/python'],
                            'status': 'managed_explicit_extension',
                        }
                    ]
                },
                ensure_ascii=False,
                indent=2,
            )
            + '\n',
            encoding='utf-8',
        )
        (manifest_dir / 'agent_probe.json').write_text(
            json.dumps({'id': 'agent_probe', 'title': 'Probe Managed Extension'}, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8',
        )

    def test_absent_platform_surfaces_do_not_exist(self) -> None:
        extensions = managed_extensions(ROOT_DIR)
        static_absent_paths = (
            'config/control_plane/references',
            'config/control_plane/samples',
            'agent/control_plane/registries/dispatch_targets.json',
            'scripts/doctor/doctor_pipeline_chain.sh',
            'python/openclaw/control_plane/cli_support/parser.py',
            'python/openclaw/control_plane/cli_support/parser_specs.py',
            'python/openclaw/doctor/platform/config_selection.py',
        )
        for rel_path in static_absent_paths:
            self.assertFalse((ROOT_DIR / rel_path).exists(), msg=rel_path)
        for extension in extensions:
            domain_id = current_managed_extension_domain_id(ROOT_DIR, extension_id=extension.id)
            for rel_path in (
                f'config/control_plane/profiles/{extension.id}.service.json',
                f'python/openclaw/extensions/{extension.id}',
                f'python/openclaw/domains/{domain_id}',
            ):
                with self.subTest(extension=extension.id, path=rel_path):
                    self.assertFalse((ROOT_DIR / rel_path).exists(), msg=rel_path)

    def test_delivery_cleanliness_rules_pass_on_repo(self) -> None:
        violations = self._repo_scan_violations()
        self.assertEqual(violations, [])

    def test_managed_extension_readme_uses_extension_scoped_authoring_terms(self) -> None:
        extensions = managed_extensions(ROOT_DIR)

        if not extensions:
            self.skipTest('base release surface has no repo-managed extension')
        for extension in extensions:
            with self.subTest(extension=extension.id):
                readme = (extension.root_dir / 'README.md').read_text(encoding='utf-8')
                assert_static_text_absent(self, '模块清单与 bridge', readme)
                self.assertIn('扩展包内模块声明与 control-plane 对象', readme)

    def test_tests_do_not_create_transient_artifacts_under_real_extension_roots(self) -> None:
        offenders: list[str] = []
        current_file = Path(__file__).resolve()
        extension_tmp_markers = tuple(
            f".tmp-{extension.id.replace('_', '-')}-cli-smoke"
            for extension in managed_extensions(ROOT_DIR)
        )
        for path_text, source in self._repo_test_sources(exclude=current_file):
            path = Path(path_text)
            for marker in ("extension_root / '.tmp", 'extension_root / ".tmp', "ROOT_DIR / 'tmp-", 'ROOT_DIR / "tmp-'):
                if marker in source:
                    offenders.append(f'{path.relative_to(ROOT_DIR)}: {marker}')
            for marker in extension_tmp_markers:
                if marker in source:
                    offenders.append(f'{path.relative_to(ROOT_DIR)}: {marker}')

        self.assertEqual(offenders, [])

    def test_default_tests_do_not_reintroduce_expensive_global_fixtures(self) -> None:
        offenders: list[str] = []
        forbidden_markers = (
            'git archive',
            'copytree(ROOT_DIR',
            'shutil.copytree(ROOT_DIR',
            "openclaw.testing.repo_unittest', '--quiet'",
            'openclaw.testing.repo_unittest", "--quiet"',
        )
        current_file = Path(__file__).resolve()
        for path_text, source in self._repo_test_sources(exclude=current_file):
            path = Path(path_text)
            rel_path = path.relative_to(ROOT_DIR).as_posix()
            for marker in forbidden_markers:
                if marker in source:
                    offenders.append(f'{rel_path}: {marker}')

        self.assertEqual(offenders, [])

    def test_platform_tests_do_not_use_positional_managed_extension_selection(self) -> None:
        offenders: list[str] = []
        current_file = Path(__file__).resolve()
        forbidden_markers = (
            'load_managed_extensions_index(ROOT_DIR)[0]',
            'current_managed_extension(ROOT_DIR)',
        )
        for path_text, source in self._repo_test_sources(exclude=current_file):
            path = Path(path_text)
            for marker in forbidden_markers:
                if marker in source:
                    offenders.append(f'{path.relative_to(ROOT_DIR)}: {marker}')

        self.assertEqual(offenders, [])

    def test_platform_tests_do_not_embed_current_managed_extension_business_names(self) -> None:
        offenders: list[str] = []
        tokens = business_name_leak_tokens(ROOT_DIR)
        if not tokens:
            self.skipTest('base release surface has no repo-managed extension business names')
        for path_text, source in self._repo_test_sources():
            path = Path(path_text)
            leaked = [token for token in tokens if token in source]
            if leaked:
                offenders.append(f'{path.relative_to(ROOT_DIR)}: {", ".join(leaked)}')

        self.assertEqual(offenders, [])

    def test_delivery_cleanliness_rules_cover_static_absent_surface_paths(self) -> None:
        self.assertEqual(RULES_PATH, ABSENT_SURFACES_PATH)
        surfaces = {rule.id: rule for rule in self._rules_for_repo()}
        self.assertIn(
            'python/openclaw/control_plane/cli_support/parser.py',
            surfaces['control_plane_cli_support_parser'].paths,
        )
        self.assertIn(
            'python/openclaw/control_plane/cli_support/parser_specs.py',
            surfaces['control_plane_cli_support_parser_specs'].paths,
        )
        self.assertIn(
            'python/openclaw/doctor/platform/config_selection.py',
            surfaces['doctor_platform_config_selection'].paths,
        )
        self.assertIn(
            'agent/control_plane/registries/dispatch_targets.json',
            surfaces['root_business_dispatch_targets_registry'].paths,
        )

    def test_managed_extension_absent_surface_paths_are_derived_from_index(self) -> None:
        expected_paths = {path for path, _reason in managed_extension_boundary_absent_paths(ROOT_DIR)}

        for extension in managed_extensions(ROOT_DIR):
            domain_id = current_managed_extension_domain_id(ROOT_DIR, extension_id=extension.id)
            with self.subTest(extension=extension.id):
                self.assertIn(f'config/control_plane/profiles/{extension.id}.service.json', expected_paths)
                self.assertIn(f'python/openclaw/extensions/{extension.id}', expected_paths)
                self.assertIn(f'python/openclaw/domains/{domain_id}', expected_paths)

    def test_managed_extension_scan_flags_derived_boundary_paths(self) -> None:
        with managed_probe_repo('delivery-cleanliness-derived-boundary') as fixture:
            blocked_profile = fixture.repo_root / 'config' / 'control_plane' / 'profiles' / f'{fixture.extension_id}.service.json'
            blocked_package = fixture.repo_root / 'python' / 'openclaw' / 'extensions' / fixture.extension_id
            blocked_domain = fixture.repo_root / 'python' / 'openclaw' / 'domains' / PROBE_OWNER_DOMAIN
            blocked_dispatch_targets = fixture.python_package_dir / 'domains' / PROBE_OWNER_DOMAIN / 'dispatch' / 'targets.py'
            blocked_profile.parent.mkdir(parents=True, exist_ok=True)
            blocked_profile.write_text('{}\n', encoding='utf-8')
            blocked_package.mkdir(parents=True)
            blocked_domain.mkdir(parents=True)
            blocked_dispatch_targets.parent.mkdir(parents=True)
            blocked_dispatch_targets.write_text('# boundary dispatch target resolver\n', encoding='utf-8')

            violations = scan_managed_extensions(fixture.repo_root, load_rules(RULES_PATH))

        targets = {violation.target for violation in violations}
        self.assertIn(f'config/control_plane/profiles/{fixture.extension_id}.service.json', targets)
        self.assertIn(f'python/openclaw/extensions/{fixture.extension_id}', targets)
        self.assertIn(f'python/openclaw/domains/{PROBE_OWNER_DOMAIN}', targets)
        self.assertIn(
            f'agent/extensions/{fixture.extension_id}/python/openclaw_ext_probe/domains/{PROBE_OWNER_DOMAIN}/dispatch/targets.py',
            targets,
        )

    def test_managed_extension_scan_uses_targeted_global_banned_rules(self) -> None:
        rule_ids = {rule.id for rule in load_managed_extension_rules(self._rules_for_repo())}
        self.assertEqual(rule_ids, set())

    def test_managed_extension_scan_passes_on_current_repo(self) -> None:
        violations = self._managed_scan_violations()
        self.assertEqual(violations, [])

    def test_managed_extension_scan_reports_boundary_issues(self) -> None:
        with isolated_test_root('delivery-cleanliness-extension') as repo_root:
            self._write_minimal_managed_extension_index(repo_root)

            violations = scan_managed_extensions(repo_root, load_rules(RULES_PATH))

        targets = {violation.target for violation in violations}
        self.assertIn('agent_probe: missing default service config ->', next(item for item in targets if 'missing default service config' in item))
        self.assertTrue(any(violation.kind == 'extension_lifecycle_doctor' for violation in violations), msg=violations)

    def test_managed_extension_scan_raises_for_invalid_index(self) -> None:
        with isolated_test_root('delivery-cleanliness-invalid-extension-index') as repo_root:
            (repo_root / 'agent' / 'extensions').mkdir(parents=True)
            (repo_root / 'agent' / 'extensions' / 'index.json').write_text('{"extensions": [', encoding='utf-8')

            with self.assertRaises(ManagedExtensionError):
                scan_managed_extensions(repo_root, load_rules(RULES_PATH))

    def test_scan_repo_flags_absent_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            docs_dir = repo_root / 'docs'
            docs_dir.mkdir()
            (docs_dir / 'blocked.md').write_text('blocked\n', encoding='utf-8')

            violations = scan_repo(
                repo_root,
                rules=(
                    AbsentSurface(
                        id='blocked_doc',
                        reason='blocked docs path',
                        paths=('docs/blocked.md',),
                    ),
                ),
            )
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].target, 'docs/blocked.md')

    def test_scan_repo_flags_workspace_residue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / 'config' / 'governance' / 'support').mkdir(parents=True)
            (repo_root / 'config' / 'governance' / 'support' / 'install_defaults.json').write_text(
                '{"defaults":{"host_state_root":"state/openclaw"}}\n',
                encoding='utf-8',
            )
            (repo_root / 'config' / 'governance' / 'support' / 'local_workspace_policy.json').write_text(
                (ROOT_DIR / 'config' / 'governance' / 'support' / 'local_workspace_policy.json').read_text(encoding='utf-8'),
                encoding='utf-8',
            )
            (repo_root / 'python' / 'tmp').mkdir(parents=True)
            (repo_root / 'state' / 'openclaw' / 'gateway' / 'plugin-runtime-deps' / 'pkg' / 'dist').mkdir(parents=True)
            (repo_root / 'state' / 'image_artifacts' / 'pkg' / 'build').mkdir(parents=True)
            (repo_root / 'release' / 'evidence' / 'pkg' / 'dist').mkdir(parents=True)
            (repo_root / 'state' / 'image_pull' / 'pkg' / 'dist').mkdir(parents=True)

            violations = scan_repo(repo_root, ())

        self.assertEqual(
            [(item.kind, item.target) for item in violations],
            [
                ('workspace_residue', 'python/tmp'),
                ('workspace_residue', 'release/evidence/pkg/dist'),
                ('workspace_residue', 'state/image_pull'),
            ],
        )

    def test_release_gate_includes_delivery_cleanliness_check(self) -> None:
        self.assertIn('delivery_cleanliness', [item.check_id for item in base_checks()])


if __name__ == '__main__':
    unittest.main()
