#!/usr/bin/env python3
"""只读摘要类命令注册。"""
from __future__ import annotations

import argparse

from openclaw.control_plane.cli_support import handlers
from openclaw.control_plane.cli_support.registration.shared import register_config_only


def _add_extension_selector(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument('--extension', default='', help='按 owner/extension id 过滤，或为裸 id 消歧')
    return parser


def register_readonly_summary_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    default_config: str,
) -> None:
    register_config_only(subparsers, default_config=default_config, command='summary', help_text='输出 control-plane 摘要', handler=handlers.cmd_summary)
    _add_extension_selector(register_config_only(subparsers, default_config=default_config, command='jobs', help_text='输出 job 摘要列表', handler=handlers.cmd_jobs))
    job_parser = register_config_only(subparsers, default_config=default_config, command='job', help_text='输出单个 job 详情', handler=handlers.cmd_job)
    job_parser.add_argument('--job-id', required=True)
    _add_extension_selector(job_parser)
    _add_extension_selector(register_config_only(subparsers, default_config=default_config, command='agents', help_text='输出 agent 摘要列表', handler=handlers.cmd_agents))
    _add_extension_selector(register_config_only(subparsers, default_config=default_config, command='agent-groups', help_text='输出 agent group 摘要列表', handler=handlers.cmd_agent_groups))
    _add_extension_selector(register_config_only(subparsers, default_config=default_config, command='agent-modules', help_text='输出 agent module 摘要列表', handler=handlers.cmd_agent_modules))

    agent_module_pluggability_parser = register_config_only(
        subparsers,
        default_config=default_config,
        command='agent-module-pluggability',
        help_text='输出 agent module 可插拔性摘要',
        handler=handlers.cmd_agent_module_pluggability,
    )
    agent_module_pluggability_parser.add_argument('--module-ref', default='')
    _add_extension_selector(register_config_only(subparsers, default_config=default_config, command='skill-sets', help_text='输出 skill set 摘要列表', handler=handlers.cmd_skill_sets))
    _add_extension_selector(register_config_only(subparsers, default_config=default_config, command='permission-policies', help_text='输出 permission policy 摘要列表', handler=handlers.cmd_permission_policies))
    _add_extension_selector(register_config_only(subparsers, default_config=default_config, command='toolsets', help_text='输出 toolset 摘要列表', handler=handlers.cmd_toolsets))
    register_config_only(subparsers, default_config=default_config, command='runtime-adapters', help_text='输出 runtime adapter 摘要列表', handler=handlers.cmd_runtime_adapters)
    _add_extension_selector(register_config_only(subparsers, default_config=default_config, command='implementations', help_text='输出 implementation 摘要列表', handler=handlers.cmd_implementations))
    _add_extension_selector(register_config_only(subparsers, default_config=default_config, command='models', help_text='输出 model 摘要列表', handler=handlers.cmd_models))
    _add_extension_selector(register_config_only(subparsers, default_config=default_config, command='targets', help_text='输出 target 摘要列表', handler=handlers.cmd_targets))
