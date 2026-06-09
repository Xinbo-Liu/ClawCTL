#!/usr/bin/env python3
"""internal-api read-only route dispatch helpers."""
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
    render_agent_group_acceptance_bindings,
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
    if bool(spec.get('authRequired', True)):
        return True
    route_id = str(spec.get('id') or '').strip()
    return not is_unauthenticated_extension_route_allowed(route_id)


def _extension_route_handler(path: str) -> dict[str, Any] | None:
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
    raw = str(value or default).strip()
    try:
        parsed = max(0, int(raw or str(default)))
    except (TypeError, ValueError):
        return None, {'error': error_key, 'value': value}
    return min(parsed, upper_bound), None


def route_requires_auth(path: str) -> bool:
    routes = route_surface()
    if path in (routes['healthz'], routes['readyz']):
        return False
    extension_route = _extension_route_specs().get(path)
    if isinstance(extension_route, dict) and not _extension_route_effective_auth_required(extension_route):
        return False
    return True


def _string_query_arg(query: Mapping[str, list[str]], key: str, default: str = '') -> str:
    return str((query.get(key) or [default])[0] or default)


def _render_config_summary_payload() -> dict[str, Any]:
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
    if path == routes.get('control_plane_agent_group_acceptance_bindings'):
        return render_agent_group_acceptance_bindings(group_ref=_string_query_arg(query, 'groupRef')), HTTPStatus.OK
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
