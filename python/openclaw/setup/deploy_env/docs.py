#!/usr/bin/env python3
"""渲染部署输入说明，平台文档只展示平台字段和扩展中性入口。"""
from __future__ import annotations

import sys
from collections import OrderedDict
import json
from pathlib import Path
from typing import Any

from openclaw.docs.support.doc_targets import require_nested_str
from openclaw.lib.cli.examples import canonical_cli_command, host_wrapper_command
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.setup.deploy_env.support import (
    build_site_env_template_lines,
    field_requirement_reason,
    format_schema_text,
    load_schema,
    render_doc_detail_section,
)
from openclaw.setup.network.private_ingress import PRIVATE_INGRESS_BIND_IP_DOC

RENDER_DEPLOYMENT_INPUTS_CMD = canonical_cli_command('docs', 'deployment-inputs', 'render-deployment-inputs')
DEPLOY_ENV_VALIDATE_CMD = host_wrapper_command('setup', 'env', 'validate', '--env-file', 'deploy/.env')
DOC_SECTIONS_PATH = resolve_repo_root(Path(__file__)) / 'config' / 'deploy_env' / 'doc_sections.json'


def _note(message: str) -> None:
    """输出部署输入文档渲染过程提示。"""
    sys.stdout.write(f'[deploy_env_control_plane] {message}\n')


def _load_doc_sections() -> dict[str, Any]:
    """读取部署输入文档的固定章节文案配置。"""
    payload = json.loads(DOC_SECTIONS_PATH.read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise ValueError(f'{DOC_SECTIONS_PATH} 顶层必须为对象')
    return payload


def default_deployment_inputs_doc_path(schema: dict[str, Any], *, root_dir: Path) -> Path:
    """从 deploy env schema 中解析默认生成文档路径。"""
    rel_path = require_nested_str(schema, ['generated_artifacts', 'deployment_inputs_doc'], prefix='deploy_env_control_plane', label='deployment_inputs_doc')
    return root_dir / rel_path


def _append_grouped_keys(
    title: str,
    *,
    fields: list[dict[str, Any]],
    groups: dict[str, dict[str, Any]],
    predicate,
    values: dict[str, str] | None = None,
    include_doc_location: bool = False,
) -> list[str]:
    """按 schema group 输出字段列表，并可附带填写位置。"""
    rows = [title, '']
    current_group = None
    for schema_field in fields:
        if not predicate(schema_field):
            continue
        group_id = schema_field.get('group')
        if group_id != current_group:
            if current_group is not None and rows[-1] != '':
                rows.append('')
            current_group = group_id
            rows.extend([f"### {groups.get(group_id, {}).get('title') or group_id}", ''])
        summary = format_schema_text(schema_field.get('doc_summary'))
        reason = field_requirement_reason(schema_field, values) if values is not None else ''
        suffix = f'（{reason}）' if reason and not schema_field.get('required') else ''
        location = str(schema_field.get('doc_location') or '').strip()
        location_suffix = f'（填写位置：`{location}`）' if include_doc_location and location else ''
        if summary:
            rows.append(f"- `{schema_field['key']}`：{summary}{suffix}{location_suffix}")
        else:
            rows.append(f"- `{schema_field['key']}`{suffix}{location_suffix}")
    rows.extend([''])
    return rows


def build_deployment_inputs_doc(*, root_dir: Path, config_path: Path | None = None) -> tuple[dict[str, Any], str]:
    """构建部署输入文档内容，扩展私有字段只保留中性填写入口。"""
    schema = load_schema(config_path=config_path)
    doc_sections = _load_doc_sections()
    groups = {group['id']: group for group in schema.get('groups', [])}
    fields = sorted(
        schema.get('fields', []),
        key=lambda row: (
            groups.get(row.get('group'), {}).get('doc_order', 999),
            row.get('doc_order', 999),
            str(row.get('key')),
        ),
    )
    example_values: OrderedDict[str, str] = OrderedDict()
    for field_row in fields:
        default_kind = field_row.get('default_kind')
        if default_kind == 'literal':
            example_values[str(field_row.get('key') or '')] = str(field_row.get('default') or '')
        elif default_kind == 'placeholder':
            example_values[str(field_row.get('key') or '')] = str(field_row.get('placeholder') or '__REQUIRED__')
        else:
            example_values[str(field_row.get('key') or '')] = ''

    platform_fields = [field_row for field_row in fields if not str(field_row.get('extensionId') or '').strip()]
    deploy_site_fields = [field_row for field_row in platform_fields if str(field_row.get('doc_location') or '').strip() == 'deploy/site.env']
    extension_fields = [field_row for field_row in fields if str(field_row.get('extensionId') or '').strip()]
    extension_env_locations = sorted({
        str(field_row.get('doc_location') or '').strip()
        for field_row in extension_fields
        if str(field_row.get('doc_location') or '').strip()
    })
    extension_secret_fields = [
        field_row
        for field_row in fields
        if field_row.get('manual_required') is True and str((field_row.get('validator') or {}).get('type') or '').strip() == 'secret_like'
    ]
    provider_source_fields = [
        field_row
        for field_row in platform_fields
        if str((field_row.get('validator') or {}).get('type') or '').strip() == 'http_url'
    ]
    deploy_site_secret_fields = [
        field_row
        for field_row in deploy_site_fields
        if field_row.get('manual_required') is True and str((field_row.get('validator') or {}).get('type') or '').strip() == 'secret_like'
    ]
    extension_secret_keys = [str(field_row.get('key') or '').strip() for field_row in extension_secret_fields if str(field_row.get('key') or '').strip()]
    deploy_site_secret_keys = [str(field_row.get('key') or '').strip() for field_row in deploy_site_secret_fields if str(field_row.get('key') or '').strip()]
    provider_source_keys = [str(field_row.get('key') or '').strip() for field_row in provider_source_fields if str(field_row.get('key') or '').strip()]
    manual_detail_fields = [
        field_row
        for field_row in platform_fields
        if (field_row.get('manual_required') is True or field_row.get('doc_details_always') is True) and field_row.get('doc_details')
    ]

    minimal_example: list[str] = []
    for secret_key in deploy_site_secret_keys:
        minimal_example.append(f'{secret_key}=<启用扩展要求的业务/API 密钥>')
    minimal_example.extend([
        'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS=<目标机实际看到的访问端来源 CIDR>,<目标机本机 full test 来源 CIDR>',
        'OPENCLAW_INGRESS_LISTEN_IP=<目标机 private ingress 绑定私网或 loopback IP>',
        'OPENCLAW_TLS_CN=<访问端真实使用的唯一主机名>',
        'OPENCLAW_TLS_MODE=self_signed',
        'OPENCLAW_INGRESS_BOUNDARY_MODE=host_firewall',
    ])
    conditional_example = [
        'OPENCLAW_TLS_MODE=provided_files',
        'OPENCLAW_TLS_CERT_SOURCE_PATH=<目标机可读取的证书路径>',
        'OPENCLAW_TLS_KEY_SOURCE_PATH=<目标机可读取的私钥路径>',
        'OPENCLAW_INGRESS_BOUNDARY_MODE=external_acl',
        'OPENCLAW_INGRESS_BOUNDARY_EVIDENCE_PATH=<external_acl 结构化 JSON 证据路径>',
    ]
    step2_inputs = [*deploy_site_secret_keys, 'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS']
    provider_source_refs = '、'.join(f'`{key}`' for key in provider_source_keys)
    model_required_note = (
        '启用的扩展声明了扩展内部业务/API 密钥；具体键名以对应扩展 README、deploy env schema 与 extension.env.example 为准。'
        if extension_secret_keys
        else '正式默认运行配置不要求额外模型/API provider 密钥。'
    )
    provider_source_note = (
        f'{provider_source_refs} 是当前 active profile 暴露的模型/API HTTP 入口字段；填写位置和校验以 deploy env schema 为准，不属于 runtime image source strategy。'
        if provider_source_keys
        else '模型/API provider 输入只有在当前 active profile 的 deploy env schema 明确声明时才可填写；未声明时不要向 deploy/site.env 增加额外 provider 字段。'
    )
    extension_env_commands: list[str] = []
    rendered_extension_env_locations: set[str] = set()
    for location in extension_env_locations:
        if location.endswith('/extension.env'):
            if location.startswith('agent/extensions/') and '/deploy/extension.env' in location:
                location = 'agent/extensions/<extension-id>/deploy/extension.env'
            if location in rendered_extension_env_locations:
                continue
            rendered_extension_env_locations.add(location)
            extension_env_commands.extend([
                f'cp {location}.example {location}',
                f'vim {location}',
            ])
    lines = [
        '# 部署输入说明',
        '',
        '本文用于补齐 `deploy/site.env`、启用扩展时的扩展内部 `agent/extensions/<extension-id>/deploy/extension.env`，并说明 `deploy/.env` 中自动生成字段的来源。默认路径是 `self_signed + host_firewall`；切换 `provided_files` 或 `external_acl` 时，仅填写对应条件字段。',
        '',
        model_required_note,
        '',
        '## 最小填写片段',
        '',
        str(doc_sections.get('minimal_example_intro') or '').strip(),
        '',
        '```text',
        *minimal_example,
        '```',
        '',
        str(doc_sections.get('conditional_example_intro') or '').strip(),
        '',
        '```text',
        *conditional_example,
        '```',
        '',
        '## 推荐填写顺序',
        '',
        '1. 先初始化 private ingress；需要指定访问端平台、目标机地址或主机名时使用第二条命令。',
        '',
        '```bash',
        'bash ./scripts/setup/init_private_ingress.sh',
        'bash ./scripts/setup/init_private_ingress.sh --platform windows -- 192.168.50.10 openclaw.internal.example',
        '```',
        '',
        '2. 打开 `deploy/site.env`，按“第 2 步最小闭环”和下方字段说明补齐平台输入；启用扩展时，扩展字段只写入对应扩展内部 `agent/extensions/<extension-id>/deploy/extension.env`。',
        '',
        '```bash',
        'vim deploy/site.env',
        *extension_env_commands,
        '```',
        '',
        '## 第 2 步最小闭环',
        '',
        *[
            f'{index}. {str(item).strip().format(step2_inputs="`" + "` 与 `".join(step2_inputs) + "`")}'
            for index, item in enumerate(list(doc_sections.get('step2_closure') or []), start=1)
            if str(item).strip()
        ],
        '',
        '## 跨 OS / 跨网络实例访问场景',
        '',
        *[f'- {str(item).strip()}' for item in list(doc_sections.get('cross_instance_access_scenario') or []) if str(item).strip()],
        '',
        '```text',
        'OPENCLAW_INGRESS_LISTEN_IP=<目标机对访问端可达的私网 IP>',
        'OPENCLAW_TLS_CN=<访问端用于访问目标机的唯一主机名>',
        'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS=<目标机实际看到的访问端源 IP>/32,<OPENCLAW_INGRESS_LISTEN_IP>/32',
        '```',
        '',
        '## 公网来源经上游 ACL 接入',
        '',
        *[f'- {str(item).strip()}' for item in list(doc_sections.get('public_upstream_acl_access') or []) if str(item).strip()],
        '',
        '```text',
        'OPENCLAW_INGRESS_BOUNDARY_MODE=external_acl',
        'OPENCLAW_INGRESS_LISTEN_IP=<目标机私网IP>',
        'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS=<目标机自检IP/32>,<上游设备私网IP/32或私网段>',
        'OPENCLAW_INGRESS_BOUNDARY_EVIDENCE_PATH=<目标机可读取的external_acl证据JSON>',
        '```',
        '',
        *_append_grouped_keys('## 需要人工填写', fields=platform_fields, groups=groups, predicate=lambda schema_field: schema_field.get('required') and schema_field.get('manual_required') is True, include_doc_location=True),
        *_append_grouped_keys('## 条件必填', fields=platform_fields, groups=groups, predicate=lambda schema_field: (not schema_field.get('required')) and bool(schema_field.get('conditional_required')), values=example_values, include_doc_location=True),
        *_append_grouped_keys('## 自动生成或自动推导', fields=platform_fields, groups=groups, predicate=lambda schema_field: schema_field.get('required') and schema_field.get('manual_required') is not True),
        '## 关键字段说明',
        '',
    ]
    for field in manual_detail_fields:
        lines.extend(render_doc_detail_section(field))

    input_constraints = [
        '## 输入约束',
        '',
    ]
    input_constraints.extend(
        [
            f'{index}. {str(item).strip().format(provider_source_note=provider_source_note, private_ingress_bind_ip_doc=PRIVATE_INGRESS_BIND_IP_DOC)}'
            for index, item in enumerate(list(doc_sections.get('input_constraints') or []), start=1)
            if str(item).strip()
        ]
    )
    input_constraints.append('')
    lines.extend(input_constraints)
    lines.extend([
        '## 填写完成后的下一步',
        '',
        '```bash',
        'bash ./scripts/setup/one_click_config.sh',
        DEPLOY_ENV_VALIDATE_CMD,
        'sudo bash ./scripts/setup/apply_ingress_boundary_rules.sh --env-file deploy/.env',
        'sudo bash ./scripts/doctor/check_ingress_boundary_evidence.sh --env-file deploy/.env',
        'bash ./scripts/setup/one_click_test_basic.sh',
        '```',
        '',
        '## 正式认证路径',
        '',
        *[f'- {str(item).strip()}' for item in list(doc_sections.get('auth_path') or []) if str(item).strip()],
        '',
    ])
    return schema, '\n'.join(lines)


def render_deployment_inputs_doc(
    output_path: Path,
    *,
    root_dir: Path,
    mode: str = 'write',
    config_path: Path | None = None,
) -> int:
    """按 write/check/stdout 模式渲染或校验部署输入文档。"""
    _, content = build_deployment_inputs_doc(root_dir=root_dir, config_path=config_path)
    existing = output_path.read_text(encoding='utf-8') if output_path.exists() else None
    if mode == 'stdout':
        sys.stdout.write(content)
        return 0 if existing == content else 1
    if mode == 'check':
        if existing == content:
            _note(f'deployment inputs 文档已同步：{output_path}')
            return 0
        sys.stderr.write(f'[deploy_env_control_plane][DRIFT] deployment inputs 文档未同步：{output_path}\n')
        return 1
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding='utf-8', newline='\n')
    _note(f'已生成：{output_path}')
    return 0


def render_site_env_example(
    output_path: Path,
    *,
    mode: str = 'write',
) -> int:
    """渲染或校验 `deploy/site.env.example`。

    参数：
        output_path: 要写入、校验或作为同步目标比对的模板路径。
        mode: `write` 写入文件，`check` 只读比对，`stdout` 只打印生成内容。
    返回：
        同步或写入成功返回 0；`check` 或 `stdout` 模式发现目标文件漂移时返回 1。
    副作用：
        `write` 模式会创建父目录并以 UTF-8/LF 写出模板；`stdout` 模式会输出到标准输出。
    失败：
        模板真源不可读取、目标文件不可读写或路径权限不足时抛出底层异常。
    """
    content = '\n'.join(build_site_env_template_lines()).rstrip() + '\n'
    existing = output_path.read_text(encoding='utf-8') if output_path.exists() else None
    if mode == 'stdout':
        sys.stdout.write(content)
        return 0 if existing == content else 1
    if mode == 'check':
        if existing == content:
            _note(f'site.env.example 已同步：{output_path}')
            return 0
        sys.stderr.write(f'[deploy_env_control_plane][DRIFT] site.env.example 未同步：{output_path}\n')
        return 1
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding='utf-8', newline='\n')
    _note(f'已生成：{output_path}')
    return 0
