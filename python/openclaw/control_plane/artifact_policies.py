#!/usr/bin/env python3
"""控制平面 job artifact policy / latest alias / run manifest 统一控制面。"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NoReturn

from openclaw.control_plane.registry import control_plane_config_path, load_registry
from openclaw.control_plane.jobs.defaults import artifact_policy_fields
from openclaw.control_plane.registry.store import runtime_files
from openclaw.lib.cli.examples import canonical_cli_command
from openclaw.lib.io.json_access import json_object
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.runtime.resolver_loader import require_path_resolver
from openclaw.scheduler.subprocess_support import safe_fragment

ROOT_DIR = resolve_repo_root(Path(__file__))


def fail(message: str, exit_code: int = 2) -> NoReturn:
    sys.stderr.write(f'[control_plane_artifact_policies][FAIL] {message}\n')
    raise SystemExit(exit_code)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _display_path(path: Path, base_root: Path) -> str:
    try:
        return str(path.relative_to(base_root))
    except ValueError:
        return str(path)


def _host_state_root(base_root: Path, *, path_resolver: Any | None = None) -> Path:
    resolver = path_resolver or require_path_resolver(repo_root=base_root)
    resolved = resolver.resolve_path('state_root', view='host')
    return Path(resolved)


def _resolved_artifact_root(run_artifact_root: str, base_root: Path, *, path_resolver: Any | None = None) -> str | None:
    entry = str(run_artifact_root or '').strip()
    if not entry:
        return None
    try:
        resolver = path_resolver or require_path_resolver(repo_root=base_root)
        return resolver.resolve_path(entry, view='host')
    except KeyError:
        path = Path(entry)
        return str(path if path.is_absolute() else (base_root / path).resolve())


def build_summary(
    *,
    config_path: Path | None = None,
    base_root: Path = ROOT_DIR,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    registry = dict(registry) if registry is not None else load_registry(config_path)
    path_resolver = require_path_resolver(repo_root=base_root)
    files = runtime_files(_host_state_root(base_root, path_resolver=path_resolver), registry)
    jobs = [job for job in list(registry.get('jobs') or []) if isinstance(job, dict)]
    jobs.sort(key=lambda item: (int(item.get('resolvedOrder') or item.get('order') or 0), str(item.get('id') or '')))
    items: list[dict[str, Any]] = []
    for job in jobs:
        artifact_policy = artifact_policy_fields(job.get('artifactPolicy'))
        outputs = json_object(job.get('resolvedOutputs'))
        inputs = json_object(job.get('resolvedInputs'))
        job_id = str(job.get('id') or '').strip()
        run_artifact_root = artifact_policy['runArtifactRoot']
        latest_alias = artifact_policy['latestAlias']
        retention_days = int(artifact_policy['retentionDays'] or 0)
        declared_output_artifacts = [str(item).strip() for item in list(outputs.get('artifacts') or []) if str(item).strip()]
        declared_status_signals = [str(item).strip() for item in list(outputs.get('statusSignals') or []) if str(item).strip()]
        declared_input_artifacts = [str(item).strip() for item in list(inputs.get('artifacts') or []) if str(item).strip()]
        runtime_job_key = str(job.get('resolvedRuntimeJobKey') or job.get('qualifiedId') or job_id).strip()
        qualified_job_id = str(job.get('qualifiedId') or '').strip()
        run_root = files.runs_dir / safe_fragment(runtime_job_key)
        items.append({
            'id': job_id,
            'localJobId': job_id,
            'qualifiedId': qualified_job_id,
            'runtimeJobKey': runtime_job_key,
            'title': str(job.get('title') or '').strip(),
            'order': int(job.get('resolvedOrder') or job.get('order') or 0),
            'enabled': bool(job.get('enabled', True)),
            'runArtifactRootEntry': run_artifact_root or None,
            'resolvedArtifactRootHostPath': _resolved_artifact_root(run_artifact_root, base_root, path_resolver=path_resolver),
            'latestAlias': latest_alias or None,
            'retentionDays': retention_days,
            'declaredInputArtifacts': declared_input_artifacts,
            'declaredOutputArtifacts': declared_output_artifacts,
            'declaredStatusSignals': declared_status_signals,
            'requiresObservedEvidence': bool(declared_output_artifacts or declared_status_signals),
            'schedulerRunDirPattern': _display_path(run_root / '<run_id>', base_root),
            'schedulerRunManifestPathPattern': _display_path(run_root / '<run_id>' / 'run.json', base_root),
            'schedulerResultManifestPathPattern': _display_path(run_root / '<run_id>' / 'result.json', base_root),
            'schedulerArtifactsManifestPathPattern': _display_path(run_root / '<run_id>' / 'artifacts.json', base_root),
            'schedulerStdoutLogPathPattern': _display_path(run_root / '<run_id>' / 'stdout.log', base_root),
            'runLedgerAcceptedField': 'latestResult.acceptedByLedger',
        })
    return {
        'schemaVersion': 1,
        'generatedAt': _now_iso(),
        'configPath': _display_path((config_path if config_path is not None else control_plane_config_path()), base_root),
        'schedulerRunsRoot': _display_path(files.runs_dir, base_root),
        'items': items,
    }


def usage() -> str:
    base_command = canonical_cli_command('control-plane', 'artifacts')
    return (
        '用法：\n'
        f'  {base_command} json\n'
        f'  {base_command} job --job-id <job_id>\n'
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {'-h', '--help'}:
        sys.stdout.write(usage())
        return 0
    command = args.pop(0)
    summary = build_summary()
    if command == 'json':
        if args:
            fail(f'json 不接受参数：{" ".join(args)}')
        sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
        return 0
    if command == 'job':
        if len(args) != 2 or args[0] != '--job-id' or not args[1].strip():
            fail('job 需要 --job-id <job_id>')
        job_id = args[1].strip()
        for item in list(summary.get('items') or []):
            selectors = {
                str(item.get('id') or '').strip(),
                str(item.get('qualifiedId') or '').strip(),
                str(item.get('runtimeJobKey') or '').strip(),
            }
            if job_id in selectors:
                sys.stdout.write(json.dumps(item, ensure_ascii=False, indent=2) + '\n')
                return 0
        fail(f'未找到 job：{job_id}', 2)
    fail(f'未知命令：{command}', 2)

if __name__ == '__main__':
    raise SystemExit(main())
