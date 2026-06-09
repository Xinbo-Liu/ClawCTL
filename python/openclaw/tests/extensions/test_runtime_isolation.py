from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path
from openclaw.lib.repo.layout import resolve_repo_root
from tempfile import TemporaryDirectory

from openclaw.lib.control_plane import diagnostic_surface, object_families as control_plane_object_families, recovery_operations_surface, router_route_surface
from openclaw.lib.dispatch import observability_surface as dispatch_observability_surface, operations_surface as dispatch_operations_surface


ROOT_DIR = resolve_repo_root(Path(__file__))


class MultiExtensionRuntimeIsolationTest(unittest.TestCase):
    def _write_json(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    def _prepare_repo_root(self, root: Path) -> None:
        (root / 'python' / 'openclaw').mkdir(parents=True, exist_ok=True)
        self._write_json(root / 'config' / 'runtime' / 'paths.json', {'entries': {}})
        if not (root / 'config' / 'control_plane' / 'service.json').exists():
            self._write_json(root / 'config' / 'control_plane' / 'service.json', {})
        platform_manifest = root / 'config' / 'control_plane' / 'extensions.d' / 'agent_platform.json'
        if not platform_manifest.exists():
            self._write_json(platform_manifest, {'id': 'agent_platform', 'title': 'Agent Platform'})

    def _extension_root(self, root: Path, extension_id: str) -> Path:
        return root / 'agent' / 'extensions' / extension_id

    def _manifest_dir(self, root: Path, extension_id: str) -> Path:
        return self._extension_root(root, extension_id) / 'config' / 'control_plane' / 'extensions.d'

    def _write_manifest(self, root: Path, extension_id: str, payload: dict[str, object]) -> None:
        manifests_dir = self._manifest_dir(root, extension_id)
        manifest = dict(payload)
        manifest.setdefault('id', extension_id)
        manifest.setdefault('title', extension_id)
        self._write_json(manifests_dir / f'{extension_id}.json', manifest)

    def _assert_surface_ambiguity(self, expected_fragment: str, callback: object) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                callback()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn(expected_fragment, stderr.getvalue())

    def _build_service(self, root: Path) -> Path:
        self._prepare_repo_root(root)
        service_path = root / 'combined.service.json'
        self._write_json(
            service_path,
            {
                'extends': '@repo/config/control_plane/service.json',
                'extensions': {
                    'manifestsDirs': [
                        str(root / 'config' / 'control_plane' / 'extensions.d'),
                        str(self._manifest_dir(root, 'ext_a')),
                        str(self._manifest_dir(root, 'ext_b')),
                    ],
                    'enabledExtensionIds': ['agent_platform', 'ext_a', 'ext_b'],
                },
            },
        )
        return service_path

    def _build_extensions(self, root: Path) -> Path:
        ext_a_dir = self._extension_root(root, 'ext_a')
        ext_b_dir = self._extension_root(root, 'ext_b')
        ext_a_dir.mkdir(parents=True)
        ext_b_dir.mkdir(parents=True)

        for ext_dir, extension_id, marker in (
            (ext_a_dir, 'ext_a', 'A'),
            (ext_b_dir, 'ext_b', 'B'),
        ):
            self._write_json(
                ext_dir / 'object_families.json',
                {
                    'families': {
                        'dispatch_runtime_state': {
                            'label': f'dispatch runtime {marker}',
                            'entries': [
                                {
                                    'id': 'dispatch_runtime_summary',
                                    'title': f'dispatch runtime {marker}',
                                    'path_kind': 'repo_relative',
                                    'path_ref': 'README.md',
                                    'usage': f'dispatch runtime {marker}',
                                }
                            ],
                        },
                        'shared_state': {
                            'label': f'shared state {marker}',
                            'entries': [
                                {
                                    'id': 'shared_entry',
                                    'title': f'shared entry {marker}',
                                    'path_kind': 'repo_relative',
                                    'path_ref': 'README.md',
                                    'usage': f'shared usage {marker}',
                                }
                            ],
                        },
                        'recovery_logs': {
                            'label': f'recovery logs {marker}',
                            'entries': [
                                {
                                    'id': 'recovery_log',
                                    'title': f'recovery log {marker}',
                                    'path_kind': 'repo_relative',
                                    'path_ref': 'README.md',
                                    'usage': f'recovery usage {marker}',
                                }
                            ],
                        },
                    },
                },
            )
            self._write_json(
                ext_dir / 'dispatch_surface.json',
                {
                    'entries': {
                        'shared_dispatch': {
                            'title': f'dispatch {marker}',
                            'steps': [f'echo dispatch {marker}'],
                        }
                    }
                },
            )
            self._write_json(
                ext_dir / 'recovery_ops.json',
                {
                    'entries': {
                        'shared_recovery': {
                            'title': f'recovery {marker}',
                            'steps': [f'echo recovery {marker}'],
                        }
                    }
                },
            )
            self._write_json(
                ext_dir / 'router_routes.json',
                {
                    'explicitRoutes': [
                        {
                            'route': 'ROUTE:shared_route',
                            'target': f'target_{marker.lower()}',
                            'summary': f'route {marker}',
                            'notes': [f'note {marker}'],
                        }
                    ],
                    'automaticRoutes': [],
                    'healthAwareRules': [f'health {marker}'],
                },
            )
            self._write_json(
                ext_dir / 'diagnostics.json',
                {
                    'actions': {
                        'actions': [
                            {
                                'action': 'shared_action',
                                'title': f'action {marker}',
                                'meaning': f'meaning {marker}',
                                'typicalAgents': [f'agent_{marker.lower()}'],
                            }
                        ]
                    },
                    'diagnostics': {
                        'blockingGroups': [],
                        'sourceDiagnosisGroups': [],
                    },
                    'reasons': {
                        'routeHintReasons': [],
                        'manualVerifyTaskReasons': [],
                        'manualVerifyResultReasons': [],
                        'manualVerifyBlockingReasons': [],
                    },
                },
            )
            self._write_manifest(
                root,
                extension_id,
                {
                    'surfaceFragments': {
                        'objectFamiliesPath': '@extension/object_families.json',
                    },
                    'governanceSurfaces': {
                        'dispatchOperationsSurfacePath': '@extension/dispatch_surface.json',
                        'recoveryOperationsSurfacePath': '@extension/recovery_ops.json',
                        'routerRouteSurfacePath': '@extension/router_routes.json',
                        'diagnosticSurfacePath': '@extension/diagnostics.json',
                    },
                },
            )
        return self._build_service(root)

    def test_control_plane_objects_require_extension_when_family_is_ambiguous(self) -> None:
        with TemporaryDirectory() as tmp:
            service_path = self._build_extensions(Path(tmp))
            with self.assertRaises(SystemExit):
                control_plane_object_families.get_family('shared_state', ROOT_DIR, config_path=service_path)
            family = control_plane_object_families.get_family('shared_state', ROOT_DIR, config_path=service_path, extension_id='ext_a')
        self.assertEqual(family.get('extensionId'), 'ext_a')

    def test_dispatch_and_recovery_entries_require_extension_when_ambiguous(self) -> None:
        with TemporaryDirectory() as tmp:
            service_path = self._build_extensions(Path(tmp))
            self._assert_surface_ambiguity(
                'ambiguous dispatch operation entry: shared_dispatch (ext_a, ext_b)',
                lambda: dispatch_operations_surface.entry_info('shared_dispatch', config_path=service_path),
            )
            self._assert_surface_ambiguity(
                'ambiguous recovery operation entry: shared_recovery (ext_a, ext_b)',
                lambda: recovery_operations_surface.entry_info('shared_recovery', config_path=service_path),
            )
            dispatch_entry = dispatch_operations_surface.entry_info('shared_dispatch', config_path=service_path, extension_id='ext_a')
            recovery_entry = recovery_operations_surface.entry_info('shared_recovery', config_path=service_path, extension_id='ext_b')
        self.assertEqual(dispatch_entry.get('extensionId'), 'ext_a')
        self.assertEqual(recovery_entry.get('extensionId'), 'ext_b')

    def test_router_and_diagnostics_require_extension_when_ambiguous(self) -> None:
        with TemporaryDirectory() as tmp:
            service_path = self._build_extensions(Path(tmp))
            self._assert_surface_ambiguity(
                'ambiguous router route: ROUTE:shared_route (ext_a, ext_b)',
                lambda: router_route_surface.render_route('ROUTE:shared_route', config_path=service_path),
            )
            self._assert_surface_ambiguity(
                'ambiguous diagnostic action: shared_action (ext_a, ext_b)',
                lambda: diagnostic_surface.render_action('shared_action', config_path=service_path),
            )
            route = router_route_surface.render_route('ROUTE:shared_route', config_path=service_path, extension_id='ext_a')
            action = diagnostic_surface.render_action('shared_action', config_path=service_path, extension_id='ext_b')
        self.assertIn('extension: ext_a', route)
        self.assertIn('extension: ext_b', action)

    def test_dispatch_observability_objects_require_extension_when_ambiguous(self) -> None:
        with TemporaryDirectory() as tmp:
            service_path = self._build_extensions(Path(tmp))
            with self.assertRaises(SystemExit):
                dispatch_observability_surface.render_objects(config_path=service_path)
            rendered = dispatch_observability_surface.render_objects(config_path=service_path, extension_id='ext_a')
        self.assertIn('README.md', rendered)


if __name__ == '__main__':
    unittest.main()
