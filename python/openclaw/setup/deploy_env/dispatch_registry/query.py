#!/usr/bin/env python3
"""Query-side helpers for deploy-env dispatch registry operations."""
from __future__ import annotations

import json
from pathlib import Path

from openclaw.lib.cli import CliError, FlagSpec, parse_typed_flag_args
from openclaw.lib.repo.config_selection import (
    CONTROL_PLANE_CONFIG_ENV,
    CONTROL_PLANE_PROFILE_ENV,
    resolve_selected_control_plane_config_path,
)
from openclaw.lib.repo.profiles import (
    control_plane_profile_id_for_config_path,
    resolve_control_plane_profile_service_config_path,
)
from openclaw.setup.deploy_env.dispatch_registry.common import (
    DISPATCH_TARGET_REGISTRY_SCHEMA_PATH,
    ROOT_DIR,
    DispatchRegistryValidationError,
    _fail,
    _note,
    _write_text,
    build_dispatch_default_exports,
    build_dispatch_registry_summary,
    load_dispatch_registry,
    require_runtime_dependencies,
)
from openclaw.setup.deploy_env.dispatch_registry.load import (
    dispatch_registry_disabled_summary,
    resolve_dispatch_registry_paths,
    resolve_dispatch_provider_paths,
)
from openclaw.setup.deploy_env.query import parse_env_file


def _resolve_registry_config_path(
    *,
    config_path: Path | None,
    control_plane_profile: str | None,
    env_file: Path | None,
) -> Path | None:
    if config_path and str(control_plane_profile or '').strip():
        _fail('--config-path 与 --control-plane-profile 不能同时使用', 2)
    if config_path:
        try:
            return resolve_selected_control_plane_config_path(config_path, start_path=ROOT_DIR)
        except ValueError as exc:
            _fail(str(exc), 2)

    profile_id = str(control_plane_profile or '').strip()
    if profile_id:
        try:
            return resolve_control_plane_profile_service_config_path(profile_id, start_path=ROOT_DIR)
        except ValueError as exc:
            _fail(str(exc), 2)

    env_values = parse_env_file(env_file)
    env_config_path = str(env_values.get(CONTROL_PLANE_CONFIG_ENV) or '').strip()
    env_profile = str(env_values.get(CONTROL_PLANE_PROFILE_ENV) or '').strip()
    if env_config_path:
        try:
            resolved_path = resolve_selected_control_plane_config_path(env_config_path, start_path=ROOT_DIR)
        except ValueError as exc:
            _fail(str(exc), 2)
        if env_profile:
            resolved_profile = control_plane_profile_id_for_config_path(resolved_path, start_path=ROOT_DIR)
            if resolved_profile != env_profile:
                _fail(
                    f'{env_file} 中的 {CONTROL_PLANE_CONFIG_ENV} 与 {CONTROL_PLANE_PROFILE_ENV} 不一致：'
                    f'{CONTROL_PLANE_CONFIG_ENV} -> {resolved_profile}, {CONTROL_PLANE_PROFILE_ENV}={env_profile}',
                    2,
                )
        return resolved_path
    if env_profile:
        try:
            return resolve_control_plane_profile_service_config_path(env_profile, start_path=ROOT_DIR)
        except ValueError as exc:
            _fail(str(exc), 2)
    return None
def validate_dispatch_registry(argv: list[str]) -> int:
    require_runtime_dependencies()
    try:
        values, positionals = parse_typed_flag_args(
            argv,
            specs={
                'registry': FlagSpec(kind='path', dest='registry_path', default=None),
                'schema': FlagSpec(kind='path', dest='schema_path', default=DISPATCH_TARGET_REGISTRY_SCHEMA_PATH),
                'config-path': FlagSpec(kind='path', dest='config_path', default=None),
                'control-plane-profile': FlagSpec(kind='str', dest='control_plane_profile', default=None),
                'env-file': FlagSpec(kind='path', dest='env_file', default=None),
                'json': FlagSpec(kind='bool', dest='json_output', default=False),
                'summary-json': FlagSpec(kind='path', dest='summary_json', default=None),
            },
        )
    except CliError as exc:
        _fail(str(exc), 2)
    if positionals:
        _fail(f'未知参数：{positionals[0]}', 2)
    config_path = _resolve_registry_config_path(
        config_path=values['config_path'],
        control_plane_profile=values['control_plane_profile'],
        env_file=values['env_file'],
    )
    registry_paths = [values['registry_path']] if values['registry_path'] else None
    schema_path = values['schema_path']
    json_output = values['json_output']
    summary_json = values['summary_json']
    provider_registry_paths: list[Path] = []
    if registry_paths is None:
        registry_paths, provider_registry_paths = resolve_dispatch_registry_paths(config_path, target_required=False, provider_required=False)
    if not registry_paths:
        summary = dispatch_registry_disabled_summary()
        if summary_json:
            _write_text(summary_json, json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
        if json_output:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            _note('当前 active profile 未启用 dispatch registry；按 kernel-only 基座视为合法空集合')
        return 0
    if not provider_registry_paths:
        provider_registry_paths = resolve_dispatch_provider_paths(config_path, required=False)
    try:
        payload = load_dispatch_registry(registry_paths, schema_path, provider_registry_paths or None)
    except DispatchRegistryValidationError as registry_error:
        _fail(str(registry_error), 2)
    summary = build_dispatch_registry_summary(payload)
    summary['registry_enabled'] = True
    if summary_json:
        _write_text(summary_json, json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    if json_output:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _note(f"dispatch target 注册表校验通过：targets={summary['target_count']}")
    return 0


def query_dispatch_registry(argv: list[str]) -> int:
    require_runtime_dependencies()
    if not argv:
        _fail('query-dispatch-registry 缺少 mode', 2)
    mode = argv.pop(0)
    try:
        values, positionals = parse_typed_flag_args(
            argv,
            specs={
                'registry': FlagSpec(kind='path', dest='registry_path', default=None),
                'schema': FlagSpec(kind='path', dest='schema_path', default=DISPATCH_TARGET_REGISTRY_SCHEMA_PATH),
                'config-path': FlagSpec(kind='path', dest='config_path', default=None),
                'control-plane-profile': FlagSpec(kind='str', dest='control_plane_profile', default=None),
                'env-file': FlagSpec(kind='path', dest='env_file', default=None),
            },
        )
    except CliError as exc:
        _fail(str(exc), 2)
    if positionals:
        _fail(f'未知参数：{positionals[0]}', 2)
    config_path = _resolve_registry_config_path(
        config_path=values['config_path'],
        control_plane_profile=values['control_plane_profile'],
        env_file=values['env_file'],
    )
    registry_paths = [values['registry_path']] if values['registry_path'] else None
    schema_path = values['schema_path']
    provider_registry_paths: list[Path] = []
    if registry_paths is None:
        registry_paths, provider_registry_paths = resolve_dispatch_registry_paths(config_path, target_required=False, provider_required=False)
    if not registry_paths:
        summary = dispatch_registry_disabled_summary()
        if mode == 'emit-default-exports':
            return 0
        if mode == 'summary':
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        _fail(f'未知 dispatch registry 查询模式：{mode}', 2)
    if not provider_registry_paths:
        provider_registry_paths = resolve_dispatch_provider_paths(config_path, required=False)
    payload = load_dispatch_registry(registry_paths, schema_path, provider_registry_paths or None)
    if mode == 'emit-default-exports':
        print('\n'.join(build_dispatch_default_exports(payload)))
        return 0
    if mode == 'summary':
        summary = build_dispatch_registry_summary(payload)
        summary['registry_enabled'] = True
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    _fail(f'未知 dispatch registry 查询模式：{mode}', 2)
