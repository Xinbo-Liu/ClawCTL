#!/usr/bin/env python3
"""部署升级主链的结构化检查入口。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

from openclaw.control_plane.surfaces import load_runtime_service_registry
from openclaw.lib.repo.contracts import repo_contract_path, repo_contract_relpath
from openclaw.lib.repo.layout import resolve_repo_root, resolve_selected_control_plane_config_path
from openclaw.setup.deploy_env.query import parse_env_file

SCHEMA_VERSION = 1
DEFAULT_HOST_STATE_ROOT = 'state/openclaw'
KEY_EXEC_FILES = (
    'scripts/setup/fix_permissions.sh',
    'scripts/runtime/run_openclaw_python_tool.sh',
    'scripts/runtime/run_runtime_service_action.sh',
    'scripts/runtime/show_runtime_service_status.sh',
    'scripts/setup/one_click_deploy.sh',
    'scripts/setup/one_click_upgrade.sh',
)
KEY_HASH_FILES = (
    'agent/extensions/lock.json',
    repo_contract_relpath('runtime.service_registry'),
    'config/services/runtime_mounts.json',
    'deploy/docker-compose.yml',
    'scripts/setup/one_click_deploy.sh',
    'scripts/setup/one_click_upgrade.sh',
)


def _print_json(payload: dict[str, Any]) -> int:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    return 0


def _file_sha256(path: Path) -> str:
    if not path.is_file():
        return ''
    hasher = hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def _deploy_env(repo_root: Path) -> dict[str, str]:
    env_path = repo_root / 'deploy' / '.env'
    if not env_path.is_file():
        return {}
    try:
        return {str(key): str(value) for key, value in parse_env_file(env_path).items()}
    except Exception:
        return {}


def _host_state_root(repo_root: Path, env_map: dict[str, str] | None = None) -> Path:
    env = env_map if env_map is not None else _deploy_env(repo_root)
    raw = str(os.environ.get('HOST_STATE_DIR') or env.get('HOST_STATE_ROOT') or DEFAULT_HOST_STATE_ROOT).strip()
    if not raw:
        raw = DEFAULT_HOST_STATE_ROOT
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (repo_root / path).resolve()


def _effective_compose_path(repo_root: Path, env_map: dict[str, str] | None = None) -> Path:
    return _host_state_root(repo_root, env_map) / 'control_plane' / 'setup' / 'docker-compose.effective.yml'


def _git_rev(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ['git', '-C', str(repo_root), 'rev-parse', 'HEAD'],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
        return result.stdout.strip()
    except Exception:
        return ''


def _exec_file_payload(repo_root: Path, rel_path: str) -> dict[str, Any]:
    path = repo_root / rel_path
    mode = path.stat().st_mode if path.exists() else 0
    return {
        'path': rel_path,
        'exists': path.is_file(),
        'mode': stat.filemode(mode) if mode else '',
        'executable': path.is_file() and os.access(path, os.X_OK),
    }


def _hash_payload(repo_root: Path, rel_path: str) -> dict[str, str]:
    path = repo_root / rel_path
    return {
        'path': rel_path,
        'sha256': _file_sha256(path),
    }


def build_readiness_payload(repo_root: Path) -> dict[str, Any]:
    """构建升级前置状态报告，不读取或输出 secret 值。"""
    env_map = _deploy_env(repo_root)
    exec_files = [_exec_file_payload(repo_root, rel_path) for rel_path in KEY_EXEC_FILES]
    blocking = [
        {
            'code': 'exec_bit_missing',
            'path': item['path'],
            'message': f"{item['path']} 缺少执行位；先执行 bash ./scripts/setup/fix_permissions.sh",
        }
        for item in exec_files
        if not item['executable']
    ]
    source_kind = 'git_worktree' if (repo_root / '.git').exists() or _git_rev(repo_root) else 'materialized_directory'
    return {
        'schemaVersion': SCHEMA_VERSION,
        'status': 'ok' if not blocking else 'blocked',
        'repoRoot': str(repo_root),
        'sourceKind': source_kind,
        'currentCommit': _git_rev(repo_root),
        'hostStateRoot': str(_host_state_root(repo_root, env_map)),
        'effectiveComposePath': str(_effective_compose_path(repo_root, env_map)),
        'execFiles': exec_files,
        'keyFileHashes': [_hash_payload(repo_root, rel_path) for rel_path in KEY_HASH_FILES],
        'blockingIssues': blocking,
        'nextActions': [] if not blocking else ['bash ./scripts/setup/fix_permissions.sh'],
    }


def _compose_declares_service(compose_text: str, service_name: str) -> bool:
    pattern = re.compile(rf'(?m)^  {re.escape(service_name)}:\s*(?:#.*)?$')
    return bool(pattern.search(compose_text))


def _service_rows(config_path: Path) -> list[dict[str, Any]]:
    payload = load_runtime_service_registry(repo_contract_path('runtime.service_registry'), config_path=config_path)
    rows = payload.get('targets') if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError('runtime service registry targets 必须为数组')
    return [row for row in rows if isinstance(row, dict)]


def build_service_plan_payload(repo_root: Path, *, config_path: Path | None = None, compose_file: Path | None = None) -> dict[str, Any]:
    """校验 active profile 的 service registry 与 effective compose 是否一致。"""
    selected_config = resolve_selected_control_plane_config_path(
        config_path,
        start_path=repo_root,
        default_to_base=True,
    ).resolve()
    env_map = _deploy_env(repo_root)
    effective_compose = compose_file.resolve() if compose_file else _effective_compose_path(repo_root, env_map)
    if not effective_compose.is_file():
        effective_compose = repo_root / 'deploy' / 'docker-compose.yml'
    compose_text = effective_compose.read_text(encoding='utf-8') if effective_compose.is_file() else ''
    rows = _service_rows(selected_config)
    seen_services: dict[str, str] = {}
    seen_targets: dict[str, str] = {}
    seen_containers: dict[str, str] = {}
    blocking: list[dict[str, str]] = []
    targets: list[dict[str, Any]] = []
    for row in rows:
        target = str(row.get('target') or '').strip()
        service = str(row.get('service') or '').strip()
        container = str(row.get('container') or '').strip()
        owner = str(row.get('extensionId') or row.get('owner') or 'base')
        if not target or not service or not container:
            blocking.append({'code': 'service_registry_incomplete', 'target': target, 'message': 'runtime service registry 记录缺少 target/service/container'})
            continue
        for label, value, seen in (
            ('target', target, seen_targets),
            ('service', service, seen_services),
            ('container', container, seen_containers),
        ):
            previous = seen.get(value)
            if previous is not None:
                blocking.append({'code': f'duplicate_{label}', 'target': target, 'message': f'{label} 重复：{value}（已有 {previous}）'})
            seen[value] = target
        declared = _compose_declares_service(compose_text, service)
        if not declared:
            blocking.append({'code': 'compose_service_missing', 'target': target, 'message': f'effective compose 缺少 service：{service}'})
        targets.append({
            'target': target,
            'service': service,
            'container': container,
            'owner': owner,
            'declaredInCompose': declared,
        })
    return {
        'schemaVersion': SCHEMA_VERSION,
        'status': 'ok' if not blocking else 'blocked',
        'repoRoot': str(repo_root),
        'configPath': str(selected_config),
        'composeFile': str(effective_compose),
        'targets': targets,
        'blockingIssues': blocking,
        'nextActions': [] if not blocking else ['bash ./scripts/runtime/run_openclaw_python_tool.sh runtime mounts sync-compose --output <effective-compose>'],
    }


def build_parser() -> argparse.ArgumentParser:
    """构建 setup upgrade 控制面 CLI。"""
    parser = argparse.ArgumentParser(prog='python -m openclaw.cli setup upgrade')
    subparsers = parser.add_subparsers(dest='command', required=True)
    readiness = subparsers.add_parser('readiness')
    readiness.add_argument('--json', action='store_true')
    service_plan = subparsers.add_parser('service-plan')
    service_plan.add_argument('--config-path', default='')
    service_plan.add_argument('--compose-file', default='')
    service_plan.add_argument('--json', action='store_true')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    repo_root = resolve_repo_root(Path(__file__)).resolve()
    if args.command == 'readiness':
        payload = build_readiness_payload(repo_root)
    elif args.command == 'service-plan':
        payload = build_service_plan_payload(
            repo_root,
            config_path=Path(args.config_path).resolve() if str(args.config_path or '').strip() else None,
            compose_file=Path(args.compose_file).resolve() if str(args.compose_file or '').strip() else None,
        )
    else:
        raise SystemExit(f'未知命令：{args.command}')
    if bool(getattr(args, 'json', False)):
        _print_json(payload)
    else:
        sys.stdout.write(f"{args.command}: {payload.get('status')}\n")
    return 0 if payload.get('status') == 'ok' else 2


if __name__ == '__main__':
    raise SystemExit(main())
