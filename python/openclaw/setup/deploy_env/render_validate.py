#!/usr/bin/env python3
"""渲染并校验部署 env，将 site.env、extension.env 与 target env 收口到 deploy/.env。"""
from __future__ import annotations

import json
import os
import sys
import ipaddress
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openclaw.lib.cli import CliError, FlagSpec, parse_typed_flag_args
from openclaw.lib.repo.layout import (
    DEFAULT_RUNTIME_CONTROL_PLANE_PROFILE_ID,
    control_plane_profile_config_rel_path,
    control_plane_profile_id_for_config_path,
    resolve_control_plane_profile_service_config_path,
    resolve_repo_root,
)
from openclaw.lib.repo.static_truth import (
    host_control_plane_file as truth_host_control_plane_file,
    host_state_root_default,
    repo_contract_relpath,
)
from openclaw.control_plane.registry import load_registry
from openclaw.lib.models.env import ModelEnvSpec, model_env_specs_from_registry
from openclaw.setup.deploy_env import dispatch_registry as deploy_env_dispatch_registry_lib
from openclaw.setup.deploy_env.query import parse_env_file, render_env_lines
from openclaw.setup.deploy_env.support import (
    build_site_env_template_lines,
    detect_first_private_ipv4_from_hostname_i,
    field_is_required,
    field_requirement_reason,
    is_truthy_flag_value,
    is_placeholder_value,
    load_schema,
    random_token,
    validate_value,
)

ROOT_DIR = resolve_repo_root(Path(__file__))
DEFAULT_SITE_ENV_PATH = ROOT_DIR / 'deploy' / 'site.env'
DEFAULT_SITE_ENV_EXAMPLE_PATH = ROOT_DIR / 'deploy' / 'site.env.example'
DEFAULT_OUTPUT_PATH = ROOT_DIR / 'deploy' / '.env'
DEFAULT_SUMMARY_JSON_PATH = ROOT_DIR / truth_host_control_plane_file('setup/config_summary.json')
DEFAULT_TARGETS_ENV_DIR = ROOT_DIR / 'deploy' / 'targets.d'
DEFAULT_EXTENSION_ENV_ROOT = ROOT_DIR / 'agent' / 'extensions'
EXTENSION_ENV_FILENAME = 'extension.env'
CONTROL_PLANE_CONTAINER_ROOT = '/opt/openclaw-tools'
CONTROL_PLANE_PROFILE_KEY = 'OPENCLAW_CONTROL_PLANE_PROFILE'
CONTROL_PLANE_CONFIG_PATH_KEY = 'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH'
DISPATCH_TARGET_ENV_FIELD_NAMES = (
    'enabledEnv',
    'endpointEnv',
    'secretEnv',
    'titleEnv',
    'atAllEnv',
    'formatEnv',
    'silenceEnabledEnv',
    'silenceMinDeltaEnv',
    'allowedReleaseLevelsEnv',
)


def current_uid() -> int:
    """返回当前执行用户 UID；Windows 上无 getuid 时回退为 0。"""
    getter = getattr(os, 'getuid', None)
    return int(getter()) if callable(getter) else 0


def current_gid() -> int:
    """返回当前执行用户 GID；Windows 上无 getgid 时回退为 0。"""
    getter = getattr(os, 'getgid', None)
    return int(getter()) if callable(getter) else 0


def display_path(path: Path) -> str:
    """把路径渲染为仓库相对路径；仓库外路径保留绝对路径。"""
    try:
        return str(path.relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


def read_text(path: Path) -> str:
    """以 UTF-8 读取文本文件。"""
    return path.read_text(encoding='utf-8')


def write_text(path: Path, content: str) -> None:
    """以 UTF-8/LF 写出文本，并先创建父目录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8', newline='\n')


def chmod_owner_only(path: Path) -> None:
    """将已存在文件权限收紧为 owner-only。"""
    if path.exists():
        path.chmod(0o600)


def harden_deploy_input_files(site_env_path: Path, targets_env_dir: Path, output_path: Path, extension_env_root: Path) -> None:
    """收紧 site/env、targets.d 和扩展 extension.env 的本地权限。"""
    chmod_owner_only(site_env_path)
    chmod_owner_only(output_path)
    if targets_env_dir.exists():
        for env_path in sorted(targets_env_dir.glob('*.env')):
            chmod_owner_only(env_path)
    if extension_env_root.exists():
        for env_path in sorted(extension_env_root.glob('*/deploy/extension.env')):
            chmod_owner_only(env_path)


def pin_value(rel_path: str, key: str) -> str:
    """从镜像 pin 真源读取指定 key 的固定值。"""
    return parse_env_file(ROOT_DIR / rel_path).get(key, '')


def pin_managed_keys(schema: dict[str, Any]) -> set[str]:
    """返回由 pin 真源托管、禁止 site.env 或 targets.d 覆盖的 env key。"""
    return {str(field.get('key') or '') for field in schema.get('fields', []) if field.get('pin_key')}


def runtime_config_path_for_profile(profile_id: str) -> str:
    """把 profile id 转换为容器内控制面 service config 路径。"""
    relative_path = control_plane_profile_config_rel_path(profile_id, ROOT_DIR)
    return f'{CONTROL_PLANE_CONTAINER_ROOT}/{relative_path}'


def host_config_path_for_profile(profile_id: str) -> Path:
    """把 profile id 转换为宿主机仓库内 service config 路径。"""
    return resolve_control_plane_profile_service_config_path(profile_id, start_path=ROOT_DIR)


def host_config_path_from_runtime_value(value: str) -> Path | None:
    """把容器内或宿主机文本路径转换为宿主机 Path；空值返回 None。"""
    text = str(value or '').strip().replace('\\', '/')
    if not text:
        return None
    prefix = f'{CONTROL_PLANE_CONTAINER_ROOT}/'
    if text == CONTROL_PLANE_CONTAINER_ROOT:
        return ROOT_DIR
    if text.startswith(prefix):
        return (ROOT_DIR / text[len(prefix):]).resolve()
    return Path(text).resolve()


def field_extension_id(field: dict[str, Any]) -> str:
    """读取 schema 字段所属扩展 id；平台字段返回空字符串。"""
    return str(field.get('extensionId') or '').strip()


def extension_env_rel_path(extension_id: str) -> str:
    """返回扩展部署输入文件的仓库相对路径。"""
    normalized = str(extension_id or '').strip()
    return f'agent/extensions/{normalized}/deploy/{EXTENSION_ENV_FILENAME}'


def extension_env_path(extension_root: Path, extension_id: str) -> Path:
    """解析扩展部署输入文件路径，并拒绝非法 extension id 越界。"""
    normalized = str(extension_id or '').strip()
    if not normalized or '/' in normalized or '\\' in normalized or normalized in {'.', '..'}:
        raise ValueError(f'非法 extension id：{extension_id}')
    return Path(extension_root).resolve() / normalized / 'deploy' / EXTENSION_ENV_FILENAME


def extension_owned_schema_keys(schema: dict[str, Any]) -> dict[str, str]:
    """返回 schema 中由扩展声明的 env key 到 extension id 的映射。"""
    return {
        str(field.get('key') or '').strip(): field_extension_id(field)
        for field in schema.get('fields', [])
        if isinstance(field, dict) and str(field.get('key') or '').strip() and field_extension_id(field)
    }


def model_spec_extension_id(spec: ModelEnvSpec) -> str:
    """从 modelProfileRef 的 owner 前缀推断模型变量所属扩展 id。"""
    source = str(spec.source_model_ref or '').strip()
    if ':' not in source:
        return ''
    owner, _ = source.split(':', 1)
    return owner.strip()


def extension_owned_model_keys(model_specs: dict[str, ModelEnvSpec]) -> dict[str, str]:
    """返回模型变量名到扩展 id 的映射。"""
    return {
        spec.name: owner
        for spec in model_specs.values()
        for owner in [model_spec_extension_id(spec)]
        if spec.name and owner
    }


def extension_ids_from_schema(schema: dict[str, Any], *, model_specs: dict[str, ModelEnvSpec] | None = None) -> list[str]:
    """汇总 schema 和模型变量中出现的扩展 id。"""
    ids = set(extension_owned_schema_keys(schema).values())
    if model_specs:
        ids.update(extension_owned_model_keys(model_specs).values())
    return sorted(item for item in ids if item)


def load_extension_env_overrides(
    *,
    schema: dict[str, Any],
    extension_env_root: Path,
    model_specs: dict[str, ModelEnvSpec] | None = None,
    fail,
) -> OrderedDict[str, str]:
    """读取启用扩展的 extension.env 覆盖值，并拒绝扩展未在 schema 声明的键。"""
    schema_key_owners = extension_owned_schema_keys(schema)
    model_key_owners = extension_owned_model_keys(model_specs or {})
    allowed_key_owners = {**schema_key_owners, **model_key_owners}
    values: OrderedDict[str, str] = OrderedDict()
    for extension_id in extension_ids_from_schema(schema, model_specs=model_specs):
        env_path = extension_env_path(extension_env_root, extension_id)
        if not env_path.exists():
            continue
        for key, value in parse_env_file(env_path).items():
            owner = allowed_key_owners.get(key)
            if owner != extension_id:
                fail(
                    'deploy_env_control_plane',
                    f'{display_path(env_path)} 包含当前扩展未声明的 env 键：{key}',
                    2,
                )
            values[key] = value
    return values


def assert_extension_env_keys_declared_by_schema(schema: dict[str, Any], *, extension_env_root: Path, fail) -> None:
    """按部署 schema 拦截扩展未声明 env 键，避免失败场景继续执行重渲染链。"""
    schema_key_owners = extension_owned_schema_keys(schema)
    for extension_id in extension_ids_from_schema(schema):
        env_path = extension_env_path(extension_env_root, extension_id)
        if not env_path.exists():
            continue
        for key in parse_env_file(env_path):
            if schema_key_owners.get(key) == extension_id:
                continue
            fail(
                'deploy_env_control_plane',
                f'{display_path(env_path)} 包含当前扩展未声明的 env 键：{key}',
                2,
            )


def assert_no_extension_keys_in_site_env(site_env_path: Path, site_env_values: OrderedDict[str, str], schema: dict[str, Any], *, fail) -> None:
    """阻止 deploy/site.env 写入扩展专属变量，避免平台输入和扩展输入混用。"""
    key_owners = extension_owned_schema_keys(schema)
    for key in site_env_values:
        owner = key_owners.get(key)
        if not owner:
            continue
        fail(
            'deploy_env_control_plane',
            f'{display_path(site_env_path)} 不允许填写扩展变量：{key}；请写入 {extension_env_rel_path(owner)}。',
            2,
        )


def infer_control_plane_profile_id_from_config_path(value: str) -> str | None:
    """从 runtime config path 反推出 profile id；无法解析时返回 None。"""
    config_path = host_config_path_from_runtime_value(value)
    if config_path is None:
        return None
    return control_plane_profile_id_for_config_path(config_path, start_path=ROOT_DIR)


def selected_control_plane_profile_id(*env_maps: OrderedDict[str, str] | dict[str, str]) -> str:
    """按显式 profile 优先、runtime config path 兜底的顺序解析当前控制面部署画像。"""
    for env_map in env_maps:
        profile_id = str((env_map or {}).get(CONTROL_PLANE_PROFILE_KEY) or '').strip()
        if profile_id:
            return profile_id
    for env_map in env_maps:
        raw_path = str((env_map or {}).get(CONTROL_PLANE_CONFIG_PATH_KEY) or '').strip()
        if not raw_path:
            continue
        profile_id = infer_control_plane_profile_id_from_config_path(raw_path)
        if profile_id:
            return profile_id
        raise ValueError(f'{CONTROL_PLANE_CONFIG_PATH_KEY} 未命中 profile registry：{raw_path}')
    return DEFAULT_RUNTIME_CONTROL_PLANE_PROFILE_ID


def assert_site_control_plane_selection_consistent(
    site_env_values: OrderedDict[str, str],
    profile_id: str,
    *,
    fail,
) -> None:
    """校验 site.env 中 profile 与内部 config path 没有互相矛盾。"""
    raw_path = str(site_env_values.get(CONTROL_PLANE_CONFIG_PATH_KEY) or '').strip()
    if not raw_path:
        return
    inferred = infer_control_plane_profile_id_from_config_path(raw_path)
    if not inferred:
        fail(
            'deploy_env_control_plane',
            f'deploy/site.env 中的 {CONTROL_PLANE_CONFIG_PATH_KEY} 不是人工配置入口，且未命中 profile registry：{raw_path}',
            2,
        )
    if inferred != profile_id:
        fail(
            'deploy_env_control_plane',
            'deploy/site.env 同时声明了不一致的 control-plane profile 与内部路径：'
            f'{CONTROL_PLANE_PROFILE_KEY}={profile_id}, {CONTROL_PLANE_CONFIG_PATH_KEY} -> {inferred}。'
            f'请只保留 {CONTROL_PLANE_PROFILE_KEY}=<profile-id>。',
            2,
        )


def normalize_control_plane_profile_values(values: OrderedDict[str, str], profile_id: str | None = None) -> str:
    """把 profile id 和容器内控制面 config path 同步写入最终 env map。"""
    resolved_profile_id = str(profile_id or '').strip() or selected_control_plane_profile_id(values)
    values[CONTROL_PLANE_PROFILE_KEY] = resolved_profile_id
    values[CONTROL_PLANE_CONFIG_PATH_KEY] = runtime_config_path_for_profile(resolved_profile_id)
    return resolved_profile_id


def active_model_env_specs(values: OrderedDict[str, str] | dict[str, str]) -> dict[str, ModelEnvSpec]:
    """读取当前 active profile 需要暴露到部署 env 的模型运行变量规格。"""
    try:
        profile_id = str((values or {}).get(CONTROL_PLANE_PROFILE_KEY) or '').strip()
        if profile_id:
            config_path = host_config_path_for_profile(profile_id)
        else:
            config_path = host_config_path_from_runtime_value(str((values or {}).get(CONTROL_PLANE_CONFIG_PATH_KEY) or ''))
            if config_path is None:
                return {}
        registry = load_registry(config_path)
    except Exception:
        return {}
    return model_env_specs_from_registry(registry, scheduler_scope=True)


def augment_model_env_values(
    values: OrderedDict[str, str],
    *,
    model_specs: dict[str, ModelEnvSpec] | None = None,
) -> None:
    """把 active profile 声明的模型变量补进 env map，必填项使用占位符提示。"""
    for spec in (model_specs if model_specs is not None else active_model_env_specs(values)).values():
        if spec.name in values:
            continue
        values[spec.name] = '__REQUIRED__' if spec.required else ''


def build_default_values(
    existing_env: OrderedDict[str, str] | None = None,
    *,
    schema: dict[str, Any] | None = None,
    control_plane_profile: str | None = None,
    dispatch_registry: dict[str, Any] | None = None,
) -> OrderedDict[str, str]:
    """构造 deploy/.env 默认值，合并 pin、profile、随机 token、UID/GID 与 dispatch target 默认项。"""
    schema = schema or load_schema()
    existing_env_map = OrderedDict(existing_env or {})
    selected_profile_id = str(control_plane_profile or '').strip() or selected_control_plane_profile_id(existing_env_map)
    values: OrderedDict[str, str] = OrderedDict()
    for field in schema.get('fields', []):
        key = str(field['key'])
        pin_key = field.get('pin_key')
        if pin_key:
            for rel_path in (
                repo_contract_relpath('image_pins.openclaw'),
                repo_contract_relpath('image_pins.runtime'),
            ):
                pinned = pin_value(rel_path, str(pin_key))
                if pinned:
                    values[key] = pinned
                    break
            if key in values:
                continue
        if key == CONTROL_PLANE_PROFILE_KEY:
            values[key] = selected_profile_id
            continue
        if field.get('generated') == 'control_plane_service_config_path':
            values[key] = runtime_config_path_for_profile(selected_profile_id)
            continue
        if key in existing_env_map and str(existing_env_map[key]).strip() and not field_extension_id(field):
            values[key] = str(existing_env_map[key]).strip()
            continue
        if field.get('generated') == 'random_token':
            values[key] = random_token()
            continue
        if field.get('generated') == 'current_uid':
            values[key] = str(current_uid())
            continue
        if field.get('generated') == 'current_gid':
            values[key] = str(current_gid())
            continue
        if field.get('generated') == 'host_state_root':
            values[key] = host_state_root_default()
            continue
        if key == 'OPENCLAW_INGRESS_LISTEN_IP':
            detected_ingress_ip = detect_first_private_ipv4_from_hostname_i()
            if detected_ingress_ip:
                values[key] = detected_ingress_ip
                continue
        default_kind = field.get('default_kind')
        if default_kind == 'literal':
            values[key] = str(field.get('default') or '')
        elif default_kind == 'placeholder':
            values[key] = str(field.get('placeholder') or '__REQUIRED__')
        else:
            values[key] = ''
    normalize_control_plane_profile_values(values, selected_profile_id)
    registry = (
        dict(dispatch_registry)
        if dispatch_registry is not None
        else deploy_env_dispatch_registry_lib.load_dispatch_targets(
            config_path=host_config_path_for_profile(selected_profile_id),
            required=False,
        )
    )
    if registry:
        for line in deploy_env_dispatch_registry_lib.build_dispatch_default_exports(registry):
            key, value = line.split('=', 1)
            if key in existing_env_map and str(existing_env_map[key]).strip():
                values[key] = str(existing_env_map[key]).strip()
            elif key.startswith('DEFAULT_'):
                continue
            else:
                values[key] = value
    return values


def ensure_site_env_example(site_env_example_path: Path = DEFAULT_SITE_ENV_EXAMPLE_PATH) -> None:
    """按当前 schema 刷新 deploy/site.env.example 模板。"""
    lines = build_site_env_template_lines()
    write_text(site_env_example_path, '\n'.join(lines).rstrip() + '\n')


def format_target_env_value(value: Any) -> str:
    """把 target registry 默认值转成 env 文件可写入的字符串。"""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, list):
        return ','.join(str(item) for item in value)
    return str(value or '')


def target_env_example_lines(target: dict[str, Any]) -> list[str]:
    """根据单个 dispatch target 定义生成 targets.d 示例 env 文件内容。"""
    target_id = str(target.get('id') or '').strip()
    title = str(target.get('titleDefault') or target_id).strip()
    boundary = target.get('boundary') if isinstance(target.get('boundary'), dict) else {}
    dispatch_lane = str(boundary.get('dispatchLane') or '').strip()
    payload_scope = str(boundary.get('payloadScope') or '').strip()
    publish_latest = bool(boundary.get('publishLatestDefault'))
    responsibility = (
        '该目标负责推进正式 dispatch latest。'
        if publish_latest
        else '该目标只写目标运行记录，不覆盖正式 dispatch latest。'
    )
    endpoint_env = str(target.get('endpointEnv') or '').strip()
    secret_env = str(target.get('secretEnv') or '').strip()
    rows = [
        f'# {target_id} - {title}',
        f'# 复制为 deploy/targets.d/{target_id}.env 后只在部署环境填写真实值。',
    ]
    if dispatch_lane or payload_scope:
        rows.append(f'# 边界：{dispatch_lane} / {payload_scope}；{responsibility}')
    rows.append('')
    field_pairs = [
        (str(target.get('enabledEnv') or '').strip(), 'true'),
        (endpoint_env, f'__{endpoint_env}__' if endpoint_env else ''),
        (secret_env, f'__{secret_env}__' if secret_env else ''),
        (str(target.get('titleEnv') or '').strip(), format_target_env_value(target.get('titleDefault'))),
        (str(target.get('atAllEnv') or '').strip(), format_target_env_value(target.get('atAllDefault'))),
        (str(target.get('formatEnv') or '').strip(), format_target_env_value(target.get('formatDefault'))),
        (str(target.get('silenceEnabledEnv') or '').strip(), format_target_env_value(target.get('silenceEnabledDefault'))),
        (str(target.get('silenceMinDeltaEnv') or '').strip(), format_target_env_value(target.get('silenceMinDeltaDefault'))),
        (str(target.get('allowedReleaseLevelsEnv') or '').strip(), format_target_env_value(target.get('allowedReleaseLevelsDefault'))),
    ]
    seen: set[str] = set()
    for key, value in field_pairs:
        if not key or key in seen:
            continue
        seen.add(key)
        rows.append(f'{key}={value}')
    return rows


def ensure_targets_env_dir(
    targets_env_dir: Path,
    registry: dict[str, Any] | None = None,
    *,
    prune_unregistered_examples: bool = True,
) -> None:
    """根据 dispatch target registry 生成 targets.d 示例文件，并清理失效模板。"""
    targets_env_dir.mkdir(parents=True, exist_ok=True)
    readme_path = targets_env_dir / 'README.md'
    content = (
        '# targets.d\n\n'
        '本目录承载 dispatch target 级别的补充变量；gateway 入口配置统一由 ingress 合同处理。\n\n'
        '`.env.example` 模板由 one_click_config 根据当前 active control-plane profile 的 dispatch target registry 生成；'
        '生成文件是本地辅助输入，不进入仓库；真实 Webhook 与签名密钥只写入同名 `.env`，不要提交到仓库。\n'
        '`one_click_config.sh` 只接受 active profile 已声明的 `<target_id>.env` 与 registry 中的 env 键；切换 profile 前先移走非当前 profile 的 target env。\n'
    )
    if not readme_path.exists() or read_text(readme_path) != content:
        write_text(readme_path, content)
    expected_example_names: set[str] = set()
    for target in sorted(list((registry or {}).get('targets') or []), key=lambda row: int(row.get('verificationOrderDefault') or 0)):
        if not isinstance(target, dict):
            continue
        target_id = str(target.get('id') or '').strip()
        if not target_id:
            continue
        expected_example_names.add(f'{target_id}.env.example')
        write_text(targets_env_dir / f'{target_id}.env.example', '\n'.join(target_env_example_lines(target)).rstrip() + '\n')
    if registry is not None and prune_unregistered_examples:
        for example_path in sorted(targets_env_dir.glob('*.env.example')):
            if example_path.name not in expected_example_names:
                example_path.unlink()


def summarize_required_keys(values: OrderedDict[str, str], schema: dict[str, Any]) -> list[dict[str, Any]]:
    """按部署 schema 汇总人工必填字段、当前状态和错误提示。"""
    rows: list[dict[str, Any]] = []
    for field in schema.get('fields', []):
        key = str(field['key'])
        if not field_is_required(field, values):
            continue
        value = values.get(key, '')
        error = validate_value(value, field.get('validator'))
        if (
            not error
            and field.get('truthy_required') is True
            and not is_truthy_flag_value(value)
        ):
            error = '必须启用（1/true/yes/on）'
        manual = field.get('manual_required') is True
        status = 'filled' if not error and not is_placeholder_value(value) else 'pending'
        requirement_reason = field_requirement_reason(field, values)
        rows.append({
            'key': key,
            'manual_required': manual,
            'status': status,
            'doc_location': field.get('doc_location'),
            'value_preview': '<secret>' if field.get('secret') else value,
            'error': error or None,
            'requirement_reason': requirement_reason or None,
        })
    return rows


def summarize_model_required_keys(
    values: OrderedDict[str, str],
    *,
    model_specs: dict[str, ModelEnvSpec] | None = None,
) -> list[dict[str, Any]]:
    """汇总 active profile 模型运行变量中的人工必填项。"""
    rows: list[dict[str, Any]] = []
    for spec in (model_specs if model_specs is not None else active_model_env_specs(values)).values():
        if not spec.required:
            continue
        value = values.get(spec.name, '')
        validator = {'type': spec.validator}
        if spec.validator == 'secret_like':
            validator['min_length'] = 8
        error = validate_value(value, validator)
        status = 'filled' if not error and not is_placeholder_value(value) else 'pending'
        rows.append({
            'key': spec.name,
            'manual_required': True,
            'status': status,
            'doc_location': extension_env_rel_path(model_spec_extension_id(spec)) if model_spec_extension_id(spec) else 'deploy/site.env',
            'value_preview': '<secret>' if spec.secret else value,
            'error': error or None,
            'requirement_reason': f'modelProfileRef={spec.source_model_ref}',
        })
    return rows


def dedupe_required_key_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 key 去重必填项摘要，保留第一次出现的来源说明。"""
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        key = str(row.get('key') or '').strip()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def secret_keys_from_schema(schema: dict[str, Any]) -> set[str]:
    """从部署 schema 读取需要脱敏输出的 secret 字段集合。"""
    return {str(field.get('key') or '').strip() for field in schema.get('fields', []) if field.get('secret') and str(field.get('key') or '').strip()}


def secret_keys_from_model_specs(
    values: OrderedDict[str, str],
    *,
    model_specs: dict[str, ModelEnvSpec] | None = None,
) -> set[str]:
    """从模型变量规格读取需要脱敏输出的 secret 字段集合。"""
    return {spec.name for spec in (model_specs if model_specs is not None else active_model_env_specs(values)).values() if spec.secret}


def secret_keys_from_env_names(values: OrderedDict[str, str]) -> set[str]:
    """按变量名中的敏感标记兜底识别需要脱敏展示的 env key。"""
    markers = ('TOKEN', 'SECRET', 'PASSWORD', 'PASS', 'API_KEY', 'ACCESS_KEY', 'WEBHOOK', 'URL')
    return {
        key
        for key in values
        if any(marker in str(key).upper() for marker in markers)
    }


def validate_local_ingress_acceptance_source(values: OrderedDict[str, str], *, allow_placeholders: bool = False) -> list[str]:
    """确认 ingress allowlist 包含目标机本机验收来源，避免部署后 /healthz 被本机访问拒绝。"""
    listen_ip = str(values.get('OPENCLAW_INGRESS_LISTEN_IP') or '').strip()
    source_cidrs = str(values.get('OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS') or '').strip()
    if not listen_ip or not source_cidrs:
        return []
    if allow_placeholders and (is_placeholder_value(listen_ip) or is_placeholder_value(source_cidrs)):
        return []
    try:
        listen_address = ipaddress.ip_address(listen_ip)
    except ValueError:
        return []
    suffix = '/32' if isinstance(listen_address, ipaddress.IPv4Address) else '/128'
    expected_network = ipaddress.ip_network(f'{listen_ip}{suffix}', strict=False)
    networks: list[Any] = []
    for raw_item in source_cidrs.split(','):
        item = raw_item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            return []
    if any(network == expected_network for network in networks):
        return []
    return [
        'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS 必须包含目标机本机验收来源 '
        f'{listen_ip}{suffix}；one_click_deploy 默认在目标机本机执行 full test，并会通过 ingress 请求 /healthz 与 /readyz。'
    ]


def validate_deploy_env_values(
    values: OrderedDict[str, str],
    schema: dict[str, Any],
    *,
    allow_placeholders: bool = False,
    model_specs: dict[str, ModelEnvSpec] | None = None,
) -> list[str]:
    """校验 deploy env 的 schema、profile、模型变量、未知键与本机 ingress 验收来源。"""
    errors: list[str] = []
    declared_keys = {str(field['key']) for field in schema.get('fields', [])}
    resolved_model_specs = model_specs if model_specs is not None else active_model_env_specs(values)
    declared_keys.update(resolved_model_specs.keys())
    profile_id = str(values.get(CONTROL_PLANE_PROFILE_KEY) or '').strip()
    config_path = str(values.get(CONTROL_PLANE_CONFIG_PATH_KEY) or '').strip()
    if profile_id and config_path and not (allow_placeholders and is_placeholder_value(profile_id)):
        expected_config_path = ''
        try:
            expected_config_path = runtime_config_path_for_profile(profile_id)
        except Exception as exc:
            errors.append(f'{CONTROL_PLANE_PROFILE_KEY}: {exc}')
        if expected_config_path and config_path.replace('\\', '/') != expected_config_path:
            errors.append(
                f'{CONTROL_PLANE_CONFIG_PATH_KEY} 必须由 {CONTROL_PLANE_PROFILE_KEY}={profile_id} 生成；'
                f'当前={config_path}，期望={expected_config_path}'
            )
    for field in schema.get('fields', []):
        key = str(field['key'])
        required = field_is_required(field, values)
        if required and key not in values:
            errors.append(f'{key} 缺失')
            continue
        if key not in values:
            continue
        value = values[key]
        if (not required) and (not str(value).strip()):
            continue
        if allow_placeholders and is_placeholder_value(value):
            if required and field.get('truthy_required') is True:
                errors.append(f'{key}: 必须启用（1/true/yes/on）')
            continue
        error = validate_value(value, field.get('validator'))
        if error:
            errors.append(f'{key}: {error}')
            continue
        if required and field.get('truthy_required') is True and not is_truthy_flag_value(value):
            errors.append(f'{key}: 必须启用（1/true/yes/on）')
    for spec in resolved_model_specs.values():
        value = values.get(spec.name, '')
        if spec.required and spec.name not in values:
            errors.append(f'{spec.name} 缺失')
            continue
        if not spec.required and not str(value).strip():
            continue
        if allow_placeholders and is_placeholder_value(value):
            continue
        validator = {'type': spec.validator}
        if spec.validator == 'secret_like':
            validator['min_length'] = 8
        error = validate_value(value, validator)
        if error:
            errors.append(f'{spec.name}: {error}')
    for key in values:
        if key.startswith('OPENCLAW_') and key not in declared_keys:
            errors.append(f'不允许未登记部署输入：{key}')
    errors.extend(validate_local_ingress_acceptance_source(values, allow_placeholders=allow_placeholders))
    return errors


def build_summary(
    values: OrderedDict[str, str],
    output_path: Path,
    schema: dict[str, Any] | None = None,
    *,
    model_specs: dict[str, ModelEnvSpec] | None = None,
) -> dict[str, Any]:
    """生成 one_click_config 摘要，说明必填项是否已闭合以及当前安全模式。"""
    schema = schema or load_schema()
    required_manual_keys = dedupe_required_key_rows(
        summarize_required_keys(values, schema)
        + summarize_model_required_keys(values, model_specs=model_specs)
    )
    unresolved = [row for row in required_manual_keys if row['status'] != 'filled']
    return {
        'schema_version': 1,
        'generated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'output_path': str(output_path),
        'status': 'ready' if not unresolved else 'manual_input_required',
        'required_manual_keys': required_manual_keys,
        'unresolved_required_count': len(unresolved),
        'auth_mode': 'official_gateway_token',
        'ingress_mode': 'private_https_ingress',
        'tls_mode': values.get('OPENCLAW_TLS_MODE') or '',
    }


def dispatch_target_env_key_map(registry: dict[str, Any] | None) -> dict[str, set[str]]:
    """从 dispatch target registry 推导每个 target 允许出现在 targets.d 的 env 键集合。"""
    result: dict[str, set[str]] = {}
    for target in list((registry or {}).get('targets') or []):
        if not isinstance(target, dict):
            continue
        target_id = str(target.get('id') or '').strip()
        if not target_id:
            continue
        keys = {
            str(target.get(field_name) or '').strip()
            for field_name in DISPATCH_TARGET_ENV_FIELD_NAMES
            if str(target.get(field_name) or '').strip()
        }
        result[target_id] = keys
    return result


def merge_target_env_overrides(
    values: OrderedDict[str, str],
    targets_env_dir: Path,
    blocked_keys: set[str],
    *,
    registry: dict[str, Any] | None,
    fail,
) -> None:
    """合并 deploy/targets.d 中的目标级变量，并拒绝覆盖 pin 或写入未登记键。"""
    if not targets_env_dir.exists():
        return
    allowed_keys_by_target = dispatch_target_env_key_map(registry)
    for env_path in sorted(targets_env_dir.glob('*.env')):
        target_id = env_path.stem
        if target_id not in allowed_keys_by_target:
            fail(
                'deploy_env_control_plane',
                f'{display_path(env_path)} 不属于当前 active profile 的 dispatch target registry；请移走非当前 profile 的 target env 或切换 {CONTROL_PLANE_PROFILE_KEY}。',
                2,
            )
        allowed_keys = allowed_keys_by_target[target_id]
        for key, value in parse_env_file(env_path).items():
            if key in blocked_keys and str(value).strip():
                fail('deploy_env_control_plane', f'{display_path(env_path)} 不允许覆盖镜像 pin 真源：{key}', 2)
            if key not in allowed_keys:
                fail(
                    'deploy_env_control_plane',
                    f'{display_path(env_path)} 包含当前 target registry 未声明的 env 键：{key}',
                    2,
                )
            values[key] = value


def render_env(argv: list[str], *, fail, note) -> int:
    """执行 env 渲染入口；成功时写出 deploy/.env、summary JSON 与本地模板文件。"""
    try:
        values, positionals = parse_typed_flag_args(
            argv,
            specs={
                'output': FlagSpec(kind='path', dest='output_path', default=DEFAULT_OUTPUT_PATH),
                'summary-json': FlagSpec(kind='path', dest='summary_json_path', default=DEFAULT_SUMMARY_JSON_PATH),
                'site-env': FlagSpec(kind='path', dest='site_env_path', default=DEFAULT_SITE_ENV_PATH),
                'targets-env-dir': FlagSpec(kind='path', dest='targets_env_dir', default=DEFAULT_TARGETS_ENV_DIR),
                'extension-env-root': FlagSpec(kind='path', dest='extension_env_root', default=DEFAULT_EXTENSION_ENV_ROOT),
                'dry-run': FlagSpec(kind='bool', dest='dry_run', default=False),
            },
        )
    except CliError as exc:
        fail('deploy_env_control_plane', str(exc), 2)
    if positionals:
        fail('deploy_env_control_plane', f'未知参数：{positionals[0]}', 2)
    output_path = values['output_path']
    summary_json_path = values['summary_json_path']
    site_env_path = values['site_env_path']
    targets_env_dir = values['targets_env_dir']
    extension_env_root = values['extension_env_root']
    dry_run = values['dry_run']
    ensure_site_env_example()
    existing_output_values = parse_env_file(output_path) if output_path.exists() else OrderedDict()
    site_env_values = parse_env_file(site_env_path) if site_env_path.exists() else OrderedDict()
    try:
        selected_profile_id = selected_control_plane_profile_id(site_env_values, existing_output_values)
        selected_config_path = host_config_path_for_profile(selected_profile_id)
    except Exception as exc:
        fail('deploy_env_control_plane', f'control-plane profile 选择无效：{exc}', 2)
    assert_site_control_plane_selection_consistent(site_env_values, selected_profile_id, fail=fail)
    schema = load_schema(config_path=selected_config_path)
    assert_no_extension_keys_in_site_env(site_env_path, site_env_values, schema, fail=fail)
    assert_extension_env_keys_declared_by_schema(schema, extension_env_root=extension_env_root, fail=fail)
    dispatch_registry = deploy_env_dispatch_registry_lib.load_dispatch_targets(config_path=selected_config_path, required=False)
    ensure_targets_env_dir(targets_env_dir, registry=dispatch_registry, prune_unregistered_examples=not dry_run)
    if not site_env_path.exists() and not dry_run:
        detected_ingress_ip = detect_first_private_ipv4_from_hostname_i()
        site_env_lines = build_site_env_template_lines(
            detected_ingress_ip=detected_ingress_ip,
            populate_detected_ingress_ip=bool(detected_ingress_ip),
        )
        write_text(site_env_path, '\n'.join(site_env_lines).rstrip() + '\n')
        chmod_owner_only(site_env_path)
        if detected_ingress_ip:
            note(
                'deploy_env_control_plane',
                f'首次生成 deploy/site.env 时已写入宿主机探测到的 OPENCLAW_INGRESS_LISTEN_IP={detected_ingress_ip}；如部署网卡不同，请手工改回目标网卡 IP。',
            )
    default_source_values = OrderedDict(existing_output_values)
    for key, value in site_env_values.items():
        if str(value).strip():
            default_source_values[key] = value
    values = build_default_values(
        default_source_values,
        schema=schema,
        control_plane_profile=selected_profile_id,
        dispatch_registry=dispatch_registry,
    )
    blocked_keys = pin_managed_keys(schema)
    if site_env_values:
        for key, value in site_env_values.items():
            stripped_value = str(value).strip()
            if key in blocked_keys and stripped_value:
                fail('deploy_env_control_plane', f'{display_path(site_env_path)} 不允许覆盖镜像 pin 真源：{key}', 2)
            if key == CONTROL_PLANE_CONFIG_PATH_KEY:
                continue
            if key.startswith('DISPATCH_') or key.startswith('TARGET_GROUP_'):
                fail(
                    'deploy_env_control_plane',
                    f'{display_path(site_env_path)} 不允许填写 target 级变量：{key}；请写入 deploy/targets.d/<target_id>.env。',
                    2,
                )
            if key == CONTROL_PLANE_PROFILE_KEY:
                values[key] = selected_profile_id
                continue
            if stripped_value or key not in values:
                values[key] = value
    model_specs = active_model_env_specs(values)
    extension_env_values = load_extension_env_overrides(
        schema=schema,
        extension_env_root=extension_env_root,
        model_specs=model_specs,
        fail=fail,
    )
    for key, value in extension_env_values.items():
        values[key] = value
    normalize_control_plane_profile_values(values, selected_profile_id)
    merge_target_env_overrides(values, targets_env_dir, blocked_keys, registry=dispatch_registry, fail=fail)
    normalize_control_plane_profile_values(values, selected_profile_id)
    augment_model_env_values(values, model_specs=model_specs)
    runtime_uid = str(values.get('OPENCLAW_RUNTIME_UID') or '').strip()
    runtime_gid = str(values.get('OPENCLAW_RUNTIME_GID') or '').strip()
    if runtime_uid == '0' or runtime_gid == '0':
        note(
            'deploy_env_control_plane',
            '当前最终配置声明 OPENCLAW_RUNTIME_UID/GID='
            f'{runtime_uid or "<empty>"}:{runtime_gid or "<empty>"}；这会让至少一部分 runtime 服务绑定为 root。'
            'root 只应承担 prepare_docker_host / apply_ingress_boundary_rules 等宿主机特权步骤；'
            'one_click_config 与部署主链建议切回固定部署用户后执行。',
        )
    summary = build_summary(values, output_path, schema, model_specs=model_specs)
    validation_errors = validate_deploy_env_values(values, schema, allow_placeholders=True, model_specs=model_specs)
    if validation_errors:
        for item in validation_errors:
            print(f'[deploy_env_control_plane][INVALID] {item}', file=sys.stderr)
        fail('deploy_env_control_plane', 'deploy env 配置校验未通过；请按 INVALID 项修正部署输入真源后重新执行 one_click_config.sh。', 2)
    content = render_env_lines(values)
    display_content = render_env_lines(
        values,
        redact_secret_keys=secret_keys_from_schema(schema) | secret_keys_from_model_specs(values, model_specs=model_specs) | secret_keys_from_env_names(values),
    )
    if not dry_run:
        write_text(output_path, content)
        chmod_owner_only(output_path)
        harden_deploy_input_files(site_env_path, targets_env_dir, output_path, extension_env_root)
        write_text(summary_json_path, json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(display_content, end='')
    pending = [row['key'] for row in summary['required_manual_keys'] if row['status'] != 'filled']
    if pending:
        note('deploy_env_control_plane', f"需要人工补齐：{', '.join(pending)}")
    else:
        note('deploy_env_control_plane', 'deploy/.env 已满足正式部署路径要求')
    return 0


def validate_env(argv: list[str], *, fail, note) -> int:
    """执行 env 校验入口；只读取指定 env 文件并输出可读 INVALID 明细。"""
    try:
        values, positionals = parse_typed_flag_args(
            argv,
            specs={
                'env-file': FlagSpec(kind='path', dest='env_file', default=DEFAULT_OUTPUT_PATH),
            },
        )
    except CliError as exc:
        fail('deploy_env_control_plane', str(exc), 2)
    if positionals:
        fail('deploy_env_control_plane', f'未知参数：{positionals[0]}', 2)
    env_file = values['env_file']
    values = parse_env_file(env_file)
    try:
        selected_profile_id = selected_control_plane_profile_id(values)
        schema = load_schema(config_path=host_config_path_for_profile(selected_profile_id))
    except Exception as exc:
        print(f'[deploy_env_control_plane][INVALID] control-plane profile 选择无效：{exc}', file=sys.stderr)
        return 2
    errors = validate_deploy_env_values(values, schema)
    if errors:
        for item in errors:
            print(f'[deploy_env_control_plane][INVALID] {item}', file=sys.stderr)
        return 2
    note('deploy_env_control_plane', f'校验通过：{env_file}')
    return 0
