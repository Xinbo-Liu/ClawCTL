#!/usr/bin/env python3
"""Registry loader helpers for extension runtime assets."""
from __future__ import annotations

from pathlib import Path

from openclaw.control_plane.runtime.adapter_registry import (
    RuntimeAdapterRegistryError,
    RuntimeAdapterSpec,
    load_runtime_adapter_registry,
    runtime_adapter_specs,
)
from openclaw.lib.cli.common import CliError


def _merge_runtime_adapter_registries(
    paths: list[Path],
    *,
    schema_path: Path,
) -> tuple[list[dict[str, object]], dict[str, dict[str, object]], dict[str, RuntimeAdapterSpec]]:
    rows: list[dict[str, object]] = []
    rows_by_id: dict[str, dict[str, object]] = {}
    specs_by_id: dict[str, RuntimeAdapterSpec] = {}
    for path in paths:
        try:
            payload = load_runtime_adapter_registry(path, schema_path=schema_path)
        except RuntimeAdapterRegistryError as exc:
            raise CliError(str(exc), 2) from exc
        specs = runtime_adapter_specs(payload)
        for row in payload.get('adapters') or []:
            if not isinstance(row, dict):
                continue
            adapter_id = str(row.get('id') or '').strip()
            if adapter_id in rows_by_id:
                raise CliError(f'control-plane runtime adapter id duplicated: {adapter_id}', 2)
            materialized = dict(row)
            materialized['sourcePath'] = str(path)
            rows.append(materialized)
            rows_by_id[adapter_id] = materialized
            if adapter_id in specs:
                specs_by_id[adapter_id] = specs[adapter_id]
    return rows, rows_by_id, specs_by_id


def _merge_job_runners(extensions: list[dict[str, object]]) -> tuple[list[dict[str, object]], dict[str, dict[str, object]], list[str]]:
    rows: list[dict[str, object]] = []
    rows_by_id: dict[str, dict[str, object]] = {}
    binding_runner_ids: list[str] = []
    for manifest in extensions:
        for row in manifest.get('jobRunners') or []:
            if not isinstance(row, dict):
                continue
            runner_id = str(row.get('id') or '').strip()
            if not runner_id:
                continue
            if runner_id in rows_by_id:
                raise CliError(f'control-plane job runner id duplicated: {runner_id}', 2)
            materialized = dict(row)
            materialized['extensionId'] = str(manifest.get('id') or '')
            materialized['sourcePath'] = str(manifest.get('sourcePath') or '')
            rows.append(materialized)
            rows_by_id[runner_id] = materialized
            if bool(materialized.get('handlesAgentBindings', False)):
                binding_runner_ids.append(runner_id)
    return rows, rows_by_id, binding_runner_ids
