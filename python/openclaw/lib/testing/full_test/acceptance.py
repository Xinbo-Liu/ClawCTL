#!/usr/bin/env python3
"""Acceptance and validation helpers for the full-test surface."""
from __future__ import annotations

import shlex
import re
from pathlib import Path
from typing import Any

from openclaw.control_plane.api import render_run_ledger_summary
from openclaw.control_plane.run_ledger import (
    row_artifact_accepted,
    row_effective_artifact_accepted,
    row_effective_execution_accepted,
    row_execution_accepted,
)
from openclaw.lib.testing.full_test.io import fail, read_manifest, write_json


def parse_required_check_pairs(csv: str) -> list[dict[str, str]]:
    if not csv:
        return []
    items: list[dict[str, str]] = []
    for part in csv.split(','):
        if not part:
            continue
        if '=' not in part:
            fail(f'required check 需要 id=status：{part}')
        check_id, status = part.split('=', 1)
        if not check_id or not status:
            fail(f'required check 需要 id=status：{part}')
        items.append({'id': check_id, 'status': status})
    return items


def write_deployment_acceptance_state(payload: dict[str, Any]) -> None:
    manifest = read_manifest()
    required_checks = parse_required_check_pairs(payload['requiredChecks'])
    run_ledger_snapshot = payload.get('runLedgerSnapshot')
    if not isinstance(run_ledger_snapshot, dict):
        run_ledger_snapshot = summarize_required_run_ledger(manifest)
    run_ledger_policy = payload.get('runLedgerPolicy')
    if not isinstance(run_ledger_policy, dict):
        run_ledger_policy = run_ledger_acceptance_policy(
            run_ledger_snapshot,
            required_run_ledger_jobs(manifest),
            required_checks_passed=all(item['status'] == 'PASS' for item in required_checks),
        )
    write_json(Path(payload['out']), {
        'schema_version': 2,
        'generated_at': payload['generatedAt'],
        'suite': payload['suite'],
        'env_file': payload['envFile'],
        'eligible': payload['eligible'],
        'accepted': payload['accepted'],
        'required_checks': required_checks,
        'required_run_ledger_jobs': required_run_ledger_jobs(manifest),
        'run_ledger_snapshot': run_ledger_snapshot,
        'run_ledger_policy': run_ledger_policy,
    })


def parse_bool(raw: str, flag: str) -> bool:
    if raw in {'1', 'true'}:
        return True
    if raw in {'0', 'false'}:
        return False
    fail(f'{flag} 只接受 true/false/1/0：{raw}')


def read_lines(file_path: str) -> list[str]:
    if not file_path:
        return []
    path = Path(file_path)
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding='utf-8').splitlines() if line]


_DURATION_MARKER_RE = re.compile(r'(?:^|\s)\[(?:full_test|setup_gate)_duration_seconds=(\d+)\]')


def _extract_duration_seconds(detail: str) -> tuple[str, int | None]:
    match = _DURATION_MARKER_RE.search(detail)
    if not match:
        return detail, None
    duration_seconds = int(match.group(1))
    cleaned = f'{detail[:match.start()]}{detail[match.end():]}'.strip()
    return cleaned, duration_seconds


def parse_result_line(line: str) -> dict[str, Any]:
    parts = line.split('|')
    padded = (parts + ['', '', '', ''])[:4]
    status, check_id, detail, group = padded
    detail, duration_seconds = _extract_duration_seconds(detail)
    row: dict[str, Any] = {'status': status, 'id': check_id, 'detail': detail, 'group': group}
    if duration_seconds is not None:
        row['duration_seconds'] = duration_seconds
    return row


def normalize_scalar_list(raw: str) -> list[str]:
    if not raw:
        return []
    seen: set[str] = set()
    items: list[str] = []
    for part in raw.split(','):
        value = part.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        items.append(value)
    return items


def group_catalog(manifest: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    payload = read_manifest() if manifest is None else manifest
    return list(payload.get('groups') or [])


def check_catalog(manifest: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    payload = read_manifest() if manifest is None else manifest
    return list(payload.get('checks') or [])


def selectable_groups(manifest: dict[str, Any] | None = None) -> list[str]:
    payload = read_manifest() if manifest is None else manifest
    valid_groups = payload.get('valid_groups') or []
    if valid_groups:
        return list(valid_groups)
    return ['all', *[item['id'] for item in group_catalog(payload) if item.get('selectable', True) is not False]]


def required_acceptance_ids(manifest: dict[str, Any] | None = None) -> list[str]:
    payload = read_manifest() if manifest is None else manifest
    return list((payload.get('acceptance_reference') or {}).get('required_checks') or [])


def required_run_ledger_jobs(manifest: dict[str, Any] | None = None) -> list[str]:
    payload = read_manifest() if manifest is None else manifest
    return list((payload.get('acceptance_reference') or {}).get('required_run_ledger_jobs') or [])


def summarize_required_run_ledger(manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = read_manifest() if manifest is None else manifest
    required_jobs = required_run_ledger_jobs(payload)
    snapshot: dict[str, Any] = {
        'exists': False,
        'generated_at': None,
        'required_jobs': required_jobs,
        'job_states': [],
        'accepted': None,
        'artifact_accepted_jobs': [],
        'artifact_missing_jobs': [],
        'execution_accepted_jobs': [],
        'missing_jobs': [],
        'failing_jobs': [],
        'artifact_failing_jobs': [],
        'recovered_jobs': [],
        'counts': {},
        'error': None,
    }
    try:
        ledger = render_run_ledger_summary()
    except Exception as exc:  # pragma: no cover
        snapshot['error'] = str(exc)
        return snapshot
    if not isinstance(ledger, dict):
        snapshot['error'] = 'run_ledger_unavailable'
        return snapshot
    snapshot['exists'] = True
    snapshot['generated_at'] = ledger.get('generatedAt')
    artifact_counts = ledger.get('artifactCounts') or ledger.get('counts') or {}
    execution_counts = ledger.get('executionCounts') or {}
    snapshot['counts'] = dict(artifact_counts) if isinstance(artifact_counts, dict) else {}
    snapshot['artifact_counts'] = dict(artifact_counts) if isinstance(artifact_counts, dict) else {}
    snapshot['execution_counts'] = dict(execution_counts) if isinstance(execution_counts, dict) else {}
    items = {str(item.get('id') or ''): item for item in (ledger.get('items') or []) if isinstance(item, dict)}
    for job_id in required_jobs:
        row = items.get(job_id)
        if row is None:
            snapshot['missing_jobs'].append(job_id)
            snapshot['job_states'].append({
                'id': job_id,
                'exists': False,
                'artifact_accepted': None,
                'effective_artifact_accepted': None,
                'execution_accepted': None,
                'effective_execution_accepted': None,
                'current_status': None,
                'issues': ['job_not_found_in_run_ledger'],
            })
            continue
        artifact_accepted = row_artifact_accepted(row)
        effective_artifact_accepted = row_effective_artifact_accepted(row)
        execution_accepted = row_execution_accepted(row)
        effective_execution_accepted = row_effective_execution_accepted(row)
        raw_issues = row.get('issues')
        issues = [str(item) for item in raw_issues] if isinstance(raw_issues, list) else []
        if effective_artifact_accepted is True:
            snapshot['artifact_accepted_jobs'].append(job_id)
        elif effective_artifact_accepted is False:
            snapshot.setdefault('artifact_failing_jobs', []).append(job_id)
        elif job_id not in snapshot['missing_jobs']:
            snapshot.setdefault('artifact_missing_jobs', []).append(job_id)
        if effective_execution_accepted:
            snapshot['execution_accepted_jobs'].append(job_id)
        elif effective_execution_accepted is False:
            snapshot['failing_jobs'].append(job_id)
        elif job_id not in snapshot['missing_jobs']:
            snapshot['missing_jobs'].append(job_id)
        if execution_accepted is False and effective_execution_accepted is True:
            snapshot.setdefault('recovered_jobs', []).append(job_id)
        snapshot['job_states'].append({
            'id': job_id,
            'exists': True,
            'artifact_accepted': artifact_accepted,
            'effective_artifact_accepted': effective_artifact_accepted,
            'execution_accepted': execution_accepted,
            'effective_execution_accepted': effective_execution_accepted,
            'effective_status': row.get('effectiveStatus'),
            'current_status': row.get('currentStatus'),
            'last_finished_at': row.get('lastFinishedAt'),
            'issues': issues,
            'latest_effective_access': row.get('latestEffectiveAccess'),
        })
    snapshot['accepted'] = bool(required_jobs) and not snapshot['missing_jobs'] and not snapshot['failing_jobs'] and not snapshot['artifact_missing_jobs'] and not snapshot['artifact_failing_jobs']
    if not required_jobs:
        snapshot['accepted'] = None
    return snapshot


def run_ledger_acceptance_policy(
    snapshot: dict[str, Any],
    required_jobs: list[str],
    *,
    required_checks_passed: bool,
) -> dict[str, Any]:
    missing_jobs = [str(item) for item in (snapshot.get('missing_jobs') or [])]
    failing_jobs = [str(item) for item in (snapshot.get('failing_jobs') or [])]
    artifact_missing_jobs = [str(item) for item in (snapshot.get('artifact_missing_jobs') or [])]
    artifact_failing_jobs = [str(item) for item in (snapshot.get('artifact_failing_jobs') or [])]
    policy: dict[str, Any] = {
        'required': bool(required_jobs),
        'blocking': False,
        'reason_code': 'not_required',
        'required_jobs': list(required_jobs),
        'missing_jobs': missing_jobs,
        'failing_jobs': failing_jobs,
        'artifact_missing_jobs': artifact_missing_jobs,
        'artifact_failing_jobs': artifact_failing_jobs,
    }
    if not required_jobs:
        return policy
    if missing_jobs:
        policy['blocking'] = True
        policy['reason_code'] = 'missing_required_run_ledger_jobs'
        return policy
    if snapshot.get('error'):
        policy['blocking'] = True
        policy['reason_code'] = 'run_ledger_unavailable'
        policy['error'] = str(snapshot.get('error') or '')
        return policy
    if failing_jobs:
        policy['blocking'] = True
        policy['reason_code'] = 'run_ledger_not_accepted'
        return policy
    if artifact_missing_jobs or artifact_failing_jobs:
        policy['blocking'] = True
        policy['reason_code'] = 'artifact_run_ledger_not_accepted'
        return policy
    if snapshot.get('accepted') is True:
        policy['reason_code'] = 'accepted'
        return policy
    policy['blocking'] = True
    policy['reason_code'] = 'run_ledger_not_accepted'
    return policy


def validate_group_name(value: str, manifest: dict[str, Any] | None = None) -> str:
    payload = read_manifest() if manifest is None else manifest
    if value not in selectable_groups(payload):
        fail(f'不支持的 group：{value}')
    return value


def execution_order(manifest: dict[str, Any] | None = None, selected_group: str = 'all') -> list[str]:
    payload = read_manifest() if manifest is None else manifest
    ordered = payload.get('execution_order') or [item['id'] for item in group_catalog(payload) if item.get('selectable', True) is not False]
    if selected_group == 'all':
        return list(ordered)
    validate_group_name(selected_group, payload)
    return [selected_group]


def group_by_id(manifest: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    payload = read_manifest() if manifest is None else manifest
    return {item['id']: item for item in group_catalog(payload)}


def check_by_id(manifest: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    payload = read_manifest() if manifest is None else manifest
    return {item['id']: item for item in check_catalog(payload)}


def normalize_check_csv(raw: str, flag_name: str = '--csv', manifest: dict[str, Any] | None = None) -> str:
    payload = read_manifest() if manifest is None else manifest
    ids = normalize_scalar_list(raw)
    known_checks = check_by_id(payload)
    for check_id in ids:
        if check_id not in known_checks:
            fail(f'{flag_name} 包含未知检查项：{check_id}')
    return ','.join(ids)


def normalize_required_acceptance_ids(raw: str, manifest: dict[str, Any] | None = None) -> list[str]:
    if not raw:
        return required_acceptance_ids(manifest)
    normalized = normalize_scalar_list(raw)
    expected = required_acceptance_ids(manifest)
    if normalized != expected:
        fail(f"required acceptance ids 与 acceptance surface 真源不一致：{','.join(normalized)}")
    return expected


def validate_check_records(checks: list[dict[str, Any]], manifest: dict[str, Any] | None = None) -> None:
    payload = read_manifest() if manifest is None else manifest
    groups = group_by_id(payload)
    known_checks = check_by_id(payload)
    for check in checks:
        spec = known_checks.get(check['id'])
        if spec is None:
            fail(f"摘要中出现未知检查项：{check['id']}")
        if check['group'] not in groups:
            fail(f"摘要中出现未知检查组：{check['group']}")
        if spec['group'] != check['group']:
            fail(f"检查项 {check['id']} 的分组与真源不一致：期望 {spec['group']}，实际 {check['group']}")


def build_acceptance_status(options: dict[str, Any], manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = read_manifest() if manifest is None else manifest
    result_lines_file = options.get('resultLinesFile', '')
    if not result_lines_file:
        fail('acceptance-status 缺少 --result-lines-file')
    validate_group_name(options.get('group', 'all'), payload)
    checks = [parse_result_line(line) for line in read_lines(result_lines_file)]
    validate_check_records(checks, payload)
    contract = payload.get('acceptance_contract') or {}
    required = required_acceptance_ids(payload)
    run_ledger_snapshot = summarize_required_run_ledger(payload)
    run_ledger_jobs = required_run_ledger_jobs(payload)
    status_by_id = {item['id']: item['status'] for item in checks}
    required_checks = [{'id': check_id, 'status': status_by_id.get(check_id, 'NOT_RUN')} for check_id in required]
    required_checks_passed = all(item['status'] == 'PASS' for item in required_checks)
    run_ledger_policy = run_ledger_acceptance_policy(
        run_ledger_snapshot,
        run_ledger_jobs,
        required_checks_passed=required_checks_passed,
    )
    eligible = (
        options.get('group', 'all') == ((contract.get('eligible_when') or {}).get('group') or 'all')
        and (not contract.get('eligible_when') or contract['eligible_when'].get('only_empty') is not True or not options.get('only'))
        and (not contract.get('eligible_when') or contract['eligible_when'].get('skip_empty') is not True or not options.get('skip'))
    )
    accepted = eligible and required_checks_passed and not run_ledger_policy['blocking']
    derived_status = 'PASS' if eligible and accepted else 'FAIL' if eligible else 'SKIP'
    if derived_status == 'PASS':
        detail = contract.get('pass_detail') or 'deployment acceptance 已满足'
    elif derived_status == 'FAIL':
        detail = contract.get('fail_detail') or 'deployment acceptance 未满足'
    else:
        detail = contract.get('skip_detail') or '当前不是默认全量 full 运行，跳过 deployment acceptance 评估'
    action = contract.get('fail_action') or '' if derived_status == 'FAIL' else ''
    return {
        'eligible': eligible,
        'accepted': accepted,
        'required_checks': required_checks,
        'run_ledger_snapshot': run_ledger_snapshot,
        'run_ledger_policy': run_ledger_policy,
        'contract': {
            'id': contract.get('check_id') or 'deployment_acceptance_contract',
            'group': contract.get('group') or 'acceptance',
            'status': derived_status,
            'detail': detail,
            'action': action,
        },
    }


def shell_escape(value: str) -> str:
    return shlex.quote(str(value))


def render_acceptance_shell(status: dict[str, Any]) -> str:
    required_csv = ','.join(f"{item['id']}={item['status']}" for item in status['required_checks'])
    return '\n'.join([
        f"FULL_TEST_ACCEPTANCE_ELIGIBLE={'true' if status['eligible'] else 'false'}",
        f"FULL_TEST_ACCEPTANCE_ACCEPTED={'true' if status['accepted'] else 'false'}",
        f"FULL_TEST_ACCEPTANCE_CONTRACT_ID={shell_escape(status['contract']['id'])}",
        f"FULL_TEST_ACCEPTANCE_CONTRACT_GROUP={shell_escape(status['contract']['group'])}",
        f"FULL_TEST_ACCEPTANCE_CONTRACT_STATUS={shell_escape(status['contract']['status'])}",
        f"FULL_TEST_ACCEPTANCE_CONTRACT_DETAIL={shell_escape(status['contract']['detail'])}",
        f"FULL_TEST_ACCEPTANCE_CONTRACT_ACTION={shell_escape(status['contract'].get('action') or '')}",
        f"FULL_TEST_ACCEPTANCE_REQUIRED_CHECKS={shell_escape(required_csv)}",
    ])


def render_acceptance_kv_lines(status: dict[str, Any]) -> str:
    required_csv = ','.join(f"{item['id']}={item['status']}" for item in status['required_checks'])

    def clean(value: Any) -> str:
        return str(value or '').replace('\r', ' ').replace('\n', ' ').strip()

    rows = [
        ('FULL_TEST_ACCEPTANCE_ELIGIBLE', 'true' if status['eligible'] else 'false'),
        ('FULL_TEST_ACCEPTANCE_ACCEPTED', 'true' if status['accepted'] else 'false'),
        ('FULL_TEST_ACCEPTANCE_CONTRACT_ID', clean(status['contract']['id'])),
        ('FULL_TEST_ACCEPTANCE_CONTRACT_GROUP', clean(status['contract']['group'])),
        ('FULL_TEST_ACCEPTANCE_CONTRACT_STATUS', clean(status['contract']['status'])),
        ('FULL_TEST_ACCEPTANCE_CONTRACT_DETAIL', clean(status['contract']['detail'])),
        ('FULL_TEST_ACCEPTANCE_CONTRACT_ACTION', clean(status['contract'].get('action') or '')),
        ('FULL_TEST_ACCEPTANCE_REQUIRED_CHECKS', clean(required_csv)),
    ]
    return '\n'.join(f'{key}={value}' for key, value in rows)


def write_acceptance_state(options: dict[str, Any]) -> dict[str, Any]:
    if not options.get('outJson'):
        fail('write-acceptance-state 缺少 --out-json')
    if not options.get('generatedAt'):
        fail('write-acceptance-state 缺少 --generated-at')
    if not options.get('envFile'):
        fail('write-acceptance-state 缺少 --env-file')
    status = build_acceptance_status(options)
    write_deployment_acceptance_state({
        'out': options['outJson'],
        'generatedAt': options['generatedAt'],
        'suite': 'one_click_test_full',
        'envFile': options['envFile'],
        'eligible': status['eligible'],
        'accepted': status['accepted'],
        'requiredChecks': ','.join(f"{item['id']}={item['status']}" for item in status['required_checks']),
        'runLedgerSnapshot': status['run_ledger_snapshot'],
        'runLedgerPolicy': status['run_ledger_policy'],
    })
    return status
