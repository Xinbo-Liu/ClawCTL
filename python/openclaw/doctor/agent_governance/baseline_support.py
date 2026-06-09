#!/usr/bin/env python3
"""Support helpers for the agent governance baseline doctor."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.doctor.agent_governance.baseline_payload import (
    build_governance_baseline_payload,
    resolve_governance_baseline_config_path,
)
from openclaw.doctor.agent_governance.registry_alignment import validate_governance_registry_alignment
from openclaw.doctor.agent_governance.repo_baseline import validate_governance_repo_baseline
from openclaw.lib.repo.layout import resolve_repo_root

ROOT_DIR = resolve_repo_root(Path(__file__))


def _run_repo_and_registry_checks(resolved_config_path: Path, errors: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """执行 repo baseline 与 resolved registry 对齐检查。"""
    workspace_registry = validate_governance_repo_baseline(resolved_config_path, errors)
    registry_alignment = validate_governance_registry_alignment(resolved_config_path, errors)
    return workspace_registry, registry_alignment


def run_governance_baseline_check(config_path: Path | None = None) -> dict[str, Any]:
    """运行 agent 治理基线检查并返回统一载荷。"""
    resolved_config_path = resolve_governance_baseline_config_path(config_path, root_dir=ROOT_DIR)
    errors: list[str] = []
    workspace_registry, registry_alignment = _run_repo_and_registry_checks(resolved_config_path, errors)
    views = dict(registry_alignment['views'])
    return build_governance_baseline_payload(
        root_dir=ROOT_DIR,
        resolved_config_path=resolved_config_path,
        alignment_views=views,
        checked_modules=list(registry_alignment['checkedModules']),
        workspace_registry=workspace_registry,
        errors=errors,
    )
