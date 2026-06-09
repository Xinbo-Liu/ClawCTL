#!/usr/bin/env python3
"""deploy flow 控制面：统一派生 one_click_deploy 的阶段顺序与说明。"""
from __future__ import annotations

import sys
from typing import Any, NoReturn

from openclaw.lib.repo.static_truth import read_repo_contract_json, repo_contract_relpath
from openclaw.setup.surface import deployment_baseline as deployment_baseline_surface

POST_DEPLOY_RESUME_STAGES = {
    'post_deploy_acceptance',
    'post_deploy_full_acceptance',
}


def fail(message: str, code: int = 2) -> NoReturn:
    sys.stderr.write(f'{message}\n')
    raise SystemExit(code)


def load_stage_manifest() -> dict[str, Any]:
    payload = read_repo_contract_json('governance.deploy_stage_flow')
    if not isinstance(payload, dict):
        fail(f'[deploy_flow_control_plane] {repo_contract_relpath("governance.deploy_stage_flow")} 顶层必须为对象')
    return payload


def stage_manifest() -> dict[str, Any]:
    return load_stage_manifest()


def parse_args(argv: list[str]) -> dict[str, str]:
    opts = {
        'mode': 'online',
        'releaseCheck': '1',
        'browserVerify': '1',
        'startServices': '1',
        'stage': '',
        'imageArchivePath': '',
    }
    index = 0
    while index < len(argv):
        arg = argv[index]
        if not arg.startswith('--'):
            fail(f'[deploy_flow_control_plane] 未知参数：{arg}')
        index += 1
        if index >= len(argv):
            fail(f'[deploy_flow_control_plane] {arg} 缺少参数值')
        value = argv[index]
        index += 1
        match arg:
            case '--mode':
                opts['mode'] = value
            case '--release-check':
                opts['releaseCheck'] = value
            case '--browser-verify':
                opts['browserVerify'] = value
            case '--start-services':
                opts['startServices'] = value
            case '--stage':
                opts['stage'] = value
            case '--image-archive':
                opts['imageArchivePath'] = value
            case _:
                fail(f'[deploy_flow_control_plane] 未知参数：{arg}')
    if opts['mode'] not in {'online', 'offline'}:
        fail(f"[deploy_flow_control_plane] --mode 仅支持 online/offline，收到：{opts['mode']}")
    return opts


def effective_stages(options: dict[str, str]) -> list[str]:
    order_config = deployment_baseline_surface.deploy_stage_order()
    order: list[str] = []
    order.extend(order_config['common'])
    if options['mode'] == 'online':
        if options['releaseCheck'] == '1':
            order.extend(order_config['online'][:1])
        order.extend(order_config['online'][1:])
    else:
        order.extend(order_config['offline'])
    order.append(order_config['image_contract'])
    order.append(order_config['compose_contract'])
    if options['browserVerify'] == '1':
        order.append(order_config['browser_verify'])
    order.append(order_config['compose_config'])
    if options['startServices'] == '1':
        order.extend(order_config['service'])
    return order


def stage_info(stage: str) -> dict[str, Any]:
    manifest = stage_manifest()
    stages = manifest.get('stages') or {}
    if not isinstance(stages, dict):
        fail(f'[deploy_flow_control_plane] {repo_contract_relpath("governance.deploy_stage_flow")}.stages 必须为对象')
    info = stages.get(stage)
    if not info:
        return {
            'label': stage,
            'explain_label': stage,
            'summary_hint': ['固定查看：', '- docs/operations/troubleshooting.md'],
            'next_commands': ['bash ./scripts/setup/one_click_deploy.sh'],
        }
    return {'label': stage, **info}


HELP_SURFACE_LINES = [
    '帮助面保证：',
    '  - --help / --explain / 未知参数 必须优先输出可阅读帮助，不得因为 Docker、Docker daemon 或控制面镜像未就绪而阻塞帮助面',
    '  - 动态帮助面只允许展示由控制面真源派生的阶段映射、固定路径与默认入口；动态帮助面不可用时，必须自动显示静态说明',
    '  - 帮助面与执行面边界统一查看 docs/getting-started/quickstart.md',
]


def render_help() -> str:
    online_options = {'mode': 'online', 'releaseCheck': '1', 'browserVerify': '1', 'startServices': '1', 'stage': '', 'imageArchivePath': ''}
    offline_options = {'mode': 'offline', 'releaseCheck': '1', 'browserVerify': '1', 'startServices': '1', 'stage': '', 'imageArchivePath': '${imageArchivePath}'}
    lines = [
        '用法：',
        '  bash ./scripts/setup/one_click_deploy.sh [选项]',
        '',
        '默认行为（在线模式）：',
        '  前置门禁：本脚本会校验同一 env/mode 的 latest basic gate proof；若 proof 缺失或已过期，默认自动补跑 one_click_test_basic.sh',
        '  部署闭环：runtime 服务启动后，若当前 profile/extension 声明 required run ledger jobs，会先受控执行 run_control_plane_run_all_once.sh，再自动执行 one_click_test_full.sh 并导出 runtime acceptance evidence',
        '  实际执行：通过统一容器化 Python 控制面进入 Docker；若缺少 Docker / Docker daemon / docker compose，或当前用户无权访问 Docker daemon（如 /var/run/docker.sock 权限不足），主路径会失败；部署摘要由控制面统一写出',
        '  权限前置：真正进入控制面前，会先检查仓库 / deploy / state 路径的读写执行权限',
        '  权限边界：当前脚本不会自动 sudo 或 chown；deploy/.env、deploy/site.env、启用扩展内部 agent/extensions/<extension-id>/deploy/extension.env、deploy/targets.d、state/ 与当前 host state root（默认值由 runtime_paths 真源派生）必须由当前部署用户可管理',
        '  用户真源：runtime 服务用户固定取 deploy/.env 中的 OPENCLAW_RUNTIME_UID / OPENCLAW_RUNTIME_GID；默认应与当前部署用户一致',
        '',
        '默认在线阶段顺序：',
    ]
    for index, stage in enumerate(effective_stages(online_options), start=1):
        lines.append(f"  {index}) {stage_info(stage)['explain_label']}")
    lines.extend([
        '',
        '默认离线阶段顺序：',
    ])
    for index, stage in enumerate(effective_stages(offline_options), start=1):
        lines.append(f"  {index}) {stage_info(stage)['explain_label']}")
    lines.extend([
        '',
        '离线模式：',
        '  - 不执行 release 检查与在线 pull',
        '  - 从镜像 tar 归档执行 docker load（deployment_images_*.tar 必须覆盖 source_strategy 声明的部署镜像合同角色）',
        '  - 默认会从 state/image_artifacts/ 自动选择最新归档；归档在继续执行前会先按当前 pin 校验部署镜像合同，并额外确认 compose 运行镜像集合没有漂移；也可显式传入路径',
        '',
        '可选项：',
        '  --offline                      进入离线模式',
        '  --image-archive <path>       指定 deployment_images_*.tar；仅 --offline 下有效',
        '  --env-file <path>             覆盖默认 deploy/.env；必须与 latest basic gate proof 使用同一文件',
        '  --prepare-only                 只执行准备阶段，不启动 runtime 服务',
        '  --resume-from <stage>          从指定阶段继续执行；会跳过它之前的阶段',
        '                                 post_deploy_acceptance 会执行 required jobs、full test 与 evidence 导出',
        '                                 post_deploy_full_acceptance 仅执行 full test 与 evidence 导出',
        '                                 后置验收 resume 不能与 --prepare-only 或 --skip-acceptance 同用',
        '  --explain                      只打印一键入口与分阶段手工命令映射，不执行任何动作',
        '  --skip-release-check           跳过 OpenClaw 上游版本检查',
        '  --strict-release-check         使用 strict_release 策略；上游 latest 高于当前 pin 时阻断',
        '  --skip-browser-verify          跳过浏览器能力校验',
        '  --require-basic-gate-proof     不自动补跑 basic gate；latest proof 缺失或过期时直接失败',
        '  --skip-acceptance              跳过部署后 full test 与 runtime evidence 导出；deployment acceptance 保持未闭合',
        '  -h, --help                     显示帮助',
        '',
        *HELP_SURFACE_LINES,
    ])
    return '\n'.join(lines)


def render_explain(options: dict[str, str]) -> str:
    lines = ['one_click_deploy 默认阶段映射', '']
    if options['mode'] == 'online':
        lines.append('在线模式：')
        lines.append('  前置门禁：若 latest basic gate proof 与当前 env/mode 不匹配，默认自动补跑 one_click_test_basic.sh')
    else:
        lines.append('离线模式（--offline）：')
        lines.append('  前置门禁：若 latest basic gate proof 与当前 env/mode/image archive 不匹配，默认自动补跑 one_click_test_basic.sh --offline')
    stages = effective_stages(options)
    for index, stage in enumerate(stages, start=1):
        lines.append(f" {index}. {stage_info(stage)['explain_label']}")
    lines.extend([
        '',
        '如果使用 --prepare-only：',
        '  - 流程会停在 compose 渲染检查，不执行 runtime 服务启动；不能视为调度已上线。',
        '',
        '部署成功后的默认下一步：',
        f"  - 若当前 profile / extension 声明 required_run_ledger_jobs，默认部署链会先执行 run_all_once，再执行 `{deployment_baseline_surface.post_deploy_default_command()}` 并导出 runtime acceptance evidence；",
        '  - 需要快速恢复或只做 compose 修复时，可加 --skip-acceptance；该模式只代表服务启动链执行完成，deployment acceptance 与 runtime evidence 均未闭合；',
        '  - dispatch doctor / preflight / send/retry dry-run 只在需要确认调度上线或排查 dispatch 问题时再补做。',
        '',
        '关联文档：',
        '  - docs/getting-started/quickstart.md',
        '  - docs/getting-started/environment-setup.md',
        '  - docs/getting-started/image-preparation.md',
        '  - docs/operations/runtime-service-reference.md',
        '',
        '运行来源默认值：',
        '  - runtime image source 统一查看 docs/operations/runtime-service-reference.md',
        '  - provider/API 入口以 active profile 的 deploy env schema 与 extension.env/site.env 输入为准',
        '',
        '恢复执行：',
        '  - 可用 --resume-from <stage> 从中间阶段继续执行，例如：',
        '    bash ./scripts/setup/one_click_deploy.sh --resume-from pull_images',
        '    bash ./scripts/setup/one_click_deploy.sh --offline --resume-from load_deployment_images',
        '    bash ./scripts/setup/one_click_deploy.sh --resume-from post_deploy_full_acceptance',
        '',
        *HELP_SURFACE_LINES,
    ])
    return '\n'.join(lines)


def render_summary_hint(stage: str) -> str:
    return '\n'.join(stage_info(stage)['summary_hint'])


def apply_template(template: str, options: dict[str, str]) -> str:
    image_archive_path = options.get('imageArchivePath') or 'state/image_artifacts/deployment_images_*.tar'
    offline_flag = ' --offline' if options.get('mode') == 'offline' else ''
    return template.replace('${imageArchivePath}', image_archive_path).replace('${offlineFlag}', offline_flag)


def render_next_commands(stage: str, options: dict[str, str]) -> str:
    return '\n'.join(apply_template(line, options) for line in stage_info(stage)['next_commands'])


def validate_resume(options: dict[str, str]) -> None:
    stages = effective_stages(options)
    if not options['stage']:
        fail('[deploy_flow_control_plane] --stage 缺少阶段名')
    if options['stage'] in POST_DEPLOY_RESUME_STAGES:
        return
    if options['stage'] not in stages:
        allowed = [*stages, *sorted(POST_DEPLOY_RESUME_STAGES)]
        fail(f"[deploy_flow_control_plane] --resume-from 指定的阶段无效：{options['stage']}\n[deploy_flow_control_plane] 当前模式下可用阶段：{' '.join(allowed)}")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        fail('[deploy_flow_control_plane] 缺少命令')
    command = args.pop(0)
    options = parse_args(args)
    match command:
        case 'help-text':
            sys.stdout.write(f'{render_help()}\n')
        case 'explain':
            sys.stdout.write(f'{render_explain(options)}\n')
        case 'effective-stages':
            sys.stdout.write('\n'.join(effective_stages(options)) + '\n')
        case 'stage-label':
            sys.stdout.write(f"{stage_info(options['stage'])['label']}\n")
        case 'summary-hint':
            sys.stdout.write(f"{render_summary_hint(options['stage'])}\n")
        case 'next-commands':
            sys.stdout.write(f"{render_next_commands(options['stage'], options)}\n")
        case 'validate-resume':
            validate_resume(options)
        case _:
            fail(f'[deploy_flow_control_plane] 未知命令：{command}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
