#!/usr/bin/env python3
"""Shared deploy-env schema rendering and validation helpers."""
from __future__ import annotations

import re
import secrets
import subprocess
import ipaddress
from collections import OrderedDict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from openclaw.control_plane.surfaces import load_deploy_env_schema
from openclaw.lib.repo.contracts import repo_contract_path
from openclaw.lib.repo.layout import (
    available_control_plane_profile_ids,
    resolve_default_runtime_control_plane_service_config_path,
    resolve_repo_root,
)
from openclaw.doctor.platform.ingress_boundary.normalization import normalize_source_cidrs
from openclaw.setup.network.private_ingress import RFC1918_V4_NETWORKS, validate_private_ingress_bind_ip
from openclaw.setup.network.tls_hostname import validate_tls_hostname

ROOT_DIR = resolve_repo_root(Path(__file__))
SCHEMA_PATH = repo_contract_path('deploy_env.schema')

PLACEHOLDER_EXACT = {
    '',
    '__required__',
    'required',
    'changeme',
    'placeholder',
    'example',
    'sample',
    'dummy',
    'todo',
    'tbd',
    'unset',
    'none',
    'null',
    'replaceme',
    'fillme',
    'fixme',
}

PLACEHOLDER_COLLAPSED = {
    '__required__',
    'required',
    'changeme',
    'placeholder',
    'example',
    'sample',
    'dummy',
    'todo',
    'tbd',
    'unset',
    'none',
    'null',
    'replaceme',
    'fillme',
    'fixme',
    'yourtoken',
    'yourapikey',
    'yoursecret',
    'yourwebhook',
    'yoururl',
    'yourdomain',
    'dummysecret',
    'dummytoken',
    'exampletoken',
    'examplekey',
    'examplesecret',
    'placeholdertoken',
    'placeholdersecret',
}

INGRESS_IP_DETECTION_COMMANDS = [
    ['hostname', '-I'],
]


def random_token() -> str:
    return secrets.token_urlsafe(32)


def load_schema(config_path: Path | None = None) -> dict[str, Any]:
    resolved_config_path = resolve_default_runtime_control_plane_service_config_path(ROOT_DIR) if config_path is None else Path(config_path).resolve()
    return load_deploy_env_schema(SCHEMA_PATH, config_path=resolved_config_path)


def detect_first_private_ipv4_from_hostname_i() -> str:
    for command in INGRESS_IP_DETECTION_COMMANDS:
        try:
            output = subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL).strip()
        except Exception:
            continue
        for token in re.split(r'\s+', output):
            candidate_text = token.strip()
            if not candidate_text:
                continue
            try:
                candidate = ipaddress.ip_address(candidate_text)
            except ValueError:
                continue
            if isinstance(candidate, ipaddress.IPv4Address) and any(candidate in network for network in RFC1918_V4_NETWORKS):
                return candidate_text
    return ''


def format_schema_text(template: object, *, detected_ingress_ip: str = '') -> str:
    text = str(template or '')
    return text.replace('{DETECTED_INGRESS_IP}', detected_ingress_ip or '')


def format_schema_lines(items: object, *, detected_ingress_ip: str = '') -> list[str]:
    if items in (None, ''):
        return []
    if isinstance(items, (str, bytes)):
        raw_items = [items]
    elif isinstance(items, (list, tuple, set)):
        raw_items = list(items)
    else:
        raw_items = [items]
    result: list[str] = []
    for item in raw_items:
        line = format_schema_text(item, detected_ingress_ip=detected_ingress_ip)
        if line:
            result.append(line)
    return result


def field_site_env_comment_lines(field: dict[str, Any], *, detected_ingress_ip: str = '') -> list[str]:
    lines: list[str] = []
    for line in format_schema_lines(field.get('site_env_examples') or [], detected_ingress_ip=detected_ingress_ip):
        lines.append(f'# 示例: {line}')
    for line in format_schema_lines(field.get('site_env_annotations') or [], detected_ingress_ip=detected_ingress_ip):
        lines.append(f'# 说明: {line}')
    for line in format_schema_lines(field.get('site_env_commands') or [], detected_ingress_ip=detected_ingress_ip):
        lines.append(f'# 命令: {line}')
    return lines


def render_doc_code_block(lines: list[str], *, language: str = 'text') -> list[str]:
    return [f'```{language}', *lines, '```']


def render_doc_detail_section(field: dict[str, Any], *, detected_ingress_ip: str = '') -> list[str]:
    key = str(field.get('key') or '')
    title = str(field.get('doc_title') or key)
    details = field.get('doc_details') if isinstance(field.get('doc_details'), dict) else {}
    heading = f'### `{key}`'
    if title and title != key:
        heading = f'{heading} - {title}'
    rows = [heading, '']

    def append_labeled(label: str, items: list[str]) -> None:
        if not items:
            return
        if len(items) == 1:
            rows.append(f'- {label}：{items[0]}')
            return
        rows.append(f'- {label}：')
        rows.extend([f'  - {item}' for item in items])

    step2_role = format_schema_text(details.get('step2_role'), detected_ingress_ip=detected_ingress_ip)
    if step2_role:
        rows.append(f'- 填写时机：{step2_role}')
    summary = format_schema_text(field.get('doc_summary'), detected_ingress_ip=detected_ingress_ip)
    meaning_items = format_schema_lines(details.get('meaning') or [], detected_ingress_ip=detected_ingress_ip)
    if not meaning_items and summary:
        meaning_items = [summary]
    sections = [
        ('含义', meaning_items),
        ('填写', format_schema_lines(details.get('how_to_get') or [], detected_ingress_ip=detected_ingress_ip)),
        ('约束', format_schema_lines(details.get('format') or [], detected_ingress_ip=detected_ingress_ip)),
        ('避免', format_schema_lines(details.get('common_errors') or [], detected_ingress_ip=detected_ingress_ip)),
        ('验证', format_schema_lines(details.get('verify') or [], detected_ingress_ip=detected_ingress_ip)),
    ]
    for heading, items in sections:
        append_labeled(heading, items)
    obtain_commands = format_schema_lines(details.get('obtain_commands') or [], detected_ingress_ip=detected_ingress_ip)
    if obtain_commands:
        rows.append('- 命令示例：')
        rows.append('')
        rows.extend(render_doc_code_block(obtain_commands, language='text'))
    rows.append('')
    return rows


def build_site_env_template_lines(*, detected_ingress_ip: str = '', populate_detected_ingress_ip: bool = False) -> list[str]:
    schema = load_schema()
    lines = [
        '# OpenClaw deploy/site.env',
        '# 说明：当前交互固定使用私有 HTTPS ingress 单一入口；gateway host/host port 由统一 ingress 合同处理。',
        '',
    ]
    for field in schema.get('fields', []):
        if not isinstance(field, dict):
            continue
        key = str(field.get('key') or '')
        if not key:
            continue
        if str(field.get('extensionId') or '').strip():
            continue
        doc_location = str(field.get('doc_location') or '').strip()
        if doc_location and doc_location != 'deploy/site.env':
            continue
        if field.get('site_env_hidden') is True:
            continue
        lines.extend(field_site_env_comment_lines(field, detected_ingress_ip=detected_ingress_ip))
        value = ''
        default_kind = field.get('default_kind')
        if default_kind == 'literal':
            value = str(field.get('default') or '')
        elif default_kind == 'placeholder':
            value = str(field.get('placeholder') or '__REQUIRED__')
        if populate_detected_ingress_ip and key == 'OPENCLAW_INGRESS_LISTEN_IP' and detected_ingress_ip:
            value = detected_ingress_ip
        lines.append(f'{key}={value}')
        lines.append('')
    return lines


def is_placeholder_value(value: object) -> bool:
    normalized = re.sub(r'\s+', '', str(value or '').lower())
    collapsed = re.sub(r'[^a-z0-9_]', '', normalized)
    if normalized in PLACEHOLDER_EXACT or collapsed in PLACEHOLDER_COLLAPSED:
        return True
    if re.match(r'^(your|example|sample|dummy|test|fake|placeholder|changeme|replace|fill)[-_a-z0-9]*(token|secret|key|webhook|url|domain)(here)?$', normalized):
        return True
    if re.match(r'^(token|secret|key|webhook|url)[-_]?(placeholder|example|sample|dummy|test|fake|changeme|replace|replaceme|fillme)$', normalized):
        return True
    return False


def validate_httpish_url(value: str, allow_query: bool = True) -> str:
    try:
        parsed = urlparse(value)
    except ValueError:
        return '不是合法 URL'
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        return '必须是 http/https URL'
    if (not allow_query) and (parsed.query or parsed.fragment):
        return 'URL 不允许 query / fragment'
    return ''


def validate_image_ref(value: str) -> str:
    text = str(value or '').strip()
    if ':' not in text:
        return '镜像引用必须包含 tag'
    if '/' not in text:
        return '镜像引用必须包含 registry/repository'
    return ''


def validate_secret_like(value: str, *, min_length: int = 1) -> str:
    text = str(value or '').strip()
    if len(text) < int(min_length):
        return f'长度不能小于 {min_length}'
    if is_placeholder_value(text):
        return '占位值未替换'
    return ''


def validate_non_empty(value: str) -> str:
    return '' if str(value or '').strip() else '不能为空'


def validate_single_token(value: str) -> str:
    text = str(value or '').strip()
    if not text:
        return '不能为空'
    if len([item for item in re.split(r'[,，;；\s]+', text) if item.strip()]) != 1:
        return '必须只填写一个值，不允许使用逗号、分号或空白分隔多个值'
    return ''


def validate_single_text(value: str) -> str:
    text = str(value or '').strip()
    if not text:
        return '不能为空'
    if len([item for item in re.split(r'[,，;；]+', text) if item.strip()]) != 1:
        return '必须只填写一个值，不允许使用逗号或分号分隔多个值'
    return ''


def validate_non_negative_int(value: str) -> str:
    text = str(value or '').strip()
    if not re.fullmatch(r'\d+', text):
        return '必须是非负整数'
    return ''


def validate_positive_int(value: str) -> str:
    text = str(value or '').strip()
    if not re.fullmatch(r'\d+', text) or int(text) <= 0:
        return '必须是正整数'
    return ''


def validate_literal_bool(value: str) -> str:
    return '' if str(value or '').strip().lower() in {'true', 'false'} else '必须是 true/false'


def is_truthy_flag_value(value: object) -> bool:
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def validate_private_cidr_csv(value: str) -> str:
    text = str(value or '').strip()
    if not text:
        return 'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS 必须提供至少一个 CIDR'
    payload, exit_code = normalize_source_cidrs(text)
    if exit_code == 0:
        return ''
    return '；'.join(str(item) for item in list(payload.get('issues') or [])) or '非法 CIDR'


def validate_enum(value: str, *, choices: list[str]) -> str:
    return '' if str(value or '').strip() in set(choices) else f'必须是以下取值之一：{", ".join(choices)}'


def validate_control_plane_profile_id(value: str) -> str:
    normalized = str(value or '').strip()
    if not normalized:
        return '不能为空'
    try:
        supported = available_control_plane_profile_ids(ROOT_DIR)
    except Exception as exc:
        return f'profile registry 不可读取：{exc}'
    if normalized not in supported:
        return f'未知 control-plane profile：{normalized}；支持值：{", ".join(supported)}'
    return ''


def validate_value(value: str, validator: dict[str, Any] | None) -> str:
    validator_payload = validator if isinstance(validator, dict) else {}
    validator_type = str(validator_payload.get('type') or '').strip()
    if validator_type == 'secret_like':
        return validate_secret_like(value, min_length=int(validator_payload.get('min_length') or 1))
    if validator_type == 'http_url':
        return validate_httpish_url(value, allow_query=True)
    if validator_type == 'image_ref':
        return validate_image_ref(value)
    if validator_type == 'private_ingress_bind_ip':
        return validate_private_ingress_bind_ip(value)
    if validator_type == 'tls_hostname':
        return validate_tls_hostname(value)
    if validator_type == 'non_empty':
        return validate_non_empty(value)
    if validator_type == 'single_token':
        return validate_single_token(value)
    if validator_type == 'single_text':
        return validate_single_text(value)
    if validator_type == 'non_negative_int':
        return validate_non_negative_int(value)
    if validator_type == 'positive_int':
        return validate_positive_int(value)
    if validator_type == 'private_cidr_csv':
        return validate_private_cidr_csv(value)
    if validator_type == 'literal_bool':
        return validate_literal_bool(value)
    if validator_type == 'enum':
        return validate_enum(value, choices=[str(item) for item in list(validator_payload.get('values') or []) if str(item).strip()])
    if validator_type == 'control_plane_profile_id':
        return validate_control_plane_profile_id(value)
    return ''


def field_requirement_reason(field: dict[str, Any], values: dict[str, str] | OrderedDict[str, str] | None = None) -> str:
    if field.get('required') is True:
        return 'always_required'
    conditional = field.get('conditional_required') if isinstance(field.get('conditional_required'), dict) else {}
    when = conditional.get('when') if isinstance(conditional.get('when'), dict) else {}
    key = str(when.get('key') or '').strip()
    expected = []
    if str(when.get('equals') or '').strip():
        expected.append(str(when.get('equals')).strip())
    expected.extend(str(item).strip() for item in list(when.get('equals_any') or []) if str(item).strip())
    actual = str((values or {}).get(key, '') or '').strip()
    if key and is_truthy_flag_value(when.get('truthy')) and is_truthy_flag_value(actual):
        return str(conditional.get('reason') or f'{key}=truthy')
    if key and expected and actual in expected:
        return str(conditional.get('reason') or f'{key}={actual}')
    requirement = field.get('required_if') if isinstance(field.get('required_if'), dict) else {}
    if not requirement:
        return ''
    key = str(requirement.get('key') or '').strip()
    expected = [str(item) for item in list(requirement.get('equals_any') or []) if str(item).strip()]
    actual = str((values or {}).get(key, '') or '').strip()
    if key and expected and actual in expected:
        return f'{key}={actual}'
    return ''


def field_is_required(field: dict[str, Any], values: dict[str, str] | OrderedDict[str, str] | None = None) -> bool:
    return bool(field_requirement_reason(field, values))


def deploy_env_required_map(
    values: dict[str, str] | OrderedDict[str, str] | None = None,
    *,
    schema: dict[str, Any] | None = None,
) -> dict[str, bool]:
    payload = schema if schema is not None else load_schema()
    result: dict[str, bool] = {}
    for field in payload.get('fields', []):
        if not isinstance(field, dict):
            continue
        key = str(field.get('key') or '').strip()
        if key:
            if values is None:
                result[key] = bool(field.get('manual_required') is True or field.get('required') is True)
            else:
                result[key] = bool(field.get('manual_required') is True or field_is_required(field, values))
    return result


def deploy_env_truthy_required_map(
    values: dict[str, str] | OrderedDict[str, str] | None = None,
    *,
    schema: dict[str, Any] | None = None,
) -> dict[str, bool]:
    payload = schema if schema is not None else load_schema()
    result: dict[str, bool] = {}
    for field in payload.get('fields', []):
        if not isinstance(field, dict):
            continue
        key = str(field.get('key') or '').strip()
        if not key:
            continue
        if field.get('truthy_required') is not True:
            result[key] = False
            continue
        if values is None:
            result[key] = bool(field.get('manual_required') is True or field.get('required') is True)
        else:
            result[key] = bool(field.get('manual_required') is True or field_is_required(field, values))
    return result


def conditional_required_fields(schema: dict[str, Any], values: dict[str, str] | OrderedDict[str, str] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for field in schema.get('fields', []):
        if not isinstance(field, dict):
            continue
        reason = field_requirement_reason(field, values)
        if not reason:
            continue
        rows.append({
            'key': str(field.get('key') or ''),
            'reason': reason,
        })
    return rows
