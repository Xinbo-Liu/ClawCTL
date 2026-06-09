#!/usr/bin/env python3
"""one_click_test_basic/full 的静态控制面。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Callable, NoReturn

from openclaw.lib.repo.static_truth import (
    governance_default_path,
    governance_setup_entrypoint,
    governance_setup_entrypoints,
    host_control_plane_file,
    repo_contract_relpath,
    runtime_paths_host_entry,
)
from openclaw.lib.repo.layout import resolve_repo_root

REPO_ROOT = resolve_repo_root(Path(__file__))

BASIC_REQUIRED_FILES = [
    'scripts/runtime/run_openclaw_python_tool.sh',
    'scripts/lib/flow_entry_surface_shell.sh',
    'scripts/lib/flow_preflight_shell.sh',
    'scripts/lib/flow_sequence_shell.sh',
    'scripts/setup/lib/setup_cli_common.sh',
    'scripts/setup/lib/deploy_env_shell.sh',
    'scripts/doctor/check_docker_host_readiness.sh',
    'scripts/doctor/check_local_runtime_fs_contract.sh',
    'scripts/doctor/check_deployment_image_readiness.sh',
    'scripts/doctor/check_runtime_bind_user_contract.sh',
    'scripts/doctor/check_runtime_compose_contract.sh',
    'scripts/images/check_openclaw_release.sh',
    repo_contract_relpath('governance.setup_entrypoints'),
]

FULL_REQUIRED_FILES = [
    'scripts/runtime/run_openclaw_python_tool.sh',
    'scripts/lib/flow_entry_surface_shell.sh',
    'scripts/lib/flow_preflight_shell.sh',
    'scripts/lib/flow_sequence_shell.sh',
    'scripts/setup/lib/setup_cli_common.sh',
    'scripts/setup/lib/test_gate_common.sh',
    'scripts/setup/lib/full_test_env_shell.sh',
    'scripts/setup/lib/full_test_group_registry.sh',
    repo_contract_relpath('governance.full_test_group_registry'),
    'scripts/setup/lib/full_test_summary_shell.sh',
    'scripts/setup/lib/full_test_acceptance_shell.sh',
    'scripts/setup/lib/full_test_group_runner.sh',
    'scripts/setup/lib/extension_env_gate.sh',
    'scripts/runtime/runtime_container_lib.sh',
    'scripts/lib/run_summary_shell.sh',
    'scripts/lib/flow_summary_common_shell.sh',
    'scripts/runtime/run_openclaw_python_tool.sh',
    repo_contract_relpath('runtime.testing_manifest'),
    repo_contract_relpath('governance.summary_manifest'),
    repo_contract_relpath('governance.setup_entrypoints'),
]


STATIC_RELATIVE_PATHS = {
    'basic-env-file-path': 'deploy/.env',
    'basic-check-openclaw-release-script-path': 'scripts/images/check_openclaw_release.sh',
    'full-env-file-path': 'deploy/.env',
}


def fail(message: str, exit_code: int = 2) -> NoReturn:
    sys.stderr.write(f'[one_click_test_control_plane][FAIL] {message}\n')
    raise SystemExit(exit_code)


def check_required_files(files: list[str]) -> None:
    for rel in files:
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
        'basic-gate-proof-path': host_control_plane_file('setup/one_click_test_basic.latest.proof.json', REPO_ROOT),
        'runtime-host-env-path': runtime_paths_host_entry('runtime_host_env', REPO_ROOT),
        'full-acceptance-state-path': governance_default_path('deployment_acceptance_state', root_dir=REPO_ROOT),
        'full-log-dir': runtime_paths_host_entry('logs_dir', REPO_ROOT),
        'full-latest-summary-json-path': governance_default_path('latest_json', profile_id='one_click_test_full', root_dir=REPO_ROOT),
        'full-latest-summary-markdown-path': governance_default_path('latest_markdown', profile_id='one_click_test_full', root_dir=REPO_ROOT),
    }


def entrypoint_info(entrypoint_id: str) -> dict[str, Any]:
    return governance_setup_entrypoint(entrypoint_id, REPO_ROOT)


def help_contract() -> dict[str, Any]:
    payload = governance_setup_entrypoints(REPO_ROOT)
    return dict(payload.get('help_surface_contract') or {})


def render_help_text(entrypoint_id: str, command: str) -> str:
    info = entrypoint_info(entrypoint_id)
    purpose = str(info.get('purpose') or f'{entrypoint_id} 的静态前置、默认路径与 helper 入口由治理真源统一派生。').strip()
    boundaries = [str(item).strip() for item in (info.get('boundaries') or []) if str(item).strip()]
    references = [str(item).strip() for item in (info.get('references') or []) if str(item).strip()]
    guarantees = [str(item).strip() for item in (help_contract().get('guarantees') or []) if str(item).strip()]
    lines = [
        '用法：',
        f'  {command} [选项]',
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


def cmd_preflight_basic(_: argparse.Namespace) -> int:
    check_required_files(BASIC_REQUIRED_FILES)
    sys.stdout.write('one_click_test_basic static preflight ok\n')
    return 0


def cmd_preflight_full(_: argparse.Namespace) -> int:
    check_required_files(FULL_REQUIRED_FILES)
    sys.stdout.write('one_click_test_full static preflight ok\n')
    return 0


def cmd_help_text_basic(_: argparse.Namespace) -> int:
    sys.stdout.write(render_help_text('one_click_test_basic', 'bash ./scripts/setup/one_click_test_basic.sh'))
    return 0


def cmd_help_text_full(_: argparse.Namespace) -> int:
    sys.stdout.write(render_help_text('one_click_test_full', 'bash ./scripts/setup/one_click_test_full.sh'))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='setup flow one-click-test', add_help=False)
    subparsers = parser.add_subparsers(dest='subcommand', required=True)
    commands: dict[str, Callable[[argparse.Namespace], int]] = {
        'preflight-basic': cmd_preflight_basic,
        'preflight-full': cmd_preflight_full,
        'help-text-basic': cmd_help_text_basic,
        'help-text-full': cmd_help_text_full,
    }
    for name, value in path_values().items():
        commands[name] = emit_value_handler(value)
    for name, func in commands.items():
        sub = subparsers.add_parser(name, add_help=False)
        sub.set_defaults(handler=func)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == '__main__':
    raise SystemExit(main())
