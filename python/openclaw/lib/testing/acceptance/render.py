from __future__ import annotations

from openclaw.lib.testing.acceptance.state import read_manifest, read_surface


def render_acceptance_summary_text(summary: dict[str, object]) -> str:
    deployment = summary['deployment_acceptance']
    ingress_boundary = summary['ingress_boundary_evidence']
    runtime = summary['runtime_acceptance']
    dispatch_runtime = summary['dispatch_runtime_check']
    ledger = summary['control_plane_run_ledger']
    official_control_plane = summary['official_cli']['control_plane']
    artifact_policies = summary['control_plane_job_artifact_policies']
    surface = read_surface()
    text_output = dict(surface.get('text_output') or {})
    lines = [
        str(text_output.get('summary_prefix') or '[acceptance_surface] 部署后验收摘要：'),
        f"  - deployment_acceptance：{'存在' if deployment['exists'] else '缺失'}，eligible={'未知' if deployment['eligible'] is None else deployment['eligible']}，accepted={'未知' if deployment['accepted'] is None else deployment['accepted']}",
        f"  - ingress_boundary_evidence：{'存在' if ingress_boundary['exists'] else '缺失'}，accepted={'未知' if ingress_boundary.get('accepted') is None else ingress_boundary.get('accepted')}，compose_ok={'未知' if ingress_boundary.get('compose_contract_ok') is None else ingress_boundary.get('compose_contract_ok')}，runtime_ok={'未知' if ingress_boundary.get('runtime_contract_ok') is None else ingress_boundary.get('runtime_contract_ok')}，nginx_policy_ok={'未知' if ingress_boundary.get('nginx_policy_ok') is None else ingress_boundary.get('nginx_policy_ok')}，rewrite_default_deny={'未知' if ingress_boundary.get('nginx_policy_rewrite_phase_default_deny') is None else ingress_boundary.get('nginx_policy_rewrite_phase_default_deny')}，access_default_deny={'未知' if ingress_boundary.get('nginx_policy_access_phase_default_deny') is None else ingress_boundary.get('nginx_policy_access_phase_default_deny')}，method={'未知' if ingress_boundary.get('boundary_method') is None else ingress_boundary.get('boundary_method')}",
        f"  - runtime_acceptance：{'存在' if runtime['exists'] else '缺失'}，eligible={'未知' if runtime['eligible'] is None else runtime['eligible']}，accepted={'未知' if runtime['accepted'] is None else runtime['accepted']}，control_plane_scheduler_healthy={'未知' if runtime.get('control_plane_scheduler_healthy') is None else runtime.get('control_plane_scheduler_healthy')}，heartbeat_age={'未知' if runtime.get('control_plane_heartbeat_age_seconds') is None else runtime.get('control_plane_heartbeat_age_seconds')}，run_ledger_accepted={'未知' if runtime.get('control_plane_run_ledger_accepted') is None else runtime.get('control_plane_run_ledger_accepted')}，missing_jobs={runtime.get('control_plane_run_ledger_missing_jobs') if runtime.get('control_plane_run_ledger_missing_jobs') is not None else '未知'}，failing_jobs={runtime.get('control_plane_run_ledger_failing_jobs') if runtime.get('control_plane_run_ledger_failing_jobs') is not None else '未知'}，artifact_missing_jobs={runtime.get('control_plane_run_ledger_artifact_missing_jobs') if runtime.get('control_plane_run_ledger_artifact_missing_jobs') is not None else '未知'}，artifact_failing_jobs={runtime.get('control_plane_run_ledger_artifact_failing_jobs') if runtime.get('control_plane_run_ledger_artifact_failing_jobs') is not None else '未知'}，recovered_jobs={runtime.get('control_plane_run_ledger_recovered_jobs') if runtime.get('control_plane_run_ledger_recovered_jobs') is not None else '未知'}，agent_groups={'未知' if runtime.get('control_plane_agent_group_count') is None else runtime.get('control_plane_agent_group_count')}，agent_modules={'未知' if runtime.get('control_plane_agent_module_count') is None else runtime.get('control_plane_agent_module_count')}，recent_agent_accesses={'未知' if runtime.get('control_plane_recent_agent_access_count') is None else runtime.get('control_plane_recent_agent_access_count')}，recent_agent_access_groups={'未知' if runtime.get('control_plane_recent_agent_access_group_count') is None else runtime.get('control_plane_recent_agent_access_group_count')}，agent_access_log_exists={'未知' if runtime.get('control_plane_agent_access_log_exists') is None else runtime.get('control_plane_agent_access_log_exists')}，agent_group_access_exists={'未知' if runtime.get('control_plane_agent_group_access_exists') is None else runtime.get('control_plane_agent_group_access_exists')}，agent_group_acceptance_bindings_exists={'未知' if runtime.get('control_plane_agent_group_acceptance_bindings_exists') is None else runtime.get('control_plane_agent_group_acceptance_bindings_exists')}，required_groups={runtime.get('control_plane_required_agent_groups') if runtime.get('control_plane_required_agent_groups') is not None else '未知'}，non_ok_groups={runtime.get('control_plane_failing_agent_groups') if runtime.get('control_plane_failing_agent_groups') is not None else '未知'}，blocked_group_bindings={runtime.get('control_plane_blocked_agent_group_acceptance_bindings') if runtime.get('control_plane_blocked_agent_group_acceptance_bindings') is not None else '未知'}，blocked_release_gates={runtime.get('control_plane_blocked_agent_group_release_gates') if runtime.get('control_plane_blocked_agent_group_release_gates') is not None else '未知'}，frozen_release_gates={runtime.get('control_plane_frozen_agent_group_release_gates') if runtime.get('control_plane_frozen_agent_group_release_gates') is not None else '未知'}",
        f"  - dispatch_runtime_check：{'存在' if dispatch_runtime['exists'] else '缺失'}，ok={'未知' if dispatch_runtime.get('ok') is None else dispatch_runtime.get('ok')}，signal_id={'未知' if dispatch_runtime.get('signal_id') is None else dispatch_runtime.get('signal_id')}",
        f"  - control_plane_run_ledger：{'存在' if ledger['exists'] else '缺失'}，artifact_accepted_jobs={'未知' if ledger['artifact_accepted_jobs'] is None else ledger['artifact_accepted_jobs']}，artifact_failed_jobs={'未知' if ledger['artifact_failed_jobs'] is None else ledger['artifact_failed_jobs']}，artifact_missing_jobs={'未知' if ledger['artifact_missing_jobs'] is None else ledger['artifact_missing_jobs']}，effective_execution_accepted_jobs={'未知' if ledger.get('execution_effective_accepted_jobs') is None else ledger.get('execution_effective_accepted_jobs')}，effective_execution_failed_jobs={'未知' if ledger.get('execution_effective_failed_jobs') is None else ledger.get('execution_effective_failed_jobs')}，artifact_effective_failed_jobs={'未知' if ledger.get('artifact_effective_failed_jobs') is None else ledger.get('artifact_effective_failed_jobs')}，recovered_jobs={'未知' if ledger.get('recovered_jobs') is None else ledger.get('recovered_jobs')}",
        f"  - control_plane_job_artifact_policies：{'存在' if artifact_policies['exists'] else '缺失'}，job_count={'未知' if artifact_policies.get('job_count') is None else artifact_policies.get('job_count')}",
        f"  - official_cli_control_plane：{'存在' if official_control_plane['exists'] else '缺失'}，doctor_passed={'未知' if official_control_plane['doctor_passed'] is None else official_control_plane['doctor_passed']}，blocking_findings={'未知' if official_control_plane['blocking_findings'] is None else official_control_plane['blocking_findings']}",
    ]
    if deployment['accepted'] is False or runtime['accepted'] is False:
        reference_intro = str(text_output.get('failure_reference_intro') or '').strip()
        if reference_intro:
            lines.append(reference_intro)
        reference_format = str(text_output.get('failure_reference_format') or '    - {label}：{target}')
        for item in surface.get('failure_references') or []:
            lines.append(reference_format.format(label=item.get('label') or '', target=item.get('target') or ''))
    return '\n'.join(lines) + '\n'


def usage() -> str:
    surface = read_surface()
    lines = ['用法：']
    lines.extend([f'  {command}' for command in surface.get('usage_commands') or []])
    lines.append('')
    return '\n'.join(lines)


def render_doc() -> str:
    surface = read_surface()
    manifest = read_manifest()
    lines = [
        f"# {manifest.get('title') or 'deployment acceptance 与证据归档'}",
        '',
        *surface.get('intro', []),
        '',
        '## 默认入口',
        '',
    ]
    for command in surface.get('usage_commands') or []:
        lines.append(f'- `{command}`')
    lines.extend(['', '## 证据产物', ''])
    for item in manifest.get('artifacts') or []:
        lines.append(f"- `{item['path']}`：{item['meaning']}")
    lines.extend(['', '## required checks', ''])
    for check_id in manifest.get('required_checks') or []:
        lines.append(f'- `{check_id}`')
    required_jobs = [str(item).strip() for item in (manifest.get('required_run_ledger_jobs') or []) if str(item).strip()]
    if required_jobs:
        lines.extend(['', '## required run ledger jobs', ''])
        for job_id in required_jobs:
            lines.append(f'- `{job_id}`')
    lines.extend(['', '## 失败时统一查看', ''])
    for item in surface.get('failure_references') or []:
        lines.append(f"- {item.get('label') or ''}：`{item.get('target') or ''}`")
    lines.extend(['', '## 维护边界', ''])
    lines.extend([f'- {line}' for line in surface.get('boundary') or []])
    return '\n'.join(lines).rstrip() + '\n'
