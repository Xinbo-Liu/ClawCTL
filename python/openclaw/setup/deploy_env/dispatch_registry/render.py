#!/usr/bin/env python3
"""Render-side helpers for deploy-env dispatch registry operations."""
from __future__ import annotations

import json
import re
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openclaw.lib.cli import CliError, FlagSpec, parse_typed_flag_args
from openclaw.lib.models.env import model_env_specs_from_registry
from openclaw.lib.runtime.resolver_loader import require_path_resolver
from openclaw.setup.deploy_env.dispatch_registry.common import (
    DISPATCH_TARGET_REGISTRY_SCHEMA_PATH,
    ROOT_DIR,
    _fail,
    _note,
    _read_text,
    _write_text,
    build_dispatch_compose_env_block,
    dispatch_runtime_env_names,
    load_dispatch_registry,
    load_registry,
    require_runtime_dependencies,
)
from openclaw.setup.deploy_env.dispatch_registry.load import (
    load_dispatch_targets,
    resolve_dispatch_provider_paths,
    resolve_dispatch_targets_paths,
)
from openclaw.setup.deploy_env.query import parse_env_file
from openclaw.setup.deploy_env.support import (
    deploy_env_required_map,
    deploy_env_truthy_required_map,
    is_truthy_flag_value,
    load_schema,
)


def _parse_runtime_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {'1', 'true', 'yes', 'on'}:
        return True
    if normalized in {'0', 'false', 'no', 'off'}:
        return False
    return default


def _target_enabled_from_env(row: dict[str, Any], env_map: OrderedDict[str, str]) -> bool:
    enabled_default = _parse_runtime_bool(row.get('enabledDefault'), False)
    enabled_env = str(row.get('enabledEnv') or '').strip()
    return _parse_runtime_bool(env_map.get(enabled_env), enabled_default) if enabled_env else enabled_default


def collect_model_env_names(registry: dict[str, Any], *, include_api_key: bool, include_base_url: bool) -> list[str]:
    specs = model_env_specs_from_registry(registry, scheduler_scope=include_api_key)
    result: list[str] = []
    for name, spec in specs.items():
        if spec.purpose == 'model_base_url' and not include_base_url:
            continue
        if spec.purpose == 'model_api_key' and not include_api_key:
            continue
        result.append(name)
    return result


def collect_model_env_requirements(registry: dict[str, Any], *, include_api_key: bool, include_base_url: bool) -> dict[str, bool]:
    specs = model_env_specs_from_registry(registry, scheduler_scope=include_api_key)
    result: dict[str, bool] = {}
    for name, spec in specs.items():
        if spec.purpose == 'model_base_url' and not include_base_url:
            continue
        if spec.purpose == 'model_api_key' and not include_api_key:
            continue
        result[name] = bool(spec.required)
    return result


def collect_extension_runtime_env_names(config_path: Path, *, schema: dict[str, Any] | None = None) -> list[str]:
    """把 active profile 的扩展部署变量带入 scheduler 类运行态 env。

    长连接 listener 和正式 agent bin 都在 scheduler 运行态内执行；扩展专属
    provider 凭据、通知目标、机器人 mention 身份和角色清单不能只停留在
    deploy/.env 派生产物之外。
    """
    schema = schema if schema is not None else load_schema(config_path=config_path)
    result: list[str] = []
    for field in schema.get('fields', []):
        if not isinstance(field, dict):
            continue
        if not str(field.get('extensionId') or '').strip():
            continue
        key = str(field.get('key') or '').strip()
        if not key or str(field.get('runtime_env') or '').strip() == 'none':
            continue
        result.append(key)
    return result


def write_runtime_service_env(
    path: Path,
    keys: list[str],
    env_map: OrderedDict[str, str],
    *,
    fixed_values: dict[str, str] | None = None,
    required_overrides: dict[str, bool] | None = None,
    schema: dict[str, Any] | None = None,
) -> None:
    required_map = deploy_env_required_map(env_map, schema=schema) if schema is not None else deploy_env_required_map()
    truthy_required_map = deploy_env_truthy_required_map(env_map, schema=schema) if schema is not None else {}
    required_map.update({key: value for key, value in (required_overrides or {}).items() if key})
    resolved_fixed_values: dict[str, str] = dict(fixed_values or {})
    lines = [
        '# 由 deploy_env_control_plane.py 生成；运行态应用 env 只允许作为 deploy/.env 的派生产物存在。',
    ]
    seen: set[str] = set()
    for key in keys:
        name = str(key or '').strip()
        if not name or name in seen:
            continue
        seen.add(name)
        if name in resolved_fixed_values:
            lines.append(f'{name}={resolved_fixed_values[name]}')
            continue
        value = env_map.get(name)
        if (value is None or value == '') and required_map.get(name, False):
            _fail(f'渲染运行态应用 env 时缺少必填项：{name}', 2)
        if truthy_required_map.get(name, False) and not is_truthy_flag_value(value):
            _fail(f'渲染运行态应用 env 时配置必须启用（1/true/yes/on）：{name}', 2)
        lines.append(f'{name}={value or ""}')
    for name in sorted(resolved_fixed_values):
        if name in seen:
            continue
        lines.append(f'{name}={resolved_fixed_values[name]}')
    _write_text(path, '\n'.join(lines) + '\n')


def render_dispatch_runtime(
    argv: list[str],
    *,
    default_env_file: Path,
    default_output: Path,
    default_summary_json: Path,
) -> int:
    require_runtime_dependencies()
    try:
        values, positionals = parse_typed_flag_args(
            argv,
            specs={
                'env-file': FlagSpec(kind='path', dest='env_file', default=default_env_file),
                'output': FlagSpec(kind='path', dest='output', default=default_output),
                'summary-json': FlagSpec(kind='path', dest='summary_json', default=default_summary_json),
                'config-path': FlagSpec(kind='path', dest='config_path', default=None),
            },
        )
    except CliError as exc:
        _fail(str(exc), 2)
    if positionals:
        _fail(f'未知参数：{positionals[0]}', 2)
    env_file = values['env_file']
    output = values['output']
    summary_json = values['summary_json']
    config_path = values['config_path']
    env_map = parse_env_file(env_file)
    registry = load_dispatch_targets(config_path=config_path, required=False)
    targets = [
        dict(row)
        for row in sorted(list((registry or {}).get('targets') or []), key=lambda item: int(item.get('verificationOrderDefault') or 0))
        if isinstance(row, dict)
    ]
    defaults = dict((registry or {}).get('defaults') or {})
    payload: dict[str, Any] = {
        'schema_version': 1,
        'generated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'source_env_file': str(env_file),
        'registry_enabled': bool(registry),
        'registry_version': int((registry or {}).get('version') or 0),
        'defaults': defaults,
        'targets': targets,
    }
    for key in ('releasePolicies', 'verificationBatches', 'lifecycleStates'):
        if key in (registry or {}):
            payload[key] = (registry or {}).get(key)
    _write_text(output, json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    summary = {
        'schema_version': 1,
        'generated_at': payload['generated_at'],
        'output': str(output),
        'registry_enabled': bool(registry),
        'target_count': len(targets),
        'enabled_target_ids': [str(item.get('id') or '') for item in targets if _target_enabled_from_env(item, env_map)],
    }
    _write_text(summary_json, json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    _note(f'已生成 dispatch runtime：{output}')
    return 0


def render_runtime_service_envs(
    argv: list[str],
    *,
    default_env_file: Path,
    default_scheduler_output: Path,
    default_internal_api_output: Path,
    default_config_path: Path,
    default_internal_api_bind: str,
) -> int:
    require_runtime_dependencies()
    try:
        values, positionals = parse_typed_flag_args(
            argv,
            specs={
                'env-file': FlagSpec(kind='path', dest='env_file', default=default_env_file),
                'scheduler-output': FlagSpec(kind='path', dest='scheduler_output', default=default_scheduler_output),
                'internal-api-output': FlagSpec(kind='path', dest='internal_api_output', default=default_internal_api_output),
                'config-path': FlagSpec(kind='path', dest='config_path', default=default_config_path),
                'internal-api-bind': FlagSpec(kind='str', dest='internal_api_bind', default=default_internal_api_bind),
                'registry': FlagSpec(kind='path', dest='registry_path', default=None),
                'schema': FlagSpec(kind='path', dest='schema_path', default=DISPATCH_TARGET_REGISTRY_SCHEMA_PATH),
            },
        )
    except CliError as exc:
        _fail(str(exc), 2)
    if positionals:
        _fail(f'未知参数：{positionals[0]}', 2)
    env_file = values['env_file']
    scheduler_output = values['scheduler_output']
    internal_api_output = values['internal_api_output']
    config_path = values['config_path']
    internal_api_bind = values['internal_api_bind'] or default_internal_api_bind
    registry_paths = [values['registry_path']] if values['registry_path'] else []
    schema_path = values['schema_path']
    registry_paths = registry_paths or resolve_dispatch_targets_paths(config_path, required=False)
    provider_registry_paths = resolve_dispatch_provider_paths(config_path, required=False)
    env_map = parse_env_file(env_file)
    registry = load_registry(config_path)
    deploy_env_schema = load_schema(config_path=Path(config_path))
    dispatch_payload = load_dispatch_registry(registry_paths, schema_path, provider_registry_paths or None) if registry_paths else {}
    path_resolver = require_path_resolver(repo_root=ROOT_DIR)
    scheduler_keys = ['OPENCLAW_INTERNAL_API_TOKEN']
    scheduler_keys.extend(collect_model_env_names(registry, include_api_key=True, include_base_url=True))
    scheduler_keys.extend(collect_extension_runtime_env_names(Path(config_path), schema=deploy_env_schema))
    scheduler_required = collect_model_env_requirements(registry, include_api_key=True, include_base_url=True)
    if dispatch_payload:
        scheduler_keys.extend(dispatch_runtime_env_names(dispatch_payload))
    write_runtime_service_env(scheduler_output, scheduler_keys, env_map, required_overrides=scheduler_required, schema=deploy_env_schema)
    internal_api_keys = ['OPENCLAW_INTERNAL_API_TOKEN']
    internal_api_keys.extend(collect_model_env_names(registry, include_api_key=False, include_base_url=True))
    internal_api_required = collect_model_env_requirements(registry, include_api_key=False, include_base_url=True)
    write_runtime_service_env(
        internal_api_output,
        internal_api_keys,
        env_map,
        required_overrides=internal_api_required,
        schema=deploy_env_schema,
        fixed_values={
            'OPENCLAW_STATE_DIR': path_resolver.resolve_path('state_root', 'scheduler'),
            'OPENCLAW_INTERNAL_API_BIND': internal_api_bind,
        },
    )
    _note(f'已生成 runtime service env：{scheduler_output} / {internal_api_output}')
    return 0


def sync_dispatch_compose_env(argv: list[str], *, default_compose_file: Path) -> int:
    require_runtime_dependencies()
    try:
        values, positionals = parse_typed_flag_args(
            argv,
            specs={
                'compose-file': FlagSpec(kind='path', dest='compose_file', default=default_compose_file),
                'registry': FlagSpec(kind='path', dest='registry_path', default=None),
                'schema': FlagSpec(kind='path', dest='schema_path', default=DISPATCH_TARGET_REGISTRY_SCHEMA_PATH),
                'config-path': FlagSpec(kind='path', dest='config_path', default=None),
                'check': FlagSpec(kind='bool', dest='check_only', default=False),
            },
        )
    except CliError as exc:
        _fail(str(exc), 2)
    if positionals:
        _fail(f'未知参数：{positionals[0]}', 2)
    compose_file = values['compose_file']
    config_path = values['config_path']
    registry_paths = [values['registry_path']] if values['registry_path'] else None
    schema_path = values['schema_path']
    check_only = values['check_only']
    registry_paths = registry_paths if registry_paths is not None else resolve_dispatch_targets_paths(config_path, required=True)
    provider_registry_paths = resolve_dispatch_provider_paths(config_path, required=False)
    payload = load_dispatch_registry(registry_paths, schema_path, provider_registry_paths or None)
    content = _read_text(compose_file)
    begin = '# DISPATCH_RUNTIME_ENV_BLOCK_BEGIN'
    end = '# DISPATCH_RUNTIME_ENV_BLOCK_END'
    pattern = re.compile(rf'(?P<indent>\s*){re.escape(begin)}\n.*?\n(?P=indent){re.escape(end)}', re.S)
    match = pattern.search(content)
    if match is None:
        _fail(f'compose 中缺少 dispatch 环境块标记：{compose_file}', 2)
    indent = match.group('indent')
    block_lines = [f'{indent}{begin}'] + build_dispatch_compose_env_block(payload, indent=indent) + [f'{indent}{end}']
    new_block = '\n'.join(block_lines)
    new_content = content[: match.start()] + new_block + content[match.end() :]
    if check_only:
        if new_content != content:
            import sys
            sys.stderr.write('[deploy_env_control_plane][DRIFT] docker-compose dispatch env block 已漂移\n')
            return 2
        _note('docker-compose dispatch env block 校验通过')
        return 0
    _write_text(compose_file, new_content)
    _note(f'已同步 compose dispatch env block：{compose_file}')
    return 0
