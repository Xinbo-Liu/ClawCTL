#!/usr/bin/env python3
"""Repo-marker and file-copy helpers for the managed probe fixture."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def write_text(path: Path, content: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    if executable:
        path.chmod(path.stat().st_mode | 0o111)


def relative_path(base: Path, target: Path) -> str:
    try:
        return Path(os.path.relpath(target.resolve(), start=base.resolve())).as_posix()
    except ValueError:
        return str(target.resolve())


def copy_if_missing(target: Path, source: Path, *, fallback: str) -> None:
    if target.exists():
        return
    if source.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding='utf-8'), encoding='utf-8')
        return
    write_text(target, fallback)


def copy_tree_files_if_missing(target_dir: Path, source_dir: Path) -> None:
    if not source_dir.exists():
        return
    for source_path in source_dir.rglob('*'):
        if not source_path.is_file():
            continue
        target_path = target_dir / source_path.relative_to(source_dir)
        if target_path.exists():
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target_path)


def ensure_repo_markers(repo_root: Path, base_repo_root: Path) -> None:
    copy_if_missing(repo_root / 'python' / 'openclaw' / '__init__.py', base_repo_root / 'python' / 'openclaw' / '__init__.py', fallback='')
    copy_tree_files_if_missing(
        repo_root / 'config' / 'governance' / 'support',
        base_repo_root / 'config' / 'governance' / 'support',
    )
    copy_if_missing(
        repo_root / 'config' / 'runtime' / 'paths.json',
        base_repo_root / 'config' / 'runtime' / 'paths.json',
        fallback=json.dumps({'roots': {}, 'entries': {}}, ensure_ascii=False, indent=2) + '\n',
    )
    copy_tree_files_if_missing(
        repo_root / 'config' / 'runtime',
        base_repo_root / 'config' / 'runtime',
    )
    copy_if_missing(
        repo_root / 'config' / 'control_plane' / 'service.json',
        base_repo_root / 'config' / 'control_plane' / 'service.json',
        fallback=json.dumps(
            {
                'schemaVersion': 1,
                'service': {
                    'name': 'openclaw-control-plane',
                    'schedulerServiceName': 'openclaw-control-plane-scheduler',
                    'stateDirName': 'control_plane_scheduler',
                    'heartbeatFile': 'control_plane_scheduler_heartbeat.json',
                    'statusFile': 'control_plane_scheduler_status.json',
                    'historyFile': 'control_plane_scheduler_history.jsonl',
                    'gatewayServiceName': 'openclaw-official-gateway',
                    'internalApiServiceName': 'openclaw-internal-api',
                    'agentAccessLogFile': 'control_plane_agent_access_log.jsonl',
                    'autoExportAgentGroupEvidence': True,
                },
                'defaults': {
                    'timezone': 'Asia/Shanghai',
                    'tickSeconds': 15,
                    'recentHistoryLimit': 20,
                },
                'registry': {
                    'jobsDir': 'jobs',
                    'modelsDir': 'models',
                    'targetsDir': 'targets',
                },
                'extensions': {
                    'manifestsDirs': ['extensions.d'],
                    'enabledExtensionIds': [],
                },
                'schemas': {
                    'jobsSchema': 'schemas/job.schema.json',
                    'modelsSchema': 'schemas/model.schema.json',
                    'targetsSchema': 'schemas/target.schema.json',
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + '\n',
    )
    profile_registry_path = repo_root / 'config' / 'control_plane' / 'profile_registry.tsv'
    if not profile_registry_path.exists():
        write_text(
            profile_registry_path,
            '# profile_id\tconfig_path\n'
            'base\tconfig/control_plane/service.json\n'
            'agent_platform\tconfig/control_plane/profiles/agent_platform.service.json\n',
        )
    repo_combination_profiles_path = repo_root / 'config' / 'control_plane' / 'repo_combination_profiles.json'
    if not repo_combination_profiles_path.exists():
        write_json(repo_combination_profiles_path, {'profiles': []})
    copy_tree_files_if_missing(
        repo_root / 'config' / 'control_plane' / 'profiles',
        base_repo_root / 'config' / 'control_plane' / 'profiles',
    )
    copy_tree_files_if_missing(
        repo_root / 'config' / 'control_plane' / 'schemas',
        base_repo_root / 'config' / 'control_plane' / 'schemas',
    )
    copy_tree_files_if_missing(
        repo_root / 'config' / 'control_plane' / 'extensions.d',
        base_repo_root / 'config' / 'control_plane' / 'extensions.d',
    )
    copy_tree_files_if_missing(
        repo_root / 'agent' / 'control_plane',
        base_repo_root / 'agent' / 'control_plane',
    )
    for rel_path in (
        'docs/architecture/agent-governance.md',
        'docs/architecture/agent-module-governance.md',
        'docs/architecture/control-plane-baseline.md',
    ):
        copy_if_missing(
            repo_root / rel_path,
            base_repo_root / rel_path,
            fallback=f'# {Path(rel_path).stem}\n',
        )
