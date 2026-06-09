#!/usr/bin/env python3
"""Public control-plane registry API."""
from __future__ import annotations

from pathlib import Path

from openclaw.control_plane.registry.command_specs import (
    DIRECT_CONTROL_PLANE_EXEC,
    SCHEDULER_SERVICE_EXEC,
    OpenClawCommandSpec,
)
from openclaw.control_plane.registry.commands import (
    build_agent_runtime_command,
    build_agent_runtime_command_spec,
    resolve_dispatch_target_binding_ref,
    resolve_dispatch_target_operation_command,
    resolve_job_command,
    resolve_job_execution_plan,
    resolve_target_binding_ref_for_operation,
    resolve_target_operation_command,
)
from openclaw.lib.cli.common import CliError
from openclaw.lib.repo.layout import resolve_control_plane_service_config_path


def control_plane_config_path() -> Path:
    return resolve_control_plane_service_config_path(Path(__file__))


def load_registry(config_path: Path | None = None) -> dict[str, object]:
    resolved = Path(config_path).resolve() if config_path is not None else control_plane_config_path()
    return load_registry_from_path(resolved)


def ensure_agent_internal_assembly_registry(config_path: Path, *, sync: bool = False) -> dict[str, object]:
    from openclaw.control_plane.registry_loader import ensure_agent_internal_assembly_registry as impl

    return impl(config_path, sync=sync)


def ensure_agent_control_plane_registry(config_path: Path, *, sync: bool = False) -> dict[str, object]:
    from openclaw.control_plane.registry_loader import ensure_agent_control_plane_registry as impl

    return impl(config_path, sync=sync)


def load_registry_from_path(config_path: Path) -> dict[str, object]:
    from openclaw.control_plane.registry_loader import load_registry_from_path as impl

    return impl(config_path)


__all__ = [
    'CliError',
    'DIRECT_CONTROL_PLANE_EXEC',
    'SCHEDULER_SERVICE_EXEC',
    'OpenClawCommandSpec',
    'build_agent_runtime_command',
    'build_agent_runtime_command_spec',
    'control_plane_config_path',
    'ensure_agent_control_plane_registry',
    'ensure_agent_internal_assembly_registry',
    'load_registry',
    'load_registry_from_path',
    'resolve_dispatch_target_binding_ref',
    'resolve_dispatch_target_operation_command',
    'resolve_job_command',
    'resolve_job_execution_plan',
    'resolve_target_binding_ref_for_operation',
    'resolve_target_operation_command',
]
