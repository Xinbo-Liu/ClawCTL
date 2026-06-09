#!/usr/bin/env python3
"""控制平面只读路由。"""
from __future__ import annotations

from typing import Any

from openclaw.control_plane.api import (
    render_agent_access_log_summary,
    render_agent_group_access_summary,
    render_agent_group_acceptance_bindings_summary,
    render_agent_group_release_gates_summary,
    render_agent_groups_summary,
    render_agent_modules_summary,
    render_agents_summary,
    render_runtime_adapters_summary,
    render_control_plane_summary,
    render_job_detail,
    render_jobs_summary,
    render_permission_policies_summary,
    render_skill_sets_summary,
    render_toolsets_summary,
    render_models_summary,
    render_run_ledger_summary,
    render_targets_summary,
)


def render_summary() -> dict[str, Any]:
    """渲染控制平面摘要响应。"""
    return render_control_plane_summary()


def render_jobs() -> dict[str, Any]:
    """渲染全部 job 摘要响应。"""
    return render_jobs_summary()


def render_job(job_id: str) -> dict[str, Any]:
    """渲染单个 job 详情响应。"""
    return render_job_detail(job_id)


def render_agents() -> dict[str, Any]:
    """渲染 agent 摘要响应。"""
    return render_agents_summary()


def render_agent_groups() -> dict[str, Any]:
    """渲染 agent group 摘要响应。"""
    return render_agent_groups_summary()


def render_agent_modules() -> dict[str, Any]:
    """渲染 agent module 摘要响应。"""
    return render_agent_modules_summary()


def render_models() -> dict[str, Any]:
    """渲染 model 摘要响应。"""
    return render_models_summary()


def render_targets() -> dict[str, Any]:
    """渲染 target 摘要响应。"""
    return render_targets_summary()


def render_run_ledger() -> dict[str, Any]:
    """渲染 run ledger 响应。"""
    return render_run_ledger_summary()


def render_runtime_adapters() -> dict[str, Any]:
    """渲染 runtime adapter 摘要响应。"""
    return render_runtime_adapters_summary()


def render_skill_sets() -> dict[str, Any]:
    """渲染 skill set 摘要响应。"""
    return render_skill_sets_summary()


def render_permission_policies() -> dict[str, Any]:
    """渲染 permission policy 摘要响应。"""
    return render_permission_policies_summary()


def render_toolsets() -> dict[str, Any]:
    """渲染 toolset 摘要响应。"""
    return render_toolsets_summary()


def render_agent_access_log(*, limit: int = 50, agent_ref: str = '', group_ref: str = '', job_id: str = '', status: str = '', source: str = '') -> dict[str, Any]:
    """渲染 agent access log 响应。"""
    return render_agent_access_log_summary(limit=limit, agent_ref=agent_ref, group_ref=group_ref, job_id=job_id, status=status, source=source)


def render_agent_group_access(*, limit: int = 200, timeline_limit: int = 20, group_ref: str = '', status: str = '', source: str = '') -> dict[str, Any]:
    """渲染 agent group access 响应。"""
    return render_agent_group_access_summary(limit=limit, timeline_limit=timeline_limit, group_ref=group_ref, status=status, source=source)




def render_agent_group_acceptance_bindings(*, group_ref: str = '') -> dict[str, Any]:
    """渲染 agent group acceptance 绑定响应。"""
    return render_agent_group_acceptance_bindings_summary(group_ref=group_ref)

def render_agent_group_release_gates(*, group_ref: str = '') -> dict[str, Any]:
    """渲染 agent group 发布门禁响应。"""
    return render_agent_group_release_gates_summary(group_ref=group_ref)
