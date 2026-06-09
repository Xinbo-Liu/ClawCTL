#!/usr/bin/env python3
"""Module lifecycle and pluggability control-plane CLI handlers."""
from __future__ import annotations

import argparse
from pathlib import Path

from openclaw.control_plane.cli_support import handler_support as cli_support
from openclaw.control_plane.jobs.surface import apply_job_surface_prune, plan_job_surface_prune
from openclaw.control_plane.modules.lifecycle import (
    apply_agent_module_drop,
    apply_agent_module_prune,
    plan_agent_module_drop,
    plan_agent_module_prune,
)
from openclaw.control_plane.modules.pluggability import build_module_pluggability_summary
from openclaw.control_plane.modules.scaffold import scaffold_agent_module
from openclaw.control_plane.module_scheduler.binding import (
    apply_agent_module_attach,
    apply_agent_module_detach,
    plan_agent_module_attach,
    plan_agent_module_detach,
)
from openclaw.control_plane.registry import CliError


def cmd_agent_module_pluggability(args: argparse.Namespace) -> int:
    """输出 agent module 的可插拔性与解绑影响摘要。"""
    registry = cli_support._load_registry_from_args(args)
    return cli_support._print_json(build_module_pluggability_summary(registry, module_ref=args.module_ref))


def cmd_scaffold_agent_module(args: argparse.Namespace) -> int:
    """生成新的 agent module 最小脚手架。"""
    payload = scaffold_agent_module(
        repo_root=Path(args.repo_root).resolve() if str(args.repo_root or '').strip() else None,
        config_path=cli_support._required_config_path_from_args(args),
        module_ref=args.module_ref,
        title=args.title,
        owner_domain=args.owner_domain,
        module_kind=args.module_kind,
        entrypoint_kind=args.entrypoint_kind,
        runtime_adapter_ref=args.runtime_adapter_ref,
        executor_kind=args.executor_kind,
        operation_ref=args.operation_ref,
        description=args.description,
        version=args.version,
        network=bool(args.network),
        model_required=bool(args.model_required),
        external_dispatch=bool(args.external_dispatch),
        filesystem_write=list(args.filesystem_write or []),
        with_agents_doc=bool(args.with_agents_doc),
        with_optional_dirs=bool(args.with_optional_dirs),
        force=bool(args.force),
    )
    return cli_support._print_json(payload)


def cmd_agent_module_attach(args: argparse.Namespace) -> int:
    """将 standalone module 挂接到 scheduler / group / target。"""
    if bool(args.retry_enabled) and bool(args.retry_disabled):
        raise CliError('--retry-enabled 与 --retry-disabled 不能同时使用', 2)
    payload = (apply_agent_module_attach if bool(args.write) else plan_agent_module_attach)(
        config_path=cli_support._required_config_path_from_args(args),
        repo_root=Path(args.repo_root).resolve() if str(args.repo_root or '').strip() else None,
        module_ref=args.module_ref,
        operation_ref=args.operation_ref,
        job_id=args.job_id,
        job_title=args.job_title,
        schedule_expr=args.schedule_expr,
        schedule_tz=args.schedule_tz,
        group_ref=args.group_ref,
        group_placement=args.group_placement,
        before_job=args.insert_before_job,
        after_job=args.insert_after_job,
        recovery_after_minutes=args.recovery_after_minutes if args.recovery_after_minutes is not None else None,
        recovery_action_kind=args.recovery_action_kind,
        target_binding_ref=args.target_binding_ref,
        depends_on=list(args.depends_on or []),
        order=args.order if args.order is not None else None,
        timeout_seconds=args.timeout_seconds if args.timeout_seconds is not None else None,
        retry_enabled=(True if bool(args.retry_enabled) else False if bool(args.retry_disabled) else None),
        retry_max_attempts=args.retry_max_attempts if args.retry_max_attempts is not None else None,
        retry_backoff_seconds=[int(item) for item in (args.retry_backoff_seconds or [])],
        run_artifact_root=args.run_artifact_root,
        latest_alias=args.latest_alias,
        retention_days=int(args.retention_days),
        retryable_classes=list(args.retryable_class or []),
        terminal_classes=list(args.terminal_class or []),
        model_profile_ref=args.model_profile_ref,
        job_file_prefix=args.job_file_prefix,
    )
    return cli_support._print_json(payload)


def cmd_agent_module_detach(args: argparse.Namespace) -> int:
    """将 scheduler-bound module 从 scheduler / group / target 卸载。"""
    payload = (apply_agent_module_detach if bool(args.write) else plan_agent_module_detach)(
        config_path=cli_support._required_config_path_from_args(args),
        repo_root=Path(args.repo_root).resolve() if str(args.repo_root or '').strip() else None,
        module_ref=args.module_ref,
        job_id=args.job_id,
        operation_ref=args.operation_ref,
    )
    return cli_support._print_json(payload)


def cmd_agent_module_prune(args: argparse.Namespace) -> int:
    """收紧 module 可选面到最小合同。"""
    payload = (apply_agent_module_prune if bool(args.write) else plan_agent_module_prune)(
        config_path=cli_support._required_config_path_from_args(args),
        repo_root=Path(args.repo_root).resolve() if str(args.repo_root or '').strip() else None,
        module_ref=args.module_ref,
    )
    return cli_support._print_json(payload)


def cmd_agent_module_drop(args: argparse.Namespace) -> int:
    """删除 standalone module 及其本地实现面。"""
    payload = (apply_agent_module_drop if bool(args.write) else plan_agent_module_drop)(
        config_path=cli_support._required_config_path_from_args(args),
        repo_root=Path(args.repo_root).resolve() if str(args.repo_root or '').strip() else None,
        module_ref=args.module_ref,
    )
    return cli_support._print_json(payload)


def cmd_job_surface_prune(args: argparse.Namespace) -> int:
    """收紧 job manifest 到最小合同。"""
    payload = (apply_job_surface_prune if bool(args.write) else plan_job_surface_prune)(
        config_path=Path(cli_support._config_path_from_args(args)).resolve(),
        repo_root=Path(args.repo_root).resolve() if str(args.repo_root or '').strip() else None,
        job_id=args.job_id,
    )
    return cli_support._print_json(payload)
