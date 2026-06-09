from __future__ import annotations

from typing import Any, Callable

from openclaw.docs.renderers.getting_started_support.fragments import (
    deploy_stage_lines,
    quickstart_step2_note_lines,
    render_bullets,
    render_code_block,
    render_numbered,
    render_paragraphs,
    render_powershell_block,
    render_step_sections,
)
from openclaw.docs.renderers.getting_started_support.loaders import load_sections
from openclaw.docs.support.doc_targets import require_nested_str
from openclaw.docs.support.markdown_tables import format_markdown_tables
from openclaw.setup.network.private_ingress import PRIVATE_INGRESS_BIND_IP_DOC
from openclaw.setup.surface import (
    control_plane_medium as control_plane_medium_surface,
    deployment_baseline as deployment_baseline_surface,
    entrypoint as entrypoint_surface,
)

DEFAULT_DEPLOY_REPO_DIR = '/opt/openclaw/clawctl'
DEFAULT_DEPLOY_USER_SWITCH_COMMAND = 'sudo -iu openclaw'


def _deploy_user_block(commands: list[str]) -> list[str]:
    cleaned = [str(command).rstrip() for command in commands if str(command).strip()]
    if not cleaned:
        return []
    return [
        "sudo runuser -u openclaw -- bash -lc '",
        "set -euo pipefail",
        f"cd {DEFAULT_DEPLOY_REPO_DIR}",
        *cleaned,
        "'",
    ]


def _browser_access_section_lines(quickstart: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for section in list(quickstart.get('browser_access_sections') or []):
        if not isinstance(section, dict):
            continue
        title = str(section.get('title') or '').strip()
        if not title:
            continue
        lines.extend([f'#### {title}', ''])
        paragraphs = [str(item).strip() for item in list(section.get('paragraphs') or []) if str(item).strip()]
        if paragraphs:
            lines.extend(render_paragraphs(paragraphs))
        for block in list(section.get('command_blocks') or []):
            if not isinstance(block, dict):
                continue
            intro = str(block.get('intro') or '').strip()
            commands = [str(command).rstrip() for command in list(block.get('commands') or []) if str(command).strip()]
            if not commands:
                continue
            if intro:
                lines.extend([intro, ''])
            language = str(block.get('language') or 'bash').strip().lower()
            if language == 'powershell':
                lines.extend([*render_powershell_block(commands), ''])
            else:
                lines.extend([*render_code_block(commands), ''])
        notes = [str(item).strip() for item in list(section.get('notes') or []) if str(item).strip()]
        if notes:
            lines.extend([*render_bullets(notes), ''])
    return lines


def _split_deployment_flow_commands(baseline: dict[str, Any], *, mode: str) -> tuple[list[str], list[str], list[str]]:
    commands = deployment_baseline_surface.default_commands(baseline, mode=mode)
    deploy_user_before_root: list[str] = []
    root_commands: list[str] = []
    deploy_user_after_root: list[str] = []
    seen_root_step = False
    for command in commands:
        if 'apply_ingress_boundary_rules.sh' in command:
            root_commands.append(command)
            seen_root_step = True
            continue
        if seen_root_step:
            deploy_user_after_root.append(command)
        else:
            deploy_user_before_root.append(command)
    return deploy_user_before_root, root_commands, deploy_user_after_root


def _deployment_flow_commands(baseline: dict[str, Any], *, mode: str) -> list[str]:
    deploy_user_before_root, root_commands, deploy_user_after_root = _split_deployment_flow_commands(baseline, mode=mode)
    rendered: list[str] = []
    rendered.extend(_deploy_user_block(deploy_user_before_root))
    if root_commands:
        rendered.append(f'cd {DEFAULT_DEPLOY_REPO_DIR}')
        rendered.extend(root_commands)
    rendered.extend(_deploy_user_block(deploy_user_after_root))
    return rendered


def quickstart_doc(
    surface: dict[str, Any],
    sections: dict[str, Any],
    schema: dict[str, Any],
    baseline: dict[str, Any],
    control_plane_medium: dict[str, Any],
    _setup_entrypoints: dict[str, Any],
    _testing_manifest: dict[str, Any],
    *,
    quickstart_notice: str,
) -> str:
    common = surface.get('common') or {}
    quickstart = surface.get('quickstart') or {}
    quickstart_sections = dict(sections.get('quickstart') or {})
    examples = common.get('private_ingress_examples') or {}
    listen_ip = str(examples.get('listen_ip') or '192.168.50.10').strip()
    tls_cn = str(examples.get('tls_cn') or 'openclaw.internal.example').strip()

    online_chain = [
        'sudo bash ./scripts/setup/prepare_docker_host.sh --all',
        f'sudo bash ./scripts/setup/prepare_deploy_user.sh --user openclaw --repo-dir {DEFAULT_DEPLOY_REPO_DIR}',
        *_deploy_user_block([
            'bash ./scripts/doctor/check_docker_host_readiness.sh',
            'bash ./scripts/setup/init_private_ingress.sh',
            'vim deploy/site.env',
            '# 启用扩展时，使用 check_extension_env_values.sh / apply_extension_env_values.sh 补齐 extension.env',
            control_plane_medium_surface.mode_command(control_plane_medium, mode='online'),
        ]),
        *_deployment_flow_commands(baseline, mode='default'),
    ]
    offline_chain_existing_runtime = [
        'sudo bash ./scripts/setup/prepare_docker_host.sh --configure-kernel --configure-daemon --open-firewall',
        f'sudo bash ./scripts/setup/prepare_deploy_user.sh --user openclaw --repo-dir {DEFAULT_DEPLOY_REPO_DIR}',
        *_deploy_user_block([
            'bash ./scripts/doctor/check_docker_host_readiness.sh --offline',
            'bash ./scripts/setup/init_private_ingress.sh',
            'vim deploy/site.env',
            '# 启用扩展时，使用 check_extension_env_values.sh / apply_extension_env_values.sh 补齐 extension.env',
            control_plane_medium_surface.mode_command(control_plane_medium, mode='offline'),
        ]),
        *_deployment_flow_commands(baseline, mode='offline'),
    ]

    private_ingress_init_commands = [str(command).strip() for command in list(common.get('private_ingress_init_commands') or []) if str(command).strip()]
    private_ingress_init_notes = [str(note).strip() for note in list(common.get('private_ingress_init_notes') or []) if str(note).strip()]
    private_ingress_access_side_notes = [str(note).strip() for note in list(quickstart.get('private_ingress_access_side_notes') or []) if str(note).strip()]
    remote_first_install = dict(quickstart.get('remote_first_install') or {})
    config_validation_commands = [str(command).rstrip() for command in list(quickstart.get('config_validation_commands') or []) if str(command).strip()]
    if not any('check_ingress_boundary_evidence.sh --env-file deploy/.env' in command for command in config_validation_commands):
        config_validation_commands.append('sudo bash ./scripts/doctor/check_ingress_boundary_evidence.sh --env-file deploy/.env')
    step2_focus_notes = quickstart_step2_note_lines(schema)
    step_labels = {str(key): str(value) for key, value in dict(quickstart.get('default_step_labels') or {}).items()}
    deployment_identity_commands = [str(command).rstrip() for command in list(quickstart.get('deployment_identity_commands') or []) if str(command).strip()]
    deployment_identity_notes = [str(item).strip() for item in list(quickstart.get('deployment_identity_notes') or []) if str(item).strip()]
    execution_switch_rules = [str(item).strip() for item in list(quickstart.get('execution_switch_rules') or []) if str(item).strip()]
    deployment_checklist = [str(item).strip() for item in list(quickstart.get('deployment_checklist') or []) if str(item).strip()]
    online_network_profile_commands = [str(command).rstrip() for command in list(quickstart.get('online_network_profile_commands') or []) if str(command).strip()]
    online_network_profile_notes = [str(item).strip() for item in list(quickstart.get('online_network_profile_notes') or []) if str(item).strip()]
    verify_commands_by_mode: dict[str, list[str]] = {}
    for mode, commands in dict(quickstart.get('verify_commands_by_mode') or {}).items():
        mode_text = str(mode).strip()
        command_lines = [str(command).rstrip() for command in list(commands or []) if str(command).strip()]
        if mode_text and command_lines:
            verify_commands_by_mode[mode_text] = command_lines
    verify_notes = [str(item).strip() for item in list(quickstart.get('verify_notes') or []) if str(item).strip()]

    deployment_identity_lines: list[str] = []
    if deployment_identity_commands or deployment_identity_notes:
        deployment_identity_lines.extend(['## 部署用户与权限边界', ''])
        if deployment_identity_commands:
            deployment_identity_lines.extend(['固定部署用户示例：', '', *render_code_block(deployment_identity_commands), ''])
        if deployment_identity_notes:
            deployment_identity_lines.extend([*render_bullets(deployment_identity_notes), ''])

    execution_switch_lines: list[str] = []
    if execution_switch_rules:
        execution_switch_lines.extend(['## 执行位置切换规则', '', *render_bullets(execution_switch_rules), ''])

    deployment_checklist_lines: list[str] = []
    if deployment_checklist:
        deployment_checklist_lines.extend(['## 部署前检查清单', '', *render_bullets(deployment_checklist), ''])

    remote_first_install_lines: list[str] = []
    if remote_first_install:
        remote_title = str(remote_first_install.get('title') or '远程首装向导').strip()
        remote_summary = str(remote_first_install.get('summary') or '').strip()
        remote_commands = [str(command).rstrip() for command in list(remote_first_install.get('commands') or []) if str(command).strip()]
        remote_notes = [str(note).strip() for note in list(remote_first_install.get('notes') or []) if str(note).strip()]
        remote_first_install_lines.extend([f'### {remote_title}', ''])
        if remote_summary:
            remote_first_install_lines.extend([remote_summary, ''])
        if remote_commands:
            remote_first_install_lines.extend([*render_code_block(remote_commands), ''])
        if remote_notes:
            remote_first_install_lines.extend([*render_bullets(remote_notes), ''])

    verify_lines: list[str] = []
    if verify_commands_by_mode:
        verify_lines.extend(['访问端 HTTPS 验证命令：', ''])
        for mode, commands in verify_commands_by_mode.items():
            verify_lines.extend([f'#### `{mode}`', '', *render_code_block(commands), ''])
        verify_lines.extend(_browser_access_section_lines(quickstart))
        if verify_notes:
            verify_lines.extend([*render_bullets(verify_notes), ''])

    lines: list[str] = [
        f"# {str(quickstart.get('title') or 'Quickstart').strip()}",
        '',
    ]
    if quickstart_notice.strip():
        lines.extend([quickstart_notice.strip(), ''])
    lines.extend([
        '## 本页解决什么问题',
        '',
        *render_paragraphs(list(quickstart_sections.get('problem_paragraphs') or [])),
        '## 适用范围',
        '',
        str(quickstart.get('intro') or '').strip(),
        '',
        *render_bullets(list(quickstart_sections.get('scope_links') or [])),
        '',
        '## 执行角色',
        '',
        *render_bullets([str(item).strip() for item in list(quickstart.get('execution_roles') or []) if str(item).strip()]),
        '',
        *deployment_identity_lines,
        *execution_switch_lines,
        '## 最短路径',
        '',
        *remote_first_install_lines,
        '### 在线首轮部署（目标机）',
        '',
        *render_code_block(online_chain),
        '',
        *render_bullets(online_network_profile_notes),
        *([''] if online_network_profile_notes and online_network_profile_commands else []),
        *(['中国国内网络首轮部署时，固定使用：', '', *render_code_block(online_network_profile_commands), ''] if online_network_profile_commands else []),
        '### 离线首轮部署（目标机，已安装 Docker / Compose 且已具备本地镜像归档）',
        '',
        *render_code_block(offline_chain_existing_runtime),
        '',
        '- 离线新机尚未安装 Docker / Compose 时，先按 `environment-setup.md` 挂载本地 RPM / YUM 源并完成 Docker / Compose 安装；不要把 `--offline` 当作安装路径。',
        '- 访问端动作只出现在第 1 步与第 6 步；其余默认命令都在目标机执行。',
        '',
        *deployment_checklist_lines,
        '## 正式步骤',
        '',
        '### 第 0 步：完成宿主机准备与 readiness 准入',
        '',
        '- 固定入口：`sudo bash ./scripts/setup/prepare_docker_host.sh --all`；中国国内网络首轮部署使用 `sudo bash ./scripts/setup/prepare_docker_host.sh --all --network-profile cn`。',
        '- 固定部署用户交接：`sudo bash ./scripts/setup/prepare_deploy_user.sh --user openclaw --repo-dir /opt/openclaw/clawctl`，随后用 `sudo -iu openclaw` 切换到该用户继续主链。',
        '- 固定准入：`bash ./scripts/doctor/check_docker_host_readiness.sh`',
        '- 详细命令、CentOS 7 仓库修复与离线分支统一查看 `environment-setup.md`。',
        '',
        '### 第 1 步：确定 private ingress 地址与访问端解析',
        '',
        f'- 目标机默认把 `OPENCLAW_INGRESS_LISTEN_IP` 选为 `hostname -I` 中的目标私网 IPv4，例如 `{listen_ip}`；也可手工使用 ULA/loopback IPv6。只有浏览器与目标服务位于同一操作系统实例时，才允许手工使用 loopback。跨 OS / 跨网络实例访问不属于该例外。',
        f'- 访问主机名 `OPENCLAW_TLS_CN` 必须解析到该地址，例如 `{tls_cn}`。',
        '- 先在目标机执行统一初始化命令，再在访问端判断是否已有可用 DNS；没有就写 hosts。',
        '',
        '目标机固定初始化命令：',
        '',
        *render_code_block(private_ingress_init_commands),
        '',
        *render_bullets(private_ingress_init_notes),
        *render_bullets(private_ingress_access_side_notes),
        '',
        '访问端 DNS / hosts 处理固定使用第 1 步初始化命令打印的平台化指令；需要手工核对时使用当前平台的事实命令：',
        '',
        '- Linux：`getent hosts "$OPENCLAW_TLS_CN"`，DNS 旁路检查可用 `nslookup` 或 `dig +short`。',
        '- macOS：`dscacheutil -q host -a name "$OPENCLAW_TLS_CN"`，改写 hosts 后刷新 DNS 缓存。',
        '- Windows PowerShell：管理员窗口写 hosts；核对 hosts 目标记录与 `ping $OpenClawTlsCn`，`Resolve-DnsName` 只验证 DNS。',
        '',
        '若访问端没有内网 DNS，使用 `init_private_ingress.sh --platform <windows|linux|macos|all> -- <listen_ip> <tls_cn>` 打印的 hosts 改写块；该块会备份 hosts、删除已有映射、写入目标映射并打印回滚命令。',
        '',
        '### 第 2 步：填写部署输入并生成 `deploy/.env`',
        '',
        *render_code_block([
            'vim deploy/site.env',
            '# 启用扩展时，使用 check_extension_env_values.sh / apply_extension_env_values.sh 补齐 extension.env',
            control_plane_medium_surface.mode_command(control_plane_medium, mode='online'),
            'bash ./scripts/setup/one_click_config.sh',
        ]),
        '',
        *render_bullets(list(quickstart_sections.get('step2_static_notes') or [])),
        *render_bullets(step2_focus_notes),
        '',
        '### 第 3 步：确认配置生成结果可进入 basic gate',
        '',
        *render_code_block(config_validation_commands),
        '',
        *render_bullets(list(quickstart_sections.get('config_validation_notes') or [])),
        '',
        f"### 第 4 步：{step_labels.get('one_click_test_basic', '执行基础测试门禁')}",
        '',
        *render_code_block(['bash ./scripts/setup/one_click_test_basic.sh']),
        '',
        *render_bullets(list(quickstart_sections.get('basic_gate_notes') or [])),
        '',
        f"### 第 5 步：{step_labels.get('one_click_deploy', '执行正式部署与自动验收闭环')}",
        '',
        *render_code_block([
            'bash ./scripts/setup/one_click_deploy.sh',
        ]),
        '',
        *render_bullets(list(quickstart_sections.get('deploy_full_test_notes') or [])),
        '',
        '### 第 6 步：人工补充核对与浏览器首连',
        '',
        *render_bullets(list(quickstart_sections.get('manual_acceptance_notes') or [])),
        '',
        *verify_lines,
        '- `../operations/runtime-service-reference.md#deployment-acceptance-default-flow`',
        '- `../operations/runtime-service-reference.md#deployment-acceptance-pass-criteria`',
        '- `../operations/runtime-service-reference.md#deployment-acceptance-artifacts`',
        '',
        '若首次访问出现 `disconnected (1008): pairing required`，按下面顺序在目标机批准一次设备：',
        '',
        *render_code_block([str(item).strip() for item in list(quickstart.get('pairing_commands') or []) if str(item).strip()]),
        '',
        '批准完成后回到访问端刷新页面；这属于真实首连动作，不视为部署失败。',
        '',
        '### 第 7 步：导出最终交付包',
        '',
        *render_code_block([str(item).strip() for item in list(quickstart.get('export_commands') or []) if str(item).strip()]),
        '',
        *render_bullets([str(item).strip() for item in list(quickstart.get('export_notes') or []) if str(item).strip()]),
        '',
        *deploy_stage_lines(quickstart_sections),
        '## 通过判据',
        '',
        *render_numbered([str(item).strip() for item in list(quickstart.get('manual_checks') or []) if str(item).strip()]),
        '',
        '## 失败分流',
        '',
        '| 当前现象 | 先跳哪里 |',
        '| --- | --- |',
        *[
            f'| {str(item.get("symptom") or "").strip()} | {str(item.get("route") or "").strip()} |'
            for item in list(quickstart_sections.get('failure_matrix') or [])
            if isinstance(item, dict) and str(item.get('symptom') or '').strip() and str(item.get('route') or '').strip()
        ],
        '',
        '## 下一步',
        '',
        *render_bullets(list(quickstart_sections.get('next_steps') or [])),
        '',
    ])
    return '\n'.join(lines)


def environment_setup_doc(
    surface: dict[str, Any],
    sections: dict[str, Any],
    schema: dict[str, Any],
    control_plane_medium: dict[str, Any],
    *,
    env_notice: str,
    ingress_manual_fields: Callable[[dict[str, Any]], dict[str, dict[str, Any]]],
) -> str:
    common = surface.get('common') or {}
    env_setup = surface.get('environment_setup') or {}
    env_sections = dict(sections.get('environment_setup') or {})
    examples = common.get('private_ingress_examples') or {}
    listen_ip = str(examples.get('listen_ip') or '<hostname -I 首个私网 IPv4>').strip()
    tls_cn = str(examples.get('tls_cn') or 'openclaw.internal.example').strip()
    ingress_fields = ingress_manual_fields(schema)
    host_prepare_entrypoint = dict(env_setup.get('host_prepare_entrypoint') or {})
    host_readiness_entrypoint = dict(env_setup.get('host_readiness_entrypoint') or {})

    lines: list[str] = [
        f"# {str(env_setup.get('title') or '基础环境准备').strip()}",
        '',
    ]
    if env_notice.strip():
        lines.extend([env_notice.strip(), ''])
    lines.extend([
        str(env_setup.get('intro') or '').strip(),
        '',
        '## 完成标准',
        '',
        '完成本页后，应同时满足：',
        '',
        *[f'{index}. {str(item).strip()}' for index, item in enumerate(list(env_setup.get('completion_standard') or []), start=1)],
        '',
        '## 运行面与网络边界',
        '',
        *render_bullets(list(env_setup.get('runtime_boundary') or [])),
        '',
        f'- `OPENCLAW_INGRESS_LISTEN_IP` {PRIVATE_INGRESS_BIND_IP_DOC}',
        '',
        '## 宿主机至少应具备的工具',
        '',
        *render_code_block(list(common.get('tool_commands') or [])),
        '',
    ])
    if host_prepare_entrypoint:
        summary = str(host_prepare_entrypoint.get('summary') or '').strip()
        commands = [str(command).rstrip() for command in list(host_prepare_entrypoint.get('commands') or []) if str(command).strip()]
        notes = [str(note).strip() for note in list(host_prepare_entrypoint.get('notes') or []) if str(note).strip()]
        lines.extend(['## 固定宿主机准备入口', ''])
        if summary:
            lines.extend([summary, ''])
        if commands:
            lines.extend([*render_code_block(commands), ''])
        if notes:
            lines.extend([*render_bullets(notes), ''])
    if host_readiness_entrypoint:
        summary = str(host_readiness_entrypoint.get('summary') or '').strip()
        commands = [str(command).rstrip() for command in list(host_readiness_entrypoint.get('commands') or []) if str(command).strip()]
        notes = [str(note).strip() for note in list(host_readiness_entrypoint.get('notes') or []) if str(note).strip()]
        lines.extend(['## 固定宿主机 readiness 准入入口', ''])
        if summary:
            lines.extend([summary, ''])
        if commands:
            lines.extend([*render_code_block(commands), ''])
        if notes:
            lines.extend([*render_bullets(notes), ''])
    lines.extend([
        '## host 控制面执行介质前置步骤',
        '',
        *render_paragraphs(list(env_sections.get('host_control_plane_intro') or [])),
        '',
        '### 在线目标机',
        '',
        *render_code_block([control_plane_medium_surface.mode_command(control_plane_medium, mode='online')]),
        '',
        *render_bullets(control_plane_medium_surface.mode_steps(control_plane_medium, mode='online')),
        '',
        '### 离线目标机',
        '',
        *render_code_block([control_plane_medium_surface.mode_command(control_plane_medium, mode='offline')]),
        '',
        *render_bullets(control_plane_medium_surface.mode_steps(control_plane_medium, mode='offline')),
        '',
        '## 帮助面与执行面边界',
        '',
        *render_bullets([str(item).strip() for item in list(entrypoint_surface.help_surface_contract().get('guarantees') or []) if str(item).strip()]),
        '',
        '固定帮助入口：',
        '',
        *render_code_block([str(item).strip() for item in list(entrypoint_surface.help_surface_contract().get('command_examples') or []) if str(item).strip()]),
        '',
        '## TLS 证书模式',
        '',
    ])
    for item in list(env_setup.get('tls_modes') or []):
        mode = str((item or {}).get('mode') or '').strip()
        summary = str((item or {}).get('summary') or '').strip()
        if not mode or not summary:
            continue
        lines.extend([f'### `{mode}`', '', summary, ''])
        notes = [str(note).strip() for note in list((item or {}).get('notes') or []) if str(note).strip()]
        if notes:
            lines.extend([*render_bullets(notes), ''])
    lines.extend(render_step_sections(list(env_setup.get('step_sections') or [])))
    lines.extend([
        '## private ingress 人工输入口径',
        '',
        '固定初始化命令：',
        '',
        *render_code_block([str(item).strip() for item in list(common.get('private_ingress_init_commands') or []) if str(item).strip()]),
        '',
        *render_bullets([str(item).strip() for item in list(common.get('private_ingress_init_notes') or []) if str(item).strip()]),
        '',
        str(env_sections.get('private_ingress_listen_ip_template') or '').strip().format(
            ingress_listen_ip_field=str((ingress_fields.get("OPENCLAW_INGRESS_LISTEN_IP") or {}).get("key") or "OPENCLAW_INGRESS_LISTEN_IP"),
            listen_ip=listen_ip,
        ),
        '',
        str(env_sections.get('private_ingress_tls_cn_template') or '').strip().format(
            tls_cn_field=str((ingress_fields.get("OPENCLAW_TLS_CN") or {}).get("key") or "OPENCLAW_TLS_CN"),
            tls_cn=tls_cn,
        ),
        '',
        *render_bullets(list(common.get('private_ingress_responsibilities') or [])),
        '',
        str(env_sections.get('private_ingress_resolution_intro') or '').strip(),
        '',
        '```text',
        'OPENCLAW_TLS_CN -> OPENCLAW_INGRESS_LISTEN_IP',
        '```',
        '',
        str(env_sections.get('private_ingress_resolution_note') or '').strip(),
        '',
        str(env_sections.get('private_ingress_boundary_note') or '').strip(),
        '',
        '## 宿主机预检',
        '',
        *render_code_block(list(common.get('precheck_commands') or [])),
        '',
        '离线镜像场景可追加：',
        '',
        *render_code_block(list(common.get('offline_precheck_commands') or [])),
        '',
    ])
    return '\n'.join(lines)


def render_docs(
    surface: dict[str, Any],
    schema: dict[str, Any],
    baseline: dict[str, Any],
    control_plane_medium: dict[str, Any],
    setup_entrypoints: dict[str, Any],
    testing_manifest: dict[str, Any],
    *,
    quickstart_notice: str,
    env_notice: str,
    ingress_manual_fields: Callable[[dict[str, Any]], dict[str, dict[str, Any]]],
) -> dict[str, str]:
    sections = load_sections()
    quickstart_doc_rel = require_nested_str(surface, ['generated_artifacts', 'quickstart_doc'], prefix='getting_started_reference', label='quickstart_doc')
    environment_setup_doc_rel = require_nested_str(surface, ['generated_artifacts', 'environment_setup_doc'], prefix='getting_started_reference', label='environment_setup_doc')
    return {
        quickstart_doc_rel: format_markdown_tables(
            quickstart_doc(surface, sections, schema, baseline, control_plane_medium, setup_entrypoints, testing_manifest, quickstart_notice=quickstart_notice)
        ),
        environment_setup_doc_rel: format_markdown_tables(
            environment_setup_doc(surface, sections, schema, control_plane_medium, env_notice=env_notice, ingress_manual_fields=ingress_manual_fields)
        ),
    }
