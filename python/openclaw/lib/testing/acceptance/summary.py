from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openclaw.control_plane.run_ledger import row_effective_artifact_accepted, row_effective_execution_accepted, row_execution_accepted
from openclaw.control_plane.surfaces import load_testing_manifest
from openclaw.lib.control_plane.object_families import get_entry
from openclaw.lib.io.json_access import json_object
from openclaw.lib.testing.acceptance.state import (
    ROOT_DIR,
    default_models_probe_summary,
    read_manifest,
    read_json,
    resolve_path,
    safe_read_json,
    write_json,
)


def build_official_cli_summary(official_dir: Path, target: str) -> dict[str, Any]:
    security = read_json(official_dir / 'security_audit_deep.json')
    models_path = official_dir / 'models_status_probe.json'
    models = safe_read_json(models_path) or default_models_probe_summary(
        reason='models_status_probe.json 缺失；按 kernel-only / 无 model_runtime 视为跳过',
        source_path=models_path,
    )
    doctor_log = (official_dir / 'doctor.log').read_text(encoding='utf-8')
    report = security.get('report') if isinstance(security, dict) and isinstance(security.get('report'), dict) else security
    findings = list(report.get('findings') or []) if isinstance(report, dict) else []
    blocking = [
        {
            'checkId': item.get('checkId') or item.get('id') or 'unknown',
            'severity': item.get('severity') or 'unknown',
            'message': item.get('message') or item.get('title') or '',
        }
        for item in findings
        if isinstance(item, dict) and str(item.get('severity', '')).lower() in {'critical', 'high'}
    ]
    return {
        'schema_version': 1,
        'generated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'target': target,
        'source_dir': str(official_dir),
        'doctor_passed': '[openclaw_doctor] FAIL' not in doctor_log,
        'security_blocking_findings': blocking,
        'models_probe': models,
    }


def write_official_cli_summary(*, official_dir: str | Path, out: str | Path, target: str) -> None:
    write_json(Path(out), build_official_cli_summary(Path(official_dir), target))


def required_run_ledger_status(run_ledger: dict[str, Any] | None) -> dict[str, Any]:
    manifest = read_manifest()
    required_jobs = list(manifest.get('required_run_ledger_jobs') or [])
    items = list(run_ledger.get('items') or []) if isinstance(run_ledger, dict) else []
    rows = {str(item.get('id') or ''): item for item in items if isinstance(item, dict)}

    missing = [job_id for job_id in required_jobs if job_id not in rows]
    artifact_missing = [job_id for job_id in required_jobs if job_id in rows and row_effective_artifact_accepted(rows[job_id]) is None]
    artifact_failing = [job_id for job_id in required_jobs if job_id in rows and row_effective_artifact_accepted(rows[job_id]) is False]
    failing = [
        job_id
        for job_id in required_jobs
        if job_id in rows and row_effective_execution_accepted(rows[job_id]) is not True
    ]
    recovered = [
        job_id
        for job_id in required_jobs
        if job_id in rows
        and row_execution_accepted(rows[job_id]) is False
        and row_effective_execution_accepted(rows[job_id]) is True
    ]
    accepted = None if not required_jobs else bool(not missing and not failing and not artifact_missing and not artifact_failing)
    return {
        'requiredJobs': required_jobs,
        'missingJobs': missing,
        'failingJobs': failing,
        'artifactMissingJobs': artifact_missing,
        'artifactFailingJobs': artifact_failing,
        'recoveredJobs': recovered,
        'accepted': accepted,
    }


def runtime_agent_group_statuses(control_plane_runtime_summary: dict[str, Any] | None) -> dict[str, Any]:
    groups = list(control_plane_runtime_summary.get('agentGroups') or []) if isinstance(control_plane_runtime_summary, dict) else []
    statuses: dict[str, str] = {}
    required_groups: list[str] = []
    failing_groups: list[str] = []
    for item in groups:
        if not isinstance(item, dict):
            continue
        group_id = str(item.get('id') or '').strip()
        if not group_id:
            continue
        observability = json_object(item.get('observabilityContract'))
        health = json_object(item.get('health'))
        status = str(health.get('status') or '').strip()
        statuses[group_id] = status
        if bool(observability.get('runLedgerRequired', False)):
            required_groups.append(group_id)
        if status in {'failed', 'blocked', 'retry_pending'}:
            failing_groups.append(group_id)
    return {
        'statuses': statuses,
        'requiredGroups': required_groups,
        'failingGroups': failing_groups,
    }


def runtime_agent_group_release_gate_statuses(control_plane_runtime_summary: dict[str, Any] | None) -> dict[str, Any]:
    summary = control_plane_runtime_summary.get('agentGroupReleaseGates') if isinstance(control_plane_runtime_summary, dict) and isinstance(control_plane_runtime_summary.get('agentGroupReleaseGates'), dict) else {}
    items = list(summary.get('items') or []) if isinstance(summary, dict) else []
    statuses: dict[str, str] = {}
    blocked_groups: list[str] = []
    frozen_groups: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        group_ref = str(item.get('groupRef') or '').strip()
        gate = json_object(item.get('releaseGate'))
        status = str(gate.get('status') or '').strip()
        if not group_ref:
            continue
        statuses[group_ref] = status
        if status == 'blocked':
            blocked_groups.append(group_ref)
        elif status == 'frozen':
            frozen_groups.append(group_ref)
    return {'statuses': statuses, 'blockedGroups': blocked_groups, 'frozenGroups': frozen_groups}


def runtime_agent_group_acceptance_binding_statuses(control_plane_runtime_summary: dict[str, Any] | None) -> dict[str, Any]:
    summary = control_plane_runtime_summary.get('agentGroupAcceptanceBindings') if isinstance(control_plane_runtime_summary, dict) and isinstance(control_plane_runtime_summary.get('agentGroupAcceptanceBindings'), dict) else {}
    items = list(summary.get('items') or []) if isinstance(summary, dict) else []
    statuses: dict[str, str] = {}
    blocked_groups: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        group_ref = str(item.get('groupRef') or '').strip()
        binding = json_object(item.get('acceptanceBinding'))
        if not group_ref:
            continue
        accepted = binding.get('accepted') is True
        statuses[group_ref] = 'accepted' if accepted else 'blocked'
        if not accepted:
            blocked_groups.append(group_ref)
    return {'statuses': statuses, 'blockedGroups': blocked_groups}


def build_runtime_acceptance_summary(
    acceptance_state_path: Path,
    control_plane_summary_path: Path,
    control_plane_run_ledger_path: Path | None = None,
    control_plane_runtime_summary_path: Path | None = None,
) -> dict[str, Any]:
    acceptance = read_json(acceptance_state_path)
    control_plane_summary = read_json(control_plane_summary_path)
    control_plane_run_ledger = read_json(control_plane_run_ledger_path) if control_plane_run_ledger_path is not None else None
    control_plane_runtime_summary = read_json(control_plane_runtime_summary_path) if control_plane_runtime_summary_path is not None else None
    scheduler = control_plane_runtime_summary.get('scheduler') if isinstance(control_plane_runtime_summary, dict) and isinstance(control_plane_runtime_summary.get('scheduler'), dict) else {}
    counts = control_plane_runtime_summary.get('counts') if isinstance(control_plane_runtime_summary, dict) and isinstance(control_plane_runtime_summary.get('counts'), dict) else {}
    ledger_status = required_run_ledger_status(control_plane_run_ledger)
    group_status = runtime_agent_group_statuses(control_plane_runtime_summary)
    group_binding_status = runtime_agent_group_acceptance_binding_statuses(control_plane_runtime_summary)
    group_release_status = runtime_agent_group_release_gate_statuses(control_plane_runtime_summary)
    doctor_passed = control_plane_summary.get('doctor_passed') is True
    scheduler_healthy = scheduler.get('healthy') is True if scheduler else None
    ledger_gate_ok = ledger_status['accepted'] is not False
    runtime_accepted = bool(acceptance.get('accepted') is True and doctor_passed and scheduler_healthy is True and ledger_gate_ok)
    return {
        'schema_version': 1,
        'generated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'source_acceptance_state_path': str(acceptance_state_path),
        'eligible': acceptance.get('eligible') is True,
        'accepted': runtime_accepted,
        'required_checks': list(acceptance.get('required_checks') or []),
        'official_cli_summary_path': str(control_plane_summary_path),
        'doctor_passed': doctor_passed,
        'security_blocking_findings': control_plane_summary.get('security_blocking_findings') or [],
        'control_plane_runtime_summary_path': str(control_plane_runtime_summary_path) if control_plane_runtime_summary_path is not None else None,
        'control_plane_scheduler_healthy': scheduler_healthy,
        'control_plane_heartbeat_age_seconds': scheduler.get('heartbeatAgeSeconds') if scheduler else None,
        'control_plane_registry_counts': counts if counts else {},
        'control_plane_agent_group_count': int((counts.get('agentGroups') or 0)) if counts else 0,
        'control_plane_agent_module_count': int((counts.get('agentModules') or 0)) if counts else 0,
        'control_plane_skill_set_count': int((counts.get('skillSets') or 0)) if counts else 0,
        'control_plane_permission_policy_count': int((counts.get('permissionPolicies') or 0)) if counts else 0,
        'control_plane_toolset_count': int((counts.get('toolsets') or 0)) if counts else 0,
        'control_plane_runtime_adapter_count': int((counts.get('runtimeAdapters') or 0)) if counts else 0,
        'control_plane_recent_agent_access_count': int((counts.get('recentAgentAccesses') or 0)) if counts else 0,
        'control_plane_recent_agent_access_group_count': int((counts.get('recentAgentAccessGroups') or 0)) if counts else 0,
        'control_plane_agent_group_acceptance_binding_count': int((counts.get('agentGroupAcceptanceBindings') or 0)) if counts else 0,
        'control_plane_agent_group_release_gate_count': int((counts.get('agentGroupReleaseGates') or 0)) if counts else 0,
        'control_plane_agent_access_log_exists': bool(isinstance(control_plane_runtime_summary, dict) and isinstance(control_plane_runtime_summary.get('agentAccessLog'), dict) and str((control_plane_runtime_summary.get('agentAccessLog') or {}).get('path') or '').strip()),
        'control_plane_agent_group_access_exists': bool(isinstance(control_plane_runtime_summary, dict) and isinstance(control_plane_runtime_summary.get('agentGroupAccess'), dict) and isinstance((control_plane_runtime_summary.get('agentGroupAccess') or {}).get('items'), list)),
        'control_plane_agent_group_acceptance_bindings_exists': bool(isinstance(control_plane_runtime_summary, dict) and isinstance(control_plane_runtime_summary.get('agentGroupAcceptanceBindings'), dict) and isinstance((control_plane_runtime_summary.get('agentGroupAcceptanceBindings') or {}).get('items'), list)),
        'control_plane_agent_group_release_gates_exists': bool(isinstance(control_plane_runtime_summary, dict) and isinstance(control_plane_runtime_summary.get('agentGroupReleaseGates'), dict) and isinstance((control_plane_runtime_summary.get('agentGroupReleaseGates') or {}).get('items'), list)),
        'control_plane_agent_group_statuses': group_status['statuses'],
        'control_plane_required_agent_groups': group_status['requiredGroups'],
        'control_plane_failing_agent_groups': group_status['failingGroups'],
        'control_plane_agent_group_acceptance_binding_statuses': group_binding_status['statuses'],
        'control_plane_blocked_agent_group_acceptance_bindings': group_binding_status['blockedGroups'],
        'control_plane_agent_group_release_gate_statuses': group_release_status['statuses'],
        'control_plane_blocked_agent_group_release_gates': group_release_status['blockedGroups'],
        'control_plane_frozen_agent_group_release_gates': group_release_status['frozenGroups'],
        'control_plane_recent_run_count': len(control_plane_runtime_summary.get('recentRuns') or []) if isinstance(control_plane_runtime_summary, dict) else None,
        'control_plane_run_ledger_path': str(control_plane_run_ledger_path) if control_plane_run_ledger_path is not None else None,
        'control_plane_run_ledger_exists': control_plane_run_ledger is not None,
        'control_plane_run_ledger_required_jobs': ledger_status['requiredJobs'],
        'control_plane_run_ledger_missing_jobs': ledger_status['missingJobs'],
        'control_plane_run_ledger_failing_jobs': ledger_status['failingJobs'],
        'control_plane_run_ledger_artifact_missing_jobs': ledger_status['artifactMissingJobs'],
        'control_plane_run_ledger_artifact_failing_jobs': ledger_status['artifactFailingJobs'],
        'control_plane_run_ledger_recovered_jobs': ledger_status['recoveredJobs'],
        'control_plane_run_ledger_accepted': ledger_status['accepted'],
    }


def write_runtime_acceptance_summary(
    *,
    acceptance_state: str | Path,
    control_plane_summary: str | Path,
    out: str | Path,
    control_plane_run_ledger: str | Path | None = None,
    control_plane_runtime_summary: str | Path | None = None,
) -> None:
    ledger_path = Path(control_plane_run_ledger) if control_plane_run_ledger is not None else None
    runtime_path = Path(control_plane_runtime_summary) if control_plane_runtime_summary is not None else None
    write_json(Path(out), build_runtime_acceptance_summary(Path(acceptance_state), Path(control_plane_summary), ledger_path, runtime_path))


def build_acceptance_summary(base_root: Path = ROOT_DIR) -> dict[str, Any]:
    manifest = read_manifest()
    deployment_entry = get_entry('acceptance_state', 'deployment_acceptance', base_root)
    ingress_boundary_entry = get_entry('acceptance_state', 'ingress_boundary_evidence', base_root)
    runtime_acceptance_entry = get_entry('runtime_evidence', 'runtime_acceptance', base_root)
    run_ledger_entry = get_entry('runtime_evidence', 'control_plane_run_ledger', base_root)
    official_cli_entry = get_entry('runtime_evidence', 'official_cli_control_plane', base_root)
    dispatch_runtime_entry = get_entry('runtime_evidence', 'dispatch_runtime_check', base_root)
    artifact_policies_entry = get_entry('runtime_evidence', 'control_plane_job_artifact_policies', base_root)

    deployment_rel = str(deployment_entry['resolved_path'])
    deployment_path = resolve_path(deployment_rel, base_root)
    ingress_boundary_rel = str(ingress_boundary_entry['resolved_path'])
    ingress_boundary_path = resolve_path(ingress_boundary_rel, base_root)
    runtime_rel = str(runtime_acceptance_entry['resolved_path'])
    runtime_path = resolve_path(runtime_rel, base_root)
    run_ledger_rel = str(run_ledger_entry['resolved_path'])
    run_ledger_path = resolve_path(run_ledger_rel, base_root)
    control_plane_rel = str(official_cli_entry['resolved_path'])
    control_plane_path = resolve_path(control_plane_rel, base_root)
    dispatch_runtime_rel = str(dispatch_runtime_entry['resolved_path'])
    dispatch_runtime_path = resolve_path(dispatch_runtime_rel, base_root)
    artifact_policies_rel = str(artifact_policies_entry['resolved_path'])
    artifact_policies_path = resolve_path(artifact_policies_rel, base_root)

    deployment = safe_read_json(deployment_path)
    runtime = safe_read_json(runtime_path)
    control_plane_summary = safe_read_json(control_plane_path)
    dispatch_runtime = safe_read_json(dispatch_runtime_path)
    artifact_policies = safe_read_json(artifact_policies_path)
    run_ledger = safe_read_json(run_ledger_path)
    ingress_boundary = safe_read_json(ingress_boundary_path)
    nginx_policy = ingress_boundary.get('nginx_policy') if isinstance(ingress_boundary, dict) and isinstance(ingress_boundary.get('nginx_policy'), dict) else {}
    run_ledger_artifact_counts = {}
    if isinstance(run_ledger, dict):
        maybe_counts = run_ledger.get('artifactCounts') or run_ledger.get('counts') or {}
        if isinstance(maybe_counts, dict):
            run_ledger_artifact_counts = maybe_counts

    return {
        'schema_version': 1,
        'generated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'root': str(base_root),
        'required_checks': manifest.get('required_checks') or [],
        'required_run_ledger_jobs': manifest.get('required_run_ledger_jobs') or [],
        'deployment_acceptance': {
            'path': deployment_rel,
            'description': str(deployment_entry.get('usage') or ''),
            'exists': deployment is not None,
            'eligible': (deployment.get('eligible') is True) if deployment is not None else None,
            'accepted': (deployment.get('accepted') is True) if deployment is not None else None,
            'required_checks': deployment.get('required_checks') if isinstance(deployment, dict) and isinstance(deployment.get('required_checks'), list) else [],
        },
        'ingress_boundary_evidence': {
            'path': ingress_boundary_rel,
            'description': str(ingress_boundary_entry.get('usage') or ''),
            'exists': ingress_boundary is not None,
            'accepted': (ingress_boundary.get('accepted') is True) if ingress_boundary is not None else None,
            'boundary_method': ingress_boundary.get('boundary_evidence', {}).get('method') if isinstance(ingress_boundary, dict) else None,
            'compose_contract_ok': ingress_boundary.get('compose_contract', {}).get('compose_contract_ok') if isinstance(ingress_boundary, dict) else None,
            'runtime_contract_ok': ingress_boundary.get('runtime_contract', {}).get('runtime_contract_ok') if isinstance(ingress_boundary, dict) else None,
            'nginx_policy_required': nginx_policy.get('required') if nginx_policy else None,
            'nginx_policy_ok': nginx_policy.get('ok') if nginx_policy else None,
            'nginx_policy_default_deny': nginx_policy.get('default_deny') if nginx_policy else None,
            'nginx_policy_rewrite_phase_default_deny': nginx_policy.get('rewrite_phase_default_deny') if nginx_policy else None,
            'nginx_policy_access_phase_default_deny': nginx_policy.get('access_phase_default_deny') if nginx_policy else None,
        },
        'runtime_acceptance': {
            'path': runtime_rel,
            'description': str(runtime_acceptance_entry.get('usage') or ''),
            'exists': runtime is not None,
            'eligible': (runtime.get('eligible') is True) if runtime is not None else None,
            'accepted': (runtime.get('accepted') is True) if runtime is not None else None,
            'doctor_passed': (runtime.get('doctor_passed') is True) if runtime is not None else None,
            'blocking_findings': len(runtime.get('security_blocking_findings') or []) if runtime is not None else None,
            'control_plane_scheduler_healthy': runtime.get('control_plane_scheduler_healthy') if runtime is not None else None,
            'control_plane_heartbeat_age_seconds': runtime.get('control_plane_heartbeat_age_seconds') if runtime is not None else None,
            'control_plane_run_ledger_accepted': runtime.get('control_plane_run_ledger_accepted') if runtime is not None else None,
            'control_plane_run_ledger_missing_jobs': runtime.get('control_plane_run_ledger_missing_jobs') if runtime is not None else None,
            'control_plane_run_ledger_failing_jobs': runtime.get('control_plane_run_ledger_failing_jobs') if runtime is not None else None,
            'control_plane_run_ledger_artifact_missing_jobs': runtime.get('control_plane_run_ledger_artifact_missing_jobs') if runtime is not None else None,
            'control_plane_run_ledger_artifact_failing_jobs': runtime.get('control_plane_run_ledger_artifact_failing_jobs') if runtime is not None else None,
            'control_plane_run_ledger_recovered_jobs': runtime.get('control_plane_run_ledger_recovered_jobs') if runtime is not None else None,
            'control_plane_agent_group_count': runtime.get('control_plane_agent_group_count') if runtime is not None else None,
            'control_plane_agent_module_count': runtime.get('control_plane_agent_module_count') if runtime is not None else None,
            'control_plane_required_agent_groups': runtime.get('control_plane_required_agent_groups') if runtime is not None else None,
            'control_plane_failing_agent_groups': runtime.get('control_plane_failing_agent_groups') if runtime is not None else None,
            'control_plane_agent_group_statuses': runtime.get('control_plane_agent_group_statuses') if runtime is not None else None,
        },
        'dispatch_runtime_check': {
            'path': dispatch_runtime_rel,
            'description': str(dispatch_runtime_entry.get('usage') or ''),
            'exists': dispatch_runtime is not None,
            'ok': (dispatch_runtime.get('ok') is True) if dispatch_runtime is not None else None,
            'signal_id': (dispatch_runtime.get('dispatch_summary') or {}).get('signal_id') if isinstance(dispatch_runtime, dict) else None,
        },
        'control_plane_job_artifact_policies': {
            'path': artifact_policies_rel,
            'description': str(artifact_policies_entry.get('usage') or ''),
            'exists': artifact_policies is not None,
            'job_count': len(artifact_policies.get('items') or []) if isinstance(artifact_policies, dict) else None,
        },
        'control_plane_run_ledger': {
            'path': run_ledger_rel,
            'description': str(run_ledger_entry.get('usage') or ''),
            'exists': run_ledger is not None,
            'artifact_accepted_jobs': run_ledger_artifact_counts.get('acceptedJobs') if run_ledger is not None else None,
            'artifact_failed_jobs': run_ledger_artifact_counts.get('failedJobs') if run_ledger is not None else None,
            'artifact_missing_jobs': run_ledger_artifact_counts.get('missingJobs') if run_ledger is not None else None,
            'execution_effective_accepted_jobs': (run_ledger.get('executionEffectiveCounts') or {}).get('acceptedJobs') if run_ledger is not None else None,
            'execution_effective_failed_jobs': (run_ledger.get('executionEffectiveCounts') or {}).get('failedJobs') if run_ledger is not None else None,
            'artifact_effective_accepted_jobs': (run_ledger.get('artifactEffectiveCounts') or {}).get('acceptedJobs') if run_ledger is not None else None,
            'artifact_effective_failed_jobs': (run_ledger.get('artifactEffectiveCounts') or {}).get('failedJobs') if run_ledger is not None else None,
            'recovered_jobs': (run_ledger.get('executionEffectiveCounts') or {}).get('recoveredJobs') if run_ledger is not None else None,
        },
        'official_cli': {
            'control_plane': {
                'path': control_plane_rel,
                'description': str(official_cli_entry.get('usage') or ''),
                'exists': control_plane_summary is not None,
                'doctor_passed': (control_plane_summary.get('doctor_passed') is True) if control_plane_summary is not None else None,
                'blocking_findings': len(control_plane_summary.get('security_blocking_findings') or []) if control_plane_summary is not None else None,
            },
        },
    }


def build_runtime_evidence_status(release_root: Path) -> dict[str, Any]:
    runtime_path = release_root / 'evidence' / 'runtime-acceptance.json'
    control_plane_path = release_root / 'evidence' / 'official-cli-summary.control-plane.json'
    dispatch_runtime_path = release_root / 'evidence' / 'dispatch-runtime-check.json'
    artifact_policies_path = release_root / 'evidence' / 'control-plane-job-artifact-policies.json'
    runtime = safe_read_json(runtime_path)
    control_plane_summary = safe_read_json(control_plane_path)
    dispatch_runtime = safe_read_json(dispatch_runtime_path)
    artifact_policies = safe_read_json(artifact_policies_path)
    return {
        'runtime_acceptance_exists': runtime is not None,
        'runtime_accepted': runtime.get('accepted') if isinstance(runtime, dict) else None,
        'official_cli_summary_exists': control_plane_summary is not None,
        'doctor_passed': control_plane_summary.get('doctor_passed') if isinstance(control_plane_summary, dict) else None,
        'dispatch_runtime_check_exists': dispatch_runtime is not None,
        'dispatch_runtime_ok': dispatch_runtime.get('ok') if isinstance(dispatch_runtime, dict) else None,
        'control_plane_artifact_policies_exists': artifact_policies is not None,
        'control_plane_artifact_policy_job_count': len(artifact_policies.get('items') or []) if isinstance(artifact_policies, dict) else None,
    }
