from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.local_workspace_policy import (
    POLICY_PATH,
    bundle_shared_excludes,
    default_cleanup_targets,
    derived_residue_paths,
    disposable_workspace_paths,
    gitignore_patterns,
    local_workspace_policy_path,
    load_local_workspace_policy,
    workspace_target_paths,
)
from openclaw.release import bundle_manifest_support
from openclaw.release.bundle_manifest_support import default_shared_excludes as bundle_manifest_default_shared_excludes
from openclaw.release.bundle_manifest_support import load_manifest as load_bundle_manifest
from openclaw.tests.support.helpers import isolated_test_root
from openclaw.tests.support.static_text_assertions import assert_static_text_absent

ROOT_DIR = resolve_repo_root(Path(__file__))


class LocalWorkspacePolicyTest(unittest.TestCase):
    def test_truth_ref_resolves_host_state_root(self) -> None:
        policy = load_local_workspace_policy()
        host_state_target = next(target for target in policy.targets if target.id == 'host_state_root')
        self.assertEqual(host_state_target.path, 'state/openclaw')
        self.assertEqual(host_state_target.truth_ref, 'host_state_root')

    def test_default_cleanup_projection_and_gitignore_patterns(self) -> None:
        policy = load_local_workspace_policy()
        self.assertEqual(
            [target.path for target in default_cleanup_targets(policy)],
            ['.idea', 'artifacts', 'tmp', 'state/image_pull', 'state/remote_first_install', 'release/history'],
        )
        self.assertIn('state/openclaw', workspace_target_paths(policy))
        patterns = gitignore_patterns(policy)
        self.assertIn('/state/openclaw/', patterns)
        self.assertIn('/state/remote_first_install/', patterns)
        self.assertIn('/deploy/nginx/certs/', patterns)
        self.assertIn('**/__pycache__/**', patterns)
        self.assertIn('python/tmp/**', patterns)
        self.assertIn('agent/extensions/*/python/tmp/**', patterns)
        self.assertIn('.coverage.*', patterns)

    def test_schema_validation_rejects_missing_required_field(self) -> None:
        with isolated_test_root('local-workspace-policy-invalid') as temp_root:
            policy_path = temp_root / 'local_workspace_policy.invalid.json'
            policy_path.write_text(
                json.dumps(
                    {
                        'schemaVersion': 1,
                        'targets': [
                            {
                                'id': 'idea_project',
                                'path': '.idea',
                                'class': 'disposable_local',
                            }
                        ],
                        'derivedGlobs': [],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + '\n',
                encoding='utf-8',
            )
            with self.assertRaisesRegex(ValueError, 'cleanupByDefault'):
                load_local_workspace_policy(path=policy_path)

    def test_disposable_workspace_paths_exclude_preserved_targets(self) -> None:
        with isolated_test_root('local-workspace-policy-scan') as temp_root:
            repo_root = temp_root / 'repo'
            (repo_root / 'config' / 'governance' / 'support').mkdir(parents=True, exist_ok=True)
            (repo_root / 'config' / 'governance' / 'support' / 'install_defaults.json').write_text(
                json.dumps({'defaults': {'host_state_root': 'state/openclaw'}}, ensure_ascii=False, indent=2) + '\n',
                encoding='utf-8',
            )
            (repo_root / 'config' / 'governance' / 'support' / 'local_workspace_policy.json').write_text(
                POLICY_PATH.read_text(encoding='utf-8'),
                encoding='utf-8',
            )
            (repo_root / '.idea').mkdir(parents=True, exist_ok=True)
            (repo_root / 'state' / 'openclaw').mkdir(parents=True, exist_ok=True)
            (repo_root / 'state' / 'openclaw' / 'gateway' / 'plugin-runtime-deps' / 'pkg' / 'dist').mkdir(parents=True, exist_ok=True)
            (repo_root / 'state' / 'image_pull').mkdir(parents=True, exist_ok=True)
            (repo_root / 'state' / 'image_pull' / 'pkg' / 'dist').mkdir(parents=True, exist_ok=True)
            (repo_root / 'state' / 'image_artifacts').mkdir(parents=True, exist_ok=True)
            (repo_root / 'state' / 'image_artifacts' / 'pkg' / 'build').mkdir(parents=True, exist_ok=True)
            (repo_root / 'pkg' / '__pycache__').mkdir(parents=True, exist_ok=True)
            (repo_root / 'python' / 'tmp' / 'repo_unittest_roots').mkdir(parents=True, exist_ok=True)
            (repo_root / '.coverage').write_text('', encoding='utf-8')

            policy = load_local_workspace_policy(
                root_dir=repo_root,
                path=repo_root / 'config' / 'governance' / 'support' / 'local_workspace_policy.json',
            )
            self.assertIn('pkg/__pycache__', derived_residue_paths(policy, root_dir=repo_root))
            self.assertIn('python/tmp', derived_residue_paths(policy, root_dir=repo_root))
            self.assertIn(
                'state/openclaw/gateway/plugin-runtime-deps/pkg/dist',
                derived_residue_paths(policy, root_dir=repo_root),
            )
            self.assertEqual(
                disposable_workspace_paths(policy, root_dir=repo_root),
                ['.coverage', '.idea', 'pkg/__pycache__', 'python/tmp', 'state/image_pull'],
            )

    def test_default_policy_path_follows_explicit_root_dir(self) -> None:
        with isolated_test_root('local-workspace-policy-default-path') as temp_root:
            repo_root = temp_root / 'repo'
            (repo_root / 'config' / 'governance' / 'support').mkdir(parents=True, exist_ok=True)
            (repo_root / 'config' / 'governance' / 'support' / 'install_defaults.json').write_text(
                json.dumps({'defaults': {'host_state_root': 'state/openclaw'}}, ensure_ascii=False, indent=2) + '\n',
                encoding='utf-8',
            )
            policy_copy = repo_root / 'config' / 'governance' / 'support' / 'local_workspace_policy.json'
            policy_copy.write_text(POLICY_PATH.read_text(encoding='utf-8'), encoding='utf-8')

            self.assertEqual(local_workspace_policy_path(root_dir=repo_root), policy_copy.resolve())
            policy = load_local_workspace_policy(root_dir=repo_root)

        self.assertEqual(policy.targets[0].path, '.idea')

    def test_disposable_workspace_paths_treat_python_bootstrap_cache_as_disposable(self) -> None:
        with isolated_test_root('local-workspace-policy-python-bootstrap') as temp_root:
            repo_root = temp_root / 'repo'
            (repo_root / 'config' / 'governance' / 'support').mkdir(parents=True, exist_ok=True)
            (repo_root / 'config' / 'governance' / 'support' / 'install_defaults.json').write_text(
                json.dumps({'defaults': {'host_state_root': 'state/openclaw'}}, ensure_ascii=False, indent=2) + '\n',
                encoding='utf-8',
            )
            (repo_root / 'config' / 'governance' / 'support' / 'local_workspace_policy.json').write_text(
                POLICY_PATH.read_text(encoding='utf-8'),
                encoding='utf-8',
            )
            cache_dir = repo_root / 'python' / '__pycache__'
            cache_dir.mkdir(parents=True, exist_ok=True)
            (cache_dir / '__init__.cpython-312.pyc').write_bytes(b'bootstrap')

            policy = load_local_workspace_policy(
                root_dir=repo_root,
                path=repo_root / 'config' / 'governance' / 'support' / 'local_workspace_policy.json',
            )

            self.assertIn('python/__pycache__', derived_residue_paths(policy, root_dir=repo_root))
            disposable = disposable_workspace_paths(policy, root_dir=repo_root)
            self.assertIn('python/__pycache__', disposable)
            self.assertIn('python/__pycache__/__init__.cpython-312.pyc', disposable)


class BundleManifestWorkspacePolicyTest(unittest.TestCase):
    def test_bundle_excludes_include_managed_runtime_roots(self) -> None:
        excludes = bundle_shared_excludes()
        for expected in (
            'state/openclaw/**',
            'state/image_artifacts/**',
            'state/image_pull/**',
            'release/history/**',
        ):
            self.assertIn(expected, excludes)

    def test_bundle_manifest_load_merges_policy_excludes_once(self) -> None:
        manifest = load_bundle_manifest(error_factory=RuntimeError)
        shared_excludes = manifest.get('sharedExcludes') or []
        self.assertIn('state/openclaw/**', shared_excludes)
        self.assertIn('state/image_artifacts/**', shared_excludes)
        self.assertNotIn('release/evidence/**', shared_excludes)

    def test_bundle_manifest_rejects_unsupported_forbidden_field(self) -> None:
        with isolated_test_root('bundle-manifest-invalid-') as tmp_root:
            manifest_path = tmp_root / 'bundle_manifest.json'
            manifest_path.write_text(
                json.dumps(
                    {
                        'bundles': {
                            'runtime-core': {
                                'include': ['python/openclaw/__init__.py'],
                                'forbidden': ['state/**'],
                            }
                        }
                    }
                ),
                encoding='utf-8',
            )

            with patch.object(bundle_manifest_support, 'MANIFEST_PATH', manifest_path):
                with self.assertRaisesRegex(RuntimeError, 'forbidden is not supported; use must_not_ship'):
                    load_bundle_manifest(error_factory=RuntimeError)

    def test_manifest_source_uses_policy_runtime_residue_excludes(self) -> None:
        manifest_text = (ROOT_DIR / 'config' / 'governance' / 'release' / 'bundle_manifest.json').read_text(encoding='utf-8')
        assert_static_text_absent(self, '"state/image_artifacts/**"', manifest_text)
        assert_static_text_absent(self, '"state/image_pull/**"', manifest_text)
        assert_static_text_absent(self, '"release/evidence/**"', manifest_text)
        assert_static_text_absent(self, '"release/history/**"', manifest_text)
        self.assertIn('".local_mounts/**"', manifest_text)

    def test_bundle_manifest_default_shared_excludes_extend_policy_truth(self) -> None:
        excludes = bundle_manifest_default_shared_excludes(error_factory=RuntimeError)
        self.assertIn('.git/**', excludes)
        self.assertIn('deploy/.env', excludes)
        self.assertIn('state/openclaw/**', excludes)
        self.assertIn('**/__pycache__/**', excludes)


if __name__ == '__main__':
    unittest.main()
