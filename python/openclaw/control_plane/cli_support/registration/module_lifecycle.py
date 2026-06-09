#!/usr/bin/env python3
"""模块生命周期类命令注册。"""
from __future__ import annotations

import argparse

from openclaw.control_plane.cli_support import handlers
from openclaw.control_plane.cli_support.registration.shared import add_explicit_or_profile_config, register_config_only


def register_module_lifecycle_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    default_config: str,
) -> None:
    agent_module_attach_parser = subparsers.add_parser(
        'agent-module-attach',
        help='把独立 agent 模块接入 scheduler/group/target',
    )
    add_explicit_or_profile_config(agent_module_attach_parser)
    agent_module_attach_parser.set_defaults(func=handlers.cmd_agent_module_attach)
    agent_module_attach_parser.add_argument('--repo-root', default='')
    agent_module_attach_parser.add_argument('--module-ref', required=True)
    agent_module_attach_parser.add_argument('--operation-ref', required=True)
    agent_module_attach_parser.add_argument('--job-id', required=True)
    agent_module_attach_parser.add_argument('--job-title', required=True)
    agent_module_attach_parser.add_argument('--schedule-expr', required=True)
    agent_module_attach_parser.add_argument('--schedule-tz', default='')
    agent_module_attach_parser.add_argument('--group-ref', default='')
    agent_module_attach_parser.add_argument('--group-placement', default='none', choices=['none', 'ordered', 'recovery'])
    agent_module_attach_parser.add_argument('--insert-before-job', default='')
    agent_module_attach_parser.add_argument('--insert-after-job', default='')
    agent_module_attach_parser.add_argument('--recovery-after-minutes', type=int)
    agent_module_attach_parser.add_argument('--recovery-action-kind', default='retry', choices=['retry', 'compensate', 'replay'])
    agent_module_attach_parser.add_argument('--target-binding-ref', default='')
    agent_module_attach_parser.add_argument('--depends-on', action='append', default=[])
    agent_module_attach_parser.add_argument('--order', type=int)
    agent_module_attach_parser.add_argument('--timeout-seconds', type=int)
    agent_module_attach_parser.add_argument('--retry-enabled', action='store_true')
    agent_module_attach_parser.add_argument('--retry-disabled', action='store_true')
    agent_module_attach_parser.add_argument('--retry-max-attempts', type=int)
    agent_module_attach_parser.add_argument('--retry-backoff-seconds', action='append', type=int, default=[])
    agent_module_attach_parser.add_argument('--run-artifact-root', default='')
    agent_module_attach_parser.add_argument('--latest-alias', default='')
    agent_module_attach_parser.add_argument('--retention-days', type=int, default=14)
    agent_module_attach_parser.add_argument('--retryable-class', action='append', default=[])
    agent_module_attach_parser.add_argument('--terminal-class', action='append', default=[])
    agent_module_attach_parser.add_argument('--model-profile-ref', default='')
    agent_module_attach_parser.add_argument('--job-file-prefix', default='')
    agent_module_attach_parser.add_argument('--write', action='store_true')

    agent_module_detach_parser = subparsers.add_parser(
        'agent-module-detach',
        help='把已接入 scheduler 的 agent 模块从 scheduler/group/target 拆离',
    )
    add_explicit_or_profile_config(agent_module_detach_parser)
    agent_module_detach_parser.set_defaults(func=handlers.cmd_agent_module_detach)
    agent_module_detach_parser.add_argument('--repo-root', default='')
    agent_module_detach_parser.add_argument('--module-ref', required=True)
    agent_module_detach_parser.add_argument('--job-id', required=True)
    agent_module_detach_parser.add_argument('--operation-ref', default='')
    agent_module_detach_parser.add_argument('--write', action='store_true')

    agent_module_prune_parser = subparsers.add_parser(
        'agent-module-prune',
        help='把模块样板面裁剪回最小合同',
    )
    add_explicit_or_profile_config(agent_module_prune_parser)
    agent_module_prune_parser.set_defaults(func=handlers.cmd_agent_module_prune)
    agent_module_prune_parser.add_argument('--repo-root', default='')
    agent_module_prune_parser.add_argument('--module-ref', default='')
    agent_module_prune_parser.add_argument('--write', action='store_true')

    agent_module_drop_parser = subparsers.add_parser(
        'agent-module-drop',
        help='删除独立 agent 模块及其实现面',
    )
    add_explicit_or_profile_config(agent_module_drop_parser)
    agent_module_drop_parser.set_defaults(func=handlers.cmd_agent_module_drop)
    agent_module_drop_parser.add_argument('--repo-root', default='')
    agent_module_drop_parser.add_argument('--module-ref', required=True)
    agent_module_drop_parser.add_argument('--write', action='store_true')

    job_surface_prune_parser = register_config_only(
        subparsers,
        default_config=default_config,
        command='job-surface-prune',
        help_text='从 job manifest 中裁剪可派生字段',
        handler=handlers.cmd_job_surface_prune,
    )
    job_surface_prune_parser.add_argument('--repo-root', default='')
    job_surface_prune_parser.add_argument('--job-id', default='')
    job_surface_prune_parser.add_argument('--write', action='store_true')

    scaffold_agent_module_parser = subparsers.add_parser('scaffold-agent-module', help='生成新的 agent 模块骨架')
    add_explicit_or_profile_config(scaffold_agent_module_parser)
    scaffold_agent_module_parser.add_argument('--repo-root', default='')
    scaffold_agent_module_parser.add_argument('--module-ref', required=True)
    scaffold_agent_module_parser.add_argument('--title', required=True)
    scaffold_agent_module_parser.add_argument('--owner-domain', required=True)
    scaffold_agent_module_parser.add_argument('--module-kind', default='worker', choices=['worker', 'control_check'])
    scaffold_agent_module_parser.add_argument('--entrypoint-kind', default='python_cli', choices=['python_cli', 'openclaw_runtime', 'delivery_adapter'])
    scaffold_agent_module_parser.add_argument('--runtime-adapter-ref', default='python_module')
    scaffold_agent_module_parser.add_argument('--executor-kind', default='')
    scaffold_agent_module_parser.add_argument('--operation-ref', default='run_default')
    scaffold_agent_module_parser.add_argument('--description', default='')
    scaffold_agent_module_parser.add_argument('--version', default='v1')
    scaffold_agent_module_parser.add_argument('--network', action='store_true')
    scaffold_agent_module_parser.add_argument('--model-required', action='store_true')
    scaffold_agent_module_parser.add_argument('--external-dispatch', action='store_true')
    scaffold_agent_module_parser.add_argument('--filesystem-write', action='append', default=[])
    scaffold_agent_module_parser.add_argument('--with-agents-doc', action='store_true')
    scaffold_agent_module_parser.add_argument('--with-optional-dirs', action='store_true')
    scaffold_agent_module_parser.add_argument('--force', action='store_true')
    scaffold_agent_module_parser.set_defaults(func=handlers.cmd_scaffold_agent_module)
