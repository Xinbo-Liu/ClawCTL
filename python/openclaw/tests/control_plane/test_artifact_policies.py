"""验证产物策略的 profile 隔离、未知路径与解析器复用。"""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from openclaw.control_plane import artifact_policies
from openclaw.doctor.agent_modules.managed_probe_fixture import (
    PROBE_JOB_REF,
    PROBE_RUNTIME_ENTRY_ID,
    materialize_managed_probe_extension,
)
from openclaw.lib.repo.layout import DEFAULT_RUNTIME_CONTROL_PLANE_SERVICE_CONFIG_REL_PATH
from openclaw.lib.runtime.resolver_loader import require_path_resolver
from openclaw.tests.support.helpers import isolated_test_root


ROOT_DIR = artifact_policies.ROOT_DIR
PLATFORM_CONFIG = ROOT_DIR / DEFAULT_RUNTIME_CONTROL_PLANE_SERVICE_CONFIG_REL_PATH


class ArtifactPoliciesProfileTest(unittest.TestCase):
    def test_explicit_fixture_profile_isolates_jobs_and_runtime_path_contracts(self) -> None:
        with isolated_test_root('artifact-policy-profile') as repo_root:
            fixture = materialize_managed_probe_extension(repo_root, base_repo_root=ROOT_DIR)
            platform_config = repo_root / DEFAULT_RUNTIME_CONTROL_PLANE_SERVICE_CONFIG_REL_PATH
            job_path = fixture.jobs_dir / f'50_{PROBE_JOB_REF}.json'
            job = json.loads(job_path.read_text(encoding='utf-8'))
            job['artifactPolicy']['runArtifactRoot'] = PROBE_RUNTIME_ENTRY_ID
            job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

            with patch.dict(os.environ, {'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH': str(platform_config), 'OPENCLAW_CONTROL_PLANE_PROFILE': 'agent_platform'}):
                summary = artifact_policies.build_summary(config_path=fixture.service_path, base_root=repo_root)
                resolver = require_path_resolver(repo_root=repo_root, config_path=fixture.service_path)
                expected = resolver.resolve_path(PROBE_RUNTIME_ENTRY_ID, view='host')
            row = next(item for item in summary['items'] if item['id'] == PROBE_JOB_REF)
            self.assertEqual(row['runArtifactRootEntry'], PROBE_RUNTIME_ENTRY_ID)
            self.assertEqual(row['resolvedArtifactRootHostPath'], expected)
            self.assertNotEqual(Path(expected), repo_root / PROBE_RUNTIME_ENTRY_ID)

            with patch.dict(os.environ, {'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH': str(fixture.service_path), 'OPENCLAW_CONTROL_PLANE_PROFILE': fixture.extension_id}):
                summary = artifact_policies.build_summary(config_path=platform_config, base_root=repo_root)
                resolver = require_path_resolver(repo_root=repo_root, config_path=platform_config)
                with self.assertRaises(KeyError):
                    resolver.resolve_path(PROBE_RUNTIME_ENTRY_ID, view='host')
            self.assertEqual(summary['configPath'], platform_config.relative_to(repo_root).as_posix())
            self.assertFalse(any(row['id'] == PROBE_JOB_REF for row in summary['items']))

    def test_unknown_artifact_entry_stays_unresolved_in_summary_and_cli(self) -> None:
        registry = {'jobs': [{'id': 'fixture', 'artifactPolicy': {'runArtifactRoot': 'unregistered_runtime_entry'}}]}
        with patch.object(artifact_policies, 'load_registry', return_value=registry), patch.dict(os.environ, {'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH': str(PLATFORM_CONFIG), 'OPENCLAW_CONTROL_PLANE_PROFILE': 'agent_platform'}):
            summary = artifact_policies.build_summary(config_path=PLATFORM_CONFIG)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(artifact_policies.main(['json']), 0)
        self.assertIsNone(summary['items'][0]['resolvedArtifactRootHostPath'])
        self.assertIsNone(json.loads(output.getvalue())['items'][0]['resolvedArtifactRootHostPath'])
        self.assertIsNone(artifact_policies._resolved_artifact_root('control_plane/not_registered', ROOT_DIR, path_resolver=require_path_resolver(repo_root=ROOT_DIR, config_path=PLATFORM_CONFIG)))

    def test_summary_loads_profile_resolver_once_and_reuses_it(self) -> None:
        resolver = Mock()
        resolver.resolve_path.return_value = 'state/injected'
        with patch.object(artifact_policies, 'require_path_resolver', return_value=resolver) as loader:
            summary = artifact_policies.build_summary(config_path=PLATFORM_CONFIG, registry={})
        loader.assert_called_once_with(repo_root=ROOT_DIR, config_path=PLATFORM_CONFIG)
        self.assertEqual(summary['schedulerRunsRoot'], 'state/injected/control_plane_scheduler/runs')


if __name__ == '__main__':
    unittest.main()
