#!/usr/bin/env python3
"""Agent module 生命周期管理入口。"""
from __future__ import annotations

from openclaw.control_plane.modules.lifecycle_filesystem import cleanup_empty_dirs as _cleanup_empty_dirs
from openclaw.control_plane.modules.lifecycle_filesystem import collect_drop_files as _collect_drop_files
from openclaw.control_plane.modules.lifecycle_optional_surfaces import (
    OPTIONAL_SURFACE_RELPATHS,
    inspect_module_optional_surfaces,
    is_agents_boilerplate as _is_agents_boilerplate,
    is_constraints_boilerplate as _is_constraints_boilerplate,
    is_contracts_boilerplate as _is_contracts_boilerplate,
    is_docs_boilerplate as _is_docs_boilerplate,
    is_tests_boilerplate as _is_tests_boilerplate,
)
from openclaw.control_plane.modules.lifecycle_plans import (
    apply_agent_module_drop,
    apply_agent_module_prune,
    build_drop_plan as _build_drop_plan,
    build_prune_plan as _build_prune_plan,
    plan_agent_module_drop,
    plan_agent_module_prune,
    resolve_modules as _resolve_modules,
)
from openclaw.control_plane.modules.lifecycle_references import (
    find_external_module_references,
    module_job_refs as _module_job_refs,
    module_owned_surface_roots,
    module_target_binding_refs as _module_target_binding_refs,
)
