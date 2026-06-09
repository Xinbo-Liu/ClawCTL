from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from unittest import mock

from openclaw.lib.testing import full_test as surface
from openclaw.tests.support.helpers import isolated_test_root


class FullTestSurfaceControlPlaneTest(unittest.TestCase):
    def _write_lines(self, path: Path, lines: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    def _ledger_snapshot(self) -> dict[str, object]:
        return {
            'exists': True,
            'generated_at': '2026-04-19T10:00:00Z',
            'required_jobs': ['job_alpha'],
            'job_states': [
                {
                    'id': 'job_alpha',
                    'exists': True,
                    'artifact_accepted': True,
                    'effective_artifact_accepted': True,
                    'execution_accepted': True,
                    'effective_execution_accepted': True,
                    'current_status': 'ok',
                    'last_finished_at': '2026-04-19T09:59:00Z',
                    'issues': [],
                }
            ],
            'accepted': True,
            'artifact_accepted_jobs': ['job_alpha'],
            'artifact_missing_jobs': [],
            'execution_accepted_jobs': ['job_alpha'],
            'missing_jobs': [],
            'failing_jobs': [],
            'artifact_failing_jobs': [],
            'counts': {'ok': 1},
            'error': None,
        }

    def test_parse_args_normalizes_paths_and_flags(self) -> None:
        with isolated_test_root('full-test-surface-args') as root:
            options = surface.parse_args([
                '--format', 'json',
                '--out-json', str(root / 'summary.json'),
                '--out-md', str(root / 'summary.md'),
                '--generated-at', '2026-04-19T10:00:00Z',
                '--env-file', 'deploy/.env',
                '--group', 'all',
                '--only', 'compose_ps',
                '--skip', 'gateway_https_root',
                '--strict', 'true',
                '--quiet', 'false',
                '--json-stdout', '0',
                '--return-code', '7',
                '--result-lines-file', str(root / 'results.txt'),
                '--next-actions-file', str(root / 'next.txt'),
                '--acceptance-state', str(root / 'acceptance.json'),
                '--required-acceptance-ids', 'compose_ps',
                '--csv', 'compose_ps,gateway_https_root',
                '--flag', '--csv',
            ])

        self.assertEqual(options['format'], 'json')
        self.assertTrue(options['outJson'].endswith('summary.json'))
        self.assertTrue(options['outMd'].endswith('summary.md'))
        self.assertTrue(options['resultLinesFile'].endswith('results.txt'))
        self.assertTrue(options['nextActionsFile'].endswith('next.txt'))
        self.assertTrue(options['acceptanceState'].endswith('acceptance.json'))
        self.assertTrue(options['strict'])
        self.assertFalse(options['quiet'])
        self.assertFalse(options['jsonStdout'])
        self.assertEqual(options['returnCode'], 7)
        self.assertEqual(options['csv'], 'compose_ps,gateway_https_root')
        self.assertEqual(options['flagName'], '--csv')

    def test_write_acceptance_state_renders_shell_and_kv_lines(self) -> None:
        with isolated_test_root('full-test-surface-acceptance') as root:
            result_lines = root / 'results.txt'
            output_path = root / 'acceptance.json'
            self._write_lines(
                result_lines,
                [
                    'PASS|compose_ps|compose up|service',
                    'FAIL|gateway_https_root|gateway unhealthy|service',
                ],
            )
            options = {
                'group': 'all',
                'only': '',
                'skip': '',
                'resultLinesFile': str(result_lines),
                'outJson': str(output_path),
                'generatedAt': '2026-04-19T10:00:00Z',
                'envFile': 'deploy/.env',
            }

            with mock.patch('openclaw.lib.testing.full_test.acceptance.summarize_required_run_ledger', return_value=self._ledger_snapshot()):
                status = surface.write_acceptance_state(options)

            payload = json.loads(output_path.read_text(encoding='utf-8'))
            shell_lines = surface.render_acceptance_shell(status)
            kv_lines = surface.render_acceptance_kv_lines(status)

        self.assertTrue(status['eligible'])
        self.assertFalse(status['accepted'])
        self.assertEqual(status['contract']['status'], 'FAIL')
        self.assertEqual(payload['schema_version'], 2)
        self.assertEqual(payload['suite'], 'one_click_test_full')
        self.assertEqual(payload['run_ledger_snapshot']['required_jobs'], ['job_alpha'])
        self.assertFalse(payload['run_ledger_policy']['blocking'])
        self.assertIn(payload['run_ledger_policy']['reason_code'], {'accepted', 'not_required'})
        self.assertIn('FULL_TEST_ACCEPTANCE_ELIGIBLE=true', shell_lines)
        self.assertIn('compose_ps=PASS', shell_lines)
        self.assertIn('gateway_https_root=FAIL', shell_lines)
        self.assertIn('FULL_TEST_ACCEPTANCE_CONTRACT_STATUS=FAIL', kv_lines)

    def test_missing_required_run_ledger_jobs_are_acceptance_gate(self) -> None:
        with isolated_test_root('full-test-surface-run-ledger-gate') as root:
            result_lines = root / 'results.txt'
            self._write_lines(result_lines, ['PASS|compose_ps|compose up|service'])
            manifest = {
                'groups': [{'id': 'service'}],
                'checks': [{'id': 'compose_ps', 'group': 'service'}],
                'acceptance_contract': {
                    'check_id': 'deployment_acceptance_contract',
                    'group': 'acceptance',
                    'eligible_when': {'group': 'all', 'only_empty': True, 'skip_empty': True},
                },
                'acceptance_reference': {
                    'required_checks': ['compose_ps'],
                    'required_run_ledger_jobs': ['job_alpha'],
                },
            }
            ledger = self._ledger_snapshot()
            ledger['accepted'] = False
            ledger['execution_accepted_jobs'] = []
            ledger['missing_jobs'] = ['job_alpha']
            ledger['job_states'][0]['exists'] = False
            ledger['job_states'][0]['artifact_accepted'] = None
            ledger['job_states'][0]['effective_artifact_accepted'] = None
            ledger['job_states'][0]['execution_accepted'] = None
            ledger['job_states'][0]['effective_execution_accepted'] = None
            ledger['job_states'][0]['issues'] = ['job_not_found_in_run_ledger']

            with mock.patch('openclaw.lib.testing.full_test.acceptance.summarize_required_run_ledger', return_value=ledger):
                status = surface.build_acceptance_status(
                    {'group': 'all', 'only': '', 'skip': '', 'resultLinesFile': str(result_lines)},
                    manifest,
                )

        self.assertFalse(status['accepted'])
        self.assertEqual(status['contract']['status'], 'FAIL')
        self.assertTrue(status['run_ledger_policy']['blocking'])
        self.assertEqual(status['run_ledger_policy']['reason_code'], 'missing_required_run_ledger_jobs')

    def test_failing_required_run_ledger_jobs_block_full_test_acceptance(self) -> None:
        with isolated_test_root('full-test-surface-run-ledger-blocking') as root:
            result_lines = root / 'results.txt'
            self._write_lines(result_lines, ['PASS|compose_ps|compose up|service'])
            manifest = {
                'groups': [{'id': 'service'}],
                'checks': [{'id': 'compose_ps', 'group': 'service'}],
                'acceptance_contract': {
                    'check_id': 'deployment_acceptance_contract',
                    'group': 'acceptance',
                    'eligible_when': {'group': 'all', 'only_empty': True, 'skip_empty': True},
                },
                'acceptance_reference': {
                    'required_checks': ['compose_ps'],
                    'required_run_ledger_jobs': ['job_alpha'],
                },
            }
            ledger = self._ledger_snapshot()
            ledger['accepted'] = False
            ledger['execution_accepted_jobs'] = []
            ledger['failing_jobs'] = ['job_alpha']
            ledger['job_states'][0]['artifact_accepted'] = True
            ledger['job_states'][0]['effective_artifact_accepted'] = True
            ledger['job_states'][0]['execution_accepted'] = False
            ledger['job_states'][0]['effective_execution_accepted'] = False
            ledger['job_states'][0]['issues'] = ['result_status=failed']

            with mock.patch('openclaw.lib.testing.full_test.acceptance.summarize_required_run_ledger', return_value=ledger):
                status = surface.build_acceptance_status(
                    {'group': 'all', 'only': '', 'skip': '', 'resultLinesFile': str(result_lines)},
                    manifest,
                )

        self.assertFalse(status['accepted'])
        self.assertEqual(status['contract']['status'], 'FAIL')
        self.assertTrue(status['run_ledger_policy']['blocking'])
        self.assertEqual(status['run_ledger_policy']['reason_code'], 'run_ledger_not_accepted')

    def test_artifact_only_run_ledger_gaps_block_full_test_acceptance(self) -> None:
        with isolated_test_root('full-test-surface-run-ledger-artifact-gap') as root:
            result_lines = root / 'results.txt'
            self._write_lines(result_lines, ['PASS|compose_ps|compose up|service'])
            manifest = {
                'groups': [{'id': 'service'}],
                'checks': [{'id': 'compose_ps', 'group': 'service'}],
                'acceptance_contract': {
                    'check_id': 'deployment_acceptance_contract',
                    'group': 'acceptance',
                    'eligible_when': {'group': 'all', 'only_empty': True, 'skip_empty': True},
                },
                'acceptance_reference': {
                    'required_checks': ['compose_ps'],
                    'required_run_ledger_jobs': ['job_alpha'],
                },
            }
            ledger = self._ledger_snapshot()
            ledger['accepted'] = False
            ledger['artifact_accepted_jobs'] = []
            ledger['execution_accepted_jobs'] = ['job_alpha']
            ledger['failing_jobs'] = []
            ledger['artifact_failing_jobs'] = ['job_alpha']
            ledger['job_states'][0]['artifact_accepted'] = False
            ledger['job_states'][0]['effective_artifact_accepted'] = False
            ledger['job_states'][0]['execution_accepted'] = True
            ledger['job_states'][0]['effective_execution_accepted'] = True
            ledger['job_states'][0]['issues'] = ['declared_outputs_without_observed_evidence']

            with mock.patch('openclaw.lib.testing.full_test.acceptance.summarize_required_run_ledger', return_value=ledger):
                status = surface.build_acceptance_status(
                    {'group': 'all', 'only': '', 'skip': '', 'resultLinesFile': str(result_lines)},
                    manifest,
                )

        self.assertFalse(status['accepted'])
        self.assertEqual(status['contract']['status'], 'FAIL')
        self.assertTrue(status['run_ledger_policy']['blocking'])
        self.assertEqual(status['run_ledger_policy']['reason_code'], 'artifact_run_ledger_not_accepted')
        self.assertEqual(status['run_ledger_policy']['artifact_failing_jobs'], ['job_alpha'])

    def test_missing_artifact_evidence_blocks_full_test_acceptance(self) -> None:
        with isolated_test_root('full-test-surface-run-ledger-artifact-missing') as root:
            result_lines = root / 'results.txt'
            self._write_lines(result_lines, ['PASS|compose_ps|compose up|service'])
            manifest = {
                'groups': [{'id': 'service'}],
                'checks': [{'id': 'compose_ps', 'group': 'service'}],
                'acceptance_contract': {
                    'check_id': 'deployment_acceptance_contract',
                    'group': 'acceptance',
                    'eligible_when': {'group': 'all', 'only_empty': True, 'skip_empty': True},
                },
                'acceptance_reference': {
                    'required_checks': ['compose_ps'],
                    'required_run_ledger_jobs': ['job_alpha'],
                },
            }
            ledger = self._ledger_snapshot()
            ledger['accepted'] = False
            ledger['artifact_accepted_jobs'] = []
            ledger['artifact_missing_jobs'] = ['job_alpha']
            ledger['job_states'][0]['artifact_accepted'] = None
            ledger['job_states'][0]['effective_artifact_accepted'] = None
            ledger['job_states'][0]['execution_accepted'] = True
            ledger['job_states'][0]['effective_execution_accepted'] = True
            ledger['job_states'][0]['issues'] = ['missing_artifacts_manifest']

            with mock.patch('openclaw.lib.testing.full_test.acceptance.summarize_required_run_ledger', return_value=ledger):
                status = surface.build_acceptance_status(
                    {'group': 'all', 'only': '', 'skip': '', 'resultLinesFile': str(result_lines)},
                    manifest,
                )

        self.assertFalse(status['accepted'])
        self.assertTrue(status['run_ledger_policy']['blocking'])
        self.assertEqual(status['run_ledger_policy']['reason_code'], 'artifact_run_ledger_not_accepted')
        self.assertEqual(status['run_ledger_policy']['artifact_missing_jobs'], ['job_alpha'])

    def test_required_run_ledger_jobs_allow_acceptance_when_all_pass(self) -> None:
        with isolated_test_root('full-test-surface-run-ledger-pass') as root:
            result_lines = root / 'results.txt'
            self._write_lines(result_lines, ['PASS|compose_ps|compose up|service'])
            manifest = {
                'groups': [{'id': 'service'}],
                'checks': [{'id': 'compose_ps', 'group': 'service'}],
                'acceptance_contract': {
                    'check_id': 'deployment_acceptance_contract',
                    'group': 'acceptance',
                    'eligible_when': {'group': 'all', 'only_empty': True, 'skip_empty': True},
                },
                'acceptance_reference': {
                    'required_checks': ['compose_ps'],
                    'required_run_ledger_jobs': ['job_alpha'],
                },
            }

            with mock.patch('openclaw.lib.testing.full_test.acceptance.summarize_required_run_ledger', return_value=self._ledger_snapshot()):
                status = surface.build_acceptance_status(
                    {'group': 'all', 'only': '', 'skip': '', 'resultLinesFile': str(result_lines)},
                    manifest,
                )

        self.assertTrue(status['accepted'])
        self.assertEqual(status['contract']['status'], 'PASS')
        self.assertEqual(status['run_ledger_policy']['reason_code'], 'accepted')

    def test_result_lines_preserve_duration_as_structured_metadata(self) -> None:
        parsed = surface.parse_result_line(
            'PASS|internal_api_runtime|ready [full_test_duration_seconds=42]|service'
        )
        basic_parsed = surface.parse_result_line(
            'PASS|check_docker_host_readiness|ready [setup_gate_duration_seconds=11]|host'
        )

        self.assertEqual(parsed['id'], 'internal_api_runtime')
        self.assertEqual(parsed['detail'], 'ready')
        self.assertEqual(parsed['duration_seconds'], 42)
        self.assertEqual(basic_parsed['id'], 'check_docker_host_readiness')
        self.assertEqual(basic_parsed['detail'], 'ready')
        self.assertEqual(basic_parsed['duration_seconds'], 11)

    def test_write_summary_and_main_dispatch_preserve_rendering_contract(self) -> None:
        with isolated_test_root('full-test-surface-summary') as root:
            result_lines = root / 'results.txt'
            next_actions = root / 'next.txt'
            out_json = root / 'summary.json'
            out_md = root / 'summary.md'
            latest_json = root / 'latest.json'
            latest_md = root / 'latest.md'
            acceptance_state = root / 'acceptance.json'
            self._write_lines(
                result_lines,
                [
                    'PASS|compose_ps|compose up [full_test_duration_seconds=3]|service',
                    'FAIL|gateway_https_root|gateway unhealthy [full_test_duration_seconds=5]|service',
                    'WARN|gateway_proxy_health|proxy slow [full_test_duration_seconds=4]|service',
                    'SKIP|official_openclaw_cli|not requested|service',
                ],
            )
            self._write_lines(next_actions, ['重新执行 gateway 健康检查', '补充入口链路日志'])
            acceptance_state.write_text(
                json.dumps(
                    {
                        'eligible': True,
                        'accepted': False,
                        'required_run_ledger_jobs': ['job_alpha'],
                        'run_ledger_snapshot': self._ledger_snapshot(),
                        'run_ledger_policy': {
                            'required': True,
                            'blocking': False,
                            'reason_code': 'accepted',
                            'required_jobs': ['job_alpha'],
                            'missing_jobs': [],
                            'failing_jobs': [],
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding='utf-8',
            )
            options = {
                'generatedAt': '2026-04-19T10:00:00Z',
                'envFile': 'deploy/.env',
                'outJson': str(out_json),
                'outMd': str(out_md),
                'group': 'all',
                'only': '',
                'skip': '',
                'strict': False,
                'quiet': False,
                'jsonStdout': False,
                'returnCode': 2,
                'resultLinesFile': str(result_lines),
                'nextActionsFile': str(next_actions),
                'acceptanceState': str(acceptance_state),
                'requiredAcceptanceIds': '',
            }

            def fake_default_path(key: str, manifest: dict[str, object] | None = None) -> Path:
                return latest_json if key == 'latest_json' else latest_md

            with mock.patch('openclaw.lib.testing.full_test.render.summarize_required_run_ledger', return_value=self._ledger_snapshot()):
                with mock.patch('openclaw.lib.testing.full_test.render.default_path', side_effect=fake_default_path):
                    summary = surface.write_summary(options)

            markdown = out_md.read_text(encoding='utf-8')
            latest_summary = json.loads(latest_json.read_text(encoding='utf-8'))

            stdout = io.StringIO()
            with mock.patch('sys.stdout', stdout):
                exit_code = surface.main(['normalize-check-csv', '--csv', 'compose_ps,compose_ps,gateway_https_root', '--flag', '--csv'])

        self.assertEqual(summary['summary']['pass'], 1)
        self.assertEqual(summary['summary']['fail'], 1)
        self.assertEqual(summary['summary']['warn'], 1)
        self.assertEqual(summary['summary']['skip'], 1)
        self.assertEqual(summary['summary']['duration_seconds_total'], 12)
        self.assertEqual(summary['slow_checks'][0]['id'], 'gateway_https_root')
        self.assertEqual(summary['deployment_acceptance']['required_run_ledger_jobs'], ['job_alpha'])
        self.assertEqual(latest_summary['summary']['return_code'], 2)
        self.assertIn('# one_click_test_full 摘要', markdown)
        self.assertIn('## 慢检查', markdown)
        self.assertIn('## run ledger policy', markdown)
        self.assertIn('| `gateway_https_root` |', markdown)
        self.assertIn('下一步动作', markdown)
        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout.getvalue().strip(), 'compose_ps,gateway_https_root')

    def test_summary_marks_nonzero_process_exit_as_blocking_when_checks_have_no_failures(self) -> None:
        with isolated_test_root('full-test-surface-process-exit') as root:
            result_lines = root / 'results.txt'
            next_actions = root / 'next.txt'
            out_json = root / 'summary.json'
            out_md = root / 'summary.md'
            acceptance_state = root / 'acceptance.json'
            self._write_lines(result_lines, ['PASS|compose_ps|compose up|service'])
            self._write_lines(next_actions, [])
            acceptance_state.write_text(
                json.dumps(
                    {
                        'eligible': True,
                        'accepted': True,
                        'required_run_ledger_jobs': [],
                        'run_ledger_snapshot': {'exists': False, 'required_jobs': [], 'accepted': None},
                        'run_ledger_policy': {'required': False, 'blocking': False, 'reason_code': 'not_required'},
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding='utf-8',
            )

            summary = surface.build_summary(
                {
                    'generatedAt': '2026-04-19T10:00:00Z',
                    'envFile': 'deploy/.env',
                    'outJson': str(out_json),
                    'outMd': str(out_md),
                    'group': 'all',
                    'only': '',
                    'skip': '',
                    'strict': False,
                    'quiet': False,
                    'jsonStdout': False,
                    'returnCode': 2,
                    'resultLinesFile': str(result_lines),
                    'nextActionsFile': str(next_actions),
                    'acceptanceState': str(acceptance_state),
                    'requiredAcceptanceIds': '',
                }
            )

        self.assertEqual(summary['summary']['fail'], 1)
        self.assertEqual(summary['blocking_checks'], ['full_test_process_exit_code'])
        process_checks = [item for item in summary['checks'] if item['id'] == 'full_test_process_exit_code']
        self.assertEqual(len(process_checks), 1)
        self.assertEqual(process_checks[0]['status'], 'FAIL')
        self.assertEqual(process_checks[0]['group'], 'process')
        self.assertIn('return_code=2', process_checks[0]['detail'])
        self.assertIn('full_test_process_exit_code', {item['id'] for item in summary['check_catalog']})
        self.assertIn('full test 进程返回非 0', summary['next_actions'][0])

    def test_terminal_rendering_truncates_long_details_but_keeps_json_detail(self) -> None:
        long_detail = 'x' * 1600
        summary = {
            'summary': {'pass': 1, 'fail': 0, 'warn': 0, 'skip': 0, 'return_code': 0, 'duration_seconds_total': 9},
            'deployment_acceptance': {'eligible': True, 'accepted': True, 'run_ledger_policy': {'reason_code': 'accepted', 'blocking': False}},
            'run_ledger': {'exists': True, 'accepted': True, 'missing_jobs': [], 'failing_jobs': []},
            'slow_checks': [{'id': 'internal_api_runtime', 'group': 'service', 'status': 'PASS', 'duration_seconds': 9}],
            'checks': [
                {
                    'id': 'internal_api_runtime',
                    'group': 'service',
                    'status': 'PASS',
                    'detail': long_detail,
                    'duration_seconds': 9,
                }
            ],
            'blocking_checks': [],
            'warning_checks': [],
            'skipped_checks': [],
            'next_actions': [],
        }

        text = surface.render_text(summary)

        self.assertIn('RECORDED_DURATION_SECONDS: 9', text)
        self.assertIn('slow checks:', text)
        self.assertIn('[detail] ' + ('x' * 1200), text)
        self.assertIn('detail 已截断 400 字符', text)
        self.assertEqual(summary['checks'][0]['detail'], long_detail)


if __name__ == '__main__':
    unittest.main()
