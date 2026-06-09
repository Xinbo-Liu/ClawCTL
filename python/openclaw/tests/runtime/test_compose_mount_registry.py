from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.runtime import compose_mount_registry
from openclaw.runtime.compose_mounts import manifest as mount_manifest
from openclaw.runtime.compose_mounts import sync as mount_sync

ROOT_DIR = resolve_repo_root(Path(__file__))


class _FakeResolver:
    def __init__(self) -> None:
        self.roots = {'host_state_root': Path(r'C:\host\state')}

    def resolve_path(self, entry_id: str, *, view: str) -> str:
        if view == 'host':
            return str(Path(r'C:\host\state') / entry_id)
        return f'/srv/{entry_id}'


class ComposeMountRegistrySplitTest(unittest.TestCase):
    def test_load_manifest_merges_extension_rows(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / 'runtime_mounts.json'
            fragment = root / 'fragment.json'
            base.write_text(json.dumps({
                'generated_artifacts': {'base': 'base.txt'},
                'compose': {'file': 'docker-compose.yml'},
                'services': [{'service': 'gateway', 'mounts': [{'source_type': 'repo_path', 'relative_path': 'base', 'container_path': '/base'}]}],
            }), encoding='utf-8')
            fragment.write_text(json.dumps({
                'generated_artifacts': {'ext': 'ext.txt'},
                'services': [{'service': 'gateway', 'mounts': [{'source_type': 'repo_path', 'relative_path': 'ext', 'container_path': '/ext'}]}],
            }), encoding='utf-8')

            with patch.object(mount_manifest, 'iter_surface_fragment_paths', return_value=[('ext_probe', fragment)]):
                payload = mount_manifest.load_manifest(
                    root_dir=root,
                    manifest_path=base,
                    fail=lambda message, code=2: (_ for _ in ()).throw(RuntimeError(f'{code}:{message}')),
                    path=base,
                    config_path=root / 'service.json',
                )

        self.assertEqual(payload['generated_artifacts']['base'], 'base.txt')
        self.assertEqual(payload['generated_artifacts']['ext'], 'ext.txt')
        self.assertEqual(len(payload['services'][0]['mounts']), 2)

    def test_render_mount_line_supports_runtime_path_entries(self) -> None:
        line = compose_mount_registry.render_mount_line(
            _FakeResolver(),
            {
                'source_type': 'runtime_path_entry',
                'entry': 'runtime-cache',
                'service_view': 'gateway',
                'mode': 'ro',
            },
            indent='  ',
        )

        self.assertIn('${HOST_STATE_ROOT:?HOST_STATE_ROOT_required}', line)
        self.assertIn('/srv/runtime-cache:ro', line)

    def test_sync_compose_updates_extension_and_mount_blocks(self) -> None:
        content = '\n'.join([
            'services:',
            '  gateway:',
            '    volumes:',
            '      # RUNTIME_MOUNTS_BEGIN gateway',
            '      # RUNTIME_MOUNTS_END gateway',
            '# RUNTIME_EXTENSION_SERVICES_BEGIN',
            '# RUNTIME_EXTENSION_SERVICES_END',
            '',
        ])

        with patch.object(mount_manifest, 'resolve_config_path', return_value=Path('service.json')):
            with patch.object(mount_manifest, 'enabled_extension_ids_for', return_value=set()):
                with patch.object(mount_manifest, 'marker_prefix', return_value='RUNTIME_MOUNTS'):
                    with patch.object(mount_manifest, 'services', return_value=[
                        {
                            'service': 'gateway',
                            'mounts': [
                                {
                                    'source_type': 'repo_path',
                                    'relative_path': 'data',
                                    'container_path': '/srv/data',
                                    'mode': 'ro',
                                    'description': 'repo data',
                                }
                            ],
                        }
                    ]):
                        with patch.object(mount_sync, 'compose_service_fragment_text', return_value='worker:\n  image: example'):
                            with patch.object(mount_sync, 'require_path_resolver', return_value=_FakeResolver()):
                                rendered = compose_mount_registry.sync_compose(content)

        self.assertIn('# RUNTIME_EXTENSION_SERVICES_BEGIN\nworker:\n  image: example\n# RUNTIME_EXTENSION_SERVICES_END', rendered)
        self.assertIn('../data:/srv/data:ro  # repo data', rendered)

    def test_runtime_control_plane_services_mount_docs_for_registry_validation(self) -> None:
        payload = json.loads((ROOT_DIR / 'config/services/runtime_mounts.json').read_text(encoding='utf-8'))
        services = {str(row.get('service') or ''): row for row in payload.get('services') or []}
        for service_name in ('openclaw-internal-api', 'openclaw-control-plane-scheduler'):
            with self.subTest(service=service_name):
                mounts = {
                    (str(row.get('relative_path') or ''), str(row.get('container_path') or ''), str(row.get('mode') or ''))
                    for row in services[service_name].get('mounts') or []
                    if row.get('source_type') == 'repo_path'
                }
                self.assertIn(('docs', '/opt/openclaw-tools/docs', 'ro'), mounts)

    def test_scheduler_mounts_gateway_state_for_cron_ui_sync(self) -> None:
        payload = json.loads((ROOT_DIR / 'config/services/runtime_mounts.json').read_text(encoding='utf-8'))
        services = {str(row.get('service') or ''): row for row in payload.get('services') or []}
        mounts = {
            (str(row.get('entry') or ''), str(row.get('container_path') or ''), str(row.get('mode') or ''))
            for row in services['openclaw-control-plane-scheduler'].get('mounts') or []
            if row.get('source_type') == 'runtime_path_entry'
        }

        self.assertIn(('gateway_host_state_dir', '/home/openclaw/.openclaw-gateway', 'rw'), mounts)


if __name__ == '__main__':
    unittest.main()
