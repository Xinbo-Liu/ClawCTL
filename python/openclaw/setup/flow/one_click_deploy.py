#!/usr/bin/env python3
"""one_click_deploy 的静态控制面。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, NoReturn

from openclaw.lib.repo.static_truth import (
    governance_default_path,
    governance_setup_entrypoint,
    governance_setup_entrypoints,
    repo_contract_relpath,
    runtime_paths_host_entry,
)
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.setup.flow import deploy_flow

REPO_ROOT = resolve_repo_root(Path(__file__))

REQUIRED_FILES = [
    'scripts/runtime/run_openclaw_python_tool.sh',
    'scripts/lib/flow_entry_surface_shell.sh',
    'scripts/lib/flow_sequence_shell.sh',
    'scripts/setup/lib/deploy_stage_runner.sh',
    'scripts/setup/lib/deploy_stage_registry.sh',
    'scripts/setup/lib/setup_cli_common.sh',
    'scripts/setup/lib/deploy_flow_control_plane_shell.sh',
    'scripts/setup/lib/deploy_flow_summary_shell.sh',
    'scripts/setup/lib/deploy_env_shell.sh',
    'scripts/lib/flow_summary_common_shell.sh',
    'scripts/lib/run_summary_shell.sh',
    'scripts/lib/image_env.sh',
    'scripts/setup/lib/deploy_runtime_context_shell.sh',
    'scripts/setup/one_click_test_basic.sh',
    'scripts/setup/lib/extension_env_gate.sh',
    'scripts/doctor/check_local_runtime_fs_contract.sh',
    'scripts/doctor/check_deployment_image_readiness.sh',
    'scripts/doctor/check_runtime_bind_user_contract.sh',
    repo_contract_relpath('governance.default_deployment_flow'),
    repo_contract_relpath('governance.deploy_stage_flow'),
    repo_contract_relpath('governance.summary_manifest'),
    repo_contract_relpath('governance.setup_entrypoints'),
]


STATIC_RELATIVE_PATHS = {
    'artifact-dir': 'state/image_artifacts',
    'env-file-path': 'deploy/.env',
    'compose-file-path': 'deploy/docker-compose.yml',
    'deploy-stage-runner-script-path': 'scripts/setup/lib/deploy_stage_runner.sh',
    'run-summary-shell-script-path': 'scripts/lib/run_summary_shell.sh',
    'image-env-script-path': 'scripts/lib/image_env.sh',
    'deploy-runtime-context-shell-script-path': 'scripts/setup/lib/deploy_runtime_context_shell.sh',
    'deploy-flow-summary-shell-script-path': 'scripts/setup/lib/deploy_flow_summary_shell.sh',
    'basic-gate-script-path': 'scripts/setup/one_click_test_basic.sh',
    'image-archive-pattern': 'state/image_artifacts/deployment_images_*.tar',
}


def fail(message: str, exit_code: int = 2) -> NoReturn:
    sys.stderr.write(f'[one_click_deploy_control_plane][FAIL] {message}\n')
    raise SystemExit(exit_code)


def check_required_files() -> None:
    for rel in REQUIRED_FILES:
        if not (REPO_ROOT / rel).is_file():
            fail(f'缺少必要文件：{rel}', 3)


def emit_value(value: str) -> int:
    sys.stdout.write(value + '\n')
    return 0


def emit_value_handler(value: str) -> Callable[[argparse.Namespace], int]:
    def handler(_: argparse.Namespace) -> int:
        return emit_value(value)

    return handler


def path_values() -> dict[str, str]:
    return {
        **STATIC_RELATIVE_PATHS,
        'runtime-host-env-path': runtime_paths_host_entry('runtime_host_env', REPO_ROOT),
        'default-log-dir': runtime_paths_host_entry('logs_dir', REPO_ROOT),
        'deploy-latest-summary-json-path': governance_default_path('latest_json', profile_id='one_click_deploy', root_dir=REPO_ROOT),
        'deploy-latest-summary-markdown-path': governance_default_path('latest_markdown', profile_id='one_click_deploy', root_dir=REPO_ROOT),
    }


def entrypoint_info() -> dict[str, Any]:
    return governance_setup_entrypoint('one_click_deploy', REPO_ROOT)


def help_contract() -> dict[str, Any]:
    payload = governance_setup_entrypoints(REPO_ROOT)
    return dict(payload.get('help_surface_contract') or {})


def render_help_text() -> str:
    info = entrypoint_info()
    purpose = str(info.get('purpose') or 'one_click_deploy 的静态前置、默认路径与 helper 入口由治理真源统一派生。').strip()
    boundaries = [str(item).strip() for item in (info.get('boundaries') or []) if str(item).strip()]
    references = [str(item).strip() for item in (info.get('references') or []) if str(item).strip()]
    guarantees = [str(item).strip() for item in (help_contract().get('guarantees') or []) if str(item).strip()]
    lines = [
        '用法：',
        '  bash ./scripts/setup/one_click_deploy.sh [选项]',
        '',
        '说明：',
        f'  {purpose}',
    ]
    if boundaries:
        lines.extend(['', '边界：'])
        lines.extend([f'  - {item}' for item in boundaries])
    if guarantees:
        lines.extend(['', '帮助面约束：'])
        lines.append(f'  - {guarantees[0]}')
    if references:
        lines.extend(['', '参考：'])
        lines.extend([f'  - {item}' for item in references])
    return '\n'.join(lines) + '\n'


def cmd_preflight(_: argparse.Namespace) -> int:
    check_required_files()
    sys.stdout.write('one_click_deploy static preflight ok\n')
    return 0


def cmd_help_text(_: argparse.Namespace) -> int:
    sys.stdout.write(render_help_text())
    return 0


def bootstrap_flow_options(args: argparse.Namespace) -> dict[str, str]:
    return {
        'mode': args.mode,
        'releaseCheck': args.release_check,
        'browserVerify': args.browser_verify,
        'startServices': args.start_services,
        'stage': args.resume_from or '',
        'imageArchivePath': args.image_archive_path or '',
    }


def cmd_bootstrap_json(args: argparse.Namespace) -> int:
    """一次性输出部署入口启动所需的静态控制面事实。"""

    check_required_files()
    flow_options = bootstrap_flow_options(args)
    if args.resume_from:
        deploy_flow.validate_resume(flow_options)
    payload = {
        'schemaVersion': 1,
        'entrypoint': 'one_click_deploy',
        'preflight': {'status': 'ok'},
        'paths': path_values(),
        'effectiveStages': deploy_flow.effective_stages(flow_options),
        'resume': {'stage': args.resume_from or '', 'status': 'ok'},
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + '\n')
    return 0


def add_bootstrap_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--mode', default='online')
    parser.add_argument('--release-check', default='1')
    parser.add_argument('--browser-verify', default='1')
    parser.add_argument('--start-services', default='1')
    parser.add_argument('--resume-from', default='')
    parser.add_argument('--image-archive-path', default='')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='setup flow one-click-deploy', add_help=False)
    subparsers = parser.add_subparsers(dest='subcommand', required=True)
    commands: dict[str, Callable[[argparse.Namespace], int]] = {
        'preflight': cmd_preflight,
        'help-text': cmd_help_text,
        'bootstrap-json': cmd_bootstrap_json,
    }
    for name, value in path_values().items():
        commands[name] = emit_value_handler(value)
    for name, func in commands.items():
        sub = subparsers.add_parser(name, add_help=False)
        if name == 'bootstrap-json':
            add_bootstrap_args(sub)
        sub.set_defaults(handler=func)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == '__main__':
    raise SystemExit(main())
