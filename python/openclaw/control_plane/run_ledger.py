#!/usr/bin/env python3
"""控制平面 run ledger 统一摘要与工件快照。"""
from __future__ import annotations

import os
import json
from datetime import datetime, timedelta, timezone
from os import stat_result
from stat import S_ISDIR
from pathlib import Path
from typing import Any

from openclaw.control_plane.registry.runtime_manifests import read_runtime_manifest_json
from openclaw.lib.io.json_access import json_array, json_object
from openclaw.lib.repo.layout import CONTROL_PLANE_CONFIG_ENV, resolve_repo_root
from openclaw.lib.runtime.time import parse_iso_datetime, utc_iso
from openclaw.lib.runtime.resolver_loader import PathResolverInstance, require_path_resolver
from openclaw.runtime.path_view import normalize_runtime_path_view

ROOT_DIR = resolve_repo_root(Path(__file__))
_MAX_SNAPSHOT_ENTRIES = 64
_MAX_OBSERVED_SCAN_DEPTH = 8


def _now_iso() -> str:
    return utc_iso()


def _parse_iso(value: object) -> datetime | None:
    parsed = parse_iso_datetime(value)
    return parsed.astimezone(timezone.utc) if parsed is not None else None


def _read_optional_json(path_value: object) -> dict[str, Any] | None:
    return read_runtime_manifest_json(path_value, resolver_factory=_resolver)


def _resolver(config_path: Path | None = None) -> PathResolverInstance:
    return require_path_resolver(repo_root=ROOT_DIR, config_path=config_path)


def _resolver_config_path(env: dict[str, str] | None) -> Path | None:
    value = str((env or {}).get(CONTROL_PLANE_CONFIG_ENV) or os.environ.get(CONTROL_PLANE_CONFIG_ENV) or '').strip()
    return Path(value).resolve() if value else None


def _artifact_runtime_view(env: dict[str, str] | None) -> str:
    return normalize_runtime_path_view((env or {}).get('OPENCLAW_RUNTIME_PATH_VIEW') or os.environ.get('OPENCLAW_RUNTIME_PATH_VIEW'), fallback='host')


def resolve_artifact_root(entry_id: str, env: dict[str, str] | None = None) -> Path | None:
    """按 runtime path entry id 解析工件根目录；env 用于选择 host/scheduler 路径视角。"""
    entry = str(entry_id or '').strip()
    if not entry:
        return None
    try:
        resolved = _resolver(_resolver_config_path(env)).resolve_path(entry, _artifact_runtime_view(env), env or os.environ)
    except (OSError, RuntimeError, ValueError, KeyError):
        return None
    return Path(resolved).resolve()


def _entry_snapshot(path: Path, base: Path, stat: stat_result | None = None) -> dict[str, Any]:
    current_stat = stat or path.stat()
    is_dir = path.is_dir() if stat is None else S_ISDIR(current_stat.st_mode)
    item: dict[str, Any] = {
        'relativePath': str(path.relative_to(base)),
        'kind': 'dir' if is_dir else 'file',
        'modifiedAt': datetime.fromtimestamp(current_stat.st_mtime, timezone.utc).isoformat().replace('+00:00', 'Z'),
    }
    if not is_dir:
        item['sizeBytes'] = int(current_stat.st_size)
    return item


def _file_snapshot(path: Path, *, source: str, stat: stat_result | None = None) -> dict[str, Any]:
    current_stat = stat or path.stat()
    item: dict[str, Any] = {
        'path': str(path),
        'kind': 'file',
        'source': source,
        'modifiedAt': datetime.fromtimestamp(current_stat.st_mtime, timezone.utc).isoformat().replace('+00:00', 'Z'),
        'sizeBytes': int(current_stat.st_size),
    }
    return item


def _stat_optional(path: Path) -> stat_result | None:
    try:
        return path.stat()
    except FileNotFoundError:
        return None


def _is_recent(stat: stat_result, threshold: datetime) -> bool:
    modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
    return modified >= threshold


def _append_snapshot(items: list[dict[str, Any]], seen: set[str], path: Path, base: Path, *, stat: stat_result | None = None) -> None:
    relative = str(path.relative_to(base))
    if relative in seen:
        return
    items.append(_entry_snapshot(path, base, stat=stat))
    seen.add(relative)

def _finalize_snapshots(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: (str(item.get('relativePath') or ''), str(item.get('modifiedAt') or '')))


def _should_descend(depth: int, name: str) -> bool:
    if depth >= _MAX_OBSERVED_SCAN_DEPTH:
        return False
    if name in {'__pycache__', '.git'}:
        return False
    return True


def _collect_latest_entries(base: Path, limit: int = _MAX_SNAPSHOT_ENTRIES) -> list[dict[str, Any]]:
    if limit <= 0 or not base.exists() or not base.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(base.iterdir(), key=lambda item: item.name):
        if len(items) >= limit:
            break
        if not path.name.startswith('latest'):
            continue
        items.append(_entry_snapshot(path, base))
    return _finalize_snapshots(items)


def _collect_declared_evidence_entries(base: Path, started_at: datetime | None, latest_alias: str, limit: int = _MAX_SNAPSHOT_ENTRIES) -> list[dict[str, Any]]:
    if limit <= 0 or not base.exists() or not base.is_dir() or started_at is None:
        return []
    threshold = started_at - timedelta(seconds=2)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_candidate(candidate_path: Path) -> None:
        if len(items) >= limit:
            return
        current_stat = _stat_optional(candidate_path)
        if current_stat is None or not _is_recent(current_stat, threshold):
            return
        _append_snapshot(items, seen, candidate_path, base, stat=current_stat)

    latest_alias_text = str(latest_alias or '').strip()
    if latest_alias_text:
        add_candidate(base / latest_alias_text)

    immediate_recent: list[Path] = []
    for path in base.iterdir():
        if latest_alias_text and path.name == latest_alias_text:
            continue
        stat = _stat_optional(path)
        if stat is None or not _is_recent(stat, threshold):
            continue
        immediate_recent.append(path)
    for path in sorted(immediate_recent, key=lambda item: item.name):
        if len(items) >= limit:
            break
        add_candidate(path)
    return _finalize_snapshots(items)


def _collect_observed_entries(base: Path, started_at: datetime | None, limit: int = _MAX_SNAPSHOT_ENTRIES) -> list[dict[str, Any]]:
    if limit <= 0 or not base.exists() or not base.is_dir() or started_at is None:
        return []
    threshold = started_at - timedelta(seconds=2)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    stack: list[tuple[Path, int]] = [(base, 0)]

    while stack and len(items) < limit:
        current, depth = stack.pop()
        try:
            iterator = os.scandir(current)
        except FileNotFoundError:
            continue
        with iterator as entries:
            for entry in entries:
                if len(items) >= limit:
                    break
                path = Path(entry.path)
                try:
                    stat = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if _should_descend(depth, entry.name):
                        stack.append((path, depth + 1))
                    continue
                if not _is_recent(stat, threshold):
                    continue
                _append_snapshot(items, seen, path, base, stat=stat)
    return _finalize_snapshots(items)


def _json_stdout_line_count(path: Path, *, max_bytes: int = 1024 * 1024) -> int:
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return 0
    if size <= 0 or size > max_bytes:
        return 0
    count = 0
    try:
        with path.open('r', encoding='utf-8') as fh:
            for line in fh:
                text = line.strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict) and payload:
                    count += 1
                elif isinstance(payload, list) and payload:
                    count += 1
    except OSError:
        return 0
    return count


def _collect_scheduler_evidence_entries(stdout_log_path: Path, started_at: datetime | None) -> list[dict[str, Any]]:
    if started_at is None:
        return []
    stat = _stat_optional(stdout_log_path)
    if stat is None or not _is_recent(stat, started_at - timedelta(seconds=2)):
        return []
    json_line_count = _json_stdout_line_count(stdout_log_path)
    if json_line_count < 1:
        return []
    item = _file_snapshot(stdout_log_path, source='scheduler_stdout', stat=stat)
    item['evidenceKind'] = 'structured_stdout_json'
    item['jsonLineCount'] = json_line_count
    return [item]


def build_artifacts_manifest(*, job: dict[str, Any], run_id: str, stdout_log_path: Path, result_status: str, started_at: str, finished_at: str | None = None, env: dict[str, str] | None = None) -> dict[str, Any]:
    """为单次 job 运行生成 artifacts manifest，返回声明输出、观测证据和验收状态。"""
    job_id = str(job.get('resolvedRuntimeJobKey') or job.get('qualifiedId') or job.get('id') or '')
    local_job_id = str(job.get('id') or '')
    outputs = json_object(job.get('resolvedOutputs'))
    inputs = json_object(job.get('resolvedInputs'))
    artifact_policy = json_object(job.get('artifactPolicy'))
    declared_output_artifacts = [str(item) for item in json_array(outputs.get('artifacts'))]
    declared_input_artifacts = [str(item) for item in json_array(inputs.get('artifacts'))]
    declared_status_signals = [str(item) for item in json_array(outputs.get('statusSignals'))]

    artifact_root_entry = str(artifact_policy.get('runArtifactRoot') or '').strip()
    latest_alias = str(artifact_policy.get('latestAlias') or '').strip()
    artifact_root_path = resolve_artifact_root(artifact_root_entry, env=env) if artifact_root_entry else None
    artifact_root_exists = bool(artifact_root_path and artifact_root_path.is_dir())
    started_dt = _parse_iso(started_at)
    latest_entries = _collect_latest_entries(artifact_root_path) if artifact_root_path is not None else []
    observed_entries: list[dict[str, Any]] = []
    if artifact_root_path is not None and not latest_entries:
        observed_entries = _collect_declared_evidence_entries(artifact_root_path, started_dt, latest_alias)
        if not observed_entries:
            observed_entries = _collect_observed_entries(artifact_root_path, started_dt)
    scheduler_entries = _collect_scheduler_evidence_entries(stdout_log_path, started_dt) if not latest_entries and not observed_entries else []

    has_declared_outputs = bool(declared_output_artifacts or declared_status_signals)
    evidence_present = bool(latest_entries or observed_entries or scheduler_entries)
    evidence_sources = []
    if latest_entries:
        evidence_sources.append('latest_alias')
    if observed_entries:
        evidence_sources.append('artifact_root_recent_file')
    if scheduler_entries:
        evidence_sources.append('scheduler_structured_stdout')
    acceptance_reasons: list[str] = []
    passed: bool | None

    if result_status == 'running':
        passed = None
    elif result_status != 'succeeded':
        passed = False
        acceptance_reasons.append(f'result_status={result_status}')
    else:
        if artifact_root_entry and artifact_root_exists is not True:
            acceptance_reasons.append(f'artifact_root_missing:{artifact_root_entry}')
        if has_declared_outputs and not evidence_present:
            acceptance_reasons.append('declared_outputs_without_observed_evidence')
        passed = not acceptance_reasons

    return {
        'schemaVersion': 1,
        'generatedAt': _now_iso(),
        'jobId': job_id,
        'localJobId': local_job_id,
        'qualifiedJobId': str(job.get('qualifiedId') or ''),
        'runId': run_id,
        'stdoutLogPath': str(stdout_log_path),
        'declaredInputArtifacts': declared_input_artifacts,
        'declaredOutputArtifacts': declared_output_artifacts,
        'declaredStatusSignals': declared_status_signals,
        'artifactPolicy': {
            'runArtifactRoot': artifact_root_entry or None,
            'latestAlias': latest_alias or None,
            'retentionDays': int(artifact_policy.get('retentionDays') or 0) if artifact_policy else None,
        },
        'artifactRoot': {
            'entryId': artifact_root_entry or None,
            'path': str(artifact_root_path) if artifact_root_path is not None else None,
            'exists': artifact_root_exists if artifact_root_entry else None,
        },
        'latestEntries': latest_entries,
        'observedEntries': observed_entries,
        'schedulerEntries': scheduler_entries,
        'acceptance': {
            'status': 'pending' if passed is None else ('pass' if passed else 'fail'),
            'passed': passed,
            'hasDeclaredOutputs': has_declared_outputs,
            'artifactRootConfigured': bool(artifact_root_entry),
            'artifactRootPresent': artifact_root_exists if artifact_root_entry else None,
            'evidencePresent': evidence_present,
            'evidenceSources': evidence_sources,
            'reasons': acceptance_reasons,
            'startedAt': started_at,
            'finishedAt': finished_at,
        },
    }


def build_job_ledger_row(job: dict[str, Any], job_state: dict[str, Any]) -> dict[str, Any]:
    """把 job registry 行和运行态 state 合并为 run ledger 的单条展示记录。"""
    run_manifest = _read_optional_json(job_state.get('lastRunManifestPath'))
    result_manifest = _read_optional_json(job_state.get('lastResultManifestPath'))
    artifacts_manifest = _read_optional_json(job_state.get('lastArtifactsPath'))
    accepted = None
    execution_accepted = None
    issues: list[str] = []

    if result_manifest is None:
        issues.append('missing_result_manifest')
    if artifacts_manifest is None:
        issues.append('missing_artifacts_manifest')
    if run_manifest is None:
        issues.append('missing_run_manifest')

    if isinstance(result_manifest, dict):
        accepted = result_manifest.get('acceptedByLedger') is True
        execution_accepted = result_manifest.get('status') == 'succeeded'
        if result_manifest.get('status') != 'succeeded':
            issues.append(f"latest_status={result_manifest.get('status')}")
    if isinstance(artifacts_manifest, dict):
        acceptance = json_object(artifacts_manifest.get('acceptance'))
        if acceptance and acceptance.get('passed') is False:
            issues.extend([str(item) for item in json_array(acceptance.get('reasons'))])

    return {
        'id': str(job.get('id') or ''),
        'runtimeJobKey': str(job.get('resolvedRuntimeJobKey') or job.get('qualifiedId') or job.get('id') or ''),
        'qualifiedId': str(job.get('qualifiedId') or ''),
        'title': str(job.get('title') or ''),
        'enabled': bool(job.get('enabled', True)),
        'currentStatus': str(job_state.get('currentStatus') or ''),
        'lastFinishedAt': str(job_state.get('lastFinishedAt') or ''),
        'lastRunId': str(job_state.get('lastRunId') or ''),
        'accepted': accepted,
        'artifactAccepted': accepted,
        'executionAccepted': execution_accepted,
        'issues': issues,
        'latestRun': run_manifest,
        'latestResult': result_manifest,
        'latestArtifacts': artifacts_manifest,
    }


def _acceptance_counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    enabled_rows = [row for row in rows if row.get('enabled') is True]
    accepted_rows = [row for row in enabled_rows if row.get(key) is True]
    missing_rows = [row for row in enabled_rows if row.get(key) is None]
    failed_rows = [row for row in enabled_rows if row.get(key) is False]
    return {
        'jobs': len(rows),
        'enabledJobs': len(enabled_rows),
        'acceptedJobs': len(accepted_rows),
        'missingJobs': len(missing_rows),
        'failedJobs': len(failed_rows),
    }


def build_run_ledger_summary(registry: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """基于 control plane registry 与 scheduler state 生成 run ledger 汇总。"""
    jobs_state = json_object(state.get('jobs'))
    rows: list[dict[str, Any]] = []
    for job in registry.get('jobs', []):
        if not isinstance(job, dict):
            continue
        job_id = str(job.get('id') or '')
        runtime_job_key = str(job.get('resolvedRuntimeJobKey') or job.get('qualifiedId') or job_id)
        job_state = json_object(jobs_state.get(runtime_job_key))
        rows.append(build_job_ledger_row(job, job_state))

    artifact_counts = _acceptance_counts(rows, 'artifactAccepted')
    execution_counts = _acceptance_counts(rows, 'executionAccepted')

    return {
        'schemaVersion': 1,
        'generatedAt': _now_iso(),
        'service': str((registry.get('service') or {}).get('name') or 'openclaw-control-plane'),
        'configPath': str(registry.get('configPath') or ''),
        'counts': dict(artifact_counts),
        'artifactCounts': dict(artifact_counts),
        'executionCounts': dict(execution_counts),
        'items': rows,
    }


def row_artifact_accepted(row: dict[str, Any]) -> Any:
    """读取单条 ledger 行的工件验收结果；缺失时返回 None。"""
    return row.get('artifactAccepted') if 'artifactAccepted' in row else None


def row_execution_accepted(row: dict[str, Any]) -> Any:
    """读取单条 ledger 行的执行结果验收状态。"""
    return row.get('executionAccepted')


def row_effective_execution_accepted(row: dict[str, Any]) -> Any:
    """读取叠加 overlay 后的执行验收状态，缺失时回退到原始执行结果。"""
    if 'effectiveExecutionAccepted' in row:
        return row.get('effectiveExecutionAccepted')
    return row_execution_accepted(row)


def row_effective_artifact_accepted(row: dict[str, Any]) -> Any:
    """读取叠加 overlay 后的工件验收状态，缺失时回退到原始工件结果。"""
    return row.get('artifactEffectiveAccepted') if 'artifactEffectiveAccepted' in row else row_artifact_accepted(row)


def _runtime_args_from_command(command: object) -> list[str]:
    if not isinstance(command, list):
        return []
    items = [str(item) for item in command]
    if '--' not in items:
        return []
    separator_index = items.index('--')
    return [item for item in items[separator_index + 1:] if item]


def _row_runtime_args(row: dict[str, Any]) -> list[str]:
    latest_run = json_object(row.get('latestRun'))
    latest_result = json_object(row.get('latestResult'))
    run_args = _runtime_args_from_command(latest_run.get('command'))
    return run_args or _runtime_args_from_command(latest_result.get('command'))


def _prefix_from_row(row: dict[str, Any]) -> str:
    for key in ('qualifiedId', 'runtimeJobKey'):
        value = str(row.get(key) or '').strip()
        if ':' in value:
            return value.split(':', 1)[0]
    latest_run = json_object(row.get('latestRun'))
    value = str(latest_run.get('qualifiedJobId') or latest_run.get('jobId') or '').strip()
    return value.split(':', 1)[0] if ':' in value else ''


def _with_qualified_ref(ref: str, prefix: str) -> set[str]:
    normalized = str(ref or '').strip()
    if not normalized:
        return set()
    values = {normalized}
    if prefix and ':' not in normalized:
        values.add(f'{prefix}:{normalized}')
    if ':' in normalized:
        values.add(normalized.split(':', 1)[1])
    return values


def _command_agent_refs(command: object) -> set[str]:
    if not isinstance(command, list):
        return set()
    items = [str(item) for item in command]
    refs: set[str] = set()
    for index, item in enumerate(items):
        if item == '--agent-ref' and index + 1 < len(items):
            value = items[index + 1].strip()
            if value:
                refs.add(value)
    return refs


def _row_agent_refs(row: dict[str, Any]) -> set[str]:
    prefix = _prefix_from_row(row)
    latest_run = json_object(row.get('latestRun'))
    latest_result = json_object(row.get('latestResult'))
    refs: set[str] = set()
    for value in [
        latest_run.get('agentRef'),
        latest_result.get('agentRef'),
        *_command_agent_refs(latest_run.get('command')),
        *_command_agent_refs(latest_result.get('command')),
    ]:
        refs.update(_with_qualified_ref(str(value or '').strip(), prefix))
    return refs


def _row_group_refs(row: dict[str, Any]) -> set[str]:
    prefix = _prefix_from_row(row)
    latest_run = json_object(row.get('latestRun'))
    refs: set[str] = set()
    for value in [latest_run.get('groupRef')]:
        refs.update(_with_qualified_ref(str(value or '').strip(), prefix))
    return refs


def _row_job_refs(row: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    for value in [row.get('id'), row.get('runtimeJobKey'), row.get('qualifiedId')]:
        text = str(value or '').strip()
        if text:
            refs.add(text)
            if ':' in text:
                refs.add(text.split(':', 1)[1])
    return refs


def _access_time(row: dict[str, Any]) -> datetime | None:
    for key in ('recordedAt', 'finishedAt', 'startedAt'):
        parsed = _parse_iso(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _row_finished_at(row: dict[str, Any]) -> datetime | None:
    parsed = _parse_iso(row.get('lastFinishedAt'))
    if parsed is not None:
        return parsed
    latest_result = json_object(row.get('latestResult'))
    parsed = _parse_iso(latest_result.get('finishedAt'))
    if parsed is not None:
        return parsed
    latest_artifacts = json_object(row.get('latestArtifacts'))
    acceptance = json_object(latest_artifacts.get('acceptance'))
    return _parse_iso(acceptance.get('finishedAt'))


def _access_matches_row(access_row: dict[str, Any], ledger_row: dict[str, Any]) -> bool:
    access_job_id = str(access_row.get('jobId') or '').strip()
    if access_job_id and access_job_id in _row_job_refs(ledger_row):
        return True

    agent_ref = str(access_row.get('agentRef') or '').strip()
    if not agent_ref or agent_ref not in _row_agent_refs(ledger_row):
        return False

    group_refs = _row_group_refs(ledger_row)
    access_group_refs = {str(item).strip() for item in json_array(access_row.get('agentGroupRefs')) if str(item).strip()}
    if group_refs and access_group_refs and not group_refs.intersection(access_group_refs):
        return False

    ledger_args = _row_runtime_args(ledger_row)
    access_args = [str(item) for item in json_array(access_row.get('runtimeArgs')) if str(item)]
    if ledger_args and access_args and ledger_args != access_args:
        return False
    return True


def _access_excerpt(row: dict[str, Any]) -> dict[str, Any]:
    return {
        'recordedAt': str(row.get('recordedAt') or '').strip(),
        'finishedAt': str(row.get('finishedAt') or '').strip(),
        'startedAt': str(row.get('startedAt') or '').strip(),
        'source': str(row.get('source') or '').strip(),
        'agentRef': str(row.get('agentRef') or '').strip(),
        'agentModuleRef': str(row.get('agentModuleRef') or '').strip(),
        'jobId': str(row.get('jobId') or '').strip(),
        'runId': str(row.get('runId') or '').strip(),
        'status': str(row.get('status') or '').strip(),
        'runtimeArgs': [str(item) for item in json_array(row.get('runtimeArgs'))],
    }


def apply_latest_agent_access_overlay(run_ledger: dict[str, Any], agent_access_log: dict[str, Any] | None) -> dict[str, Any]:
    """用更新的 agent access 事实补齐调度运行账本的执行口径。"""
    rows = [dict(item) for item in json_array(run_ledger.get('items')) if isinstance(item, dict)]
    access_rows = [dict(item) for item in json_array((agent_access_log or {}).get('items')) if isinstance(item, dict)]
    annotated_rows: list[dict[str, Any]] = []
    for row in rows:
        artifact_accepted = row_artifact_accepted(row)
        execution_accepted = row_execution_accepted(row)
        effective_artifact_accepted = row_effective_artifact_accepted(row)
        effective_execution_accepted = row_effective_execution_accepted(row)
        effective_status = 'accepted' if effective_execution_accepted is True else ('failed' if effective_execution_accepted is False else 'pending')
        effective_source = 'scheduler_run_ledger'
        latest_access: dict[str, Any] | None = None
        row_time = _row_finished_at(row)
        matches = [
            item
            for item in access_rows
            if _access_matches_row(item, row)
            and _access_time(item) is not None
            and (row_time is None or _access_time(item) > row_time)
        ]
        matches.sort(key=lambda item: _access_time(item) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        if matches:
            latest_access = matches[0]
            latest_status = str(latest_access.get('status') or '').strip().lower()
            effective_source = 'latest_agent_access'
            if latest_status == 'succeeded':
                effective_execution_accepted = True
                effective_status = 'recovered' if execution_accepted is False else 'accepted'
            elif latest_status in {'failed', 'error', 'blocked', 'retry_pending'}:
                effective_execution_accepted = False
                effective_status = latest_status
            else:
                effective_execution_accepted = None
                effective_status = latest_status or 'pending'
        row['artifactAccepted'] = artifact_accepted
        row['executionAccepted'] = execution_accepted
        row['artifactEffectiveAccepted'] = effective_artifact_accepted
        row['effectiveExecutionAccepted'] = effective_execution_accepted
        row['effectiveStatus'] = effective_status
        row['effectiveSource'] = effective_source
        if latest_access is not None:
            row['latestEffectiveAccess'] = _access_excerpt(latest_access)
        annotated_rows.append(row)

    enabled_rows = [row for row in annotated_rows if row.get('enabled') is True]
    artifact_effective_accepted_rows = [row for row in enabled_rows if row.get('artifactEffectiveAccepted') is True]
    artifact_effective_missing_rows = [row for row in enabled_rows if row.get('artifactEffectiveAccepted') is None]
    artifact_effective_failed_rows = [row for row in enabled_rows if row.get('artifactEffectiveAccepted') is False]
    execution_effective_accepted_rows = [row for row in enabled_rows if row.get('effectiveExecutionAccepted') is True]
    execution_effective_missing_rows = [row for row in enabled_rows if row.get('effectiveExecutionAccepted') is None]
    execution_effective_failed_rows = [row for row in enabled_rows if row.get('effectiveExecutionAccepted') is False]
    artifact_counts = _acceptance_counts(annotated_rows, 'artifactAccepted')
    execution_counts = _acceptance_counts(annotated_rows, 'executionAccepted')
    recovered_rows = [
        row
        for row in enabled_rows
        if row_execution_accepted(row) is False
        and row.get('effectiveExecutionAccepted') is True
    ]
    effective_counts = {
        'enabledJobs': len(enabled_rows),
        'acceptedJobs': len(execution_effective_accepted_rows),
        'missingJobs': len(execution_effective_missing_rows),
        'failedJobs': len(execution_effective_failed_rows),
        'recoveredJobs': len(recovered_rows),
    }
    return {
        **run_ledger,
        'items': annotated_rows,
        'counts': dict(artifact_counts),
        'artifactCounts': dict(artifact_counts),
        'executionCounts': dict(execution_counts),
        'executionEffectiveCounts': dict(effective_counts),
        'artifactEffectiveCounts': {
            'enabledJobs': len(enabled_rows),
            'acceptedJobs': len(artifact_effective_accepted_rows),
            'missingJobs': len(artifact_effective_missing_rows),
            'failedJobs': len(artifact_effective_failed_rows),
            'recoveredJobs': 0,
        },
    }
