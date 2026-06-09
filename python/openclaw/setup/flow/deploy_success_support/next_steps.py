#!/usr/bin/env python3
"""Next-step derivation for deploy_success summary surface."""
from __future__ import annotations

from typing import Any, Callable


def build_next_steps(
    summary: dict[str, Any],
    *,
    scenario_info: Callable[[str, str], dict[str, Any]],
    list_str: Callable[[dict[str, Any], str], list[str]],
) -> list[str]:
    steps: list[str] = []
    start_services = bool(summary['deploy_run']['start_services'])
    deployment_accepted = summary['deployment_acceptance'].get('accepted') is True
    runtime_accepted = summary['runtime_evidence'].get('runtime_accepted') is True
    if not start_services:
        scenario = scenario_info('one_click_deploy', 'success_prepare_only')
        steps.extend(list_str(scenario, 'commands'))
    elif not deployment_accepted:
        steps.append('bash ./scripts/setup/one_click_test_full.sh')
    elif not runtime_accepted:
        steps.append('bash ./scripts/runtime/export_runtime_acceptance_evidence.sh')
    if start_services:
        steps.append('bash ./scripts/runtime/show_runtime_service_status.sh')
        if not deployment_accepted or not runtime_accepted:
            steps.append('bash ./scripts/runtime/show_runtime_container_logs.sh --target gateway')
            steps.append('bash ./scripts/runtime/show_runtime_container_logs.sh --target ingress')
    ingress = summary['ingress_boundary_evidence']
    if ingress.get('accepted') is not True or ingress.get('nginx_policy_ok') is not True:
        steps.append('sudo bash ./scripts/doctor/check_ingress_boundary_evidence.sh --env-file deploy/.env --require-nginx-policy')
    if summary['runtime_evidence'].get('runtime_acceptance_exists') is not True:
        steps.append('bash ./scripts/runtime/export_runtime_acceptance_evidence.sh')
    if start_services and (not deployment_accepted or not runtime_accepted):
        steps.append('bash ./scripts/runtime/show_runtime_service_status.sh --target scheduler')
    return list(dict.fromkeys(steps))
