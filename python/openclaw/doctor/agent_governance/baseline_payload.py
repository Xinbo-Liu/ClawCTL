#!/usr/bin/env python3
"""Payload helpers for the agent governance baseline doctor."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def resolve_governance_baseline_config_path(config_path: Path | None, *, root_dir: Path) -> Path:
    """解析治理基线检查应使用的配置路径。"""
    return Path(config_path or (root_dir / 'config' / 'control_plane' / 'service.json')).resolve()


def build_governance_baseline_payload(
    *,
    root_dir: Path,
    resolved_config_path: Path,
    alignment_views: dict[str, Any],
    checked_modules: list[str],
    workspace_registry: dict[str, Any],
    errors: list[str],
) -> dict[str, Any]:
    """构造治理基线检查结果载荷。"""
    return {
        'ok': not errors,
        'root': str(root_dir),
        'configPath': str(resolved_config_path),
        'counts': {
            'agents': len(alignment_views.get('agents') or []),
            'groups': len(alignment_views.get('groupsById') or {}),
            'implementations': len(alignment_views.get('implementationsById') or {}),
            'jobs': len(alignment_views.get('jobsById') or {}),
            'skillSets': len(alignment_views.get('skillSetsById') or {}),
            'permissionPolicies': len(alignment_views.get('permissionPoliciesById') or {}),
            'toolsets': len(alignment_views.get('toolsetsById') or {}),
            'moduleBridgeDirs': len(checked_modules),
            'workspaceTemplates': len(workspace_registry.get('templateRefs') or []),
        },
        'errors': errors,
    }
