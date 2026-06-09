from __future__ import annotations

from typing import Any

from openclaw.docs.renderers.getting_started_support.loaders import ROOT_DIR, SECTIONS_PATH, fail, read_json, sorted_fields
from openclaw.lib.repo.static_truth import repo_contract_path, repo_contract_relpath
from openclaw.setup.flow import deploy_flow as deploy_flow_control_plane
from openclaw.setup.surface import deployment_baseline as deployment_baseline_surface


def render_code_block(lines: list[str]) -> list[str]:
    cleaned = [str(line).rstrip() for line in lines if str(line).strip()]
    return ['```bash', *cleaned, '```']


def render_powershell_block(lines: list[str]) -> list[str]:
    cleaned = [str(line).rstrip() for line in lines if str(line).strip()]
    return ['```powershell', *cleaned, '```']


def render_text_block(lines: list[str]) -> list[str]:
    cleaned = [str(line).rstrip() for line in lines if str(line).strip()]
    return ['```text', *cleaned, '```']


def render_bullets(items: list[str]) -> list[str]:
    return [f'- {str(item).strip()}' for item in items if str(item).strip()]


def render_numbered(items: list[str]) -> list[str]:
    return [f'{index}. {str(item).strip()}' for index, item in enumerate(items, start=1) if str(item).strip()]


def render_paragraphs(items: list[str]) -> list[str]:
    lines: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        lines.extend([text, ''])
    return lines


def render_step_sections(items: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get('title') or '').strip()
        commands = [str(command).rstrip() for command in list(item.get('commands') or []) if str(command).strip()]
        notes = [str(note).strip() for note in list(item.get('notes') or []) if str(note).strip()]
        if not title:
            continue
        lines.extend([f'## {title}', ''])
        if commands:
            lines.extend([*render_code_block(commands), ''])
        if notes:
            lines.extend([*render_bullets(notes), ''])
    return lines


def quickstart_step2_note_lines(schema: dict[str, Any]) -> list[str]:
    field_map = {str(field.get('key') or '').strip(): field for field in sorted_fields(schema)}
    required_keys = {
        'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS',
        'OPENCLAW_INGRESS_LISTEN_IP',
        'OPENCLAW_TLS_CN',
        'OPENCLAW_TLS_MODE',
        'OPENCLAW_INGRESS_BOUNDARY_MODE',
    }
    missing_keys = sorted(key for key in required_keys if key not in field_map)
    if missing_keys:
        fail(f"deploy_env schema 缺少第 2 步所需字段：{', '.join(missing_keys)}")
    notes = [
        '第 2 步只做三类动作：先补必填人工项，再复核第 1 步已回填的地址/主机名，最后只在切换模式时补条件字段。',
        '`OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS` 是第 2 步最关键的人工确认项，它描述“哪些来源可以访问 ingress”，必须按目标机最终看到的源地址网段填写，而不是目标机绑定地址。',
        '默认部署会在目标机本机执行 full test，因此来源 CIDR 必须同时包含访问端来源，以及 `OPENCLAW_INGRESS_LISTEN_IP` 对应的本机来源精确主机段。',
        '第 1 步会写入 `OPENCLAW_INGRESS_LISTEN_IP` 与 `OPENCLAW_TLS_CN`；第 2 步默认只复核两者是否分别等于真实对外监听私网 IP 与访问端实际使用的唯一主机名。只有浏览器与目标服务位于同一操作系统实例时，才允许把监听地址填写为 loopback。跨 OS / 跨网络实例访问不属于该例外。',
        '只有切到 `OPENCLAW_TLS_MODE=provided_files` 时，才补 `OPENCLAW_TLS_CERT_SOURCE_PATH` / `OPENCLAW_TLS_KEY_SOURCE_PATH`；只有切到 `OPENCLAW_INGRESS_BOUNDARY_MODE=external_acl` 时，才补 `OPENCLAW_INGRESS_BOUNDARY_EVIDENCE_PATH`。',
        '跨 OS / 跨网络实例访问时，`OPENCLAW_TLS_CN` 必须解析到目标机 ingress 地址，`OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS` 必须填写目标机实际看到的访问端源 IP `/32`、NAT 翻译后地址 `/32`，或确有必要时填写对应链路网段；同时保留目标机本机 full test 来源。',
    ]
    model_secret_keys = [
        key
        for key, field in field_map.items()
        if field.get('secret') is True
        and field.get('manual_required') is True
        and (
            str(field.get('group') or '') == 'model_providers'
            or '模型' in str(field.get('doc_summary') or '')
            or 'model' in key.lower()
        )
    ]
    if model_secret_keys:
        rendered_keys = '、'.join(f'`{key}`' for key in model_secret_keys)
        notes.insert(1, f'{rendered_keys} 是当前已启用模型作业的业务密钥；启用这类扩展时必须人工补齐，并且不得提交到 git。')
    return notes


def default_flow_steps(baseline: dict[str, Any]) -> list[dict[str, Any]]:
    return list(deployment_baseline_surface.default_flow_steps(baseline))


def deploy_stage_lines(sections: dict[str, Any]) -> list[str]:
    deploy_stage = dict(sections.get('deploy_stage') or {})
    online_options = {
        'mode': 'online',
        'releaseCheck': '1',
        'browserVerify': '1',
        'startServices': '1',
        'stage': '',
        'imageArchivePath': '',
    }
    offline_options = dict(online_options)
    offline_options['mode'] = 'offline'

    def stage_line(stage_id: str) -> str:
        label = str(deploy_flow_control_plane.stage_info(stage_id).get('explain_label') or stage_id).strip()
        return f'- `{stage_id}`：{label}'

    stage_flow = read_json(repo_contract_path('governance.deploy_stage_flow'))
    focus_stage_ids = [str(item).strip() for item in list(deploy_stage.get('focus_stage_ids') or []) if str(item).strip()]
    if not focus_stage_ids:
        fail(f'{SECTIONS_PATH.relative_to(ROOT_DIR)} -> quickstart.deploy_stage.focus_stage_ids 不能为空')
    post_resume_stage_ids = [
        str(item).strip()
        for item in list(deploy_stage.get('post_resume_stage_ids') or [])
        if str(item).strip()
    ]

    lines = [
        f"## {str(deploy_stage.get('title') or '部署阶段与 `--resume-from`').strip()}",
        '',
        str(deploy_stage.get('resume_intro') or '').strip(),
        '',
        str(deploy_stage.get('online_title') or '在线默认阶段：').strip(),
        '',
        *[stage_line(stage_id) for stage_id in deploy_flow_control_plane.effective_stages(online_options)],
        '',
        str(deploy_stage.get('offline_title') or '离线默认阶段：').strip(),
        '',
        *[stage_line(stage_id) for stage_id in deploy_flow_control_plane.effective_stages(offline_options)],
        '',
        str(deploy_stage.get('post_resume_title') or '后置 resume 阶段：').strip(),
        '',
        *[stage_line(stage_id) for stage_id in post_resume_stage_ids],
        '',
        *render_bullets([str(item).strip() for item in list(deploy_stage.get('resume_rules') or []) if str(item).strip()]),
        '',
        '### 关键阶段恢复执行命令',
        '',
        str(deploy_stage.get('command_section_intro') or '').strip().format(
            deploy_stage_flow_relpath=repo_contract_relpath('governance.deploy_stage_flow')
        ),
        '',
    ]
    for stage_id in focus_stage_ids:
        stage = dict((stage_flow.get('stages') or {}).get(stage_id) or {})
        commands = [str(command).rstrip() for command in list(stage.get('next_commands') or []) if str(command).strip()]
        if not commands:
            fail(f'deploy_stage_flow.stages.{stage_id}.next_commands 不能为空')
        lines.extend([
            f'#### `{stage_id}`',
            '',
            *render_code_block(commands),
            '',
        ])
    return lines
