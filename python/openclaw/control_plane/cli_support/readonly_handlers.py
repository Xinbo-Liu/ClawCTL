#!/usr/bin/env python3
"""Readonly and evidence-oriented control-plane CLI handlers."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from openclaw.control_plane.api import (
    render_agent_access_log_summary,
    render_agent_group_access_summary,
    render_agent_group_acceptance_bindings_summary,
    render_agent_group_release_gates_summary,
    render_agent_groups_summary,
    render_agent_modules_summary,
    render_agents_summary,
    render_control_plane_summary,
    render_job_detail,
    render_jobs_summary,
    render_models_summary,
    render_permission_policies_summary,
    render_run_ledger_summary,
    render_runtime_adapters_summary,
    render_skill_sets_summary,
    render_targets_summary,
    render_toolsets_summary,
)
from openclaw.control_plane.evidence_export import export_agent_group_evidence
from openclaw.control_plane.cli_support import handler_support as cli_support
from openclaw.control_plane.registry import (
    ensure_agent_control_plane_registry,
    ensure_agent_internal_assembly_registry,
)
from openclaw.control_plane.registry.owners import QUALIFIED_REF_SEPARATOR, row_owner_id


def _extension_selector(args: argparse.Namespace) -> str:
    return str(getattr(args, 'extension', '') or '').strip()


def _qualify_ref_for_extension(ref: str, extension_id: str) -> str:
    normalized_ref = str(ref or '').strip()
    normalized_extension = str(extension_id or '').strip()
    if not normalized_ref or not normalized_extension or QUALIFIED_REF_SEPARATOR in normalized_ref:
        return normalized_ref
    return f'{normalized_extension}{QUALIFIED_REF_SEPARATOR}{normalized_ref}'


def _item_matches_extension(item: Any, extension_id: str) -> bool:
    if not isinstance(item, dict):
        return False
    return row_owner_id(item) == extension_id


def _filter_items_by_extension(payload: Any, extension_id: str) -> Any:
    normalized = str(extension_id or '').strip()
    if not normalized or not isinstance(payload, dict):
        return payload
    items = payload.get('items')
    if not isinstance(items, list):
        return payload
    filtered = [item for item in items if _item_matches_extension(item, normalized)]
    result = dict(payload)
    result['items'] = filtered
    if isinstance(result.get('counts'), dict):
        counts = dict(result['counts'])
        if 'items' in counts:
            counts['items'] = len(filtered)
        result['counts'] = counts
    result['filters'] = dict(result.get('filters') or {})
    result['filters']['extension'] = normalized
    return result


def _print_extension_filtered_config_json(args: argparse.Namespace, render: Any) -> int:
    extension_id = _extension_selector(args)
    payload = cli_support._render_config_scoped_payload(args, render)
    return cli_support._print_json(_filter_items_by_extension(payload, extension_id))


def _print_extension_filtered_registry_json(args: argparse.Namespace, render: Any) -> int:
    extension_id = _extension_selector(args)
    payload = render(cli_support._load_registry_from_args(args))
    return cli_support._print_json(_filter_items_by_extension(payload, extension_id))


def cmd_check_agent_control_plane_registry(args: argparse.Namespace) -> int:
    """校验 agent / implementation 视图是否可由 module manifest 派生。"""
    return cli_support._print_json(ensure_agent_control_plane_registry(Path(cli_support._config_path_from_args(args)).resolve(), sync=False))


def cmd_check_agent_assembly_registry(args: argparse.Namespace) -> int:
    """校验 skill / permission / tool 视图是否可由 agent 内部资产派生。"""
    return cli_support._print_json(ensure_agent_internal_assembly_registry(Path(cli_support._config_path_from_args(args)).resolve(), sync=False))


def cmd_validate_registry(args: argparse.Namespace) -> int:
    """校验控制平面 registry 及其交叉引用。"""
    registry = cli_support._load_registry_from_args(args)
    payload = {
        'status': 'ok',
        'configPath': str(registry.get('configPath') or ''),
        'registryPaths': registry.get('registryPaths') if isinstance(registry.get('registryPaths'), dict) else {},
        'registryPathDetails': registry.get('registryPathDetails') if isinstance(registry.get('registryPathDetails'), dict) else {},
        'schemaPaths': registry.get('schemaPaths') if isinstance(registry.get('schemaPaths'), dict) else {},
        'counts': {
            'jobs': len(registry.get('jobs', [])),
            'agents': len(registry.get('agents', [])),
            'agentGroups': len(registry.get('agentGroups', [])),
            'agentModules': len(registry.get('agentModules', [])),
            'skillSets': len(registry.get('skillSets', [])),
            'permissionPolicies': len(registry.get('permissionPolicies', [])),
            'toolsets': len(registry.get('toolsets', [])),
            'runtimeAdapters': len(registry.get('runtimeAdapters', [])),
            'models': len(registry.get('models', [])),
            'targets': len(registry.get('targets', [])),
            'implementations': len(registry.get('implementations', [])),
        },
    }
    return cli_support._print_json(payload)


def cmd_summary(args: argparse.Namespace) -> int:
    """输出控制平面摘要。"""
    return cli_support._print_config_scoped_json(args, render_control_plane_summary)


def cmd_jobs(args: argparse.Namespace) -> int:
    """输出全部 job 摘要。"""
    return _print_extension_filtered_config_json(args, render_jobs_summary)


def cmd_job(args: argparse.Namespace) -> int:
    """输出单个 job 详情。"""
    job_ref = _qualify_ref_for_extension(args.job_id, _extension_selector(args))
    payload = cli_support._render_config_scoped_payload(args, lambda: render_job_detail(job_ref))
    if 'error' in payload:
        return cli_support.fail(f"任务不存在：{job_ref}", 4)
    return cli_support._print_json(payload)


def cmd_agents(args: argparse.Namespace) -> int:
    """输出 agent 契约摘要。"""
    return _print_extension_filtered_config_json(args, render_agents_summary)


def cmd_agent_groups(args: argparse.Namespace) -> int:
    """输出 agent group 契约与运行摘要。"""
    return _print_extension_filtered_config_json(args, render_agent_groups_summary)


def cmd_agent_modules(args: argparse.Namespace) -> int:
    """输出 agent module 契约摘要。"""
    return _print_extension_filtered_config_json(args, render_agent_modules_summary)


def cmd_skill_sets(args: argparse.Namespace) -> int:
    """输出 skill set 契约摘要。"""
    return _print_extension_filtered_config_json(args, render_skill_sets_summary)


def cmd_permission_policies(args: argparse.Namespace) -> int:
    """输出 permission policy 契约摘要。"""
    return _print_extension_filtered_config_json(args, render_permission_policies_summary)


def cmd_toolsets(args: argparse.Namespace) -> int:
    """输出 toolset 契约摘要。"""
    return _print_extension_filtered_config_json(args, render_toolsets_summary)


def cmd_runtime_adapters(args: argparse.Namespace) -> int:
    """输出 runtime adapter 契约摘要。"""
    return cli_support._print_config_scoped_json(args, render_runtime_adapters_summary)


def cmd_agent_access_log(args: argparse.Namespace) -> int:
    """输出 agent 访问日志摘要。"""
    return cli_support._print_config_scoped_json(
        args,
        lambda: render_agent_access_log_summary(
            limit=int(args.limit),
            agent_ref=getattr(args, 'agent_ref', '') or '',
            group_ref=getattr(args, 'group_ref', '') or '',
            job_id=getattr(args, 'job_id', '') or '',
            status=getattr(args, 'status', '') or '',
            source=getattr(args, 'source', '') or '',
        ),
    )


def cmd_agent_group_access(args: argparse.Namespace) -> int:
    """输出按 agent group 聚合的访问视图。"""
    return cli_support._print_config_scoped_json(
        args,
        lambda: render_agent_group_access_summary(
            limit=int(args.limit),
            timeline_limit=int(args.timeline_limit),
            group_ref=getattr(args, 'group_ref', '') or '',
            status=getattr(args, 'status', '') or '',
            source=getattr(args, 'source', '') or '',
        ),
    )


def cmd_agent_group_acceptance_bindings(args: argparse.Namespace) -> int:
    """输出 agent group 的 acceptance 绑定摘要。"""
    return cli_support._print_config_scoped_json(
        args,
        lambda: render_agent_group_acceptance_bindings_summary(
            group_ref=getattr(args, 'group_ref', '') or '',
        ),
    )


def cmd_agent_group_release_gates(args: argparse.Namespace) -> int:
    """输出 agent group 的发布门禁摘要。"""
    return cli_support._print_config_scoped_json(
        args,
        lambda: render_agent_group_release_gates_summary(
            group_ref=getattr(args, 'group_ref', '') or '',
        ),
    )


def cmd_export_agent_group_evidence(args: argparse.Namespace) -> int:
    """把 group 相关 evidence 导出到 control-plane state runtime evidence。"""
    state_root = Path(args.state_root).resolve() if str(args.state_root or '').strip() else None
    return cli_support._print_registry_json(
        args,
        lambda registry: export_agent_group_evidence(
            registry,
            state_root=state_root,
            agent_access_limit=int(args.agent_access_limit),
            group_access_limit=int(args.group_access_limit),
            timeline_limit=int(args.timeline_limit),
        ),
    )


def cmd_implementations(args: argparse.Namespace) -> int:
    """输出 implementation 契约摘要。"""
    return _print_extension_filtered_registry_json(args, lambda registry: {'items': registry.get('implementations', [])})


def cmd_models(args: argparse.Namespace) -> int:
    """输出 model 契约摘要。"""
    return _print_extension_filtered_config_json(args, render_models_summary)


def cmd_targets(args: argparse.Namespace) -> int:
    """输出 target 契约摘要。"""
    return _print_extension_filtered_config_json(args, render_targets_summary)


def cmd_run_ledger(args: argparse.Namespace) -> int:
    """输出 run ledger 摘要。"""
    return cli_support._print_config_scoped_json(args, render_run_ledger_summary)
