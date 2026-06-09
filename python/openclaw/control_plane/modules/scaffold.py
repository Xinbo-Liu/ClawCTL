#!/usr/bin/env python3
"""Agent module 脚手架生成器。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.control_plane.modules.scaffold_builders import (
    build_scaffold_payloads,
    build_scaffold_write_plan,
    execute_write_plan,
    normalize_scaffold_request,
    resolve_scaffold_layout,
    runtime_adapter_specs_for,
)
from openclaw.control_plane.modules.scaffold_models import display_relative_path

_runtime_adapter_specs = runtime_adapter_specs_for


def scaffold_agent_module(
    *,
    repo_root: Path | None = None,
    config_path: Path | None = None,
    module_ref: str,
    title: str,
    owner_domain: str,
    module_kind: str = 'worker',
    entrypoint_kind: str = 'python_cli',
    runtime_adapter_ref: str = 'python_module',
    executor_kind: str | None = None,
    operation_ref: str = 'run_default',
    description: str = '',
    version: str = 'v1',
    network: bool = False,
    model_required: bool = False,
    external_dispatch: bool = False,
    filesystem_write: list[str] | None = None,
    with_agents_doc: bool = False,
    with_optional_dirs: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    request = normalize_scaffold_request(
        repo_root=repo_root,
        config_path=config_path,
        module_ref=module_ref,
        title=title,
        owner_domain=owner_domain,
        module_kind=module_kind,
        entrypoint_kind=entrypoint_kind,
        runtime_adapter_ref=runtime_adapter_ref,
        executor_kind=executor_kind,
        operation_ref=operation_ref,
        description=description,
        version=version,
        network=network,
        model_required=model_required,
        external_dispatch=external_dispatch,
        filesystem_write=filesystem_write,
        with_agents_doc=with_agents_doc,
        with_optional_dirs=with_optional_dirs,
        force=force,
    )
    layout = resolve_scaffold_layout(
        request,
        runtime_specs_builder=_runtime_adapter_specs,
    )
    payloads = build_scaffold_payloads(request, layout)
    write_plan = build_scaffold_write_plan(layout, payloads)
    written_paths = execute_write_plan(write_plan)
    return {
        'status': 'ok',
        'moduleRef': request.module_ref,
        'repoRoot': str(request.repo_root),
        'moduleDir': str(layout.module_dir),
        'moduleKind': request.module_kind,
        'runtimeAdapterRef': request.runtime_adapter_ref,
        'entrypointKind': request.entrypoint_kind,
        'schedulerBound': False,
        'writtenPaths': [display_relative_path(path, request.repo_root) for path in written_paths],
    }


__all__ = ['scaffold_agent_module', '_runtime_adapter_specs']
