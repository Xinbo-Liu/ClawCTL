from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from openclaw.control_plane.extensions.lifecycle import write_lock
from openclaw.control_plane.stack.release import (
    EXTENSIONS_PROVENANCE_REL_PATH,
    STACK_LOCK_REL_PATH,
    StackReleaseError,
    base_release_bundle_hash,
    build_stack_lock_payload,
    materialize_stack,
    main as stack_release_main,
    update_stack_source_provenance,
    verify_stack_lock,
    write_stack_lock,
)
from openclaw.doctor.agent_modules.managed_probe_fixture import (
    materialize_managed_probe_extension,
    remove_managed_extension,
)
from openclaw.doctor.agent_modules.managed_probe_fixture_repo_markers import ensure_repo_markers
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.tests.support.helpers import isolated_test_root
from openclaw.tests.support.hermetic_git import git_test_environment


ROOT_DIR = resolve_repo_root(Path(__file__))


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _set_platform_version(repo_root: Path, version: str) -> None:
    _write_json(
        repo_root / 'config' / 'control_plane' / 'platform_version.json',
        {
            'controlPlaneVersion': version,
            'schemaVersion': version,
            'runtimeContractVersion': version,
        },
    )


def _write_repo_markers(repo_root: Path) -> None:
    (repo_root / 'python' / 'openclaw').mkdir(parents=True, exist_ok=True)
    _write_json(repo_root / 'config' / 'runtime' / 'paths.json', {'entries': {}})
    _write_json(repo_root / 'config' / 'control_plane' / 'service.json', {'extensions': {'enabledExtensionIds': []}})
    _write_json(repo_root / 'config' / 'control_plane' / 'profiles' / 'agent_platform.service.json', {})
    (repo_root / 'config' / 'control_plane' / 'profile_registry.tsv').write_text(
        '# profile_id\tconfig_path\n'
        'base\tconfig/control_plane/service.json\n'
        'agent_platform\tconfig/control_plane/profiles/agent_platform.service.json\n',
        encoding='utf-8',
    )


def _with_stack_metadata(path: Path, *, version: str = '1.2.3', compat: str = '>=1.0.0') -> None:
    payload = json.loads(path.read_text(encoding='utf-8'))
    payload['version'] = version
    payload['compat'] = {'controlPlane': compat}
    payload['dependencies'] = [{'id': 'agent_platform', 'version': '>=1.0.0'}]
    payload['migrations'] = []
    _write_json(path, payload)


def _init_git_repo(git, path: Path) -> None:
    git.git(path, 'init')
    git.git(path, 'checkout', '-B', 'main')
    git.git(path, 'config', 'user.name', 'OpenClaw Stack Test')
    git.git(path, 'config', 'user.email', 'stack-test@example.invalid')


def _commit_all(git, path: Path, message: str) -> str:
    git.git(path, 'add', '-A')
    git.git(path, 'commit', '-m', message)
    return git.git(path, 'rev-parse', 'HEAD')


class StackReleaseTest(unittest.TestCase):
    def test_build_stack_lock_records_base_versions_and_extension_hashes(self) -> None:
        with isolated_test_root('stack-release-build') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            _set_platform_version(repo_root, '1.4.0')
            _with_stack_metadata(fixture.manifest_path, version='1.2.3', compat='>=1.0.0,<2.0.0')
            write_lock(repo_root)

            payload = build_stack_lock_payload(repo_root)

        self.assertEqual(payload['schemaVersion'], 1)
        self.assertEqual(payload['base']['controlPlaneVersion'], '1.4.0')
        self.assertRegex(payload['base']['releaseBundleHash'], r'^[0-9a-f]{64}$')
        self.assertEqual(payload['extensions'][0]['id'], fixture.extension_id)
        self.assertEqual(payload['extensions'][0]['manifestVersion'], '1.2.3')
        self.assertRegex(payload['extensions'][0]['contentHash'], r'^[0-9a-f]{64}$')
        self.assertEqual(payload['compatibility']['status'], 'ok')
        self.assertRegex(payload['generated']['extensionLockHash'], r'^[0-9a-f]{64}$')

    def test_base_release_bundle_excludes_composition_generated_registry(self) -> None:
        with isolated_test_root('stack-release-base-hash') as repo_root:
            ensure_repo_markers(repo_root, ROOT_DIR)
            _set_platform_version(repo_root, '1.0.0')
            before = base_release_bundle_hash(repo_root)
            (repo_root / 'config' / 'control_plane' / 'profile_registry.tsv').write_text(
                '# profile_id\tconfig_path\nbase\tconfig/control_plane/service.json\nagent_extra\tmissing.json\n',
                encoding='utf-8',
            )

            after = base_release_bundle_hash(repo_root)

        self.assertEqual(after, before)

    def test_base_release_bundle_excludes_runtime_protected_files(self) -> None:
        with isolated_test_root('stack-release-runtime-protected-hash') as repo_root:
            ensure_repo_markers(repo_root, ROOT_DIR)
            _set_platform_version(repo_root, '1.0.0')
            before = base_release_bundle_hash(repo_root)
            protected_files = [
                repo_root / 'deploy' / '.env',
                repo_root / 'deploy' / 'site.env',
                repo_root / 'deploy' / 'targets.d' / 'primary.env',
                repo_root / 'deploy' / 'nginx' / 'certs' / 'openclaw.key',
                repo_root / 'deploy' / 'certs' / 'local.pem',
                repo_root / 'deploy' / 'secrets' / 'local.secret',
                repo_root / 'certs' / 'host.pem',
                repo_root / 'logs' / 'upgrade.log',
            ]
            for path in protected_files:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('runtime-local\n', encoding='utf-8')

            after = base_release_bundle_hash(repo_root)

        self.assertEqual(after, before)

    def test_verify_stack_lock_reports_drift(self) -> None:
        with isolated_test_root('stack-release-drift') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            _set_platform_version(repo_root, '1.0.0')
            _with_stack_metadata(fixture.manifest_path, version='1.0.0')
            write_lock(repo_root)
            write_stack_lock(repo_root)
            _with_stack_metadata(fixture.manifest_path, version='1.0.1')

            result = verify_stack_lock(repo_root)

        self.assertEqual(result['status'], 'fail')
        self.assertTrue(any('stack lock drift' in issue for issue in result['issues']), msg=result)

    def test_verify_stack_lock_rejects_incompatible_base_version(self) -> None:
        with isolated_test_root('stack-release-compat') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            _set_platform_version(repo_root, '2.0.0')
            _with_stack_metadata(fixture.manifest_path, version='1.0.0', compat='>=1.0.0,<2.0.0')
            write_lock(repo_root)
            write_stack_lock(repo_root)

            result = verify_stack_lock(repo_root)

        self.assertEqual(result['status'], 'fail')
        self.assertTrue(any('compat.controlPlane' in issue for issue in result['issues']), msg=result)

    def test_strict_verify_rejects_dirty_base_tree(self) -> None:
        with isolated_test_root('stack-release-strict-dirty') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            _set_platform_version(repo_root, '1.0.0')
            _with_stack_metadata(fixture.manifest_path, version='1.0.0', compat='>=1.0.0')
            write_lock(repo_root)
            source_metadata = {
                'extensions': {
                    fixture.extension_id: {
                        'repo': 'https://example.invalid/agent_probe.git',
                        'commit': 'abc123',
                    }
                }
            }
            write_stack_lock(
                repo_root,
                source_metadata=source_metadata,
                base_repo='https://example.invalid/base.git',
                base_commit='def456',
            )

            with patch('openclaw.control_plane.stack.release._git_dirty_paths', return_value=['python/openclaw/foo.py']):
                result = verify_stack_lock(repo_root, source_metadata=source_metadata, strict_release=True)

        self.assertEqual(result['status'], 'fail')
        self.assertTrue(any('working tree must be clean' in issue for issue in result['issues']), msg=result)

    def test_strict_verify_ignores_runtime_protected_untracked_files(self) -> None:
        with isolated_test_root('stack-release-strict-protected-local') as repo_root:
            with git_test_environment() as git:
                _write_repo_markers(repo_root)
                _set_platform_version(repo_root, '1.0.0')
                _init_git_repo(git, repo_root)
                base_commit = _commit_all(git, repo_root, 'chore: base release')
                protected_files = [
                    repo_root / 'deploy' / '.env',
                    repo_root / 'deploy' / 'site.env',
                    repo_root / 'deploy' / 'targets.d' / 'primary.env',
                    repo_root / 'deploy' / 'nginx' / 'certs' / 'openclaw.key',
                    repo_root / 'logs' / 'upgrade.log',
                ]
                for path in protected_files:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text('runtime-local\n', encoding='utf-8')
                write_stack_lock(
                    repo_root,
                    base_repo='https://example.invalid/base.git',
                    base_commit=base_commit,
                )

                with patch('openclaw.control_plane.stack.release._base_commit_matches_materialized_tree', return_value=True):
                    result = verify_stack_lock(repo_root, strict_release=True)

        self.assertEqual(result['status'], 'ok', msg=result)

    def test_strict_verify_rejects_floating_commits(self) -> None:
        with isolated_test_root('stack-release-strict-floating') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            _set_platform_version(repo_root, '1.0.0')
            _with_stack_metadata(fixture.manifest_path, version='1.0.0', compat='>=1.0.0')
            write_lock(repo_root)
            source_metadata = {
                'extensions': {
                    fixture.extension_id: {
                        'repo': 'https://example.invalid/agent_probe.git',
                        'commit': 'main',
                    }
                }
            }
            write_stack_lock(
                repo_root,
                source_metadata=source_metadata,
                base_repo='https://example.invalid/base.git',
                base_commit='release/latest',
            )

            with patch('openclaw.control_plane.stack.release._git_dirty_paths', return_value=[]):
                result = verify_stack_lock(repo_root, source_metadata=source_metadata, strict_release=True)

        self.assertEqual(result['status'], 'fail')
        self.assertTrue(any('base.commit must be a full 40-character git SHA' in issue for issue in result['issues']), msg=result)
        self.assertTrue(any('commit must be a full 40-character git SHA' in issue for issue in result['issues']), msg=result)

    def test_materialize_refresh_current_rewrites_registry_lock_and_stack_lock(self) -> None:
        with isolated_test_root('stack-release-materialize-refresh') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            _set_platform_version(repo_root, '1.0.0')
            _with_stack_metadata(fixture.manifest_path)

            result = materialize_stack(repo_root, refresh_current=True)
            profile_registry = (repo_root / 'config' / 'control_plane' / 'profile_registry.tsv').read_text(encoding='utf-8')
            stack_lock_exists = (repo_root / STACK_LOCK_REL_PATH).is_file()

        self.assertEqual(result['status'], 'ok')
        self.assertIn(fixture.extension_id, result['extensionIds'])
        self.assertIn(
            f'{fixture.extension_id}\tagent/extensions/{fixture.extension_id}/config/control_plane/profiles/{fixture.extension_id}.service.json',
            profile_registry,
        )
        self.assertTrue(stack_lock_exists)

    def test_materialize_composition_carries_source_metadata_into_stack_lock(self) -> None:
        with isolated_test_root('stack-release-materialize-composition') as repo_root:
            source_repo = repo_root / 'tmp' / 'source-repo'
            fixture = materialize_managed_probe_extension(source_repo, base_repo_root=ROOT_DIR)
            ensure_repo_markers(repo_root, ROOT_DIR)
            _with_stack_metadata(fixture.manifest_path, version='1.2.0')
            _set_platform_version(repo_root, '1.0.0')
            composition_path = repo_root / 'composition.json'
            _write_json(
                composition_path,
                {
                    'extensions': [
                        {
                            'id': fixture.extension_id,
                            'sourcePath': str(fixture.package_root),
                            'repo': 'https://example.invalid/agent_probe.git',
                            'commit': 'abc123',
                            'tag': 'v1.2.0',
                        }
                    ]
                },
            )

            result = materialize_stack(repo_root, composition_path=composition_path)

        extension = result['stackLock']['extensions'][0]
        self.assertEqual(extension['id'], fixture.extension_id)
        self.assertEqual(extension['repo'], 'https://example.invalid/agent_probe.git')
        self.assertEqual(extension['commit'], 'abc123')
        self.assertEqual(extension['tag'], 'v1.2.0')
        self.assertEqual(extension['manifestVersion'], '1.2.0')

    def test_materialize_remote_ref_records_resolved_commit_sha(self) -> None:
        with isolated_test_root('stack-release-materialize-ref') as repo_root:
            with git_test_environment() as git:
                source_repo = repo_root / 'tmp' / 'source-repo'
                remote_repo = repo_root / 'tmp' / 'source.git'
                fixture = materialize_managed_probe_extension(source_repo, base_repo_root=ROOT_DIR)
                ensure_repo_markers(repo_root, ROOT_DIR)
                _with_stack_metadata(fixture.manifest_path, version='1.2.0')
                _set_platform_version(repo_root, '1.0.0')
                _init_git_repo(git, source_repo)
                source_commit = _commit_all(git, source_repo, 'chore: extension release')
                git.git(repo_root, 'init', '--bare', str(remote_repo))
                git.git(source_repo, 'remote', 'add', 'origin', str(remote_repo))
                git.git(source_repo, 'push', '-u', 'origin', 'main')
                composition_path = repo_root / 'composition.json'
                _write_json(
                    composition_path,
                    {
                        'extensions': [
                            {
                                'id': fixture.extension_id,
                                'repo': str(remote_repo),
                                'commit': 'main',
                                'subdir': f'agent/extensions/{fixture.extension_id}',
                            }
                        ]
                    },
                )

                result = materialize_stack(repo_root, composition_path=composition_path)

        extension = result['stackLock']['extensions'][0]
        self.assertEqual(extension['commit'], source_commit)
        self.assertRegex(extension['commit'], r'^[0-9a-f]{40}$')

    def test_verify_detects_lock_source_metadata_tampering_against_provenance(self) -> None:
        with isolated_test_root('stack-release-provenance-tamper') as repo_root:
            source_repo = repo_root / 'tmp' / 'source-repo'
            fixture = materialize_managed_probe_extension(source_repo, base_repo_root=ROOT_DIR)
            ensure_repo_markers(repo_root, ROOT_DIR)
            _with_stack_metadata(fixture.manifest_path, version='1.2.0')
            _set_platform_version(repo_root, '1.0.0')
            composition_path = repo_root / 'composition.json'
            _write_json(
                composition_path,
                {
                    'extensions': [
                        {
                            'id': fixture.extension_id,
                            'sourcePath': str(fixture.package_root),
                            'repo': 'https://example.invalid/agent_probe.git',
                            'commit': 'b' * 40,
                        }
                    ]
                },
            )
            materialize_stack(repo_root, composition_path=composition_path)
            self.assertTrue((repo_root / EXTENSIONS_PROVENANCE_REL_PATH).is_file())
            lock_path = repo_root / STACK_LOCK_REL_PATH
            lock_payload = json.loads(lock_path.read_text(encoding='utf-8'))
            lock_payload['extensions'][0]['commit'] = 'f' * 40
            _write_json(lock_path, lock_payload)

            result = verify_stack_lock(repo_root)

        self.assertEqual(result['status'], 'fail')
        self.assertTrue(any('stack lock drift' in issue for issue in result['issues']), msg=result)

    def test_strict_verify_requires_source_provenance(self) -> None:
        with isolated_test_root('stack-release-strict-provenance') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            _set_platform_version(repo_root, '1.0.0')
            _with_stack_metadata(fixture.manifest_path, version='1.0.0', compat='>=1.0.0')
            write_lock(repo_root)
            source_metadata = {
                'extensions': {
                    fixture.extension_id: {
                        'repo': 'https://example.invalid/agent_probe.git',
                        'commit': 'b' * 40,
                    }
                }
            }
            write_stack_lock(
                repo_root,
                source_metadata=source_metadata,
                base_repo='https://example.invalid/base.git',
                base_commit='a' * 40,
            )

            with patch('openclaw.control_plane.stack.release._git_dirty_paths', return_value=[]):
                result = verify_stack_lock(repo_root, source_metadata=source_metadata, strict_release=True)

        self.assertEqual(result['status'], 'fail')
        self.assertTrue(any('stack source provenance is required' in issue for issue in result['issues']), msg=result)
        self.assertTrue(any('source provenance is required' in issue for issue in result['issues']), msg=result)

    def test_materialize_floating_base_ref_records_current_head(self) -> None:
        with isolated_test_root('stack-release-base-ref') as repo_root:
            with git_test_environment() as git:
                _write_repo_markers(repo_root)
                _set_platform_version(repo_root, '1.0.0')
                _init_git_repo(git, repo_root)
                head_commit = _commit_all(git, repo_root, 'chore: base release')
                composition_path = repo_root / 'composition.json'
                _write_json(
                    composition_path,
                    {
                        'base': {
                            'repo': 'https://example.invalid/base.git',
                            'commit': 'main',
                        },
                        'extensions': [],
                    },
                )

                result = materialize_stack(repo_root, composition_path=composition_path)

        self.assertEqual(result['stackLock']['base']['commit'], head_commit)

    def test_strict_verify_rejects_base_commit_content_mismatch(self) -> None:
        with isolated_test_root('stack-release-base-content') as repo_root:
            with git_test_environment() as git:
                _write_repo_markers(repo_root)
                _set_platform_version(repo_root, '1.0.0')
                _init_git_repo(git, repo_root)
                base_commit = _commit_all(git, repo_root, 'chore: base release')
                _set_platform_version(repo_root, '1.0.1')
                write_stack_lock(
                    repo_root,
                    base_repo='https://example.invalid/base.git',
                    base_commit=base_commit,
                )

                with patch('openclaw.control_plane.stack.release._git_dirty_paths', return_value=[]):
                    result = verify_stack_lock(repo_root, strict_release=True)

        self.assertEqual(result['status'], 'fail')
        self.assertTrue(any('base.commit content must match base release bundle hash' in issue for issue in result['issues']), msg=result)

    def test_verify_keeps_locked_base_commit_without_extension_source_fallback(self) -> None:
        with isolated_test_root('stack-release-base-lock') as repo_root:
            with git_test_environment() as git:
                _write_repo_markers(repo_root)
                _set_platform_version(repo_root, '1.0.0')
                _init_git_repo(git, repo_root)
                base_commit = _commit_all(git, repo_root, 'chore: base release')
                _set_platform_version(repo_root, '1.0.1')
                write_stack_lock(
                    repo_root,
                    base_repo='https://example.invalid/base.git',
                    base_commit=base_commit,
                )

                result = verify_stack_lock(repo_root)

        self.assertEqual(result['status'], 'ok')

    def test_verify_rejects_locked_base_commit_against_expected_source_metadata(self) -> None:
        with isolated_test_root('stack-release-base-source-metadata') as repo_root:
            _write_repo_markers(repo_root)
            _set_platform_version(repo_root, '1.0.0')
            write_stack_lock(
                repo_root,
                base_repo='https://example.invalid/base.git',
                base_commit='a' * 40,
            )

            result = verify_stack_lock(
                repo_root,
                source_metadata={
                    'base': {
                        'repo': 'https://example.invalid/base.git',
                        'commit': 'b' * 40,
                    }
                },
            )

        self.assertEqual(result['status'], 'fail')
        self.assertTrue(any('base.commit does not match expected source metadata' in issue for issue in result['issues']), msg=result)

    def test_verify_accepts_release_equivalent_source_metadata_commit(self) -> None:
        with isolated_test_root('stack-release-source-metadata-release-equivalent') as repo_root:
            _write_repo_markers(repo_root)
            _set_platform_version(repo_root, '1.0.0')
            lock = write_stack_lock(
                repo_root,
                base_repo='https://example.invalid/base.git',
                base_commit='a' * 40,
            )

            result = verify_stack_lock(
                repo_root,
                source_metadata={
                    'base': {
                        'repo': 'https://example.invalid/base.git',
                        'commit': 'b' * 40,
                        'releaseBundleHash': lock['base']['releaseBundleHash'],
                    }
                },
            )

        self.assertEqual(result['status'], 'ok', msg=result)

    def test_verify_rejects_source_metadata_release_hash_drift(self) -> None:
        with isolated_test_root('stack-release-source-metadata-release-hash-drift') as repo_root:
            _write_repo_markers(repo_root)
            _set_platform_version(repo_root, '1.0.0')
            write_stack_lock(
                repo_root,
                base_repo='https://example.invalid/base.git',
                base_commit='a' * 40,
            )

            result = verify_stack_lock(
                repo_root,
                source_metadata={
                    'base': {
                        'repo': 'https://example.invalid/base.git',
                        'commit': 'b' * 40,
                        'releaseBundleHash': '0' * 64,
                    }
                },
            )

        self.assertEqual(result['status'], 'fail')
        self.assertTrue(
            any('source metadata base.releaseBundleHash does not match current base release files' in issue for issue in result['issues']),
            msg=result,
        )

    def test_verify_rejects_floating_base_commit_in_source_metadata(self) -> None:
        with isolated_test_root('stack-release-source-metadata-floating-base') as repo_root:
            _write_repo_markers(repo_root)
            _set_platform_version(repo_root, '1.0.0')
            write_stack_lock(
                repo_root,
                base_repo='https://example.invalid/base.git',
                base_commit='a' * 40,
            )

            result = verify_stack_lock(
                repo_root,
                source_metadata={
                    'base': {
                        'repo': 'https://example.invalid/base.git',
                        'commit': 'main',
                    }
                },
            )

        self.assertEqual(result['status'], 'fail')
        self.assertTrue(any('source metadata base.commit must be a full 40-character git SHA' in issue for issue in result['issues']), msg=result)

    def test_verify_rejects_invalid_source_metadata_release_hash(self) -> None:
        with isolated_test_root('stack-release-source-metadata-invalid-release-hash') as repo_root:
            _write_repo_markers(repo_root)
            _set_platform_version(repo_root, '1.0.0')
            write_stack_lock(
                repo_root,
                base_repo='https://example.invalid/base.git',
                base_commit='a' * 40,
            )

            result = verify_stack_lock(
                repo_root,
                source_metadata={
                    'base': {
                        'repo': 'https://example.invalid/base.git',
                        'commit': 'b' * 40,
                        'releaseBundleHash': 'not-a-sha256',
                    }
                },
            )

        self.assertEqual(result['status'], 'fail')
        self.assertTrue(
            any('source metadata base.releaseBundleHash must be a full 64-character SHA-256' in issue for issue in result['issues']),
            msg=result,
        )

    def test_lock_rejects_mixed_base_source_metadata_and_base_override(self) -> None:
        with isolated_test_root('stack-release-mixed-base-source') as repo_root:
            _write_repo_markers(repo_root)
            _set_platform_version(repo_root, '1.0.0')

            with self.assertRaisesRegex(StackReleaseError, 'cannot be mixed'):
                write_stack_lock(
                    repo_root,
                    source_metadata={
                        'base': {
                            'repo': 'https://example.invalid/base.git',
                            'commit': 'b' * 40,
                        }
                    },
                    base_commit='a' * 40,
                )

    def test_cli_rejects_mixed_base_sources_before_updating_provenance(self) -> None:
        with isolated_test_root('stack-release-cli-mixed-base-source') as repo_root:
            _write_repo_markers(repo_root)
            _set_platform_version(repo_root, '1.0.0')
            metadata_path = repo_root / 'state' / 'upgrade' / 'source_sync_metadata.json'
            _write_json(
                metadata_path,
                {
                    'base': {
                        'repo': 'https://example.invalid/base.git',
                        'commit': 'b' * 40,
                    }
                },
            )

            code = stack_release_main([
                'lock',
                '--repo-root',
                str(repo_root),
                '--source-metadata',
                str(metadata_path),
                '--base-commit',
                'a' * 40,
                '--update-source-provenance',
            ])

        self.assertEqual(code, 2)
        self.assertFalse((repo_root / EXTENSIONS_PROVENANCE_REL_PATH).exists())

    def test_source_metadata_lock_refresh_updates_provenance_truth(self) -> None:
        with isolated_test_root('stack-release-provenance-refresh') as repo_root:
            _write_repo_markers(repo_root)
            _set_platform_version(repo_root, '1.0.0')
            metadata_path = repo_root / 'state' / 'upgrade' / 'source_sync_metadata.json'
            _write_json(
                metadata_path,
                {
                    'base': {
                        'repo': 'https://example.invalid/base.git',
                        'commit': 'b' * 40,
                    }
                },
            )

            update_stack_source_provenance(repo_root, source_metadata_path=metadata_path)
            write_stack_lock(repo_root, source_metadata_path=metadata_path)
            result = verify_stack_lock(repo_root)
            provenance = json.loads((repo_root / EXTENSIONS_PROVENANCE_REL_PATH).read_text(encoding='utf-8'))

        self.assertEqual(result['status'], 'ok', msg=result)
        self.assertEqual(provenance['base']['commit'], 'b' * 40)

    def test_source_metadata_lock_refresh_records_bundled_extension_provenance(self) -> None:
        with isolated_test_root('stack-release-bundled-extension-provenance') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            _set_platform_version(repo_root, '1.0.0')
            _with_stack_metadata(fixture.manifest_path, version='1.0.0')
            write_lock(repo_root)
            metadata_path = repo_root / 'state' / 'upgrade' / 'source_sync_metadata.json'
            _write_json(
                metadata_path,
                {
                    'base': {
                        'repo': 'https://example.invalid/base.git',
                        'commit': 'b' * 40,
                        'releaseBundleHash': base_release_bundle_hash(repo_root),
                    }
                },
            )

            update_stack_source_provenance(repo_root, source_metadata_path=metadata_path)
            lock = write_stack_lock(repo_root, source_metadata_path=metadata_path)
            provenance = json.loads((repo_root / EXTENSIONS_PROVENANCE_REL_PATH).read_text(encoding='utf-8'))

        extension = lock['extensions'][0]
        provenance_extension = provenance['extensions'][fixture.extension_id]
        self.assertEqual(extension['repo'], 'https://example.invalid/base.git')
        self.assertEqual(extension['commit'], 'b' * 40)
        self.assertEqual(extension['sourcePath'], f'agent/extensions/{fixture.extension_id}')
        self.assertEqual(provenance_extension['repo'], 'https://example.invalid/base.git')
        self.assertEqual(provenance_extension['commit'], 'b' * 40)
        self.assertEqual(provenance_extension['sourcePath'], f'agent/extensions/{fixture.extension_id}')

    def test_git_worktree_stack_lock_uses_current_bundled_source_over_stale_provenance(self) -> None:
        with isolated_test_root('stack-release-worktree-stale-provenance') as repo_root:
            with git_test_environment() as git:
                fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
                _set_platform_version(repo_root, '1.0.0')
                _with_stack_metadata(fixture.manifest_path, version='1.0.0')
                write_lock(repo_root)
                _init_git_repo(git, repo_root)
                git.git(repo_root, 'remote', 'add', 'origin', 'https://example.invalid/base.git')
                old_commit = _commit_all(git, repo_root, 'chore: old source')
                update_stack_source_provenance(
                    repo_root,
                    source_metadata={
                        'base': {
                            'repo': 'https://example.invalid/base.git',
                            'commit': old_commit,
                            'releaseBundleHash': base_release_bundle_hash(repo_root),
                        }
                    },
                )
                _with_stack_metadata(fixture.manifest_path, version='1.1.0')
                new_commit = _commit_all(git, repo_root, 'feat: current bundled extension')

                payload = build_stack_lock_payload(repo_root)

        extension = payload['extensions'][0]
        self.assertEqual(payload['base']['commit'], new_commit)
        self.assertEqual(extension['commit'], new_commit)
        self.assertEqual(extension['sourcePath'], f'agent/extensions/{fixture.extension_id}')

    def test_plain_lock_in_git_worktree_refreshes_provenance_truth(self) -> None:
        with isolated_test_root('stack-release-worktree-lock-refreshes-provenance') as repo_root:
            with git_test_environment() as git:
                fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
                _set_platform_version(repo_root, '1.0.0')
                _with_stack_metadata(fixture.manifest_path, version='1.0.0', compat='>=1.0.0')
                write_lock(repo_root)
                _init_git_repo(git, repo_root)
                git.git(repo_root, 'remote', 'add', 'origin', 'https://example.invalid/base.git')
                commit = _commit_all(git, repo_root, 'chore: release source')

                code = stack_release_main(['lock', '--repo-root', str(repo_root)])
                result = verify_stack_lock(repo_root, strict_release=True)
                provenance = json.loads((repo_root / EXTENSIONS_PROVENANCE_REL_PATH).read_text(encoding='utf-8'))

        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'ok', msg=result)
        self.assertEqual(provenance['base']['commit'], commit)
        self.assertEqual(provenance['extensions'][fixture.extension_id]['commit'], commit)

    def test_strict_verify_accepts_lock_only_commit_with_release_equivalent_base(self) -> None:
        with isolated_test_root('stack-release-worktree-lock-only-commit') as repo_root:
            with git_test_environment() as git:
                fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
                _set_platform_version(repo_root, '1.0.0')
                _with_stack_metadata(fixture.manifest_path, version='1.0.0', compat='>=1.0.0')
                write_lock(repo_root)
                _init_git_repo(git, repo_root)
                git.git(repo_root, 'remote', 'add', 'origin', 'https://example.invalid/base.git')
                source_commit = _commit_all(git, repo_root, 'chore: release source')

                code = stack_release_main(['lock', '--repo-root', str(repo_root)])
                lock = json.loads((repo_root / STACK_LOCK_REL_PATH).read_text(encoding='utf-8'))
                lock_commit = _commit_all(git, repo_root, 'chore: record release locks')
                release_hash = base_release_bundle_hash(repo_root)
                result = verify_stack_lock(repo_root, strict_release=True)

        self.assertEqual(code, 0)
        self.assertNotEqual(source_commit, lock_commit)
        self.assertEqual(lock['base']['commit'], source_commit)
        self.assertEqual(lock['base']['releaseBundleHash'], release_hash)
        self.assertEqual(result['status'], 'ok', msg=result)

    def test_strict_verify_rejects_stack_lock_when_provenance_content_is_stale(self) -> None:
        with isolated_test_root('stack-release-strict-stale-provenance') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            _set_platform_version(repo_root, '1.0.0')
            _with_stack_metadata(fixture.manifest_path, version='1.0.0', compat='>=1.0.0')
            write_lock(repo_root)
            release_hash = base_release_bundle_hash(repo_root)
            old_metadata = {
                'base': {
                    'repo': 'https://example.invalid/base.git',
                    'commit': 'b' * 40,
                    'releaseBundleHash': release_hash,
                }
            }
            new_metadata = {
                'base': {
                    'repo': 'https://example.invalid/base.git',
                    'commit': 'c' * 40,
                    'releaseBundleHash': release_hash,
                }
            }
            update_stack_source_provenance(repo_root, source_metadata=old_metadata)
            write_stack_lock(repo_root, source_metadata=new_metadata)

            result = verify_stack_lock(repo_root, source_metadata=new_metadata, strict_release=True)

        self.assertEqual(result['status'], 'fail')
        self.assertTrue(
            any('stack source provenance base.commit does not match stack lock' in issue for issue in result['issues']),
            msg=result,
        )
        self.assertTrue(
            any(f'{fixture.extension_id}: source provenance commit does not match stack lock' in issue for issue in result['issues']),
            msg=result,
        )

    def test_strict_verify_accepts_source_metadata_for_materialized_directory(self) -> None:
        with isolated_test_root('stack-release-strict-materialized-source-metadata') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            _set_platform_version(repo_root, '1.0.0')
            _with_stack_metadata(fixture.manifest_path, version='1.0.0', compat='>=1.0.0')
            write_lock(repo_root)
            source_metadata = {
                'base': {
                    'repo': 'https://example.invalid/base.git',
                    'commit': 'b' * 40,
                    'releaseBundleHash': base_release_bundle_hash(repo_root),
                }
            }
            update_stack_source_provenance(repo_root, source_metadata=source_metadata)
            write_stack_lock(repo_root, source_metadata=source_metadata)

            result = verify_stack_lock(repo_root, source_metadata=source_metadata, strict_release=True)

        self.assertEqual(result['status'], 'ok', msg=result)

    def test_strict_verify_accepts_provenance_release_hash_for_materialized_directory(self) -> None:
        with isolated_test_root('stack-release-strict-materialized-provenance') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            _set_platform_version(repo_root, '1.0.0')
            _with_stack_metadata(fixture.manifest_path, version='1.0.0', compat='>=1.0.0')
            write_lock(repo_root)
            source_metadata = {
                'base': {
                    'repo': 'https://example.invalid/base.git',
                    'commit': 'b' * 40,
                    'releaseBundleHash': base_release_bundle_hash(repo_root),
                }
            }
            update_stack_source_provenance(repo_root, source_metadata=source_metadata)
            write_stack_lock(repo_root, source_metadata=source_metadata)

            result = verify_stack_lock(repo_root, strict_release=True)

        self.assertEqual(result['status'], 'ok', msg=result)

    def test_source_provenance_update_rejects_floating_base_commit(self) -> None:
        with isolated_test_root('stack-release-provenance-floating-base') as repo_root:
            _write_repo_markers(repo_root)
            _set_platform_version(repo_root, '1.0.0')
            metadata_path = repo_root / 'state' / 'upgrade' / 'source_sync_metadata.json'
            _write_json(
                metadata_path,
                {
                    'base': {
                        'repo': 'https://example.invalid/base.git',
                        'commit': 'main',
                    }
                },
            )

            with self.assertRaisesRegex(StackReleaseError, 'source metadata base.commit must be a full'):
                update_stack_source_provenance(repo_root, source_metadata_path=metadata_path)

    def test_materialize_composition_rejects_unlisted_materialized_extensions(self) -> None:
        with isolated_test_root('stack-release-materialize-extra') as repo_root:
            source_repo = repo_root / 'tmp' / 'source-repo'
            fixture = materialize_managed_probe_extension(source_repo, base_repo_root=ROOT_DIR)
            materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR, extension_id='agent_extra')
            _with_stack_metadata(fixture.manifest_path, version='1.2.0')
            _set_platform_version(repo_root, '1.0.0')
            composition_path = repo_root / 'composition.json'
            _write_json(
                composition_path,
                {
                    'extensions': [
                        {
                            'id': fixture.extension_id,
                            'sourcePath': str(fixture.package_root),
                        }
                    ]
                },
            )

            with self.assertRaisesRegex(StackReleaseError, '必须声明完整扩展集合'):
                materialize_stack(repo_root, composition_path=composition_path)

    def test_materialize_dry_run_does_not_clone_repo_sources(self) -> None:
        with isolated_test_root('stack-release-materialize-dry-run') as repo_root:
            composition_path = repo_root / 'composition.json'
            _write_json(
                composition_path,
                {
                    'extensions': [
                        {
                            'id': 'agent_probe',
                            'repo': 'https://example.invalid/agent_probe.git',
                            'commit': 'abc123',
                            'subdir': 'extension',
                        }
                    ]
                },
            )

            with patch('openclaw.control_plane.stack.release.subprocess.run') as run:
                result = materialize_stack(repo_root, composition_path=composition_path, dry_run=True)

        run.assert_not_called()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['actions'][0]['source'], 'https://example.invalid/agent_probe.git@abc123:extension')


if __name__ == '__main__':
    unittest.main()
