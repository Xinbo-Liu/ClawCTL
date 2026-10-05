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

from openclaw.control_plane.registry_loader.config import load_registry_service_context
from openclaw.control_plane.surfaces import load_deploy_env_schema, load_runtime_service_registry, load_testing_manifest
from openclaw.lib.repo.contracts import repo_contract_path, repo_contract_relpath
from openclaw.lib.repo.layout import resolve_repo_root, resolve_selected_control_plane_config_path
from openclaw.lib.repo.managed_extensions import managed_explicit_extensions
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


def _enabled_extension_ids(config_path: Path) -> set[str]:
    """返回控制面配置启用的扩展 id 集合。

    参数：
        config_path（Path）：控制面 service config 路径。

    返回：
        返回 set[str]，用于判断 live acceptance 请求是否落在当前 active profile 内。
    """
    context = load_registry_service_context(config_path)
    return {str(item or '').strip() for item in context.get('enabledExtensionIds') or [] if str(item or '').strip()}


def _extension_python_package_prefixes(repo_root: Path, extension_id: str) -> tuple[str, ...]:
    """返回扩展 python root 下可作为模块入口的包名前缀。

    参数：
        repo_root（Path）：仓库根目录，用于读取受管扩展索引。
        extension_id（str）：受管扩展 id。

    返回：
        返回 tuple[str, ...]；每一项是该扩展自身 python root 下的顶层包名。
    """
    for extension in managed_explicit_extensions(repo_root):
        if extension.id != extension_id:
            continue
        package_names: list[str] = []
        for python_root in extension.python_roots:
            if not python_root.is_dir():
                continue
            for child in sorted(python_root.iterdir()):
                if child.is_dir() and (child / '__init__.py').is_file():
                    package_names.append(child.name)
        return tuple(dict.fromkeys(package_names))
    return ()


def _validate_live_acceptance_check(check: dict[str, Any], *, repo_root: Path, extension_id: str) -> list[dict[str, str]]:
    """校验单条 live acceptance 声明的通用结构与归属。

    参数：
        check（dict[str, Any]）：testing manifest 中的一条 live acceptance check。
        repo_root（Path）：仓库根目录，用于校验 module 是否属于扩展 python 包。
        extension_id（str）：请求验收的扩展 id。

    返回：
        返回 list[dict[str, str]]；为空表示声明可由升级主链执行。

    副作用：
        只检查传入的 manifest 条目结构，不读取仓库文件、不执行 live 检查。
    """
    issues: list[dict[str, str]] = []
    check_id = str(check.get('id') or '').strip()
    command = check.get('command') if isinstance(check.get('command'), dict) else {}
    script = str(command.get('script') or '').strip()
    module = str(command.get('module') or '').strip()
    if not check_id:
        issues.append({'code': 'live_acceptance_check_id_missing', 'message': 'live_acceptance_checks 条目缺少 id'})
    if str(check.get('extensionId') or '').strip() != extension_id:
        issues.append({'code': 'live_acceptance_extension_mismatch', 'check': check_id, 'message': f'live acceptance check 不属于扩展 {extension_id}'})
    if check.get('requiresExplicitLive') is not True:
        issues.append({'code': 'live_acceptance_explicit_flag_missing', 'check': check_id, 'message': 'live acceptance check 必须声明 requiresExplicitLive=true'})
    if bool(script) == bool(module):
        issues.append({'code': 'live_acceptance_command_invalid', 'check': check_id, 'message': 'live acceptance command 必须且只能声明 script 或 module'})
    if script:
        normalized_script = script.replace('\\', '/')
        expected_prefix = f'agent/extensions/{extension_id}/'
        if not normalized_script.startswith(expected_prefix):
            issues.append({
                'code': 'live_acceptance_script_owner_mismatch',
                'check': check_id,
                'message': f'live acceptance script 必须位于 {expected_prefix}',
            })
    if module:
        package_prefixes = _extension_python_package_prefixes(repo_root, extension_id)
        if not package_prefixes or not any(module == prefix or module.startswith(prefix + '.') for prefix in package_prefixes):
            expected = ', '.join(package_prefixes) or f'{extension_id} 的 python root 包'
            issues.append({
                'code': 'live_acceptance_module_owner_mismatch',
                'check': check_id,
                'message': f'live acceptance module 必须属于扩展 {extension_id} 的 python 包：{expected}',
            })
    return issues


def build_live_acceptance_plan_payload(repo_root: Path, extension_id: str, *, config_path: Path | None = None) -> dict[str, Any]:
    """按 active profile 和 testing manifest 生成 live acceptance 执行计划。

    参数：
        repo_root（Path）：仓库根目录。
        extension_id（str）：要求执行 live acceptance 的扩展 id。
        config_path（Path | None）：控制面 service config；为空时使用当前部署选择。

    返回：
        返回 dict[str, Any]；仅包含脱敏的检查声明、阻断项和下一步动作。
    """
    normalized_extension_id = str(extension_id or '').strip()
    selected_config = resolve_selected_control_plane_config_path(
        config_path,
        start_path=repo_root,
        default_to_base=True,
    ).resolve()
    blocking: list[dict[str, str]] = []
    if not normalized_extension_id:
        blocking.append({'code': 'extension_id_missing', 'message': '--extension 不能为空'})
    enabled_ids = _enabled_extension_ids(selected_config)
    if normalized_extension_id and normalized_extension_id not in enabled_ids:
        blocking.append({
            'code': 'extension_not_enabled',
            'extensionId': normalized_extension_id,
            'message': f'{normalized_extension_id} 未在当前 active profile 启用',
        })
    manifest = load_testing_manifest(config_path=selected_config)
    checks = [
        dict(row)
        for row in manifest.get('live_acceptance_checks') or []
        if isinstance(row, dict) and str(row.get('extensionId') or '').strip() == normalized_extension_id
    ]
    if normalized_extension_id and normalized_extension_id in enabled_ids and not checks:
        blocking.append({
            'code': 'live_acceptance_not_declared',
            'extensionId': normalized_extension_id,
            'message': f'{normalized_extension_id} 未在 testing manifest 声明 live_acceptance_checks',
        })
    for check in checks:
        blocking.extend(_validate_live_acceptance_check(check, repo_root=repo_root, extension_id=normalized_extension_id))
        command = check.get('command') if isinstance(check.get('command'), dict) else {}
        script = str(command.get('script') or '').strip()
        if script and not (repo_root / script).is_file():
            blocking.append({
                'code': 'live_acceptance_script_missing',
                'check': str(check.get('id') or '').strip(),
                'message': f'live acceptance script 不存在：{script}',
            })
    return {
        'schemaVersion': SCHEMA_VERSION,
        'status': 'ok' if not blocking else 'blocked',
        'repoRoot': str(repo_root),
        'configPath': str(selected_config),
        'extensionId': normalized_extension_id,
        'enabledExtensionIds': sorted(enabled_ids),
        'checks': checks,
        'blockingIssues': blocking,
        'nextActions': [] if not blocking else ['确认 active profile、testing manifest live_acceptance_checks 与扩展脚本归属后重新执行 one_click_upgrade.sh'],
    }


def build_live_acceptance_env_keys_payload(repo_root: Path, extension_id: str, *, config_path: Path | None = None) -> dict[str, Any]:
    """返回扩展 live acceptance 可注入的 deploy env key。

    参数：
        repo_root（Path）：仓库根目录。
        extension_id（str）：要求执行 live acceptance 的扩展 id。
        config_path（Path | None）：控制面 service config；为空时使用当前部署选择。

    返回：
        返回 dict[str, Any]；`envKeys` 只包含当前扩展 schema 中显式声明 `live_acceptance_env=true` 的字段。
    """
    normalized_extension_id = str(extension_id or '').strip()
    selected_config = resolve_selected_control_plane_config_path(
        config_path,
        start_path=repo_root,
        default_to_base=True,
    ).resolve()
    blocking: list[dict[str, str]] = []
    if not normalized_extension_id:
        blocking.append({'code': 'extension_id_missing', 'message': '--extension 不能为空'})
    enabled_ids = _enabled_extension_ids(selected_config)
    if normalized_extension_id and normalized_extension_id not in enabled_ids:
        blocking.append({
            'code': 'extension_not_enabled',
            'extensionId': normalized_extension_id,
            'message': f'{normalized_extension_id} 未在当前 active profile 启用',
        })
    schema = load_deploy_env_schema(config_path=selected_config)
    env_keys: list[str] = []
    for field in schema.get('fields') if isinstance(schema.get('fields'), list) else []:
        if not isinstance(field, dict):
            continue
        if str(field.get('extensionId') or '').strip() != normalized_extension_id:
            continue
        if field.get('live_acceptance_env') is not True:
            continue
        key = str(field.get('key') or '').strip()
        if not key:
            blocking.append({
                'code': 'live_acceptance_env_key_missing',
                'extensionId': normalized_extension_id,
                'message': 'deploy env schema 中 live_acceptance_env 字段缺少 key',
            })
            continue
        env_keys.append(key)
    return {
        'schemaVersion': SCHEMA_VERSION,
        'status': 'ok' if not blocking else 'blocked',
        'repoRoot': str(repo_root),
        'configPath': str(selected_config),
        'extensionId': normalized_extension_id,
        'envKeys': list(dict.fromkeys(env_keys)),
        'blockingIssues': blocking,
        'nextActions': [] if not blocking else ['确认 active profile 与扩展 deploy env schema 后重新执行 one_click_upgrade.sh'],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='python -m openclaw.cli setup upgrade')
    subparsers = parser.add_subparsers(dest='command', required=True)
    readiness = subparsers.add_parser('readiness')
    readiness.add_argument('--json', action='store_true')
    service_plan = subparsers.add_parser('service-plan')
    service_plan.add_argument('--config-path', default='')
    service_plan.add_argument('--compose-file', default='')
    service_plan.add_argument('--json', action='store_true')
    live_plan = subparsers.add_parser('live-acceptance-plan')
    live_plan.add_argument('--extension', required=True)
    live_plan.add_argument('--config-path', default='')
    live_plan.add_argument('--json', action='store_true')
    live_env_keys = subparsers.add_parser('live-acceptance-env-keys')
    live_env_keys.add_argument('--extension', required=True)
    live_env_keys.add_argument('--config-path', default='')
    live_env_keys.add_argument('--json', action='store_true')
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
    elif args.command == 'live-acceptance-plan':
        payload = build_live_acceptance_plan_payload(
            repo_root,
            str(args.extension or ''),
            config_path=Path(args.config_path).resolve() if str(args.config_path or '').strip() else None,
        )
    elif args.command == 'live-acceptance-env-keys':
        payload = build_live_acceptance_env_keys_payload(
            repo_root,
            str(args.extension or ''),
            config_path=Path(args.config_path).resolve() if str(args.config_path or '').strip() else None,
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
