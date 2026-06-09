#!/usr/bin/env python3
"""控制平面 CLI 命令分发入口。"""
from __future__ import annotations

from openclaw.control_plane.cli_support.execution_handlers import (
    cmd_due_preview,
    cmd_resolve_job_command,
    cmd_resolve_job_plan,
    cmd_resolve_target_operation,
    cmd_run_agent_runtime,
    cmd_run_target_operation,
)
from openclaw.control_plane.cli_support.extension_env_handlers import (
    cmd_extension_env_ensure,
    cmd_extension_env_prune,
    cmd_extension_env_status,
    cmd_extension_env_verify,
)
from openclaw.control_plane.cli_support.handler_support import fail
from openclaw.control_plane.cli_support.module_handlers import (
    cmd_agent_module_attach,
    cmd_agent_module_detach,
    cmd_agent_module_drop,
    cmd_agent_module_pluggability,
    cmd_agent_module_prune,
    cmd_job_surface_prune,
    cmd_scaffold_agent_module,
)
from openclaw.control_plane.cli_support.readonly_handlers import (
    cmd_agent_access_log,
    cmd_agent_group_access,
    cmd_agent_group_acceptance_bindings,
    cmd_agent_group_release_gates,
    cmd_agent_groups,
    cmd_agent_modules,
    cmd_agents,
    cmd_check_agent_assembly_registry,
    cmd_check_agent_control_plane_registry,
    cmd_export_agent_group_evidence,
    cmd_implementations,
    cmd_job,
    cmd_jobs,
    cmd_models,
    cmd_permission_policies,
    cmd_run_ledger,
    cmd_runtime_adapters,
    cmd_skill_sets,
    cmd_summary,
    cmd_targets,
    cmd_toolsets,
    cmd_validate_registry,
)
from openclaw.control_plane.registry import CliError
