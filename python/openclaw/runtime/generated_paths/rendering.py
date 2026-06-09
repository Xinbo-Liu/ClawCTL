"""运行态派生产物统一渲染与漂移检查。"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
from typing import Any, Dict

from openclaw.lib.runtime.path_resolver import PathResolver

from .constants import RENDER_GENERATED_RUNTIME_PATHS_CMD
from .env import build_env_outputs, env_targets, render_envs
from .gateway.config import build_public_openclaw_config_output, public_openclaw_state_path, render_public_openclaw_config
from .gateway.cron import build_gateway_cron_jobs_output, gateway_cron_jobs_state_path, render_gateway_cron_jobs
from .gateway.workspace import (
    gateway_agent_core_file_targets,
    gateway_healthcheck_script_targets,
    render_gateway_agent_state_dirs,
    render_gateway_exec_approvals,
)
from .path_index import build_path_index_outputs, render_path_index
from .registry import _load_registry
from .shared import _json_object_rows

def render_generated_outputs(repo_root: Path, resolver: PathResolver, config_path: Path | None = None) -> None:
    resolver.absolute_host_path('gateway_host_state_dir').mkdir(parents=True, exist_ok=True)
    resolver.absolute_host_path('control_plane_host_state_dir').mkdir(parents=True, exist_ok=True)
    render_public_openclaw_config(repo_root, resolver, config_path)
    render_gateway_cron_jobs(resolver, config_path)
    render_gateway_agent_state_dirs(repo_root, resolver, config_path)
    render_path_index(resolver)
    render_envs(repo_root, resolver)
    render_gateway_exec_approvals(resolver)


def _normalized_public_openclaw_for_check(content: str) -> Dict[str, Any] | None:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    payload = deepcopy(payload)
    meta = payload.get('meta')
    if isinstance(meta, dict):
        meta['lastTouchedAt'] = '<gateway-managed>'
        meta['lastTouchedVersion'] = '<gateway-managed>'
        payload['meta'] = meta
    return payload


def _normalized_gateway_cron_jobs_for_check(content: str) -> Dict[str, Any] | None:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    payload = deepcopy(payload)
    for job in _json_object_rows(payload.get('jobs')):
        state = job.get('state')
        if not isinstance(state, dict):
            continue
        if 'lastStatus' in state:
            state['lastStatus'] = '<scheduler-derived>'
        if 'lastSummary' in state:
            state['lastSummary'] = '<scheduler-derived>'
        state.pop('lastRunAtMs', None)
        if 'nextRunAtMs' in state:
            state['nextRunAtMs'] = '<scheduler-derived>'
    return payload


def _generated_content_matches(
    target: Path,
    expected_content: str,
    actual_content: str,
    *,
    openclaw_state_path: Path,
    cron_jobs_state_path: Path,
) -> bool:
    if target == cron_jobs_state_path:
        expected_payload = _normalized_gateway_cron_jobs_for_check(expected_content)
        actual_payload = _normalized_gateway_cron_jobs_for_check(actual_content)
        if expected_payload is None or actual_payload is None:
            return actual_content == expected_content
        return actual_payload == expected_payload
    if target != openclaw_state_path:
        return actual_content == expected_content
    expected_payload = _normalized_public_openclaw_for_check(expected_content)
    actual_payload = _normalized_public_openclaw_for_check(actual_content)
    if expected_payload is None or actual_payload is None:
        return actual_content == expected_content
    return actual_payload == expected_payload


def check_generated_outputs(repo_root: Path, resolver: PathResolver, config_path: Path | None = None) -> int:
    expected: Dict[Path, str] = {}
    openclaw_state_path = public_openclaw_state_path(resolver)
    cron_jobs_path = gateway_cron_jobs_state_path(resolver)
    expected[openclaw_state_path] = build_public_openclaw_config_output(repo_root, resolver, config_path)
    expected[cron_jobs_path] = build_gateway_cron_jobs_output(config_path or resolver.config_path)
    expected.update(gateway_healthcheck_script_targets(repo_root, resolver))
    expected.update(gateway_agent_core_file_targets(_load_registry(config_path or resolver.config_path), resolver))
    path_targets = {
        'path-index.json': resolver.absolute_host_path('path_index_json'),
        'path-index.md': resolver.absolute_host_path('path_index_markdown'),
    }
    for name, content in build_path_index_outputs(resolver).items():
        expected[path_targets[name]] = content
    current_env_targets = env_targets(resolver)
    for name, content in build_env_outputs(repo_root, resolver).items():
        expected[current_env_targets[name]] = content
    expected[resolver.gateway_exec_approvals_paths()['host_output']] = resolver.read_gateway_exec_approvals_source()

    missing: list[str] = []
    mismatches: list[str] = []
    for target, content in expected.items():
        if not target.exists():
            missing.append(str(target))
            continue
        actual = target.read_text(encoding='utf-8')
        if not _generated_content_matches(
            target,
            content,
            actual,
            openclaw_state_path=openclaw_state_path,
            cron_jobs_state_path=cron_jobs_path,
        ):
            mismatches.append(str(target))
    if missing or mismatches:
        if missing:
            print('[render_paths][MISSING] 下列产物不存在：', file=sys.stderr)
            for item in missing:
                print(f'- {item}', file=sys.stderr)
        if mismatches:
            print('[render_paths][DRIFT] 下列产物与仓库真源或 manifest 结果不一致：', file=sys.stderr)
            for item in mismatches:
                print(f'- {item}', file=sys.stderr)
        print(f'[render_paths] 请先执行 {RENDER_GENERATED_RUNTIME_PATHS_CMD} 或 bootstrap.sh 同步路径派生产物，再重新校验。', file=sys.stderr)
        return 2
    print(f'[render_paths] check passed for repo {repo_root}')
    return 0
