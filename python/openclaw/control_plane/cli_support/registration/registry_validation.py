#!/usr/bin/env python3
"""Registry 校验类命令注册。"""
from __future__ import annotations

import argparse

from openclaw.control_plane.cli_support import handlers
from openclaw.control_plane.cli_support.registration.shared import register_config_only


def register_registry_validation_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    default_config: str,
) -> None:
    register_config_only(
        subparsers,
        default_config=default_config,
        command='validate-registry',
        help_text='校验 control-plane registry 与引用闭合',
        handler=handlers.cmd_validate_registry,
    )
    register_config_only(
        subparsers,
        default_config=default_config,
        command='check-agent-control-plane-registry',
        help_text='校验 agent/runtime 视图由 module manifest 派生',
        handler=handlers.cmd_check_agent_control_plane_registry,
    )
    register_config_only(
        subparsers,
        default_config=default_config,
        command='check-agent-assembly-registry',
        help_text='校验 skill/permission/tool 视图由 agent 资产派生',
        handler=handlers.cmd_check_agent_assembly_registry,
    )
