from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from openclaw.control_plane.cli import summary_entry
from openclaw.control_plane.registry_loader.config import load_registry_service_context
from openclaw.control_plane.registry import load_registry_from_path
from openclaw.lib.repo.control_plane_config_surface import main
from openclaw.doctor.agent_modules.managed_probe_fixture import materialize_managed_probe_extension
from openclaw.doctor.agent_modules.managed_probe_fixture_repo_markers import ensure_repo_markers
from openclaw.doctor.agent_modules.managed_probe_fixture_scaffold import PROBE_GROUP_REF, PROBE_MODEL_REF, PROBE_PACKAGE_NAME, write_control_plane_manifests
from openclaw.doctor.agent_modules.managed_probe_fixture_repo_markers import write_json, write_text
from openclaw.lib.repo.layout import (
    CONTROL_PLANE_CONFIG_ENV,
    CONTROL_PLANE_PROFILE_ENV,
    available_control_plane_profile_ids,
    control_plane_profile_config_rel_path,
    control_plane_profile_status_rows,
    resolve_control_plane_profile_service_config_path,
    resolve_repo_root,
)
from openclaw.lib.repo.managed_extensions import load_managed_extensions_index, managed_explicit_extensions
from openclaw.tests.support.helpers import isolated_test_root
from openclaw.tests.support.managed_extensions import managed_extensions


ROOT_DIR = resolve_repo_root(Path(__file__))
MANAGED_EXTENSIONS = tuple(sorted(managed_extensions(ROOT_DIR), key=lambda row: row.id))
MANAGED_EXTENSION = MANAGED_EXTENSIONS[0] if MANAGED_EXTENSIONS else None
EXTENSION_CONFIG_PATH = MANAGED_EXTENSION.default_service_config_path if MANAGED_EXTENSION is not None else None
EXTENSION_CONFIG_REL = EXTENSION_CONFIG_PATH.relative_to(ROOT_DIR).as_posix() if EXTENSION_CONFIG_PATH is not None else ''
MANAGED_AGENT_REF = (
    sorted(path.name for path in (MANAGED_EXTENSION.root_dir / 'agent' / 'modules').iterdir() if path.is_dir())[0]
    if MANAGED_EXTENSION is not None
    else ''
)


def run_surface(*args: str) -> str:
    stdout = io.StringIO()
    with mock.patch.dict(os.environ, {CONTROL_PLANE_CONFIG_ENV: '', CONTROL_PLANE_PROFILE_ENV: ''}, clear=False):
        with redirect_stdout(stdout):
            exit_code = main(list(args))
    if exit_code != 0:
        raise AssertionError(f'control-plane config exited with {exit_code}')
    return stdout.getvalue().strip()


def run_surface_with_env(*args: str) -> str:
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(list(args))
    if exit_code != 0:
        raise AssertionError(f'control-plane config exited with {exit_code}')
    return stdout.getvalue().strip()


def run_surface_failure(*args: str) -> tuple[int, str]:
    stderr = io.StringIO()
    with mock.patch.dict(os.environ, {CONTROL_PLANE_CONFIG_ENV: '', CONTROL_PLANE_PROFILE_ENV: ''}, clear=False):
        with redirect_stderr(stderr):
            with unittest.TestCase().assertRaises(SystemExit) as raised:
                main(list(args))
    return int(raised.exception.code), stderr.getvalue()


def make_discovery_only_probe(repo_root: Path):
    fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
    index_path = repo_root / 'agent' / 'extensions' / 'index.json'
    if index_path.exists():
        index_path.unlink()
    return fixture


def make_lightweight_discovery_candidate(repo_root: Path, extension_id: str):
    ensure_repo_markers(repo_root, ROOT_DIR)
    package_root = repo_root / 'agent' / 'extensions' / extension_id
    manifest_dir = package_root / 'config' / 'control_plane' / 'extensions.d'
    service_path = package_root / 'config' / 'control_plane' / 'profiles' / f'{extension_id}.service.json'
    manifest_path = manifest_dir / f'{extension_id}.json'
    write_control_plane_manifests(
        repo_root=repo_root,
        extension_id=extension_id,
        service_path=service_path,
        manifest_path=manifest_path,
        runtime_paths_path=manifest_dir / f'{extension_id}.runtime_paths.json',
        testing_manifest_path=manifest_dir / f'{extension_id}.testing_manifest.json',
        diagnostic_surface_path=manifest_dir / f'{extension_id}.diagnostic_surface.json',
    )
    for rel_path in (
        'agent/control_plane/groups',
        'agent/control_plane/jobs',
        'agent/control_plane/models',
        'agent/control_plane/targets',
        'agent/control_plane/registries',
        'agent/modules',
    ):
        (package_root / rel_path).mkdir(parents=True, exist_ok=True)
    write_json(package_root / 'agent' / 'control_plane' / 'registries' / 'dispatch_targets.json', {'version': 7, 'targets': []})
    write_text(package_root / 'python' / PROBE_PACKAGE_NAME / '__init__.py', '')
    return SimpleNamespace(
        extension_id=extension_id,
        package_root=package_root.resolve(),
        service_path=service_path.resolve(),
        manifest_path=manifest_path.resolve(),
    )


def row_by_id(rows: tuple[dict[str, object], ...], profile_id: str, *, source: str | None = None) -> dict[str, object]:
    for row in rows:
        if row.get('id') == profile_id and (source is None or row.get('source') == source):
            return row
    raise AssertionError(f'profile row not found: {profile_id} source={source or "*"}')


class ControlPlaneConfigSurfaceTest(unittest.TestCase):
    _REPO_EXTENSION_REQUIRED_TESTS = {
        'test_profile_rel_path_helper_matches_repo_profiles',
        'test_profile_resolution_supports_managed_extension_profile',
        'test_container_path_maps_extension_profile_inside_repo_mount',
        'test_container_path_prefers_explicit_config_path_over_internal_container_override',
        'test_profile_id_reports_extension_when_given_extension_config_path',
        'test_agent_host_path_uses_managed_extension_default_config',
        'test_agent_host_path_uses_managed_extension_default_config_for_qualified_ref',
    }

    def setUp(self) -> None:
        if self._testMethodName in self._REPO_EXTENSION_REQUIRED_TESTS and MANAGED_EXTENSION is None:
            self.skipTest('base release surface has no repo-managed extension')

    def test_profile_rel_path_helper_matches_repo_profiles(self) -> None:
        self.assertEqual(
            control_plane_profile_config_rel_path('agent_platform'),
            'config/control_plane/profiles/agent_platform.service.json',
        )
        self.assertEqual(
            control_plane_profile_config_rel_path('base'),
            'config/control_plane/service.json',
        )
        self.assertEqual(
            control_plane_profile_config_rel_path(MANAGED_EXTENSION.id, ROOT_DIR),
            EXTENSION_CONFIG_REL,
        )

    def test_profile_resolution_supports_managed_extension_profile(self) -> None:
        self.assertEqual(
            resolve_control_plane_profile_service_config_path(MANAGED_EXTENSION.id, start_path=ROOT_DIR),
            EXTENSION_CONFIG_PATH,
        )

    def test_host_path_defaults_to_runtime_profile(self) -> None:
        result = run_surface('host-path')
        self.assertEqual(result, str((ROOT_DIR / control_plane_profile_config_rel_path('agent_platform')).resolve()))

    def test_container_path_maps_extension_profile_inside_repo_mount(self) -> None:
        result = run_surface('container-path', '--config-path', str(EXTENSION_CONFIG_PATH))
        self.assertEqual(
            result,
            f'/opt/openclaw-tools/{EXTENSION_CONFIG_REL}',
        )

    def test_container_path_prefers_explicit_config_path_over_internal_container_override(self) -> None:
        with mock.patch.dict(os.environ, {'CONTROL_PLANE_CONTAINER_CONFIG_PATH': '/tmp/custom-container-config.json'}, clear=False):
            result = run_surface('container-path', '--config-path', str(EXTENSION_CONFIG_PATH))
        self.assertEqual(
            result,
            f'/opt/openclaw-tools/{EXTENSION_CONFIG_REL}',
        )

    def test_container_path_uses_internal_container_override_when_public_selection_is_absent(self) -> None:
        with mock.patch.dict(os.environ, {'CONTROL_PLANE_CONTAINER_CONFIG_PATH': '/tmp/custom-container-config.json'}, clear=False):
            result = run_surface_with_env('container-path')
        self.assertEqual(result, '/tmp/custom-container-config.json')

    def test_profile_id_reports_extension_when_given_extension_config_path(self) -> None:
        result = run_surface('profile-id', '--config-path', str(EXTENSION_CONFIG_PATH))
        self.assertEqual(result, MANAGED_EXTENSION.id)

    def test_profile_id_reports_custom_for_unregistered_config_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / 'custom.service.json'
            config_path.write_text('{}\n', encoding='utf-8')
            result = run_surface('profile-id', '--config-path', str(config_path))
        self.assertEqual(result, 'custom')

    def test_agent_host_path_uses_managed_extension_default_config(self) -> None:
        result = run_surface('agent-host-path', '--agent-ref', MANAGED_AGENT_REF)
        self.assertEqual(result, str(EXTENSION_CONFIG_PATH))

    def test_agent_host_path_uses_managed_extension_default_config_for_qualified_ref(self) -> None:
        result = run_surface('agent-host-path', '--agent-ref', f'{MANAGED_EXTENSION.id}:{MANAGED_AGENT_REF}')
        self.assertEqual(result, str(EXTENSION_CONFIG_PATH))

    def test_agent_host_path_falls_back_to_runtime_profile_when_agent_is_not_managed_extension(self) -> None:
        result = run_surface('agent-host-path', '--agent-ref', 'missing_agent')
        self.assertEqual(result, str((ROOT_DIR / control_plane_profile_config_rel_path('agent_platform')).resolve()))

    def test_agent_host_path_ignores_unrelated_bad_manifest_when_module_directory_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir).resolve()
            write_text(repo_root / 'python' / 'openclaw' / '__init__.py', '')
            write_json(repo_root / 'config' / 'runtime' / 'paths.json', {'entries': {}})
            write_json(repo_root / 'config' / 'control_plane' / 'service.json', {'extensions': {'enabledExtensionIds': []}})
            write_json(
                repo_root / 'agent' / 'extensions' / 'index.json',
                {
                    'extensions': [
                        {
                            'id': 'good_extension',
                            'title': 'Good Extension',
                            'rootDir': 'agent/extensions/good_extension',
                            'defaultServiceConfigPath': 'agent/extensions/good_extension/config/control_plane/profiles/good_extension.service.json',
                            'manifestDir': 'agent/extensions/good_extension/config/control_plane/extensions.d',
                            'pythonRoots': ['agent/extensions/good_extension/python'],
                            'status': 'managed_explicit_extension',
                        },
                        {
                            'id': 'bad_extension',
                            'title': 'Bad Extension',
                            'rootDir': 'agent/extensions/bad_extension',
                            'defaultServiceConfigPath': 'agent/extensions/bad_extension/config/control_plane/profiles/bad_extension.service.json',
                            'manifestDir': 'agent/extensions/bad_extension/config/control_plane/extensions.d',
                            'pythonRoots': ['agent/extensions/bad_extension/python'],
                            'status': 'managed_explicit_extension',
                        },
                    ]
                },
            )
            write_text(
                repo_root / 'agent' / 'extensions' / 'good_extension' / 'agent' / 'modules' / 'good_agent' / 'module.json',
                '{"id":"good_agent","agentRef":"good_agent"}\n',
            )
            write_text(
                repo_root / 'agent' / 'extensions' / 'bad_extension' / 'agent' / 'modules' / 'bad_agent' / 'module.json',
                '{"id": ',
            )
            write_text(
                repo_root / 'agent' / 'extensions' / 'good_extension' / 'config' / 'control_plane' / 'profiles' / 'good_extension.service.json',
                '{}\n',
            )
            write_text(
                repo_root / 'agent' / 'extensions' / 'bad_extension' / 'config' / 'control_plane' / 'profiles' / 'bad_extension.service.json',
                '{}\n',
            )

            result = run_surface('agent-host-path', '--agent-ref', 'good_agent', '--repo-root', str(repo_root))

        self.assertEqual(
            result,
            str(
                (
                    repo_root
                    / 'agent'
                    / 'extensions'
                    / 'good_extension'
                    / 'config'
                    / 'control_plane'
                    / 'profiles'
                    / 'good_extension.service.json'
                ).resolve()
            ),
        )

    def test_agent_host_path_reports_invalid_managed_extension_index_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir).resolve()
            write_text(repo_root / 'python' / 'openclaw' / '__init__.py', '')
            write_json(repo_root / 'config' / 'runtime' / 'paths.json', {'entries': {}})
            write_json(repo_root / 'config' / 'control_plane' / 'service.json', {'extensions': {'enabledExtensionIds': []}})
            write_text(repo_root / 'agent' / 'extensions' / 'index.json', '{"extensions": [',)

            exit_code, stderr = run_surface_failure('agent-host-path', '--agent-ref', 'good_agent', '--repo-root', str(repo_root))

        self.assertEqual(exit_code, 2)
        self.assertIn('[control_plane_config][FAIL]', stderr)
        self.assertIn('managed extension index is unreadable', stderr)
        self.assertNotIn('Traceback', stderr)

    def test_discovery_only_extension_profile_can_be_selected_and_loaded(self) -> None:
        with isolated_test_root('control-plane-discovered-profile') as repo_root:
            fixture = make_discovery_only_probe(repo_root)

            self.assertEqual(load_managed_extensions_index(repo_root), ())
            self.assertIn(fixture.extension_id, available_control_plane_profile_ids(repo_root))
            self.assertEqual(
                resolve_control_plane_profile_service_config_path(fixture.extension_id, start_path=repo_root),
                fixture.service_path,
            )

            registry = load_registry_from_path(fixture.service_path)

        self.assertIn(PROBE_GROUP_REF, registry.get('agentGroupsById') or {})

    def test_default_profiles_do_not_load_discovered_extension_business_surfaces(self) -> None:
        with isolated_test_root('control-plane-discovered-defaults') as repo_root:
            fixture = make_discovery_only_probe(repo_root)
            base_context = load_registry_service_context(resolve_control_plane_profile_service_config_path('base', start_path=repo_root))
            platform_context = load_registry_service_context(
                resolve_control_plane_profile_service_config_path('agent_platform', start_path=repo_root)
            )

        self.assertNotIn(fixture.extension_id, base_context.get('enabledExtensionIds') or [])
        self.assertNotIn(fixture.extension_id, platform_context.get('enabledExtensionIds') or [])
        self.assertNotIn(fixture.extension_id, [str(item.get('id') or '') for item in platform_context.get('extensions') or []])

    def test_profiles_json_reports_discovered_source_and_status(self) -> None:
        with isolated_test_root('control-plane-profiles-json') as repo_root:
            fixture = make_discovery_only_probe(repo_root)
            payload = json.loads(run_surface('profiles', '--repo-root', str(repo_root), '--format', 'json'))

        row = row_by_id(tuple(payload['profiles']), fixture.extension_id, source='discovered')
        self.assertEqual(row['status'], 'valid')
        self.assertEqual(row['issues'], [])
        self.assertEqual(payload['counts']['discovered'], 1)

    def test_discovery_cache_revalidates_referenced_extension_files(self) -> None:
        with isolated_test_root('control-plane-discovery-cache') as repo_root:
            fixture = make_discovery_only_probe(repo_root)

            self.assertIn(fixture.extension_id, available_control_plane_profile_ids(repo_root))
            fixture.diagnostic_surface_path.unlink()

            self.assertNotIn(fixture.extension_id, available_control_plane_profile_ids(repo_root))
            rows = control_plane_profile_status_rows(repo_root)

        row = row_by_id(rows, fixture.extension_id, source='discovered')
        self.assertEqual(row['status'], 'invalid')
        self.assertIn('diagnosticSurfacePath', '\n'.join(row['issues']))

    def _assert_invalid_discovered_candidates(self, case_names: tuple[str, ...]) -> None:
        extension_ids = {
            'missing_service': 'bad_missing_service',
            'missing_manifest': 'bad_missing_manifest',
            'id_mismatch': 'bad_id_mismatch',
            'no_self': 'bad_no_self',
            'path_escape': 'bad_path_escape',
            'manifest_source': 'bad_manifest_source',
            'extra_enabled': 'bad_extra_enabled',
            'extra_manifest_dir': 'bad_extra_manifest_dir',
            'registry_file_escape': 'bad_registry_file_escape',
            'surface_escape': 'bad_surface_escape',
            'unknown_manifest_field': 'bad_unknown_manifest_field',
        }
        with isolated_test_root(f'control-plane-invalid-discovery-{case_names[0]}') as repo_root:
            fixtures = {
                case_name: make_lightweight_discovery_candidate(repo_root, extension_ids[case_name])
                for case_name in case_names
            }
            if 'missing_service' in fixtures:
                fixtures['missing_service'].service_path.unlink()
            if 'missing_manifest' in fixtures:
                fixtures['missing_manifest'].manifest_path.unlink()
            if 'id_mismatch' in fixtures:
                mismatch_manifest = json.loads(fixtures['id_mismatch'].manifest_path.read_text(encoding='utf-8'))
                mismatch_manifest['id'] = 'wrong_id'
                write_json(fixtures['id_mismatch'].manifest_path, mismatch_manifest)
            if 'no_self' in fixtures:
                no_self_service = json.loads(fixtures['no_self'].service_path.read_text(encoding='utf-8'))
                no_self_service['extensions']['enabledExtensionIds'] = ['agent_platform']
                write_json(fixtures['no_self'].service_path, no_self_service)
            if 'path_escape' in fixtures:
                escape_manifest = json.loads(fixtures['path_escape'].manifest_path.read_text(encoding='utf-8'))
                escape_manifest['registry']['jobsDirs'] = ['@repo/config/control_plane/extensions.d']
                write_json(fixtures['path_escape'].manifest_path, escape_manifest)
            if 'manifest_source' in fixtures:
                outside_manifest = {'id': 'bad_manifest_source', 'title': 'Outside Manifest Source', 'registry': {}}
                write_json(repo_root / 'config' / 'control_plane' / 'extensions.d' / 'bad_manifest_source.json', outside_manifest)
                manifest_source_service = json.loads(fixtures['manifest_source'].service_path.read_text(encoding='utf-8'))
                manifest_source_service['extensions']['manifestsDirs'] = ['@repo/config/control_plane/extensions.d']
                write_json(fixtures['manifest_source'].service_path, manifest_source_service)
            if 'extra_enabled' in fixtures:
                extra_enabled_service = json.loads(fixtures['extra_enabled'].service_path.read_text(encoding='utf-8'))
                extra_enabled_service['extensions']['enabledExtensionIds'] = ['agent_platform', fixtures['extra_enabled'].extension_id, 'shadow_extension']
                write_json(fixtures['extra_enabled'].service_path, extra_enabled_service)
            if 'extra_manifest_dir' in fixtures:
                extra_manifest_dir_service = json.loads(fixtures['extra_manifest_dir'].service_path.read_text(encoding='utf-8'))
                extra_manifest_dir_service['extensions']['manifestsDirs'] = ['@repo/config/control_plane/extensions.d', '@extension/config/control_plane/extensions.d', '@repo/config/runtime']
                write_json(fixtures['extra_manifest_dir'].service_path, extra_manifest_dir_service)
            if 'registry_file_escape' in fixtures:
                registry_file_escape_manifest = json.loads(fixtures['registry_file_escape'].manifest_path.read_text(encoding='utf-8'))
                registry_file_escape_manifest['registry']['dispatchTargetRegistryPaths'] = ['@repo/config/control_plane/extensions.d/agent_platform.json']
                write_json(fixtures['registry_file_escape'].manifest_path, registry_file_escape_manifest)
            if 'surface_escape' in fixtures:
                surface_escape_manifest = json.loads(fixtures['surface_escape'].manifest_path.read_text(encoding='utf-8'))
                surface_escape_manifest['surfaceFragments']['runtimePathsPath'] = '@repo/config/runtime/paths.json'
                write_json(fixtures['surface_escape'].manifest_path, surface_escape_manifest)
            if 'unknown_manifest_field' in fixtures:
                unknown_manifest = json.loads(fixtures['unknown_manifest_field'].manifest_path.read_text(encoding='utf-8'))
                unknown_manifest['sampleContract'] = {}
                write_json(fixtures['unknown_manifest_field'].manifest_path, unknown_manifest)

            index_path = repo_root / 'agent' / 'extensions' / 'index.json'
            if index_path.exists():
                index_path.unlink()
            rows = control_plane_profile_status_rows(repo_root)
            available = {str(row.get('id') or '') for row in rows if row.get('status') == 'valid'}

        for fixture in fixtures.values():
            self.assertFalse(fixture.extension_id in available, fixture.extension_id)
            self.assertEqual(row_by_id(rows, fixture.extension_id, source='discovered')['status'], 'invalid')

        expected_fragments = {
            'missing_service': 'missing service profile',
            'missing_manifest': 'missing extension manifest',
            'id_mismatch': 'manifest id must match extension directory',
            'no_self': 'service profile must enable extension id',
            'path_escape': 'escapes extension root',
            'manifest_source': 'service profile must load own manifest from convention path',
            'extra_enabled': 'may only enable agent_platform and extension id',
            'extra_manifest_dir': 'may only load platform and own manifest dirs',
            'registry_file_escape': 'manifest registry.dispatchTargetRegistryPaths[0] escapes extension root',
            'surface_escape': 'manifest surfaceFragments.runtimePathsPath escapes extension root',
            'unknown_manifest_field': 'unsupported manifest field(s): sampleContract',
        }
        for case_name in case_names:
            self.assertIn(expected_fragments[case_name], '\n'.join(row_by_id(rows, extension_ids[case_name])['issues']))

    def test_invalid_discovered_candidates_report_missing_and_identity_guards(self) -> None:
        self._assert_invalid_discovered_candidates((
            'missing_service',
            'missing_manifest',
            'id_mismatch',
            'no_self',
            'path_escape',
            'manifest_source',
        ))

    def test_invalid_discovered_candidates_report_registry_and_surface_guards(self) -> None:
        self._assert_invalid_discovered_candidates((
            'extra_enabled',
            'extra_manifest_dir',
            'registry_file_escape',
            'surface_escape',
            'unknown_manifest_field',
        ))

    def test_profile_registry_rejects_discovered_profile_alias_path(self) -> None:
        with isolated_test_root('control-plane-discovery-conflict') as repo_root:
            fixture = make_discovery_only_probe(repo_root)
            write_text(
                repo_root / 'config' / 'control_plane' / 'profile_registry.tsv',
                '\n'.join(
                    [
                        '# profile_id\tconfig_path',
                        'base\tconfig/control_plane/service.json',
                        'agent_platform\tconfig/control_plane/profiles/agent_platform.service.json',
                        f'{fixture.extension_id}\tconfig/control_plane/service.json',
                        '',
                    ]
                ),
            )

            with self.assertRaisesRegex(ValueError, 'profile registry 路径必须使用合同路径'):
                control_plane_profile_config_rel_path(fixture.extension_id, repo_root)

    def test_explicit_index_id_blocks_discovery_managed_view_when_status_is_not_active(self) -> None:
        with isolated_test_root('control-plane-discovery-index-id-conflict') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            index_path = repo_root / 'agent' / 'extensions' / 'index.json'
            payload = json.loads(index_path.read_text(encoding='utf-8'))
            payload['extensions'][0]['status'] = 'retired'
            write_json(index_path, payload)

            managed_ids = {row.id for row in managed_explicit_extensions(repo_root)}

        self.assertNotIn(fixture.extension_id, managed_ids)

    def test_grouped_summary_accepts_discovered_profile_without_static_choices(self) -> None:
        with isolated_test_root('control-plane-summary-discovered-profile') as repo_root:
            fixture = make_discovery_only_probe(repo_root)
            stdout = io.StringIO()
            fake_handler_file = repo_root / 'python' / 'openclaw' / 'control_plane' / 'cli_support' / 'handler_support.py'
            with mock.patch('openclaw.control_plane.cli_support.handler_support.__file__', str(fake_handler_file)):
                with redirect_stdout(stdout):
                    exit_code = summary_entry(['--control-plane-profile', fixture.extension_id, 'models'])

            payload = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 0)
        self.assertEqual([str(item.get('id') or '') for item in payload.get('items') or []], [PROBE_MODEL_REF])


if __name__ == '__main__':
    unittest.main()
