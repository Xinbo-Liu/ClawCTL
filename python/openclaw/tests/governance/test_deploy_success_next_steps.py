from __future__ import annotations

import unittest
from typing import Any

from openclaw.setup.flow.deploy_success_support.next_steps import build_next_steps


def _summary(
    *,
    start_services: bool = True,
    deployment_accepted: bool = True,
    runtime_accepted: bool = True,
    runtime_acceptance_exists: bool = True,
    ingress_accepted: bool = True,
    nginx_policy_ok: bool = True,
) -> dict[str, Any]:
    return {
        'deploy_run': {'start_services': start_services},
        'deployment_acceptance': {'accepted': deployment_accepted},
        'runtime_evidence': {
            'runtime_accepted': runtime_accepted,
            'runtime_acceptance_exists': runtime_acceptance_exists,
        },
        'ingress_boundary_evidence': {
            'accepted': ingress_accepted,
            'nginx_policy_ok': nginx_policy_ok,
        },
    }


def _scenario_info(_entry_id: str, scenario_id: str) -> dict[str, Any]:
    if scenario_id == 'success_prepare_only':
        return {'commands': ['bash ./scripts/setup/one_click_deploy.sh']}
    return {'commands': []}


def _list_str(payload: dict[str, Any], key: str) -> list[str]:
    return [str(item) for item in list(payload.get(key) or [])]


class DeploySuccessNextStepsTest(unittest.TestCase):
    def test_completed_runtime_acceptance_does_not_repeat_full_or_export_steps(self) -> None:
        steps = build_next_steps(_summary(), scenario_info=_scenario_info, list_str=_list_str)

        self.assertEqual(steps, ['bash ./scripts/runtime/show_runtime_service_status.sh'])

    def test_missing_runtime_acceptance_keeps_export_repair_step(self) -> None:
        steps = build_next_steps(
            _summary(runtime_accepted=False, runtime_acceptance_exists=False),
            scenario_info=_scenario_info,
            list_str=_list_str,
        )

        self.assertIn('bash ./scripts/runtime/export_runtime_acceptance_evidence.sh', steps)
        self.assertIn('bash ./scripts/runtime/show_runtime_container_logs.sh --target gateway', steps)
        self.assertIn('bash ./scripts/runtime/show_runtime_service_status.sh --target scheduler', steps)

    def test_prepare_only_uses_prepare_followup_commands(self) -> None:
        steps = build_next_steps(
            _summary(start_services=False, deployment_accepted=False, runtime_accepted=False),
            scenario_info=_scenario_info,
            list_str=_list_str,
        )

        self.assertEqual(steps, ['bash ./scripts/setup/one_click_deploy.sh'])


if __name__ == '__main__':
    unittest.main()
