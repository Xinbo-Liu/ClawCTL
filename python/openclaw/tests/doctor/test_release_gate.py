from __future__ import annotations

import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openclaw.doctor.release import repo_release_gate
from openclaw.doctor.release import repo_release_gate_support
from openclaw.lib.repo import verification_tiers
from openclaw.lib.repo.verification_tiers import verification_tier_rows
from openclaw.lib.runtime.bounded_process import BoundedProcessResult


class RepoReleaseGateModeTest(unittest.TestCase):
    def _process_outcome(
        self,
        exit_code: int | None,
        stdout: str = '',
        *,
        stderr: str = '',
        duration: float = 0.25,
        timed_out: bool = False,
    ) -> BoundedProcessResult:
        return BoundedProcessResult(exit_code, stdout, stderr, duration, timed_out)

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
            return_value=self._process_outcome(
                2,
                stderr='[python_container] 未检测到 docker；Python 工具必须通过控制面容器执行，请先安装并启动 Docker。',
            ),
        ) as mocked_run:
            result = repo_release_gate.run_check(self._spec(), quiet=True, json_output=True)

        self.assertEqual(result.status, 'FAIL')
        self.assertEqual(result.mode, repo_release_gate.STRICT_MODE)
        self.assertEqual(mocked_run.call_count, 1)

    def test_run_check_fails_when_json_status_fail_has_zero_exit_code(self) -> None:
        with patch.object(
            repo_release_gate,
            'run_command',
            return_value=self._process_outcome(0, '{"status": "fail", "issues": ["stack lock drift"]}'),
        ) as mocked_run:
            result = repo_release_gate.run_check(self._spec('stack_lock_verify'), quiet=True, json_output=True)

        self.assertEqual(result.status, 'FAIL')
        self.assertEqual(result.mode, repo_release_gate.STRICT_MODE)
        self.assertIn('JSON 顶层 status=fail', result.detail)
        self.assertEqual(mocked_run.call_count, 1)

    def test_run_check_fails_when_json_ok_false_has_zero_exit_code(self) -> None:
        with patch.object(
            repo_release_gate,
            'run_command',
            return_value=self._process_outcome(0, '{"ok": false, "offenders": ["probe"]}'),
        ):
            result = repo_release_gate.run_check(self._spec('json_probe'), quiet=True, json_output=True)

        self.assertEqual(result.status, 'FAIL')
        self.assertIn('JSON 顶层 ok=false', result.detail)

    def test_run_check_fails_when_json_failure_is_wrapped_by_logs(self) -> None:
        detail = '\n'.join([
            '[probe] starting',
            '{"status": "blocked", "issues": ["release lock missing"]}',
            '[probe] done',
        ])
        with patch.object(repo_release_gate, 'run_command', return_value=self._process_outcome(0, detail)):
            result = repo_release_gate.run_check(self._spec('wrapped_json_probe'), quiet=True, json_output=True)

        self.assertEqual(result.status, 'FAIL')
        self.assertIn('JSON 顶层 status=blocked', result.detail)

    def test_run_check_fails_when_nested_json_summary_reports_failures(self) -> None:
        detail = '{"summary": {"pass": 9, "fail": 1}, "results": [{"status": "PASS"}]}'
        with patch.object(repo_release_gate, 'run_command', return_value=self._process_outcome(0, detail)):
            result = repo_release_gate.run_check(self._spec('summary_json_probe'), quiet=True, json_output=True)

        self.assertEqual(result.status, 'FAIL')
        self.assertIn('JSON 顶层 summary fail=1', result.detail)

    def test_run_generated_docs_check_stays_strict_when_container_is_unavailable(self) -> None:
        with patch.object(
            repo_release_gate,
            'run_command',
            return_value=self._process_outcome(
                2,
                stderr='[python_container] 未检测到 docker；Python 工具必须通过控制面容器执行，请先安装并启动 Docker。',
            ),
        ) as mocked_run:
            result = repo_release_gate.run_generated_docs_check(quiet=True, json_output=True)

        self.assertEqual(result.status, 'FAIL')
        self.assertEqual(result.mode, repo_release_gate.STRICT_MODE)
        self.assertEqual(mocked_run.call_count, 1)

    def test_documentation_batch_runs_once_and_preserves_child_results(self) -> None:
        specs = [
            self._spec('docs_registry_sync'),
            self._spec('documentation_navigation'),
        ]
        payload = {
            'suite': 'documentation_validators',
            'status': 'FAIL',
            'checks': [
                {
                    'id': 'docs_registry_sync',
                    'status': 'PASS',
                    'exitCode': 0,
                    'timedOut': False,
                    'durationSeconds': 0.2,
                    'detail': '',
                },
                {
                    'id': 'documentation_navigation',
                    'status': 'FAIL',
                    'exitCode': 1,
                    'timedOut': False,
                    'durationSeconds': 0.3,
                    'detail': 'missing anchor',
                },
            ],
        }
        with patch.object(
            repo_release_gate,
            'run_command',
            return_value=self._process_outcome(1, __import__('json').dumps(payload)),
        ) as run_command:
            results = repo_release_gate.run_documentation_batch(specs, quiet=True, json_output=True)

        self.assertEqual(run_command.call_count, 1)
        self.assertEqual([item.check_id for item in results], [item.check_id for item in specs])
        self.assertEqual([item.status for item in results], ['PASS', 'FAIL'])
        self.assertEqual(results[1].detail, 'missing anchor')
        self.assertEqual(results[1].duration_seconds, 0.3)

    def test_parse_args_supports_quiet_and_json(self) -> None:
        quiet, json_output, lanes = repo_release_gate.parse_args(
            ['--quiet', '--json', '--lane', 'static', '--lane', 'integration']
        )

        self.assertTrue(quiet)
        self.assertTrue(json_output)
        self.assertEqual(lanes, ('static', 'integration'))

    def test_base_checks_include_host_python_governance(self) -> None:
        self.assertIn('host_python_governance', [item.check_id for item in repo_release_gate.base_checks()])
        self.assertIn('docstring_governance', [item.check_id for item in repo_release_gate.base_checks()])
        self.assertIn('centos7_host_shell_guard', [item.check_id for item in repo_release_gate.base_checks()])
        self.assertIn('import_closure', [item.check_id for item in repo_release_gate.base_checks()])
        self.assertIn('cold_start_imports', [item.check_id for item in repo_release_gate.base_checks()])
        self.assertIn('shell_pythonpath_contract', [item.check_id for item in repo_release_gate.base_checks()])
        self.assertIn('stack_lock_verify', [item.check_id for item in repo_release_gate.base_checks()])

    def test_ordered_checks_filter_lanes_without_changing_default(self) -> None:
        all_checks = repo_release_gate.ordered_check_specs()
        static_checks = repo_release_gate.ordered_check_specs(('static',))

        self.assertGreater(len(all_checks), len(static_checks))
        self.assertTrue(all(item.lane == 'static' for item in static_checks))
        self.assertIn('cold_start_imports', [item.check_id for item in all_checks])
        self.assertNotIn('cold_start_imports', [item.check_id for item in static_checks])

    def test_managed_static_governance_checks_are_batch_eligible(self) -> None:
        eligible = [
            item
            for item in repo_release_gate_support.managed_extension_release_checks()
            if repo_release_gate_support.is_agent_governance_batch_spec(item)
        ]

        self.assertTrue(eligible)
        self.assertTrue(all(item.lane == 'static' for item in eligible))
        self.assertFalse(
            repo_release_gate_support.is_agent_governance_batch_spec(
                repo_release_gate_support.CheckSpec(
                    'lifecycle',
                    'Lifecycle',
                    'bash ./scripts/doctor/check_agent_module_lifecycle_matrix.sh',
                    (
                        'bash',
                        str(
                            repo_release_gate_support.ROOT_DIR
                            / 'scripts/doctor/check_agent_module_lifecycle_matrix.sh'
                        ),
                    ),
                    lane='integration',
                )
            )
        )

    def test_managed_release_check_requires_explicit_lane_and_timeout(self) -> None:
        base_row = {
            'id': 'probe',
            'title': 'Probe',
            'command': {'script': 'scripts/doctor/check_agent_governance_baseline.sh', 'args': []},
        }
        with self.assertRaisesRegex(ValueError, '必须显式声明 lane'):
            repo_release_gate_support._release_gate_check_spec(
                base_row,
                extension_id='agent_probe',
                profile_id='agent_probe',
                config_path=Path('agent_probe.service.json'),
            )
        with self.assertRaisesRegex(ValueError, '必须显式声明 timeoutSeconds'):
            repo_release_gate_support._release_gate_check_spec(
                base_row | {'lane': 'static'},
                extension_id='agent_probe',
                profile_id='agent_probe',
                config_path=Path('agent_probe.service.json'),
            )

    def test_render_json_adds_duration_timeout_and_lane_fields(self) -> None:
        result = repo_release_gate.CheckResult(
            'probe',
            'Probe',
            'probe command',
            'FAIL',
            'timed out',
            lane='integration',
            duration_seconds=3.5,
            exit_code=None,
            timed_out=True,
        )

        payload = __import__('json').loads(repo_release_gate.render_json([result]))

        self.assertEqual(payload['summary']['durationSeconds'], 3.5)
        self.assertEqual(payload['summary']['slowestCheck']['id'], 'probe')
        self.assertEqual(payload['checks'][0]['lane'], 'integration')
        self.assertTrue(payload['checks'][0]['timedOut'])

    def test_render_json_prefers_explicit_wall_duration(self) -> None:
        result = repo_release_gate.CheckResult(
            'probe',
            'Probe',
            'probe command',
            'PASS',
            '',
            duration_seconds=3.5,
        )

        payload = __import__('json').loads(
            repo_release_gate.render_json([result], duration_seconds=1.25)
        )

        self.assertEqual(payload['summary']['durationSeconds'], 1.25)

    def test_multiple_lanes_run_concurrently_and_preserve_declared_order(self) -> None:
        specs = [
            repo_release_gate.CheckSpec('static-first', 'Static', 'static', (), lane='static'),
            repo_release_gate.CheckSpec('integration-middle', 'Integration', 'integration', (), lane='integration'),
            repo_release_gate.CheckSpec('static-last', 'Static last', 'static-last', (), lane='static'),
        ]
        barrier = threading.Barrier(2)

        def fake_run_specs(
            lane_specs: list[repo_release_gate.CheckSpec],
            *,
            quiet: bool,
            json_output: bool,
        ) -> list[repo_release_gate.CheckResult]:
            self.assertTrue(quiet)
            self.assertTrue(json_output)
            barrier.wait(timeout=2)
            return [
                repo_release_gate.CheckResult(
                    spec.check_id,
                    spec.title,
                    spec.command_text,
                    'PASS',
                    '',
                    lane=spec.lane,
                )
                for spec in lane_specs
            ]

        with patch.object(repo_release_gate, '_run_specs', side_effect=fake_run_specs):
            results = repo_release_gate._run_selected_specs(specs, quiet=True, json_output=True)

        self.assertEqual(
            [item.check_id for item in results],
            ['static-first', 'integration-middle', 'static-last'],
        )

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

        self.assertTrue(expected_specs)
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
        self.assertIn('bash ./scripts/doctor/check_repo_prod_docstring_governance.sh --scope repo-prod --mode report', usage)
        self.assertIn('静态 Python 检查仍固定要求 Docker 与控制面执行介质', usage)
        self.assertIn('验证层级：', usage)
        self.assertIn('正式 Docker / 控制面容器门禁（正式门禁）', usage)
        self.assertIn('宿主机静态前置检查（诊断补充）', usage)
        self.assertIn('不得执行仓库 Python', usage)
        self.assertIn('config/control_plane/profile_registry.tsv', usage)
        self.assertIn('release_gate_checks 声明', usage)
        self.assertIn(repo_release_gate_support.managed_extension_release_summary(), usage)
        self.assertIn('不依赖默认 agent_platform 空业务面', usage)
        self.assertIn('并发执行 lane，每个 lane 内仍保持声明顺序', usage)
        self.assertIn('apply_ingress_boundary_rules -> fix_permissions -> one_click_test_basic', usage)

    def test_verification_tiers_truth_declares_release_and_diagnostic_layers(self) -> None:
        tiers = {row['id']: row for row in verification_tier_rows()}

        self.assertTrue(tiers['official_release']['release_required'])
        self.assertFalse(tiers['official_release']['diagnostic_only'])
        self.assertFalse(tiers['host_diagnostic']['release_required'])
        self.assertTrue(tiers['host_diagnostic']['diagnostic_only'])
        self.assertIn('bash ./scripts/doctor/run_repo_release_gate.sh', tiers['official_release']['commands'])
        self.assertIn('bash ./scripts/doctor/check_host_python_governance.sh --json', tiers['host_diagnostic']['commands'])
        self.assertFalse(any('python -m' in item for item in tiers['host_diagnostic']['commands']))

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
