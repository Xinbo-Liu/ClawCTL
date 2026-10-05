#!/usr/bin/env python3
"""部署输入真源路由与批量写入入口。"""
from __future__ import annotations

import json
import os
import sys
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openclaw.control_plane.registry_loader.config import load_registry_service_context
from openclaw.lib.cli import FlagSpec, parse_typed_flag_args
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.managed_extensions import load_managed_extensions_index
from openclaw.lib.repo.profiles import (
    control_plane_repo_combination_profile,
    resolve_control_plane_profile_service_config_path,
)
from openclaw.setup.deploy_env import dispatch_registry as deploy_env_dispatch_registry_lib
from openclaw.setup.deploy_env.query import parse_env_file, parse_env_value, render_env_lines
from openclaw.setup.deploy_env.render_validate import (
    CONTROL_PLANE_PROFILE_KEY,
    DEFAULT_SITE_ENV_PATH,
    DEFAULT_TARGETS_ENV_DIR,
    EXTENSION_ENV_FILENAME,
    active_model_env_specs,
    dispatch_target_env_key_map,
    extension_env_rel_path,
    field_extension_id,
    host_config_path_for_profile,
    model_spec_extension_id,
    target_env_example_lines,
)
from openclaw.setup.deploy_env.support import load_schema

ROOT_DIR = resolve_repo_root(Path(__file__))
DEFAULT_EXTENSION_ENV_ROOT = ROOT_DIR / 'agent' / 'extensions'
KEY_PATTERN = set('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_')


@dataclass(frozen=True)
class DeployInputRoute:
    """单个部署输入键的写入归属。"""

    key: str
    scope: str
    owner: str
    rel_path: str
    secret: bool = False
    required: bool = False
    manual_required: bool = False
    group: str = 'default'
    group_title: str = 'default'


def _fail(message: str, code: int = 2) -> None:
    print(f'[deploy_input_values][FAIL] {message}', file=sys.stderr)
    raise SystemExit(code)


def _validate_key(key: str) -> str:
    normalized = str(key or '').strip()
    if not normalized or any(ch not in KEY_PATTERN for ch in normalized):
        _fail(f'非法键名：{key}；仅允许大写字母、数字与下划线')
    return normalized


def _validate_env_value(key: str, value: str) -> None:
    if '\n' in value or '\r' in value or '\t' in value:
        _fail(f'{key} 的值包含换行、回车或制表符，无法安全写入部署输入真源')


def _assert_owner_only_input_file(path: Path) -> None:
    if os.environ.get('OPENCLAW_DEPLOY_INPUT_OWNER_ONLY_CHECKED') == '1':
        return
    if os.name == 'nt':
        return
    try:
        mode = path.stat().st_mode & 0o777
    except OSError as exc:
        _fail(f'无法读取输入 env 文件权限：{path} ({exc})')
    if mode & 0o077:
        _fail(f'输入 env 文件必须是 owner-only 权限，请执行 chmod 600 {path}')


def _parse_deploy_input_file(path: Path) -> OrderedDict[str, str]:
    values: OrderedDict[str, str] = OrderedDict()
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        normalized_key = key.strip()
        if normalized_key in values:
            _fail(f'输入 env 文件重复声明部署输入键：{normalized_key}')
        values[normalized_key] = parse_env_value(value)
    return values


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT_DIR).as_posix()
    except ValueError:
        return str(path)


def _managed_extension_ids() -> set[str]:
    return {row.id for row in load_managed_extensions_index(ROOT_DIR)}


def _managed_extension_root_by_id() -> dict[str, Path]:
    return {row.id: row.root_dir for row in load_managed_extensions_index(ROOT_DIR)}


def _extension_env_path(extension_id: str) -> Path:
    roots = _managed_extension_root_by_id()
    root = roots.get(extension_id)
    if root is None:
        _fail(f'未知 managed extension：{extension_id}')
    return root / 'deploy' / EXTENSION_ENV_FILENAME


def _extension_schema_path(extension_id: str) -> Path:
    roots = _managed_extension_root_by_id()
    root = roots.get(extension_id)
    if root is None:
        _fail(f'未知 managed extension：{extension_id}')
    return root / 'config' / 'control_plane' / 'extensions.d' / f'{extension_id}.deploy_env_schema.json'


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        _fail(f'无法读取 JSON：{_display_path(path)} ({exc})')
    if not isinstance(payload, dict):
        _fail(f'JSON 根节点必须是对象：{_display_path(path)}')
    return payload


def _enabled_managed_extension_ids(profile_id: str) -> list[str]:
    config_path = resolve_control_plane_profile_service_config_path(profile_id, start_path=ROOT_DIR)
    context = load_registry_service_context(config_path)
    managed_ids = _managed_extension_ids()
    return [item for item in context.get('enabledExtensionIds') or [] if item in managed_ids]


def _shared_keys_for_profile(profile_id: str) -> set[str]:
    row = control_plane_repo_combination_profile(profile_id, start_path=ROOT_DIR) or {}
    keys: set[str] = set()
    for raw in row.get('sharedDeployEnvFields') or []:
        if not isinstance(raw, dict):
            continue
        for key in raw.get('keys') or []:
            normalized = str(key or '').strip()
            if normalized:
                keys.add(normalized)
    return keys


def _groups_by_id(schema: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in schema.get('groups') or []:
        if not isinstance(row, dict):
            continue
        group_id = str(row.get('id') or '').strip()
        if group_id:
            result[group_id] = str(row.get('title') or group_id).strip() or group_id
    return result


def _route_from_schema_field(field: dict[str, Any], *, shared_keys: set[str]) -> DeployInputRoute | None:
    key = str(field.get('key') or '').strip()
    if not key:
        return None
    group = str(field.get('group') or 'default').strip() or 'default'
    owner = field_extension_id(field)
    doc_location = str(field.get('doc_location') or '').strip()
    base = {
        'key': key,
        'secret': bool(field.get('secret')),
        'required': bool(field.get('required')),
        'manual_required': bool(field.get('manual_required')),
        'group': group,
    }
    if key in shared_keys or doc_location == 'deploy/site.env' or not owner:
        return DeployInputRoute(scope='site', owner='', rel_path='deploy/site.env', **base)
    return DeployInputRoute(scope='extension', owner=owner, rel_path=extension_env_rel_path(owner), **base)


def _merge_or_add_schema_route(
    routes: dict[str, DeployInputRoute],
    route: DeployInputRoute,
    *,
    group_titles: dict[str, str],
    shared_keys: set[str],
) -> None:
    normalized = DeployInputRoute(
        key=route.key,
        scope=route.scope,
        owner=route.owner,
        rel_path=route.rel_path,
        secret=route.secret,
        required=route.required,
        manual_required=route.manual_required,
        group=route.group,
        group_title=group_titles.get(route.group, route.group),
    )
    existing = routes.get(route.key)
    if existing is None:
        routes[route.key] = normalized
        return
    if route.key in shared_keys and existing.scope == 'site' and route.scope == 'site':
        routes[route.key] = DeployInputRoute(
            key=existing.key,
            scope='site',
            owner='',
            rel_path='deploy/site.env',
            secret=existing.secret or normalized.secret,
            required=existing.required or normalized.required,
            manual_required=existing.manual_required or normalized.manual_required,
            group=existing.group,
            group_title=existing.group_title,
        )
        return
    if existing.scope == route.scope and existing.owner == route.owner and existing.rel_path == route.rel_path:
        routes[route.key] = DeployInputRoute(
            key=existing.key,
            scope=existing.scope,
            owner=existing.owner,
            rel_path=existing.rel_path,
            secret=existing.secret or normalized.secret,
            required=existing.required or normalized.required,
            manual_required=existing.manual_required or normalized.manual_required,
            group=existing.group,
            group_title=existing.group_title,
        )
        return
    _fail(
        f'部署输入键归属冲突：{route.key} 同时指向 '
        f'{existing.scope}/{existing.owner or "site"} 与 {route.scope}/{route.owner or "site"}'
    )


def _dispatch_target_routes_by_key(profile_id: str) -> dict[str, list[DeployInputRoute]]:
    """按 profile 的 dispatch registry 返回 target env 键到写入路由的映射。

    参数：
        profile_id（str）：active control-plane profile 标识，用于定位 dispatch target registry。

    返回：
        返回 dict[str, list[DeployInputRoute]]，键为 env 变量名，值为该变量需要写入的 target env 路由列表。
    """

    config_path = host_config_path_for_profile(profile_id)
    dispatch_registry = deploy_env_dispatch_registry_lib.load_dispatch_targets(config_path=config_path, required=False)
    result: dict[str, list[DeployInputRoute]] = {}
    for target_id, keys in dispatch_target_env_key_map(dispatch_registry).items():
        for key in keys:
            target_routes = result.setdefault(key, [])
            if any(route.owner != target_id for route in target_routes):
                _fail(f'部署输入键同时出现在多个 dispatch target registry：{key}')
            if any(route.owner == target_id for route in target_routes):
                _fail(f'部署输入键在同一 dispatch target registry 重复：{target_id}/{key}')
            secret = key.endswith('_SECRET') or key.endswith('_TOKEN') or key.endswith('_WEBHOOK_URL')
            target_routes.append(
                DeployInputRoute(
                    key=key,
                    scope='target',
                    owner=target_id,
                    rel_path=f'deploy/targets.d/{target_id}.env',
                    secret=secret,
                    group='dispatch_targets',
                    group_title='dispatch target',
                )
            )
    return result


def _same_destination(left: DeployInputRoute, right: DeployInputRoute) -> bool:
    """判断两个部署输入路由是否指向同一个 env 真源文件。

    参数：
        left（DeployInputRoute）：左侧部署输入路由。
        right（DeployInputRoute）：右侧部署输入路由。

    返回：
        返回 bool，表示 scope、owner 与 rel_path 是否完全一致。
    """

    return left.scope == right.scope and left.owner == right.owner and left.rel_path == right.rel_path


def _route_destinations(
    route: DeployInputRoute,
    *,
    target_routes_by_key: dict[str, list[DeployInputRoute]],
) -> list[DeployInputRoute]:
    """返回部署输入键需要写入的主路由与 dispatch target 镜像路由。

    参数：
        route（DeployInputRoute）：schema、model 或 target registry 解析出的主路由。
        target_routes_by_key（dict[str, list[DeployInputRoute]]）：dispatch target env 键到 target 路由列表的映射。

    返回：
        返回 list[DeployInputRoute]，包含去重后的主路由和同键 target 镜像路由。
    """

    destinations = [route]
    for target_route in target_routes_by_key.get(route.key, []):
        if any(_same_destination(existing, target_route) for existing in destinations):
            continue
        destinations.append(target_route)
    return destinations


def _route_destinations_for_allowed(
    route: DeployInputRoute,
    *,
    target_routes_by_key: dict[str, list[DeployInputRoute]],
    allowed_scopes: set[str],
) -> list[DeployInputRoute]:
    """返回当前入口权限允许写入的部署输入目的地列表。

    参数：
        route（DeployInputRoute）：当前部署输入键的主路由。
        target_routes_by_key（dict[str, list[DeployInputRoute]]）：dispatch target env 键到 target 路由列表的映射。
        allowed_scopes（set[str]）：当前 CLI 入口允许写入的 scope 集合。

    返回：
        返回 list[DeployInputRoute]，只包含 scope 在 allowed_scopes 中的写入目的地。
    """

    return [
        destination
        for destination in _route_destinations(route, target_routes_by_key=target_routes_by_key)
        if destination.scope in allowed_scopes
    ]


def _destination_for_group(
    key: str,
    *,
    scope: str,
    owner: str,
    route: DeployInputRoute,
    target_routes_by_key: dict[str, list[DeployInputRoute]],
) -> DeployInputRoute:
    """按 validate-only 输出分组解析对应的真实写入目的地。

    参数：
        key（str）：部署输入 env 键名。
        scope（str）：validate-only 分组中的目标 scope。
        owner（str）：validate-only 分组中的目标 owner。
        route（DeployInputRoute）：当前部署输入键的主路由。
        target_routes_by_key（dict[str, list[DeployInputRoute]]）：dispatch target env 键到 target 路由列表的映射。

    返回：
        返回 DeployInputRoute，表示与分组 scope/owner 对应的真实写入目的地。
    """

    for destination in _route_destinations(route, target_routes_by_key=target_routes_by_key):
        if destination.scope == scope and destination.owner == owner:
            return destination
    return route


def build_input_routes(profile_id: str) -> dict[str, DeployInputRoute]:
    """按 active profile 返回部署输入键到写入真源的映射。"""

    normalized_profile = str(profile_id or '').strip() or 'agent_platform'
    config_path = host_config_path_for_profile(normalized_profile)
    schema = load_schema(config_path=config_path)
    shared_keys = _shared_keys_for_profile(normalized_profile)
    routes: dict[str, DeployInputRoute] = {}
    group_titles = _groups_by_id(schema)

    for field in schema.get('fields') or []:
        if not isinstance(field, dict):
            continue
        route = _route_from_schema_field(field, shared_keys=shared_keys)
        if route is None:
            continue
        _merge_or_add_schema_route(routes, route, group_titles=group_titles, shared_keys=shared_keys)

    model_specs = active_model_env_specs({CONTROL_PLANE_PROFILE_KEY: normalized_profile})
    for spec in model_specs.values():
        if not spec.name or spec.name in routes:
            continue
        owner = model_spec_extension_id(spec)
        if spec.name in shared_keys or not owner:
            routes[spec.name] = DeployInputRoute(
                key=spec.name,
                scope='site',
                owner='',
                rel_path='deploy/site.env',
                secret=spec.secret,
                required=spec.required,
                manual_required=spec.required,
                group='model_providers',
                group_title='模型 provider 与共享模型通道',
            )
        else:
            routes[spec.name] = DeployInputRoute(
                key=spec.name,
                scope='extension',
                owner=owner,
                rel_path=extension_env_rel_path(owner),
                secret=spec.secret,
                required=spec.required,
                manual_required=spec.required,
                group='model_providers',
                group_title='模型 provider 与本地推理通道',
            )

    for key, target_routes in _dispatch_target_routes_by_key(normalized_profile).items():
        if key in routes:
            continue
        routes[key] = target_routes[0]
    return routes


def _write_env_file(path: Path, updates: dict[str, str], *, init_lines: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        if init_lines is None:
            path.write_text('', encoding='utf-8', newline='\n')
        else:
            path.write_text('\n'.join(init_lines).rstrip() + '\n', encoding='utf-8', newline='\n')
    existing_lines = path.read_text(encoding='utf-8').splitlines()
    rendered = render_env_lines({key: value for key, value in updates.items()}).splitlines()
    assignment_by_key = {line.split('=', 1)[0]: line for line in rendered if '=' in line}
    seen: set[str] = set()
    output: list[str] = []
    for raw in existing_lines:
        line = raw.rstrip('\r')
        stripped = line.strip()
        if stripped and not stripped.startswith('#') and '=' in line:
            key = line.split('=', 1)[0].strip()
            if key in assignment_by_key:
                output.append(assignment_by_key[key])
                seen.add(key)
                continue
        output.append(line)
    for key in updates:
        if key not in seen:
            output.append(assignment_by_key[key])
    path.write_text('\n'.join(output).rstrip() + '\n', encoding='utf-8', newline='\n')
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _extension_init_lines_for_profile(extension_id: str, init_lines: list[str], routes: dict[str, DeployInputRoute]) -> list[str]:
    """保留属于当前扩展的 example 行，避免共享 site 键写入扩展 env。

    参数：
        extension_id（str）：当前需要初始化 extension.env 的受管扩展标识。
        init_lines（list[str]）：extension.env.example 读取出的原始行列表。
        routes（dict[str, DeployInputRoute]）：active profile 下部署输入键到主路由的映射。

    返回：
        返回 list[str]，只包含注释、空行以及归属当前扩展的 KEY=VALUE 行。
    """

    allowed_keys = {route.key for route in routes.values() if route.scope == 'extension' and route.owner == extension_id}
    filtered: list[str] = []
    for raw in init_lines:
        line = raw.rstrip('\r')
        stripped = line.strip()
        if stripped and not stripped.startswith('#') and '=' in line:
            key = line.split('=', 1)[0].strip()
            if key and key not in allowed_keys:
                continue
        filtered.append(raw)
    return filtered


def _target_example_lines(profile_id: str, target_id: str) -> list[str]:
    config_path = host_config_path_for_profile(profile_id)
    registry = deploy_env_dispatch_registry_lib.load_dispatch_targets(config_path=config_path, required=False)
    for target in registry.get('targets') or []:
        if isinstance(target, dict) and str(target.get('id') or '').strip() == target_id:
            return target_env_example_lines(target)
    _fail(f'当前 profile 未声明 dispatch target：{target_id}')


def _is_missing_value(value: str) -> bool:
    return not str(value or '').strip() or str(value).strip() == '__REQUIRED__'


def _fix_command(route: DeployInputRoute) -> str:
    if route.scope == 'site':
        if route.secret:
            return f'export {route.key}=<secret>; bash ./scripts/setup/apply_site_env_values.sh --init-from-example --from-env {route.key}'
        return f'bash ./scripts/setup/apply_site_env_values.sh --init-from-example --set {route.key}=<value>'
    if route.scope == 'extension':
        if route.secret:
            return f'export {route.key}=<secret>; bash ./scripts/setup/apply_extension_env_values.sh --extension {route.owner} --init-from-example --set-secret-from-env {route.key}'
        return f'bash ./scripts/setup/apply_extension_env_values.sh --extension {route.owner} --init-from-example --set {route.key}=<value>'
    if route.secret:
        return f'export {route.key}=<secret>; bash ./scripts/setup/apply_target_env_values.sh --target {route.owner} --init-from-example --from-env {route.key}'
    return f'bash ./scripts/setup/apply_target_env_values.sh --target {route.owner} --init-from-example --set {route.key}=<value>'


def _append_extension_check_rows(
    rows: list[dict[str, Any]],
    *,
    profile_id: str,
    extension_id: str,
    routes: dict[str, DeployInputRoute],
    site_values: dict[str, str],
) -> int:
    env_path = _extension_env_path(extension_id)
    env_values = parse_env_file(env_path)
    schema_path = _extension_schema_path(extension_id)
    schema = _load_json(schema_path)
    group_titles = _groups_by_id(schema)
    missing_count = 0
    for field in schema.get('fields') or []:
        if not isinstance(field, dict):
            continue
        key = str(field.get('key') or '').strip()
        if not key or not (field.get('required') is True or field.get('manual_required') is True or field.get('secret') is True):
            continue
        route = routes.get(key)
        if route is None:
            continue
        values = site_values if route.scope == 'site' else env_values
        value = values.get(key, '')
        required = bool(field.get('required')) or route.required
        manual_required = bool(field.get('manual_required')) or route.manual_required
        missing = _is_missing_value(value) and (required or manual_required)
        if missing:
            missing_count += 1
        group = str(field.get('group') or route.group or 'default').strip() or 'default'
        rows.append({
            'profile': profile_id,
            'extension': extension_id,
            'scope': route.scope,
            'owner': route.owner,
            'envPath': route.rel_path,
            'key': key,
            'group': group,
            'groupTitle': group_titles.get(group, route.group_title or group),
            'required': required,
            'manualRequired': manual_required,
            'secret': bool(field.get('secret')) or route.secret,
            'present': not _is_missing_value(value),
            'missing': missing,
            'fixCommand': _fix_command(route),
        })
    return missing_count


def _emit_extension_check_result(profile_id: str, rows: list[dict[str, Any]], missing_count: int, *, format_name: str) -> int:
    """输出扩展 env 检查结果，并把缺项纳入退出码。

    参数：
        profile_id（str）：active profile 标识；单扩展检查时为空字符串。
        rows（list[dict[str, Any]]）：必填字段检查行。
        missing_count（int）：缺失必填字段数量。
        format_name（str）：输出格式，支持 text 或 json。

    返回：
        返回 int；0 表示 ready，2 表示存在缺项。
    """
    if format_name == 'json':
        payload = {
            'profile': profile_id,
            'status': 'ready' if missing_count == 0 else 'missing',
            'fields': rows,
            'missing': [row for row in rows if row['missing']],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if missing_count == 0:
            print('[check_extension_env_values] 扩展 env 必填项已闭合。')
        else:
            print(f'[check_extension_env_values][FAIL] 扩展 env 未通过：missing={missing_count}')
            printed: set[tuple[str, str, str, str]] = set()
            for row in rows:
                if not row['missing']:
                    continue
                group_key = (row['extension'], row['scope'], row['group'], row['groupTitle'])
                if group_key not in printed:
                    printed.add(group_key)
                    print()
                    print(f"## {row['extension']} / {row['groupTitle']} ({row['group']} -> {row['envPath']})")
                labels = []
                if row['required']:
                    labels.append('required')
                if row['manualRequired']:
                    labels.append('manual_required')
                if row['secret']:
                    labels.append('secret')
                suffix = f" {' '.join(labels)}" if labels else ''
                print(f"- {row['key']}{suffix}")
                print(f"  修复：{row['fixCommand']}")
    return 0 if missing_count == 0 else 2


def check_extension_env_values(profile_id: str, *, format_name: str = 'text') -> int:
    """检查 active profile 的扩展必填输入，组合共享字段从 site.env 判定。"""

    routes = build_input_routes(profile_id)
    site_values = parse_env_file(DEFAULT_SITE_ENV_PATH)
    rows: list[dict[str, Any]] = []
    missing_count = 0
    for extension_id in _enabled_managed_extension_ids(profile_id):
        missing_count += _append_extension_check_rows(
            rows,
            profile_id=profile_id,
            extension_id=extension_id,
            routes=routes,
            site_values=site_values,
        )
    return _emit_extension_check_result(profile_id, rows, missing_count, format_name=format_name)


def check_single_extension_env_values(extension_id: str, *, format_name: str = 'text') -> int:
    """按 extension id 检查单个扩展自身 extension.env。"""

    schema = _load_json(_extension_schema_path(extension_id))
    group_titles = _groups_by_id(schema)
    routes: dict[str, DeployInputRoute] = {}
    for field in schema.get('fields') or []:
        if not isinstance(field, dict):
            continue
        key = str(field.get('key') or '').strip()
        if not key:
            continue
        group = str(field.get('group') or 'default').strip() or 'default'
        routes[key] = DeployInputRoute(
            key=key,
            scope='extension',
            owner=extension_id,
            rel_path=extension_env_rel_path(extension_id),
            secret=bool(field.get('secret')),
            required=bool(field.get('required')),
            manual_required=bool(field.get('manual_required')),
            group=group,
            group_title=group_titles.get(group, group),
        )
    rows: list[dict[str, Any]] = []
    missing_count = _append_extension_check_rows(
        rows,
        profile_id='',
        extension_id=extension_id,
        routes=routes,
        site_values={},
    )
    return _emit_extension_check_result('', rows, missing_count, format_name=format_name)


def _reject_disallowed_scope(route: DeployInputRoute, allowed_scopes: set[str]) -> None:
    if route.scope in allowed_scopes:
        return
    if allowed_scopes == {'extension'} and route.scope == 'site':
        _fail(
            f'{route.key} 属于 deploy/site.env 共享/平台输入；请使用 '
            f'bash ./scripts/setup/apply_site_env_values.sh --init-from-example --set {route.key}=<value>，'
            '或使用 apply_deploy_input_values.sh 统一导入 owner-only env 文件。'
        )
    if allowed_scopes == {'extension'} and route.scope == 'target':
        _fail(
            f'{route.key} 属于 dispatch target {route.owner}；请使用 '
            f'bash ./scripts/setup/apply_target_env_values.sh --target {route.owner} --init-from-example --set {route.key}=<value>，'
            '或使用 apply_deploy_input_values.sh 统一导入 owner-only env 文件。'
        )
    _fail(f'{route.key} 归属 {route.scope}/{route.owner}，不允许由当前入口写入。')


def apply_input_values(
    profile_id: str,
    input_env_file: Path,
    *,
    init: bool,
    allowed_scopes: set[str] | None = None,
    validate_only: bool = False,
) -> int:
    """按 active profile 路由把输入 env 文件写入 site/extension/target 真源。"""

    if not input_env_file.is_file():
        _fail(f'输入 env 文件不存在：{input_env_file}')
    _assert_owner_only_input_file(input_env_file)
    allowed = set(allowed_scopes or {'site', 'extension', 'target'})
    routes = build_input_routes(profile_id)
    target_routes_by_key = _dispatch_target_routes_by_key(profile_id)
    input_values = _parse_deploy_input_file(input_env_file)
    if not input_values:
        _fail(f'输入 env 文件没有可写入的 KEY=VALUE：{input_env_file}')
    grouped: dict[tuple[str, str], dict[str, str]] = {}
    secret_keys = {
        key
        for key, route in routes.items()
        if any(destination.secret for destination in _route_destinations(route, target_routes_by_key=target_routes_by_key))
    }
    for key, value in input_values.items():
        normalized_key = _validate_key(key)
        _validate_env_value(normalized_key, value)
        route = routes.get(normalized_key)
        if route is None:
            _fail(f'{_display_path(input_env_file)} 包含当前 profile 未声明的部署输入键：{normalized_key}')
        destinations = _route_destinations_for_allowed(
            route,
            target_routes_by_key=target_routes_by_key,
            allowed_scopes=allowed,
        )
        if not destinations:
            _reject_disallowed_scope(route, allowed)
        for destination in destinations:
            grouped.setdefault((destination.scope, destination.owner), {})[normalized_key] = value

    if validate_only:
        print('[deploy_input_values] validate_only=true')
        missing_required = [
            route
            for route in sorted(routes.values(), key=lambda item: (item.scope, item.owner, item.key))
            if route.scope in allowed and (route.required or route.manual_required) and route.key not in input_values
        ]
        for (scope, owner), values in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1])):
            key = next(iter(values))
            route = routes[key]
            destination = _destination_for_group(
                key,
                scope=scope,
                owner=owner,
                route=route,
                target_routes_by_key=target_routes_by_key,
            )
            print(f'[deploy_input_values] 将写入 {destination.rel_path}')
            for key in values:
                if key in secret_keys:
                    print(f'[deploy_input_values] 已识别 {key}=<redacted>')
                else:
                    print(f'[deploy_input_values] 已识别 {key}')
        if missing_required:
            print('[deploy_input_values][WARN] validate-only 输入文件未包含以下 required/manual_required 键：')
            for route in missing_required:
                print(f'[deploy_input_values][WARN] {route.key} -> {route.rel_path}')
        return 0

    for (scope, owner), values in grouped.items():
        if scope == 'site':
            target_path = DEFAULT_SITE_ENV_PATH
            init_lines = None
            if init and not target_path.exists():
                example = ROOT_DIR / 'deploy' / 'site.env.example'
                init_lines = example.read_text(encoding='utf-8').splitlines() if example.exists() else []
        elif scope == 'extension':
            target_path = _extension_env_path(owner)
            init_lines = None
            if init and not target_path.exists():
                example = target_path.with_name('extension.env.example')
                example_lines = example.read_text(encoding='utf-8').splitlines() if example.exists() else []
                init_lines = _extension_init_lines_for_profile(owner, example_lines, routes)
        elif scope == 'target':
            target_path = DEFAULT_TARGETS_ENV_DIR / f'{owner}.env'
            init_lines = _target_example_lines(profile_id, owner) if init and not target_path.exists() else None
        else:
            _fail(f'内部错误：未知部署输入 scope：{scope}')
        if not init and not target_path.exists():
            _fail(f'目标文件不存在：{_display_path(target_path)}；请追加 --init')
        _write_env_file(target_path, values, init_lines=init_lines)
        print(f'[deploy_input_values] 已写入 {_display_path(target_path)}')
        for key in values:
            if key in secret_keys:
                print(f'[deploy_input_values] 已更新 {key}=<redacted>')
            else:
                print(f'[deploy_input_values] 已更新 {key}')
    return 0


def _take_value(argv: list[str], index: int, flag: str) -> tuple[str, int]:
    if '=' in flag:
        return flag.split('=', 1)[1], index + 1
    if index + 1 >= len(argv):
        _fail(f'{flag} 缺少参数')
    return argv[index + 1], index + 2


def _parse_write_target_env_args(argv: list[str]) -> dict[str, Any]:
    values: dict[str, Any] = {'profile': None, 'target_id': None, 'pairs': [], 'input_env_file': None, 'init': False}
    index = 0
    while index < len(argv):
        current = argv[index]
        if (
            current in {'--profile', '--target', '--set', '--input-env-file'}
            or current.startswith('--profile=')
            or current.startswith('--target=')
            or current.startswith('--set=')
            or current.startswith('--input-env-file=')
        ):
            value, index = _take_value(argv, index, current)
            if current.startswith('--profile'):
                values['profile'] = value
            elif current.startswith('--target'):
                values['target_id'] = value
            elif current.startswith('--input-env-file'):
                values['input_env_file'] = Path(value).resolve()
            else:
                values['pairs'].append(value)
            continue
        if current == '--init':
            values['init'] = True
            index += 1
            continue
        _fail(f'未知参数：{current}')
    return values


def write_target_env(profile_id: str, target_id: str, pairs: list[str], *, init: bool, input_env_file: Path | None = None) -> int:
    """从 active profile target registry 初始化并写入单个 target env。"""

    routes = build_input_routes(profile_id)
    target_routes_by_key = _dispatch_target_routes_by_key(profile_id)
    updates: dict[str, str] = {}
    all_pairs = list(pairs)
    if input_env_file is not None:
        if not input_env_file.is_file():
            _fail(f'输入 env 文件不存在：{input_env_file}')
        _assert_owner_only_input_file(input_env_file)
        input_values = _parse_deploy_input_file(input_env_file)
        if not input_values:
            _fail(f'输入 env 文件没有可写入的 KEY=VALUE：{input_env_file}')
        all_pairs.extend(f'{key}={value}' for key, value in input_values.items())
    if not all_pairs:
        _fail('至少提供一个 --set 或输入 env 文件')
    pair_values: OrderedDict[str, str] = OrderedDict()
    for pair in all_pairs:
        if '=' not in pair:
            _fail('--set 需要 KEY=VALUE 形式')
        key, value = pair.split('=', 1)
        normalized_key = _validate_key(key)
        _validate_env_value(normalized_key, value)
        pair_values[normalized_key] = value
    for normalized_key, value in pair_values.items():
        target_route = next(
            (
                route
                for route in target_routes_by_key.get(normalized_key, [])
                if route.scope == 'target' and route.owner == target_id
            ),
            None,
        )
        if target_route is None:
            route = routes.get(normalized_key)
            if route is not None and route.scope == 'target' and route.owner == target_id:
                target_route = route
        if target_route is None:
            _fail(f'{normalized_key} 不属于当前 profile 的 target：{target_id}')
        updates[normalized_key] = value
    target_path = DEFAULT_TARGETS_ENV_DIR / f'{target_id}.env'
    init_lines = _target_example_lines(profile_id, target_id) if init and not target_path.exists() else None
    if not init and not target_path.exists():
        _fail(f'目标文件不存在：{_display_path(target_path)}；请先创建，或追加 --init-from-example')
    _write_env_file(target_path, updates, init_lines=init_lines)
    print(f'[deploy_input_values] 已写入 {_display_path(target_path)}')
    secret_keys = {
        key
        for key, target_routes in target_routes_by_key.items()
        if any(route.scope == 'target' and route.owner == target_id and route.secret for route in target_routes)
    }
    secret_keys.update({key for key, route in routes.items() if route.scope == 'target' and route.owner == target_id and route.secret})
    for key in updates:
        if key in secret_keys:
            print(f'[deploy_input_values] 已更新 {key}=<redacted>')
        else:
            print(f'[deploy_input_values] 已更新 {key}')
    return 0


def _profile_from_values(profile_arg: str | None) -> str:
    profile = str(profile_arg or '').strip()
    if profile:
        return profile
    site_values = parse_env_file(DEFAULT_SITE_ENV_PATH)
    return str(site_values.get(CONTROL_PLANE_PROFILE_KEY) or '').strip() or 'agent_platform'


def _normalize_apply_input_alias_args(argv: list[str]) -> list[str]:
    """把 apply 命令的输入文件别名统一为内部参数名。

    参数：
        argv（list[str]）：apply 子命令收到的原始参数列表。

    返回：
        返回 list[str]，其中 ``--input`` 与 ``--input=...`` 已转换为 ``--input-env-file`` 形式。
    """
    normalized: list[str] = []
    for item in argv:
        if item == '--input':
            normalized.append('--input-env-file')
            continue
        if item.startswith('--input='):
            normalized.append('--input-env-file=' + item.split('=', 1)[1])
            continue
        normalized.append(item)
    return normalized


def main(argv: list[str] | None = None) -> int:
    args = list(argv or [])
    if not args:
        _fail('缺少命令；支持 apply / check-extension-env / write-target-env / routes-json')
    command = args.pop(0)
    if command == 'routes-json':
        values, positionals = parse_typed_flag_args(
            args,
            specs={'profile': FlagSpec(kind='str', dest='profile', default=None)},
        )
        if positionals:
            _fail(f'未知参数：{positionals[0]}')
        profile_id = _profile_from_values(values['profile'])
        routes = build_input_routes(profile_id)
        target_routes_by_key = _dispatch_target_routes_by_key(profile_id)
        payload: dict[str, Any] = {}
        for key, route in sorted(routes.items()):
            destinations = _route_destinations(route, target_routes_by_key=target_routes_by_key)
            item = dict(route.__dict__)
            item['destinations'] = [dict(destination.__dict__) for destination in destinations]
            payload[key] = item
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if command == 'check-extension-env':
        values, positionals = parse_typed_flag_args(
            args,
            specs={
                'profile': FlagSpec(kind='str', dest='profile', default=None),
                'extension': FlagSpec(kind='str', dest='extension', default=None),
                'format': FlagSpec(kind='str', dest='format_name', default='text'),
            },
        )
        if positionals:
            _fail(f'未知参数：{positionals[0]}')
        if values['format_name'] not in {'text', 'json'}:
            _fail('--format 仅支持 text|json')
        if values['profile'] and values['extension']:
            _fail('--profile 与 --extension 不能同时使用')
        if values['extension']:
            return check_single_extension_env_values(values['extension'], format_name=values['format_name'])
        return check_extension_env_values(
            _profile_from_values(values['profile']),
            format_name=values['format_name'],
        )
    if command == 'apply':
        values, positionals = parse_typed_flag_args(
            _normalize_apply_input_alias_args(args),
            specs={
                'profile': FlagSpec(kind='str', dest='profile', default=None),
                'input-env-file': FlagSpec(kind='path', dest='input_env_file', default=None),
                'init': FlagSpec(kind='bool', dest='init', default=False),
                'scope': FlagSpec(kind='str', dest='scope', default='all', choices=('all', 'extension')),
                'validate-only': FlagSpec(kind='bool', dest='validate_only', default=False),
            },
        )
        if positionals:
            _fail(f'未知参数：{positionals[0]}')
        if values['input_env_file'] is None:
            _fail('--input 缺少路径参数')
        allowed_scopes = {'extension'} if values['scope'] == 'extension' else None
        return apply_input_values(
            _profile_from_values(values['profile']),
            values['input_env_file'],
            init=values['init'],
            allowed_scopes=allowed_scopes,
            validate_only=values['validate_only'],
        )
    if command == 'write-target-env':
        values = _parse_write_target_env_args(args)
        target_id = str(values['target_id'] or '').strip()
        if not target_id:
            _fail('--target 缺少参数')
        return write_target_env(
            _profile_from_values(values['profile']),
            target_id,
            list(values['pairs'] or []),
            init=values['init'],
            input_env_file=values['input_env_file'],
        )
    _fail(f'未知命令：{command}')


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
