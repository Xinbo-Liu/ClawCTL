from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openclaw.setup.flow import basic_summary, config_summary
from openclaw.setup.surface import failure as failure_surface
from openclaw.setup.surface import followup as followup_surface


class SetupSummarySurfacesTest(unittest.TestCase):
    def test_config_summary_uses_failure_surface_contract(self) -> None:
        payload = config_summary.build_summary(
            {
                'generated_at': '2026-04-23T00:00:00Z',
                'output_path': 'deploy/.env',
                'failed_stage': 'render_control_plane',
                'exit_code': 7,
                'failure_message': 'render failed',
                'dry_run': False,
            }
        )

        scenario = failure_surface.scenario_info('one_click_config', 'render_failed')
        self.assertEqual(payload['status'], 'render_failed')
        self.assertEqual(payload['scenario_title'], scenario['title'])
        self.assertEqual(payload['next_actions'], failure_surface.list_str(scenario, 'commands'))
        self.assertEqual(payload['generator']['mode'], 'python_surface')

    def test_config_summary_routes_effective_compose_stage(self) -> None:
        payload = config_summary.build_summary(
            {
                'generated_at': '2026-04-23T00:00:00Z',
                'output_path': 'deploy/.env',
                'failed_stage': 'effective_compose_render',
                'exit_code': 7,
                'failure_message': 'compose failed',
                'dry_run': False,
            }
        )

        scenario = failure_surface.scenario_info('one_click_config', 'effective_compose_render_failed')
        self.assertEqual(payload['status'], 'effective_compose_render_failed')
        self.assertEqual(payload['scenario_title'], scenario['title'])
        self.assertEqual(payload['next_actions'], failure_surface.list_str(scenario, 'commands'))

    def test_basic_summary_success_uses_followup_surface(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result_lines = Path(tmpdir) / 'results.txt'
            result_lines.write_text(
                'PASS|env_file_exists|[setup_gate_duration_seconds=1]|config\n'
                'PASS|check_docker_host_readiness|ready [setup_gate_duration_seconds=9]|host\n',
                encoding='utf-8',
            )

            payload = basic_summary.build_summary(
                {
                    'generated_at': '2026-04-23T00:00:00Z',
                    'env_file': 'deploy/.env',
                    'offline': False,
                    'return_code': 0,
                    'result_lines_file': str(result_lines),
                    'failed_stage': '',
                    'failure_detail': '',
                    'image_archive_path': '',
                }
            )

        scenario = followup_surface.scenario_info('one_click_test_basic', 'success_online')
        self.assertEqual(payload['summary']['fail'], 0)
        self.assertEqual(payload['summary']['duration_seconds_total'], 10)
        self.assertEqual(payload['slow_checks'][0]['id'], 'check_docker_host_readiness')
        self.assertEqual(payload['next_actions'], followup_surface.list_str(scenario, 'commands'))
        self.assertEqual(payload['generator']['reason'], 'basic_summary_control_plane')

    def test_basic_summary_failure_uses_failure_surface_and_preserves_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result_lines = Path(tmpdir) / 'results.txt'
            result_lines.write_text('FAIL|env_required_placeholders|missing required|config\n', encoding='utf-8')

            payload = basic_summary.build_summary(
                {
                    'generated_at': '2026-04-23T00:00:00Z',
                    'env_file': 'deploy/.env',
                    'offline': True,
                    'return_code': 2,
                    'result_lines_file': str(result_lines),
                    'failed_stage': '',
                    'failure_detail': '',
                    'image_archive_path': '/tmp/deployment_images.tar',
                }
            )

        scenario = failure_surface.scenario_info('one_click_test_basic', 'config_failed')
        self.assertEqual(payload['summary']['fail'], 1)
        self.assertEqual(payload['setup_failure_bucket']['scenario_id'], 'config_failed')
        self.assertEqual(payload['setup_failure_bucket']['scenario_title'], scenario['title'])
        self.assertTrue(payload['next_actions'])
        self.assertTrue(all(item for item in payload['next_actions']))


if __name__ == '__main__':
    unittest.main()
