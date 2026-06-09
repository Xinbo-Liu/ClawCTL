#!/usr/bin/env python3
"""Router reference rendering helpers for reference_specs surfaces."""
from __future__ import annotations

from typing import Any, Callable

from openclaw.control_plane.governance_surfaces import load_router_route_surface
from openclaw.docs.support.reference_specs_support.io import load_specs, normalize_spacing


def router_route_specs(*, load_specs_fn: Callable[[str], Any] = load_specs) -> dict[str, Any]:
    data = load_specs_fn('router_route_specs.json')
    if not isinstance(data, dict):
        raise ValueError('router_route_specs.json 顶层必须为对象')
    return data


def render_explicit_route_list(*, config_path=None) -> list[str]:
    spec = load_router_route_surface(config_path=config_path)
    result: list[str] = []
    for item in spec.get('explicitRoutes') or []:
        note_suffix = f"（{'；'.join(item.get('notes') or [])}）" if item.get('notes') else ''
        result.append(f"- `{item['route']}` -> `{item['target']}`：{item['summary']}{note_suffix}")
    return result


def render_automatic_route_list(*, config_path=None) -> list[str]:
    spec = load_router_route_surface(config_path=config_path)
    return [f"- `{item['target']}`：当 {item['when']} 时，{item['action']}" for item in spec.get('automaticRoutes') or []]


def render_health_aware_list(*, config_path=None) -> list[str]:
    return [f'- {item}' for item in (load_router_route_surface(config_path=config_path).get('healthAwareRules') or [])]


def render_router_workspace_section(
    *,
    config_path=None,
    load_router_route_surface_fn: Callable[..., dict[str, Any]] = load_router_route_surface,
    render_explicit_route_list_fn: Callable[..., list[str]] = render_explicit_route_list,
    render_automatic_route_list_fn: Callable[..., list[str]] = render_automatic_route_list,
    render_health_aware_list_fn: Callable[..., list[str]] = render_health_aware_list,
) -> str:
    spec = load_router_route_surface_fn(config_path=config_path)
    lines = [
        '### 1) 显式路由指令',
        '',
        *render_explicit_route_list_fn(config_path=config_path),
        '',
        '### 2) 自动分流',
        '',
        *render_automatic_route_list_fn(config_path=config_path),
        '',
        '### 3) 健康感知分流',
        '',
        *render_health_aware_list_fn(config_path=config_path),
        '',
        f"> {str(spec.get('description') or '').strip()}",
        '',
    ]
    return normalize_spacing('\n'.join(lines))


def render_router_route_reference_doc(
    *,
    config_path=None,
    router_route_specs_fn: Callable[[], dict[str, Any]] = router_route_specs,
    render_explicit_route_list_fn: Callable[..., list[str]] = render_explicit_route_list,
    render_automatic_route_list_fn: Callable[..., list[str]] = render_automatic_route_list,
    render_health_aware_list_fn: Callable[..., list[str]] = render_health_aware_list,
) -> str:
    spec = router_route_specs_fn()
    lines = [
        '# Router 路由参考',
        '',
        f"## {str(spec.get('heading') or '').strip()}",
        '',
        str(spec.get('description') or ''),
        '',
        '这是一页路由规则索引，用来查询 route 指令、自动分流结果与使用边界。首次部署说明与日常值守步骤以对应专题页为准。',
        '',
        '若你还在判断当前应该执行哪条链路，请先看 `docs/operations/dispatch-targets.md` 或 `docs/operations/troubleshooting.md`；当确认需要解释 route 或 handoff 时，再回本页查询。',
        '',
        '## 显式路由指令',
        '',
        *render_explicit_route_list_fn(config_path=config_path),
        '',
        '## 自动分流结果',
        '',
        *render_automatic_route_list_fn(config_path=config_path),
        '',
        '## 健康感知规则',
        '',
        *render_health_aware_list_fn(config_path=config_path),
        '',
        '## 使用边界',
        '',
        '- `router_local_ro` 属于基座工作区模板；仓内 extension 如需补充额外路由目标，只能通过正式 extension 机制与仓内合同 service 的显式 `--config-path` 接入。',
        '- `router_local_ro` 只负责初始分流与最小信息收集，不直接代替 control-plane 正式执行入口执行主链路。',
        '- 对需要全权限变更的场景，router 只负责提示进入受控变更流程；权限升级统一由对应流程承接。',
        '- 当存在人工核验任务清单时，router 应把任务内容直接内联到转交指令里，而不是只丢文件路径。',
        '',
    ]
    return normalize_spacing('\n'.join(lines))
