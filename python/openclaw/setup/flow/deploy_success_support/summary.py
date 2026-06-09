#!/usr/bin/env python3
"""Summary assembly for deploy_success surface."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from openclaw.lib.repo.contracts import repo_contract_path
from openclaw.lib.runtime.source_strategy import deployment_image_roles, runtime_service_image_roles


def _image_ref_pin_contracts(root_dir: Path) -> dict[str, tuple[str, ...]]:
    """从 runtime source strategy 派生部署镜像 env key 与 pin 合同关系。"""
    return {role.env_key: (role.pin_contract,) for role in deployment_image_roles(root_dir)}


def _selected_image(
    env_map: dict[str, str],
    key: str,
    *,
    root_dir: Path,
    parse_env_file_fn: Callable[[Path], dict[str, str]],
    pin_contracts: dict[str, tuple[str, ...]],
) -> str | None:
    """按 deploy env 优先、pin 真源兜底的顺序解析镜像引用。"""
    value = str(env_map.get(key) or '').strip()
    if value:
        return value
    for contract_id in pin_contracts.get(key, ()):
        pin_path = repo_contract_path(contract_id, root_dir=root_dir)
        if not pin_path.is_file():
            continue
        pinned = str(parse_env_file_fn(pin_path).get(key) or '').strip()
        if pinned:
            return pinned
    return None


def build_summary(
    options: dict[str, Any],
    *,
    root_dir: Path,
    parse_env_file_fn: Callable[[Path], dict[str, str]],
    read_json_fn: Callable[[Path], Any | None],
    build_private_ingress_plan_fn: Callable[[Path], dict[str, Any]],
    build_runtime_evidence_status_fn: Callable[[Path], dict[str, Any]],
    default_path_fn: Callable[[str], Path],
    next_steps_fn: Callable[[dict[str, Any]], list[str]],
    relative_or_self_fn: Callable[..., str],
    utc_now_iso_fn: Callable[[], str],
) -> dict[str, Any]:
    env_map = parse_env_file_fn(Path(options['env_file']))
    config_summary_path = Path(options['config_summary']) if options['config_summary'] else default_path_fn('config_summary')
    acceptance_state_path = Path(options['acceptance_state']) if options['acceptance_state'] else default_path_fn('deployment_acceptance_state')
    ingress_boundary_evidence_path = default_path_fn('ingress_boundary_evidence')
    config_summary = read_json_fn(config_summary_path)
    acceptance = read_json_fn(acceptance_state_path)
    ingress_boundary_evidence = read_json_fn(ingress_boundary_evidence_path)
    nginx_policy = ingress_boundary_evidence.get('nginx_policy') if isinstance(ingress_boundary_evidence, dict) and isinstance(ingress_boundary_evidence.get('nginx_policy'), dict) else {}
    ingress_plan = build_private_ingress_plan_fn(Path(options['env_file']))
    release_root = Path(options['release_root'])
    if not options.get('release_root_explicit') and not (release_root / 'evidence' / 'runtime-acceptance.json').exists():
        host_state_root = Path(env_map.get('HOST_STATE_ROOT') or 'state/openclaw')
        if not host_state_root.is_absolute():
            host_state_root = root_dir / host_state_root
        candidate_release_root = host_state_root / 'control_plane' / 'release'
        if candidate_release_root.exists():
            release_root = candidate_release_root
    post_acceptance = options.get('post_acceptance', True) is True
    runtime_evidence = build_runtime_evidence_status_fn(release_root)
    if not post_acceptance:
        runtime_evidence = {
            **runtime_evidence,
            'runtime_acceptance_exists': False,
            'runtime_accepted': False,
            'official_cli_summary_exists': False,
            'skipped_by_current_run': True,
        }
    latest_json_path = default_path_fn('latest_json')
    latest_markdown_path = default_path_fn('latest_markdown')
    deployment_roles = deployment_image_roles(root_dir)
    runtime_roles = runtime_service_image_roles(root_dir)
    pin_contracts = _image_ref_pin_contracts(root_dir)
    selected_by_env = {
        role.env_key: _selected_image(env_map, role.env_key, root_dir=root_dir, parse_env_file_fn=parse_env_file_fn, pin_contracts=pin_contracts)
        for role in deployment_roles
    }
    deployment_image_refs = [
        {
            'role': role.role,
            'image_id': role.image_id,
            'env_key': role.env_key,
            'scope': role.scope,
            'label': role.label,
            'compose_enabled': role.compose_enabled,
            'compose_selector': role.compose_selector,
            'image': selected_by_env.get(role.env_key),
        }
        for role in deployment_roles
    ]
    summary: dict[str, Any] = {
        'schema_version': 1,
        'generated_at': utc_now_iso_fn(),
        'deploy_run': {
            'status': options['status'],
            'timestamp': options['timestamp'] or None,
            'mode': options['mode'],
            'resume_from': options['resume_from'] or None,
            'env_file': relative_or_self_fn(options['env_file'], root_dir=root_dir),
            'log_path': relative_or_self_fn(options['log_path'], root_dir=root_dir),
            'summary_json_path': relative_or_self_fn(options['summary_json_path'] or options['out_json'], root_dir=root_dir),
            'summary_markdown_path': relative_or_self_fn(options['summary_md_path'] or options['out_md'], root_dir=root_dir),
            'start_services': options['start_services'],
            'prepare_only': not options['start_services'],
            'post_acceptance': post_acceptance,
            'image_archive_path': options['image_archive_path'] or None,
        },
        'fixed_latest_summary': {
            'json_path': relative_or_self_fn(latest_json_path, root_dir=root_dir),
            'markdown_path': relative_or_self_fn(latest_markdown_path, root_dir=root_dir),
        },
        'deploy_env_summary': {
            'config_summary_path': relative_or_self_fn(config_summary_path, root_dir=root_dir),
            'exists': config_summary is not None,
            'status': config_summary.get('status') if isinstance(config_summary, dict) else None,
            'required_manual_keys': config_summary.get('required_manual_keys') if isinstance(config_summary, dict) and isinstance(config_summary.get('required_manual_keys'), list) else [],
            'unresolved_required_keys': [
                item.get('key')
                for item in (config_summary.get('required_manual_keys') or [])
                if isinstance(item, dict) and item.get('status') != 'filled'
            ] if isinstance(config_summary, dict) and isinstance(config_summary.get('required_manual_keys'), list) else [],
        },
        'private_ingress': {
            'access_host': env_map.get('OPENCLAW_TLS_CN') or None,
            'access_host_source': 'OPENCLAW_TLS_CN',
            'access_host_role': 'single_access_hostname_and_control_ui_origin',
            'bind_ip': env_map.get('OPENCLAW_INGRESS_LISTEN_IP') or None,
            'bind_ip_source': 'OPENCLAW_INGRESS_LISTEN_IP',
            'bind_ip_role': 'host_port_binding_only',
            'auth_mode': 'official_gateway_token',
            'trusted_proxy_enabled': False,
            'network_exposure_plane': (ingress_plan.get('ingress') or {}).get('exposurePolicyPlane') or 'infra_managed',
            'network_boundary_in_repo': (ingress_plan.get('ingress') or {}).get('networkBoundaryInRepo'),
            'tls_mode': (ingress_plan.get('ingress') or {}).get('tlsMode') or None,
            'nginx_render_scope': 'tls_hsts_source_allowlist_and_reverse_proxy',
            'hsts_max_age': (ingress_plan.get('nginx') or {}).get('hstsMaxAge'),
            'nginx_output_path': relative_or_self_fn((ingress_plan.get('nginx') or {}).get('outputPath') or '', root_dir=root_dir),
        },
        'selected_images': {
            'deployment_image_refs': deployment_image_refs,
            'official_gateway_image': selected_by_env.get('OPENCLAW_OFFICIAL_GATEWAY_IMAGE'),
            'control_plane_image': selected_by_env.get('OPENCLAW_CONTROL_PLANE_IMAGE'),
            'runtime_python_image': selected_by_env.get('OPENCLAW_RUNTIME_PYTHON_IMAGE'),
            'nginx_image': selected_by_env.get('NGINX_IMAGE'),
            'runtime_service_image_set': [
                selected_by_env.get(role.env_key)
                for role in runtime_roles
            ],
        },
        'ingress_boundary_evidence': {
            'path': relative_or_self_fn(ingress_boundary_evidence_path, root_dir=root_dir),
            'exists': ingress_boundary_evidence is not None,
            'accepted': ingress_boundary_evidence.get('accepted') is True if isinstance(ingress_boundary_evidence, dict) else False,
            'boundary_method': ingress_boundary_evidence.get('boundary_evidence', {}).get('method') if isinstance(ingress_boundary_evidence, dict) else None,
            'nginx_policy_ok': nginx_policy.get('ok') is True if nginx_policy else False,
            'nginx_policy_default_deny': nginx_policy.get('default_deny') is True if nginx_policy else False,
            'nginx_policy_rewrite_phase_default_deny': nginx_policy.get('rewrite_phase_default_deny') is True if nginx_policy else False,
            'nginx_policy_access_phase_default_deny': nginx_policy.get('access_phase_default_deny') is True if nginx_policy else False,
        },
        'deployment_acceptance': {
            'acceptance_state_path': relative_or_self_fn(acceptance_state_path, root_dir=root_dir),
            'exists': acceptance is not None and post_acceptance,
            'eligible': acceptance.get('eligible') is True if isinstance(acceptance, dict) and post_acceptance else None,
            'accepted': acceptance.get('accepted') is True if isinstance(acceptance, dict) and post_acceptance else False,
            'required_checks': acceptance.get('required_checks') if isinstance(acceptance, dict) and post_acceptance and isinstance(acceptance.get('required_checks'), list) else [],
            'skipped_by_current_run': not post_acceptance,
        },
        'runtime_evidence': runtime_evidence,
    }
    summary['next_steps'] = next_steps_fn(summary)
    return summary
