#!/usr/bin/env python3
"""运行执行类命令注册。"""
from __future__ import annotations

import argparse

from openclaw.control_plane.cli_support import handlers
from openclaw.control_plane.cli_support.registration.shared import add_config_path, register_config_only


def _add_config_path_override(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--config-path', default=argparse.SUPPRESS)
    parser.add_argument('--control-plane-profile', default=argparse.SUPPRESS)


def _add_extension_selector(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--extension', default='')
    group.add_argument('--all', action='store_true')
    group.add_argument('--enabled', action='store_true', help='选择当前 control-plane config 启用的 managed extension')


def register_runtime_execution_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    default_config: str,
) -> None:
    resolve_job_parser = register_config_only(
        subparsers,
        default_config=default_config,
        command='resolve-job-command',
        help_text='解析单个 job 的最终执行命令',
        handler=handlers.cmd_resolve_job_command,
    )
    resolve_job_parser.add_argument('--job-id', required=True)
    resolve_job_parser.add_argument('--extension', default='', help='用 owner/extension id 消歧裸 job id')

    resolve_job_plan_parser = register_config_only(
        subparsers,
        default_config=default_config,
        command='resolve-job-plan',
        help_text='解析单个 job 的执行计划',
        handler=handlers.cmd_resolve_job_plan,
    )
    resolve_job_plan_parser.add_argument('--job-id', required=True)
    resolve_job_plan_parser.add_argument('--extension', default='', help='用 owner/extension id 消歧裸 job id')

    resolve_target_parser = register_config_only(
        subparsers,
        default_config=default_config,
        command='resolve-target-operation',
        help_text='解析单个 target operation 的最终执行命令',
        handler=handlers.cmd_resolve_target_operation,
    )
    resolve_target_selector_group = resolve_target_parser.add_mutually_exclusive_group()
    resolve_target_selector_group.add_argument('--target-binding-ref', default='')
    resolve_target_selector_group.add_argument('--dispatch-target-id', default='')
    resolve_target_parser.add_argument('--extension', default='', help='用 owner/extension id 消歧裸 targetBindingRef')
    resolve_target_parser.add_argument('--operation', required=True)
    resolve_target_parser.add_argument('--agent-ref', default='')
    resolve_target_parser.add_argument('passthrough', nargs=argparse.REMAINDER)

    run_target_parser = register_config_only(
        subparsers,
        default_config=default_config,
        command='run-target-operation',
        help_text='执行单个 target operation',
        handler=handlers.cmd_run_target_operation,
    )
    run_target_selector_group = run_target_parser.add_mutually_exclusive_group()
    run_target_selector_group.add_argument('--target-binding-ref', default='')
    run_target_selector_group.add_argument('--dispatch-target-id', default='')
    run_target_parser.add_argument('--extension', default='', help='用 owner/extension id 消歧裸 targetBindingRef')
    run_target_parser.add_argument('--operation', required=True)
    run_target_parser.add_argument('--agent-ref', default='')
    run_target_parser.add_argument('passthrough', nargs=argparse.REMAINDER)

    run_agent_parser = register_config_only(
        subparsers,
        default_config=default_config,
        command='run-agent-runtime',
        help_text='通过 runtime adapter 运行单个 agent',
        handler=handlers.cmd_run_agent_runtime,
    )
    run_agent_parser.add_argument('--agent-ref', required=True)
    run_agent_parser.add_argument('--state-root', default='')
    run_agent_parser.add_argument('passthrough', nargs=argparse.REMAINDER)

    register_config_only(subparsers, default_config=default_config, command='run-ledger-summary', help_text='输出 run-ledger 摘要', handler=handlers.cmd_run_ledger)

    due_parser = register_config_only(
        subparsers,
        default_config=default_config,
        command='due-preview',
        help_text='预览当前时刻应触发的 jobs',
        handler=handlers.cmd_due_preview,
    )
    due_parser.add_argument('--state-root', default='')
    due_parser.add_argument('--at', default='', help='使用 ISO 时间预览；留空表示当前时间')
    due_parser.add_argument('--force-all', action='store_true', help='按 run-all-once 语义预览')

    extension_env_parser = subparsers.add_parser('extension-env', help='管理 managed extension 独立 runtime venv')
    add_config_path(extension_env_parser, default_config=default_config)
    extension_env_subparsers = extension_env_parser.add_subparsers(dest='extension_env_command', required=True)

    ensure_parser = extension_env_subparsers.add_parser('ensure', help='同步离线 wheelhouse 并确保扩展 runtime venv 可用')
    _add_config_path_override(ensure_parser)
    _add_extension_selector(ensure_parser)
    ensure_parser.add_argument('--offline', action='store_true', help='离线安装；默认即为离线')
    ensure_parser.add_argument('--allow-online', action='store_true', help='受控联网模式：允许 pip 联网下载，但仍要求 lock/hash')
    ensure_parser.add_argument('--no-clean', action='store_true', help='保留 runtime wheelhouse 中未被 lock 引用的 wheel')
    ensure_parser.add_argument('--json', action='store_true')
    ensure_parser.set_defaults(func=handlers.cmd_extension_env_ensure)

    status_parser = extension_env_subparsers.add_parser('status', help='查看扩展 runtime venv 状态')
    _add_config_path_override(status_parser)
    _add_extension_selector(status_parser)
    status_parser.add_argument('--json', action='store_true')
    status_parser.set_defaults(func=handlers.cmd_extension_env_status)

    verify_parser = extension_env_subparsers.add_parser('verify', help='校验扩展 runtime venv 可用性')
    _add_config_path_override(verify_parser)
    _add_extension_selector(verify_parser)
    verify_parser.add_argument('--json', action='store_true')
    verify_parser.set_defaults(func=handlers.cmd_extension_env_verify)

    prune_parser = extension_env_subparsers.add_parser('prune', help='清理旧扩展 runtime venv')
    _add_config_path_override(prune_parser)
    _add_extension_selector(prune_parser)
    prune_parser.add_argument('--keep', type=int, default=2)
    prune_parser.set_defaults(func=handlers.cmd_extension_env_prune)
