from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openclaw.doctor.release import repo_release_gate
from openclaw.doctor.release import repo_release_gate_support
from openclaw.lib.repo import verification_tiers
from openclaw.lib.repo.verification_tiers import verification_tier_rows


class RepoReleaseGateModeTest(unittest.TestCase):
    def _spec(self, check_id: str = 'docs_registry_sync') -> repo_release_gate.CheckSpec:
        return repo_release_gate.CheckSpec(
            check_id=check_id,
            title='dummy',
            command_text='bash ./dummy.sh',
            command=('bash', './dummy.sh'),
        )

    def test_run_check_stays_strict_when_container_is_unavailable(self) -> None:
        with patch.object(
            repo_release_gate,
            'run_command',
            return_value=(2, '[python_container] 未检测到 docker；Python 工具必须通过控制面容器执行，请先安装并启动 Docker。'),
        ) as mocked_run:
            result = repo_release_gate.run_check(self._spec(), quiet=True, json_output=True)

        self.assertEqual(result.status, 'FAIL')
        self.assertEqual(result.mode, repo_release_gate.STRICT_MODE)
        self.assertEqual(mocked_run.call_count, 1)

    def test_run_generated_docs_check_stays_strict_when_container_is_unavailable(self) -> None:
        with patch.object(
            repo_release_gate,
            'run_command',
            return_value=(2, '[python_container] 未检测到 docker；Python 工具必须通过控制面容器执行，请先安装并启动 Docker。'),
        ) as mocked_run:
            result = repo_release_gate.run_generated_docs_check(quiet=True, json_output=True)

        self.assertEqual(result.status, 'FAIL')
        self.assertEqual(result.mode, repo_release_gate.STRICT_MODE)
        self.assertEqual(mocked_run.call_count, 1)

    def test_parse_args_supports_quiet_and_json(self) -> None:
        quiet, json_output = repo_release_gate.parse_args(['--quiet', '--json'])

        self.assertTrue(quiet)
        self.assertTrue(json_output)

    def test_base_checks_include_host_python_governance(self) -> None:
        self.assertIn('host_python_governance', [item.check_id for item in repo_release_gate.base_checks()])
        self.assertIn('platform_docstring_governance', [item.check_id for item in repo_release_gate.base_checks()])
        self.assertIn('centos7_host_shell_guard', [item.check_id for item in repo_release_gate.base_checks()])
        self.assertIn('cold_start_imports', [item.check_id for item in repo_release_gate.base_checks()])
        self.assertIn('shell_pythonpath_contract', [item.check_id for item in repo_release_gate.base_checks()])
        self.assertIn('stack_lock_verify', [item.check_id for item in repo_release_gate.base_checks()])

    def test_stack_lock_verify_uses_control_plane_stack_cli(self) -> None:
        checks = {item.check_id: item for item in repo_release_gate.base_checks()}
        spec = checks['stack_lock_verify']

        self.assertEqual(spec.command_text, 'openclaw control-plane stack verify --strict-release --json')
        self.assertEqual(
            tuple(spec.command),
            (
                repo_release_gate_support.sys.executable,
                '-m',
                'openclaw.cli',
                'control-plane',
                'stack',
                'verify',
                '--strict-release',
                '--json',
            ),
        )

    def test_base_checks_include_managed_extension_release_gate_checks(self) -> None:
        checks = {item.check_id: item for item in repo_release_gate.base_checks()}
        expected_specs = {
            item.check_id: item
            for item in repo_release_gate_support.managed_extension_release_checks()
        }

        if not expected_specs:
            self.assertFalse(
                [check_id for check_id in checks if check_id.startswith('agent_module_smoke_tests_')]
            )
            return
        for check_id, expected_spec in expected_specs.items():
            with self.subTest(check_id=check_id):
                self.assertIn(check_id, checks)
                self.assertEqual(checks[check_id].command_text, expected_spec.command_text)
                self.assertEqual(tuple(checks[check_id].command), tuple(expected_spec.command))

    def test_git_bash_candidates_are_resolved_from_install_markers(self) -> None:
        with TemporaryDirectory() as tmpdir:
            git_root = Path(tmpdir) / 'Git'
            git_executable = git_root / 'cmd' / 'git.exe'
            bash_executable = git_root / 'bin' / 'bash.exe'
            git_executable.parent.mkdir(parents=True)
            bash_executable.parent.mkdir(parents=True)
            git_executable.write_text('', encoding='utf-8')
            bash_executable.write_text('', encoding='utf-8')

            self.assertEqual(
                repo_release_gate_support._git_bash_candidates(str(git_executable)),
                [str(bash_executable.resolve())],
            )

    def test_usage_tracks_actual_check_order(self) -> None:
        usage = repo_release_gate.usage()
        expected_lines = [
            f'    {index}. {spec.title}'
            for index, spec in enumerate(repo_release_gate.ordered_check_specs(), start=1)
        ]

        for line in expected_lines:
            self.assertIn(line, usage)
        self.assertIn('bash ./scripts/testing/check_repo_test_readiness.sh', usage)
        self.assertIn('--with-docker-sock', usage)
        self.assertIn('可独立于完整 release gate 运行的前置检查入口：', usage)
        self.assertIn('bash ./scripts/doctor/check_host_python_governance.sh', usage)
        self.assertIn('bash ./scripts/doctor/check_platform_docstring_governance.sh --mode report', usage)
        self.assertIn('静态 Python 检查仍固定要求 Docker 与控制面执行介质', usage)
        self.assertIn('验证层级：', usage)
        self.assertIn('正式 Docker / 控制面容器门禁（正式门禁）', usage)
        self.assertIn('Windows 宿主机诊断回归（诊断补充）', usage)
        self.assertIn('不得替代正式 Docker / 控制面容器门禁', usage)
        self.assertIn('config/control_plane/profile_registry.tsv', usage)
        self.assertIn('release_gate_checks 声明', usage)
        self.assertIn(repo_release_gate_support.managed_extension_release_summary(), usage)
        self.assertIn('不依赖默认 agent_platform 空业务面', usage)
        self.assertIn('apply_ingress_boundary_rules -> fix_permissions -> one_click_test_basic', usage)

    def test_verification_tiers_truth_declares_release_and_diagnostic_layers(self) -> None:
        tiers = {row['id']: row for row in verification_tier_rows()}

        self.assertTrue(tiers['official_release']['release_required'])
        self.assertFalse(tiers['official_release']['diagnostic_only'])
        self.assertFalse(tiers['host_diagnostic']['release_required'])
        self.assertTrue(tiers['host_diagnostic']['diagnostic_only'])
        self.assertIn('bash ./scripts/doctor/run_repo_release_gate.sh', tiers['official_release']['commands'])
        self.assertTrue(any('openclaw.testing.repo_host' in item for item in tiers['host_diagnostic']['commands']))

    def test_verification_tiers_reject_ambiguous_layer_flags(self) -> None:
        payload = {
            'schemaVersion': 1,
            'tiers': [
                {
                    'id': 'ambiguous',
                    'title': '模糊层',
                    'description': '没有清晰区分正式门禁和诊断补充。',
                    'releaseRequired': False,
                    'diagnosticOnly': False,
                    'commands': ['bash ./dummy.sh'],
                }
            ],
        }

        with patch.object(verification_tiers, 'read_repo_contract_json', return_value=payload):
            with self.assertRaisesRegex(ValueError, '必须且只能属于正式门禁或诊断补充之一'):
                verification_tiers.load_verification_tiers()

    def test_verification_tiers_reject_empty_command_items(self) -> None:
        payload = {
            'schemaVersion': 1,
            'tiers': [
                {
                    'id': 'official_release',
                    'title': '正式门禁',
                    'description': '命令不能为空。',
                    'releaseRequired': True,
                    'diagnosticOnly': False,
                    'commands': [''],
                }
            ],
        }

        with patch.object(verification_tiers, 'read_repo_contract_json', return_value=payload):
            with self.assertRaisesRegex(ValueError, '缺少 title、description 或 commands'):
                verification_tiers.load_verification_tiers()


if __name__ == '__main__':
    unittest.main()
