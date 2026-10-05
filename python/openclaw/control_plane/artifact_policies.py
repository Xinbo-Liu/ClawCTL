#!/usr/bin/env python3
"""汇总控制平面作业的产物策略、latest alias 声明与 scheduler manifest 路径模式。"""
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
    """将产物根逻辑 entry 解析为宿主机路径；未声明或未登记时返回 None。

    参数：
        run_artifact_root（str）：job 声明的逻辑路径 entry。
        base_root（Path）：未注入解析器时使用的仓库根目录。
        path_resolver（Any | None）：与 job registry 使用同一 profile 的解析器。
    返回：
        str | None：已登记 entry 的宿主机路径；空声明或未知 entry 返回 None。
    副作用：
        只解析仓库路径合同，不推测文件路径，也不读取运行产物。
    """
    entry = str(run_artifact_root or '').strip()
    if not entry:
        return None
    try:
        resolver = path_resolver or require_path_resolver(repo_root=base_root)
        return resolver.resolve_path(entry, view='host')
    except KeyError:
        return None


def build_summary(
    *,
    config_path: Path | None = None,
    base_root: Path = ROOT_DIR,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """用同一 service 配置汇总 job 产物声明与 scheduler run 路径。

    参数：
        config_path（Path | None）：作业 registry 的 service 配置；缺省使用当前控制面选择。
        base_root（Path）：显示相对路径时使用的仓库根目录。
        registry（dict[str, Any] | None）：已加载的作业 registry，应与 config_path 一致；None 按 config_path 加载。

    返回：
        dict[str, Any]：作业产物策略、声明与 scheduler manifest 路径模式，不读取实际运行产物。
    副作用：
        读取所选 profile 的 registry 与路径合同，使用共享解析器缓存，不访问部署 env 文件或运行产物。
    """
    registry = dict(registry) if registry is not None else load_registry(config_path)
    path_resolver = require_path_resolver(repo_root=base_root, config_path=config_path)
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
