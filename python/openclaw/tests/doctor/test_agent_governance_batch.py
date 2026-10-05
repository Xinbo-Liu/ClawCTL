"""验证受管扩展静态治理批处理复用昂贵输入并保持检查身份。"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from openclaw.doctor.agent_modules import governance_batch
from openclaw.doctor.release.repo_release_gate_support import CheckSpec


def _spec(check_id: str, script_name: str, *args: str) -> CheckSpec:
    script_path = governance_batch.ROOT_DIR / 'scripts' / 'doctor' / script_name
    return CheckSpec(
        check_id,
        check_id,
        f'bash ./scripts/doctor/{script_name}',
        ('bash', str(script_path), *args),
        lane='static',
        timeout_seconds=120,
    )


class AgentGovernanceBatchTest(unittest.TestCase):
    def test_batch_reuses_registry_per_profile_and_corpus_across_extensions(self) -> None:
        specs = [
            _spec('orphan_alpha', 'check_agent_runtime_script_orphans.sh', '--extension', 'alpha'),
            _spec('orphan_beta', 'check_agent_runtime_script_orphans.sh', '--extension', 'beta'),
            _spec('baseline_alpha', 'check_agent_governance_baseline.sh', '--control-plane-profile', 'alpha'),
            _spec('optional_alpha', 'check_agent_module_optional_surface.sh', '--control-plane-profile', 'alpha'),
            _spec('jobs_alpha', 'check_agent_job_surface.sh', '--control-plane-profile', 'alpha'),
        ]
        registry = {'agentModules': [], 'jobs': []}
        config_path = Path('/profiles/alpha.service.json')
        with (
            mock.patch.object(governance_batch.runtime_script_orphans, 'repo_text_files', return_value=[]) as corpus,
            mock.patch.object(
                governance_batch.runtime_script_orphans,
                'build_orphan_report',
                return_value={'ok': True, 'orphanScripts': []},
            ) as orphan_report,
            mock.patch.object(governance_batch, '_config_path', return_value=config_path),
            mock.patch.object(governance_batch, 'load_registry', return_value=registry) as load_registry,
            mock.patch.object(
                governance_batch,
                'run_governance_baseline_check',
                return_value={'errors': []},
            ) as baseline,
            mock.patch.object(governance_batch.optional_surface, 'build_report', return_value={'ok': True}),
            mock.patch.object(governance_batch.job_surface, 'build_report', return_value={'ok': True}),
        ):
            report = governance_batch.build_report(specs)

        self.assertEqual(report['status'], 'PASS')
        self.assertEqual([item['id'] for item in report['checks']], [spec.check_id for spec in specs])
        self.assertEqual(corpus.call_count, 1)
        self.assertEqual(orphan_report.call_count, 2)
        self.assertEqual(load_registry.call_count, 1)
        baseline.assert_called_once_with(config_path, registry_payload=registry)


if __name__ == '__main__':
    unittest.main()
