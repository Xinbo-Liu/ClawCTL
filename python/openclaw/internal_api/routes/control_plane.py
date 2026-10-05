#!/usr/bin/env python3
"""控制平面只读路由。"""
from __future__ import annotations

from typing import Any

from openclaw.control_plane.api import (
    render_agent_access_log_summary,
    render_agent_group_access_summary,
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
    """返回 internal-api `/v1/control-plane` 的聚合视图。

    返回：
        返回 dict[str, Any]，包含 scheduler、registry、job、group 和 release gate 的聚合状态。
    """
    return render_control_plane_summary()


def render_jobs() -> dict[str, Any]:
    """返回 internal-api job 列表视图。

    返回：
        返回 dict[str, Any]，包含 control-plane registry 中所有 job 的运行与 artifact 摘要。
    """
    return render_jobs_summary()


def render_job(job_id: str) -> dict[str, Any]:
    """按路由参数返回单个 job 的运行视图。

    参数：
        job_id（str）：control-plane job 标识。

    返回：
        返回 dict[str, Any]，命中时为 job 详情；未命中时为错误 payload。
    """
    return render_job_detail(job_id)


def render_agents() -> dict[str, Any]:
    """返回当前 registry 暴露的 agent 列表。

    返回：
        返回 dict[str, Any]，包含 registry 当前 agents 列表。
    """
    return render_agents_summary()


def render_agent_groups() -> dict[str, Any]:
    """返回 agent group 的成员、健康与发布门禁视图。

    返回：
        返回 dict[str, Any]，包含 group 成员、健康状态和发布门禁摘要。
    """
    return render_agent_groups_summary()


def render_agent_modules() -> dict[str, Any]:
    """返回当前 registry 暴露的 agent module 列表。

    返回：
        返回 dict[str, Any]，包含 registry 当前 agent modules 列表。
    """
    return render_agent_modules_summary()


def render_models() -> dict[str, Any]:
    """返回当前 registry 暴露的模型声明列表。

    返回：
        返回 dict[str, Any]，包含 registry 当前模型声明列表。
    """
    return render_models_summary()


def render_targets() -> dict[str, Any]:
    """返回当前 registry 暴露的 dispatch target 列表。

    返回：
        返回 dict[str, Any]，包含 registry 当前 target 声明列表。
    """
    return render_targets_summary()


def render_run_ledger() -> dict[str, Any]:
    """返回 control-plane run ledger 的执行与 artifact 归并视图。

    返回：
        返回 dict[str, Any]，包含 job 执行、artifact 和最新访问覆盖后的有效状态。
    """
    return render_run_ledger_summary()


def render_runtime_adapters() -> dict[str, Any]:
    """返回当前 registry 暴露的 runtime adapter 列表。

    返回：
        返回 dict[str, Any]，包含 registry 当前 runtime adapters 列表。
    """
    return render_runtime_adapters_summary()


def render_skill_sets() -> dict[str, Any]:
    """返回当前 registry 暴露的 skill set 列表。

    返回：
        返回 dict[str, Any]，包含 registry 当前 skill sets 列表。
    """
    return render_skill_sets_summary()


def render_permission_policies() -> dict[str, Any]:
    """返回当前 registry 暴露的 permission policy 列表。

    返回：
        返回 dict[str, Any]，包含 registry 当前 permission policies 列表。
    """
    return render_permission_policies_summary()


def render_toolsets() -> dict[str, Any]:
    """返回当前 registry 暴露的 toolset 列表。

    返回：
        返回 dict[str, Any]，包含 registry 当前 toolsets 列表。
    """
    return render_toolsets_summary()


def render_agent_access_log(*, limit: int = 50, agent_ref: str = '', group_ref: str = '', job_id: str = '', status: str = '', source: str = '') -> dict[str, Any]:
    """按查询参数返回 agent access log 记录。

    参数：
        limit（int）：最多返回的访问记录数。
        agent_ref（str）：按 agent 标识过滤；空字符串表示不过滤。
        group_ref（str）：按 agent group 标识过滤；空字符串表示不过滤。
        job_id（str）：按 job 标识过滤；空字符串表示不过滤。
        status（str）：按访问状态过滤；空字符串表示不过滤。
        source（str）：按访问来源过滤；空字符串表示不过滤。

    返回：
        返回 dict[str, Any]，包含匹配访问记录、计数和来源路径。
    """
    return render_agent_access_log_summary(limit=limit, agent_ref=agent_ref, group_ref=group_ref, job_id=job_id, status=status, source=source)


def render_agent_group_access(*, limit: int = 200, timeline_limit: int = 20, group_ref: str = '', status: str = '', source: str = '') -> dict[str, Any]:
    """按查询参数返回 agent group 访问聚合。

    参数：
        limit（int）：最多返回的 group 访问聚合条数。
        timeline_limit（int）：每个 group 最多保留的时间线记录数。
        group_ref（str）：按 agent group 标识过滤；空字符串表示不过滤。
        status（str）：按访问状态过滤；空字符串表示不过滤。
        source（str）：按访问来源过滤；空字符串表示不过滤。

    返回：
        返回 dict[str, Any]，包含 group 维度访问聚合、时间线和来源路径。
    """
    return render_agent_group_access_summary(limit=limit, timeline_limit=timeline_limit, group_ref=group_ref, status=status, source=source)


def render_agent_group_release_gates(*, group_ref: str = '') -> dict[str, Any]:
    """按查询参数返回 agent group 发布门禁视图。

    参数：
        group_ref（str）：按 agent group 标识过滤；空字符串表示返回全部。

    返回：
        返回 dict[str, Any]，包含每个 group 的发布门禁状态、失败检查和缺失证据。
    """
    return render_agent_group_release_gates_summary(group_ref=group_ref)
