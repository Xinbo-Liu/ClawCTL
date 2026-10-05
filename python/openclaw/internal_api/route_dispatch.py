#!/usr/bin/env python3
"""internal-api 只读请求分派与扩展路由接入。"""
from __future__ import annotations

import os
from http import HTTPStatus
from typing import Any, Mapping, cast

from openclaw.control_plane.api import render_control_plane_summary
from openclaw.control_plane.extensions.api import (
    extension_internal_api_routes,
    import_extension_callable,
)
from openclaw.control_plane.extensions.policy import (
    is_unauthenticated_extension_route_allowed,
)
from openclaw.internal_api.contract import control_plane_job_detail_prefix, route_surface
from openclaw.internal_api.routes.control_plane import (
    render_agent_access_log,
    render_agent_group_access,
    render_agent_group_release_gates,
    render_agent_groups,
    render_agent_modules,
    render_agents,
    render_runtime_adapters,
    render_job,
    render_jobs,
    render_models,
    render_permission_policies,
    render_run_ledger,
    render_skill_sets,
    render_summary,
    render_targets,
    render_toolsets,
)
from openclaw.internal_api.routes.health import render_health, render_ready

ACCESS_LOG_LIMIT_MAX = 500
AGENT_GROUP_ACCESS_LIMIT_MAX = 500
TIMELINE_LIMIT_MAX = 50


def _extension_route_specs() -> dict[str, dict[str, Any]]:
    """加载扩展声明的 internal-api 路由，并拒绝路径冲突。

    返回：
        返回 dict[str, dict[str, Any]]，键为 HTTP path，值为扩展路由 id、模块、callable 与鉴权声明。
    """
    specs: dict[str, dict[str, Any]] = {}
    base_paths = {str(value).strip() for value in route_surface().values() if isinstance(value, str) and str(value).strip()}
    for row in extension_internal_api_routes():
        if not isinstance(row, dict):
            continue
        route_path = str(row.get('path') or '').strip()
        if not route_path:
            continue
        if route_path in base_paths:
            raise RuntimeError(f'extension internal API route 与基座路由冲突：{route_path}')
        materialized = {
            'id': str(row.get('id') or '').strip(),
            'module': str(row.get('module') or '').strip(),
            'callableName': str(row.get('callable') or '').strip(),
            'authRequired': bool(row.get('authRequired', True)),
        }
        existing = specs.get(route_path)
        if existing is not None and existing != materialized:
            raise RuntimeError(f'extension internal API route path 冲突：{route_path}')
        specs[route_path] = materialized
    return specs


def _extension_route_effective_auth_required(spec: dict[str, Any]) -> bool:
    """计算扩展路由最终是否需要 internal-api token。

    参数：
        spec（dict[str, Any]）：扩展路由声明，包含 `id` 与 `authRequired`。

    返回：
        返回 bool，`True` 表示该路由必须通过 token 访问。
    """
    if bool(spec.get('authRequired', True)):
        return True
    route_id = str(spec.get('id') or '').strip()
    return not is_unauthenticated_extension_route_allowed(route_id)


def _extension_route_handler(path: str) -> dict[str, Any] | None:
    """解析指定 path 对应的扩展路由 handler。

    参数：
        path（str）：HTTP 请求路径。

    返回：
        返回 dict[str, Any] | None；命中扩展路由时包含 callable 与鉴权信息，未命中时返回 None。
    """
    spec = _extension_route_specs().get(path)
    if not isinstance(spec, dict):
        return None
    return {
        'id': str(spec.get('id') or '').strip(),
        'callable': import_extension_callable(str(spec.get('module') or '').strip(), str(spec.get('callableName') or '').strip()),
        'authRequired': bool(spec.get('authRequired', True)),
        'effectiveAuthRequired': _extension_route_effective_auth_required(spec),
    }


def _parse_bounded_non_negative_int(value: object, *, default: int, upper_bound: int, error_key: str) -> tuple[int | None, dict[str, Any] | None]:
    """解析带上限的非负整数查询参数。

    参数：
        value（object）：原始查询参数值。
        default（int）：缺省整数值。
        upper_bound（int）：允许返回的最大值。
        error_key（str）：解析失败时写入错误 payload 的错误码。

    返回：
        返回 tuple[int | None, dict[str, Any] | None]；成功时第一项为裁剪后的整数，失败时第二项为错误 payload。
    """
    raw = str(value or default).strip()
    try:
        parsed = max(0, int(raw or str(default)))
    except (TypeError, ValueError):
        return None, {'error': error_key, 'value': value}
    return min(parsed, upper_bound), None


def route_requires_auth(path: str) -> bool:
    """判断 internal-api 路由是否需要鉴权。

    参数：
        path（str）：HTTP 请求路径。

    返回：
        返回 bool，`False` 仅用于 health/ready 和明确允许匿名访问的扩展路由。
    """
    routes = route_surface()
    if path in (routes['healthz'], routes['readyz']):
        return False
    extension_route = _extension_route_specs().get(path)
    if isinstance(extension_route, dict) and not _extension_route_effective_auth_required(extension_route):
        return False
    return True


def _string_query_arg(query: Mapping[str, list[str]], key: str, default: str = '') -> str:
    """读取单值字符串查询参数。

    参数：
        query（Mapping[str, list[str]]）：按 key 存储的 query string 列表值。
        key（str）：要读取的查询参数名。
        default（str）：参数缺失或为空时返回的缺省字符串。

    返回：
        返回 str，取列表第一项；空值回退到 `default`。
    """
    return str((query.get(key) or [default])[0] or default)


def _render_config_summary_payload() -> dict[str, Any]:
    """渲染 internal-api 配置与扩展路由摘要。

    返回：
        返回 dict[str, Any]，包含 token 是否配置、control-plane 摘要和扩展路由鉴权状态。
    """
    extension_routes = _extension_route_specs()
    return {
        'service': 'openclaw-internal-api',
        'controlPlane': render_control_plane_summary(),
        'auth': {'tokenConfigured': bool(os.environ.get('OPENCLAW_INTERNAL_API_TOKEN', '').strip())},
        'extensions': {
            'routes': [
                {
                    'id': str(item.get('id') or '').strip(),
                    'path': route_path,
                    'authRequired': bool(item.get('authRequired', True)),
                    'effectiveAuthRequired': _extension_route_effective_auth_required(item),
                }
                for route_path, item in sorted(extension_routes.items())
            ]
        },
    }


def dispatch_readonly_request(path: str, query: Mapping[str, list[str]]) -> tuple[dict[str, Any], HTTPStatus]:
    """把只读 HTTP 请求分派到基座或扩展路由。

    参数：
        path（str）：HTTP 请求路径。
        query（Mapping[str, list[str]]）：已解析的 query string 参数。

    返回：
        返回 tuple[dict[str, Any], HTTPStatus]，第一项是响应 JSON payload，第二项是 HTTP 状态码。
    """
    routes = route_surface()
    if path == routes['healthz']:
        return render_health(), HTTPStatus.OK
    if path == routes['readyz']:
        return render_ready(), HTTPStatus.OK
    if path == routes['control_plane_summary']:
        return render_summary(), HTTPStatus.OK
    if path == routes['control_plane_jobs']:
        return render_jobs(), HTTPStatus.OK
    if path == routes['control_plane_run_ledger']:
        return render_run_ledger(), HTTPStatus.OK
    if path.startswith(control_plane_job_detail_prefix()):
        job_id = path.rsplit('/', 1)[-1]
        payload = render_job(job_id)
        status = HTTPStatus.OK if 'error' not in payload else HTTPStatus.NOT_FOUND
        return payload, status
    if path == routes.get('control_plane_agents'):
        return render_agents(), HTTPStatus.OK
    if path == routes.get('control_plane_agent_groups'):
        return render_agent_groups(), HTTPStatus.OK
    if path == routes.get('control_plane_agent_modules'):
        return render_agent_modules(), HTTPStatus.OK
    if path == routes.get('control_plane_agent_access_log'):
        limit, error = _parse_bounded_non_negative_int(_string_query_arg(query, 'limit', '50'), default=50, upper_bound=ACCESS_LOG_LIMIT_MAX, error_key='invalid_limit')
        if error is not None or limit is None:
            return cast(dict[str, Any], error), HTTPStatus.BAD_REQUEST
        return render_agent_access_log(
            limit=limit,
            agent_ref=_string_query_arg(query, 'agentRef'),
            group_ref=_string_query_arg(query, 'groupRef'),
            job_id=_string_query_arg(query, 'jobId'),
            status=_string_query_arg(query, 'status'),
            source=_string_query_arg(query, 'source'),
        ), HTTPStatus.OK
    if path == routes.get('control_plane_agent_group_access'):
        limit, error = _parse_bounded_non_negative_int(_string_query_arg(query, 'limit', '200'), default=200, upper_bound=AGENT_GROUP_ACCESS_LIMIT_MAX, error_key='invalid_limit')
        if error is not None or limit is None:
            return cast(dict[str, Any], error), HTTPStatus.BAD_REQUEST
        timeline_limit, timeline_error = _parse_bounded_non_negative_int(_string_query_arg(query, 'timelineLimit', '20'), default=20, upper_bound=TIMELINE_LIMIT_MAX, error_key='invalid_timeline_limit')
        if timeline_error is not None or timeline_limit is None:
            return cast(dict[str, Any], timeline_error), HTTPStatus.BAD_REQUEST
        return render_agent_group_access(
            limit=limit,
            timeline_limit=timeline_limit,
            group_ref=_string_query_arg(query, 'groupRef'),
            status=_string_query_arg(query, 'status'),
            source=_string_query_arg(query, 'source'),
        ), HTTPStatus.OK
    if path == routes.get('control_plane_agent_group_release_gates'):
        return render_agent_group_release_gates(group_ref=_string_query_arg(query, 'groupRef')), HTTPStatus.OK
    if path == routes.get('control_plane_skill_sets'):
        return render_skill_sets(), HTTPStatus.OK
    if path == routes.get('control_plane_permission_policies'):
        return render_permission_policies(), HTTPStatus.OK
    if path == routes.get('control_plane_toolsets'):
        return render_toolsets(), HTTPStatus.OK
    if path == routes['control_plane_runtime_adapters']:
        return render_runtime_adapters(), HTTPStatus.OK
    if path == routes['control_plane_models']:
        return render_models(), HTTPStatus.OK
    if path == routes['control_plane_targets']:
        return render_targets(), HTTPStatus.OK
    if path == routes['config_summary']:
        return _render_config_summary_payload(), HTTPStatus.OK
    extension_route = _extension_route_handler(path)
    if isinstance(extension_route, dict):
        payload = extension_route['callable']()
        if not isinstance(payload, dict):
            raise RuntimeError(f'extension route {path} 必须返回对象')
        return payload, HTTPStatus.OK
    return {'error': 'not_found', 'path': path}, HTTPStatus.NOT_FOUND
