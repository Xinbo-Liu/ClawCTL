#!/usr/bin/env python3
"""internal-api 固定合同读取。"""
from __future__ import annotations

import json
from pathlib import Path

from openclaw.lib.repo.layout import resolve_repo_root
from typing import Any

from openclaw.control_plane.extensions.api import extension_internal_api_routes

ROOT_DIR = resolve_repo_root(Path(__file__))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_contract(root_dir: Path = ROOT_DIR) -> dict[str, Any]:
    contract_path = root_dir / "config" / "services" / "internal_api.json"
    contract = read_json(contract_path)
    if not isinstance(contract, dict):
        raise ValueError("internal_api.json 顶层必须为对象")
    for key in ("service", "control_plane", "routes"):
        if not isinstance(contract.get(key), dict):
            raise ValueError(f"internal_api.json.{key} 必须为对象")
    generated = contract.get("generated_artifacts")
    if not isinstance(generated, dict) or not str(generated.get("runtime_contract_doc") or "").strip():
        raise ValueError("internal_api.json.generated_artifacts.runtime_contract_doc 必须存在")
    return contract


def route_surface(root_dir: Path = ROOT_DIR, *, include_extensions: bool = False) -> dict[str, str]:
    contract = read_contract(root_dir)
    routes = contract["routes"]
    control_plane = contract["control_plane"]
    surface = {
        "healthz": str(routes.get("healthzRoute") or "/healthz"),
        "readyz": str(routes.get("readyzRoute") or "/readyz"),
        "control_plane_summary": str(control_plane.get("summaryRoute") or "/v1/control-plane/summary"),
        "control_plane_jobs": str(control_plane.get("jobsRoute") or "/v1/control-plane/jobs"),
        "control_plane_job_detail": str(control_plane.get("jobDetailRouteTemplate") or "/v1/control-plane/jobs/<job_id>"),
        "control_plane_runtime_adapters": str(control_plane.get("runtimeAdaptersRoute") or "/v1/control-plane/runtime-adapters"),
        "control_plane_models": str(control_plane.get("modelsRoute") or "/v1/control-plane/models"),
        "control_plane_targets": str(control_plane.get("targetsRoute") or "/v1/control-plane/targets"),
        "control_plane_run_ledger": str(control_plane.get("runLedgerRoute") or "/v1/control-plane/run-ledger"),
        "config_summary": str(routes.get("configSummaryRoute") or "/v1/config/summary"),
        "control_plane_agents": str(control_plane.get("agentsRoute") or "/v1/control-plane/agents"),
        "control_plane_agent_groups": str(control_plane.get("agentGroupsRoute") or "/v1/control-plane/agent-groups"),
        "control_plane_agent_modules": str(control_plane.get("agentModulesRoute") or "/v1/control-plane/agent-modules"),
        "control_plane_agent_access_log": str(control_plane.get("agentAccessLogRoute") or "/v1/control-plane/agent-access-log"),
        "control_plane_agent_group_access": str(control_plane.get("agentGroupAccessRoute") or "/v1/control-plane/agent-group-access"),
        "control_plane_agent_group_acceptance_bindings": str(control_plane.get("agentGroupAcceptanceBindingsRoute") or "/v1/control-plane/agent-group-acceptance-bindings"),
        "control_plane_agent_group_release_gates": str(control_plane.get("agentGroupReleaseGatesRoute") or "/v1/control-plane/agent-group-release-gates"),
        "control_plane_skill_sets": str(control_plane.get("skillSetsRoute") or "/v1/control-plane/skill-sets"),
        "control_plane_permission_policies": str(control_plane.get("permissionPoliciesRoute") or "/v1/control-plane/permission-policies"),
        "control_plane_toolsets": str(control_plane.get("toolsetsRoute") or "/v1/control-plane/toolsets"),
    }
    if include_extensions:
        for row in extension_internal_api_routes():
            if not isinstance(row, dict):
                continue
            route_id = str(row.get("id") or "").strip()
            route_path = str(row.get("path") or "").strip()
            if route_id and route_path:
                surface[route_id] = route_path
    return surface


def control_plane_job_detail_prefix(root_dir: Path = ROOT_DIR) -> str:
    template = route_surface(root_dir)["control_plane_job_detail"]
    return template.split("<", 1)[0]
