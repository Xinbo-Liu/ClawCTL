from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from openclaw.control_plane.extensions.lifecycle import (
    ExtensionLifecycleError,
    build_lock_payload,
    content_hash,
    enable_extension,
    find_source_manifest,
    install_extension,
    lifecycle_doctor_issues,
    write_lock,
)
from openclaw.doctor.agent_modules.managed_probe_fixture import materialize_managed_probe_extension, remove_managed_extension
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.tests.support.helpers import isolated_test_root


ROOT_DIR = resolve_repo_root(Path(__file__))


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _with_lifecycle_metadata(path: Path, *, dependencies: list[dict[str, object]] | None = None) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding='utf-8'))
    payload['version'] = '1.2.3'
    payload['compat'] = {'controlPlane': '>=1.0.0'}
    payload['dependencies'] = list(dependencies or [])
    payload['migrations'] = []
    _write_json(path, payload)
    return payload


class ExtensionLifecycleTest(unittest.TestCase):
    def test_doctor_requires_managed_manifest_version_and_lock(self) -> None:
        with isolated_test_root('extension-lifecycle-missing-version') as repo_root:
            materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)

            issues = lifecycle_doctor_issues(repo_root)

        self.assertTrue(any('manifest version missing' in issue for issue in issues), msg=issues)
        self.assertTrue(any('lock.json missing' in issue for issue in issues), msg=issues)

    def test_lock_records_version_hash_and_dependencies(self) -> None:
        with isolated_test_root('extension-lifecycle-lock') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            _with_lifecycle_metadata(fixture.manifest_path)

            lock_payload = write_lock(repo_root)
            issues = lifecycle_doctor_issues(repo_root)

        entry = lock_payload['extensions']['agent_probe']
        self.assertEqual(entry['installedVersion'], '1.2.3')
        self.assertRegex(entry['contentHash'], r'^[0-9a-f]{64}$')
        self.assertEqual(entry['dependencies'], [])
        self.assertEqual(issues, [])

    def test_lock_ignores_private_extension_env(self) -> None:
        with isolated_test_root('extension-lifecycle-private-env') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            _with_lifecycle_metadata(fixture.manifest_path)
            first_hash = build_lock_payload(repo_root)['extensions']['agent_probe']['contentHash']
            deploy_dir = fixture.package_root / 'deploy'
            deploy_dir.mkdir(parents=True)
            (deploy_dir / 'extension.env').write_text('SECRET_VALUE=runtime-only\n', encoding='utf-8')
            second_hash = build_lock_payload(repo_root)['extensions']['agent_probe']['contentHash']
            (deploy_dir / 'extension.env.example').write_text('SECRET_VALUE=\n', encoding='utf-8')
            third_hash = build_lock_payload(repo_root)['extensions']['agent_probe']['contentHash']

        self.assertEqual(first_hash, second_hash)
        self.assertNotEqual(first_hash, third_hash)

    def test_content_hash_normalizes_text_line_endings_but_not_binary_payloads(self) -> None:
        with isolated_test_root('extension-lifecycle-line-endings') as repo_root:
            lf_root = repo_root / 'agent' / 'extensions' / 'agent_lf'
            crlf_root = repo_root / 'agent' / 'extensions' / 'agent_crlf'
            lf_root.mkdir(parents=True)
            crlf_root.mkdir(parents=True)
            (lf_root / 'manifest.json').write_bytes(b'{\n  "id": "agent_probe"\n}\n')
            (crlf_root / 'manifest.json').write_bytes(b'{\r\n  "id": "agent_probe"\r\n}\r\n')
            self.assertEqual(content_hash(lf_root), content_hash(crlf_root))

            (lf_root / 'payload.bin').write_bytes(b'abc\n\x00payload')
            (crlf_root / 'payload.bin').write_bytes(b'abc\r\n\x00payload')
            self.assertNotEqual(content_hash(lf_root), content_hash(crlf_root))

    def test_doctor_reports_lock_drift(self) -> None:
        with isolated_test_root('extension-lifecycle-lock-drift') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            _with_lifecycle_metadata(fixture.manifest_path)
            write_lock(repo_root)
            payload = build_lock_payload(repo_root)
            payload['extensions']['agent_probe']['installedVersion'] = '0.0.1'
            _write_json(repo_root / 'agent' / 'extensions' / 'lock.json', payload)

            issues = lifecycle_doctor_issues(repo_root)

        self.assertTrue(any('lock installedVersion drift' in issue for issue in issues), msg=issues)

    def test_find_source_manifest_file_resolves_extension_root(self) -> None:
        with isolated_test_root('extension-lifecycle-source-manifest') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)

            extension_id, extension_root, manifest_path, _manifest = find_source_manifest(fixture.manifest_path)

        self.assertEqual(extension_id, fixture.extension_id)
        self.assertEqual(extension_root, fixture.package_root)
        self.assertEqual(manifest_path, fixture.manifest_path)

    def test_enable_validates_before_writing_profile(self) -> None:
        with isolated_test_root('extension-lifecycle-enable-atomic') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            _with_lifecycle_metadata(
                fixture.manifest_path,
                dependencies=[{'id': 'missing_dependency', 'version': '>=1.0.0'}],
            )
            profile_payload = json.loads(fixture.service_path.read_text(encoding='utf-8'))
            profile_payload['extensions']['enabledExtensionIds'] = ['agent_platform']
            _write_json(fixture.service_path, profile_payload)

            with self.assertRaises(ExtensionLifecycleError):
                enable_extension(
                    repo_root,
                    profile=str(fixture.service_path),
                    extension_id=fixture.extension_id,
                    dry_run=False,
                )
            after = json.loads(fixture.service_path.read_text(encoding='utf-8'))

        self.assertEqual(after['extensions']['enabledExtensionIds'], ['agent_platform'])

    def test_install_rolls_back_copy_index_lock_and_profile_on_enable_failure(self) -> None:
        with isolated_test_root('extension-lifecycle-install-atomic') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            _with_lifecycle_metadata(
                fixture.manifest_path,
                dependencies=[{'id': 'missing_dependency', 'version': '>=1.0.0'}],
            )
            source_root = repo_root / 'external_sources' / fixture.extension_id
            shutil.copytree(fixture.package_root, source_root)
            remove_managed_extension(repo_root, fixture.extension_id)
            profile_path = repo_root / 'config' / 'control_plane' / 'install.service.json'
            _write_json(
                profile_path,
                {
                    'extends': '@repo/config/control_plane/service.json',
                    'extensions': {
                        'manifestsDirs': [
                            '@repo/config/control_plane/extensions.d',
                            '@repo/agent/extensions/agent_probe/config/control_plane/extensions.d',
                        ],
                        'enabledExtensionIds': ['agent_platform'],
                    },
                },
            )

            with self.assertRaises(ExtensionLifecycleError):
                install_extension(
                    repo_root,
                    source=source_root,
                    enable_profile=str(profile_path),
                    mode='copy',
                    dry_run=False,
                )
            index_payload = json.loads((repo_root / 'agent' / 'extensions' / 'index.json').read_text(encoding='utf-8'))
            profile_payload = json.loads(profile_path.read_text(encoding='utf-8'))
            copied_extension_exists = (repo_root / 'agent' / 'extensions' / fixture.extension_id).exists()
            lock_exists = (repo_root / 'agent' / 'extensions' / 'lock.json').exists()

        self.assertFalse(copied_extension_exists)
        self.assertFalse(lock_exists)
        self.assertNotIn(fixture.extension_id, [row.get('id') for row in index_payload.get('extensions') or [] if isinstance(row, dict)])
        self.assertEqual(profile_payload['extensions']['enabledExtensionIds'], ['agent_platform'])

    def test_install_rejects_external_in_place_source(self) -> None:
        with isolated_test_root('extension-lifecycle-install-in-place-boundary') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            _with_lifecycle_metadata(fixture.manifest_path)
            source_root = repo_root / 'external_sources' / fixture.extension_id
            shutil.copytree(fixture.package_root, source_root)
            remove_managed_extension(repo_root, fixture.extension_id)

            with self.assertRaises(ExtensionLifecycleError):
                install_extension(
                    repo_root,
                    source=source_root,
                    mode='in-place',
                    dry_run=False,
                )
            index_payload = json.loads((repo_root / 'agent' / 'extensions' / 'index.json').read_text(encoding='utf-8'))

        self.assertNotIn(fixture.extension_id, [row.get('id') for row in index_payload.get('extensions') or [] if isinstance(row, dict)])

    def test_doctor_reports_dependency_cycle(self) -> None:
        with isolated_test_root('extension-lifecycle-dependency-cycle') as repo_root:
            fixture_a = materialize_managed_probe_extension(
                repo_root,
                base_repo_root=ROOT_DIR,
                extension_id='agent_probe_a',
            )
            fixture_b = materialize_managed_probe_extension(
                repo_root,
                base_repo_root=ROOT_DIR,
                extension_id='agent_probe_b',
            )
            _with_lifecycle_metadata(
                fixture_a.manifest_path,
                dependencies=[{'id': 'agent_probe_b', 'version': '>=1.0.0'}],
            )
            _with_lifecycle_metadata(
                fixture_b.manifest_path,
                dependencies=[{'id': 'agent_probe_a', 'version': '>=1.0.0'}],
            )
            write_lock(repo_root)

            issues = lifecycle_doctor_issues(repo_root)

        self.assertTrue(any('dependency cycle' in issue for issue in issues), msg=issues)


if __name__ == '__main__':
    unittest.main()
