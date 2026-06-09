#!/usr/bin/env python3
"""Agent group 证据类命令注册。"""
from __future__ import annotations

import argparse

from openclaw.control_plane.cli_support import handlers
from openclaw.control_plane.cli_support.registration.shared import register_config_only


def register_group_evidence_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    default_config: str,
) -> None:
    agent_access_log_parser = register_config_only(
        subparsers,
        default_config=default_config,
        command='agent-access-log',
        help_text='输出 agent 访问日志摘要',
        handler=handlers.cmd_agent_access_log,
    )
    agent_access_log_parser.add_argument('--limit', type=int, default=50)
    agent_access_log_parser.add_argument('--agent-ref', default='')
    agent_access_log_parser.add_argument('--group-ref', default='')
    agent_access_log_parser.add_argument('--job-id', default='')
    agent_access_log_parser.add_argument('--status', default='')
    agent_access_log_parser.add_argument('--source', default='')

    agent_group_access_parser = register_config_only(
        subparsers,
        default_config=default_config,
        command='agent-group-access',
        help_text='输出 agent-group 访问视图',
        handler=handlers.cmd_agent_group_access,
    )
    agent_group_access_parser.add_argument('--limit', type=int, default=200)
    agent_group_access_parser.add_argument('--timeline-limit', type=int, default=20)
    agent_group_access_parser.add_argument('--group-ref', default='')
    agent_group_access_parser.add_argument('--status', default='')
    agent_group_access_parser.add_argument('--source', default='')

    agent_group_acceptance_bindings_parser = register_config_only(
        subparsers,
        default_config=default_config,
        command='agent-group-acceptance-bindings',
        help_text='输出 agent-group acceptance binding 摘要',
        handler=handlers.cmd_agent_group_acceptance_bindings,
    )
    agent_group_acceptance_bindings_parser.add_argument('--group-ref', default='')

    agent_group_release_gates_parser = register_config_only(
        subparsers,
        default_config=default_config,
        command='agent-group-release-gates',
        help_text='输出 agent-group release gate 摘要',
        handler=handlers.cmd_agent_group_release_gates,
    )
    agent_group_release_gates_parser.add_argument('--group-ref', default='')

    export_agent_group_evidence_parser = register_config_only(
        subparsers,
        default_config=default_config,
        command='export-agent-group-evidence',
        help_text='导出 group evidence 到 control-plane state runtime evidence',
        handler=handlers.cmd_export_agent_group_evidence,
    )
    export_agent_group_evidence_parser.add_argument('--state-root', default='')
    export_agent_group_evidence_parser.add_argument('--agent-access-limit', type=int, default=200)
    export_agent_group_evidence_parser.add_argument('--group-access-limit', type=int, default=200)
    export_agent_group_evidence_parser.add_argument('--timeline-limit', type=int, default=20)
