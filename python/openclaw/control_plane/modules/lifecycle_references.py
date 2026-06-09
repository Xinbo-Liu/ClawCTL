#!/usr/bin/env python3
"""Structured reference discovery for agent module lifecycle operations."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.control_plane.modules.change_set import read_json_object, relative_to_repo
from openclaw.lib.io.json_access import json_array, json_object
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.path_contracts import resolve_path_contract


def module_job_refs(module_payload: dict[str, Any]) -> list[str]:
    operations = json_object(module_payload.get('operations'))
    result: list[str] = []
    for payload in operations.values():
        if not isinstance(payload, dict):
            continue
        job_bindings = json_object(payload.get('jobBindings'))
        for job_ref in job_bindings:
            normalized_job_ref = str(job_ref or '').strip()
            if normalized_job_ref and normalized_job_ref not in result:
                result.append(normalized_job_ref)
    return result


def module_target_binding_refs(module_payload: dict[str, Any]) -> list[str]:
    operations = json_object(module_payload.get('operations'))
    result: list[str] = []
    for payload in operations.values():
        if not isinstance(payload, dict):
            continue
        job_bindings = json_object(payload.get('jobBindings'))
        for binding in job_bindings.values():
            if not isinstance(binding, dict):
                continue
            target_binding_ref = str(binding.get('targetBindingRef') or '').strip()
            if target_binding_ref and target_binding_ref not in result:
                result.append(target_binding_ref)
    return result


def module_logic_source_paths(module_payload: dict[str, Any]) -> list[Path]:
    source_path = Path(str(module_payload.get('sourcePath') or '')).resolve()
    module_dir = source_path.parent
    logic = json_object(module_payload.get('logic'))
    result: list[Path] = []
    for item in (logic.get('sourcePaths') or []):
        rel = str(item or '').strip()
        if not rel:
            continue
        target = resolve_path_contract(rel, base_dir=module_dir, start_path=module_dir)
        if target is None:
            continue
        if target not in result:
            result.append(target)
    return result


def registry_module_rows(registry: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(registry, dict):
        return []
    rows = registry.get('agentModules')
    if isinstance(rows, list) and rows:
        return [item for item in rows if isinstance(item, dict)]
    rows_by_id = registry.get('agentModulesById')
    if isinstance(rows_by_id, dict):
        return [item for item in rows_by_id.values() if isinstance(item, dict)]
    return []


def shared_logic_source_paths(registry: dict[str, Any] | None) -> set[Path]:
    counts: dict[Path, int] = {}
    for module_row in registry_module_rows(registry):
        source_path = Path(str(module_row.get('sourcePath') or '')).resolve()
        if not source_path.exists():
            continue
        try:
            module_payload = read_json_object(source_path)
        except OSError:
            continue
        for target in module_logic_source_paths({**module_payload, 'sourcePath': str(source_path)}):
            counts[target] = counts.get(target, 0) + 1
    return {path for path, count in counts.items() if count > 1}


def module_owned_logic_source_paths(module_payload: dict[str, Any], *, registry: dict[str, Any] | None = None) -> list[Path]:
    shared_paths = shared_logic_source_paths(registry) if registry is not None else set()
    result: list[Path] = []
    for target in module_logic_source_paths(module_payload):
        if target in shared_paths:
            continue
        if target not in result:
            result.append(target)
    return result


def module_owned_surface_roots(
    module_payload: dict[str, Any],
    *,
    repo_root: Path | None = None,
    registry: dict[str, Any] | None = None,
) -> list[Path]:
    effective_repo_root = resolve_repo_root(Path(__file__)) if repo_root is None else Path(repo_root).resolve()
    source_path = Path(str(module_payload.get('sourcePath') or '')).resolve()
    module_dir = source_path.parent
    roots: list[Path] = [module_dir]
    _ = effective_repo_root
    for target in module_owned_logic_source_paths(module_payload, registry=registry):
        if target not in roots:
            roots.append(target)
    return roots


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _normalize_text(value: Any) -> str:
    return str(value or '').strip()


def _append_unique_ref(rows: list[str], seen: set[str], value: str) -> None:
    normalized = str(value or '').strip()
    if not normalized or normalized in seen:
        return
    seen.add(normalized)
    rows.append(normalized)


def _registry_row_source_label(repo_root: Path, row: dict[str, Any], *, fallback: str) -> str:
    rel = relative_to_repo(repo_root, row.get('sourcePath'))
    return rel or fallback


def _module_structured_paths(module_payload: dict[str, Any]) -> list[tuple[str, Path]]:
    source_path = Path(str(module_payload.get('sourcePath') or '')).resolve()
    module_dir = source_path.parent
    assets = json_object(module_payload.get('assets'))
    logic = json_object(module_payload.get('logic'))
    rows: list[tuple[str, Path]] = []
    for key in sorted(assets):
        rel = _normalize_text(assets.get(key))
        if not rel or not str(key).endswith('Path'):
            continue
        rows.append((f'assets.{key}', (module_dir / rel).resolve()))
    for index, item in enumerate(json_array(logic.get('sourcePaths'))):
        rel = _normalize_text(item)
        if not rel:
            continue
        target = resolve_path_contract(rel, base_dir=module_dir, start_path=module_dir)
        if target is not None:
            rows.append((f'logic.sourcePaths[{index}]', target.resolve()))
    return rows


def _module_runtime_module_ref(module_payload: dict[str, Any]) -> str:
    control_plane = json_object(module_payload.get('controlPlane'))
    implementation = json_object(control_plane.get('implementation'))
    runtime = json_object(implementation.get('runtime'))
    config = json_object(runtime.get('config'))
    return _normalize_text(config.get('module'))


def _module_owned_path_refs(
    repo_root: Path,
    module_payload: dict[str, Any],
    *,
    registry: dict[str, Any] | None = None,
) -> list[str]:
    owned_roots = module_owned_surface_roots(module_payload, repo_root=repo_root, registry=registry)
    current_module_ref = _normalize_text(module_payload.get('id'))
    refs: list[str] = []
    seen: set[str] = set()
    for other_module in registry_module_rows(registry):
        other_module_ref = _normalize_text(other_module.get('id'))
        if other_module_ref == current_module_ref:
            continue
        source_label = _registry_row_source_label(repo_root, other_module, fallback=f'agent module {other_module_ref or "<unknown>"}')
        for field_label, target_path in _module_structured_paths(other_module):
            if any(target_path.resolve() == root.resolve() or _path_is_within(target_path, root) for root in owned_roots):
                _append_unique_ref(
                    refs,
                    seen,
                    f'{source_label}: {field_label} -> {relative_to_repo(repo_root, target_path)}',
                )
    return refs


def _module_structured_identifier_refs(
    repo_root: Path,
    module_payload: dict[str, Any],
    *,
    registry: dict[str, Any] | None = None,
) -> list[str]:
    current_module_ref = _normalize_text(module_payload.get('id'))
    current_agent_ref = _normalize_text(module_payload.get('agentRef'))
    current_implementation_ref = _normalize_text(json_object(module_payload.get('logic')).get('implementationRef'))
    current_assembly = json_object(module_payload.get('assembly'))
    current_runtime_module = _module_runtime_module_ref(module_payload)
    refs: list[str] = []
    seen: set[str] = set()
    for other_module in registry_module_rows(registry):
        other_module_ref = _normalize_text(other_module.get('id'))
        if other_module_ref == current_module_ref:
            continue
        source_label = _registry_row_source_label(repo_root, other_module, fallback=f'agent module {other_module_ref or "<unknown>"}')
        other_logic = json_object(other_module.get('logic'))
        other_assembly = json_object(other_module.get('assembly'))
        if current_agent_ref and _normalize_text(other_module.get('agentRef')) == current_agent_ref:
            _append_unique_ref(refs, seen, f'{source_label}: agentRef -> {current_agent_ref}')
        if current_implementation_ref and _normalize_text(other_logic.get('implementationRef')) == current_implementation_ref:
            _append_unique_ref(refs, seen, f'{source_label}: logic.implementationRef -> {current_implementation_ref}')
        if current_runtime_module and _module_runtime_module_ref(other_module) == current_runtime_module:
            _append_unique_ref(refs, seen, f'{source_label}: controlPlane.implementation.runtime.config.module -> {current_runtime_module}')
        for field_label, current_value in (
            ('assembly.skillSetRef', _normalize_text(current_assembly.get('skillSetRef'))),
            ('assembly.permissionPolicyRef', _normalize_text(current_assembly.get('permissionPolicyRef'))),
            ('assembly.toolsetRef', _normalize_text(current_assembly.get('toolsetRef'))),
        ):
            if current_value and _normalize_text(other_assembly.get(field_label.split('.', 1)[1])) == current_value:
                _append_unique_ref(refs, seen, f'{source_label}: {field_label} -> {current_value}')
    return refs


def _job_structured_refs(
    repo_root: Path,
    module_payload: dict[str, Any],
    *,
    registry: dict[str, Any] | None = None,
) -> list[str]:
    current_agent_ref = _normalize_text(module_payload.get('agentRef'))
    scoped_job_refs = set(module_job_refs(module_payload))
    refs: list[str] = []
    seen: set[str] = set()
    job_rows = (registry.get('jobs') or []) if isinstance(registry, dict) else []
    for job_row in job_rows:
        if not isinstance(job_row, dict):
            continue
        job_id = _normalize_text(job_row.get('id')) or '<unknown>'
        source_label = _registry_row_source_label(repo_root, job_row, fallback=f'job {job_id}')
        if current_agent_ref and _normalize_text(job_row.get('agentRef')) == current_agent_ref:
            _append_unique_ref(refs, seen, f'{source_label}: agentRef -> {current_agent_ref}')
        for index, item in enumerate(json_array(job_row.get('dependsOn'))):
            if isinstance(item, dict):
                job_ref = _normalize_text(item.get('jobId'))
                field_label = f'dependsOn[{index}].jobId'
            else:
                job_ref = _normalize_text(item)
                field_label = f'dependsOn[{index}]'
            if job_ref and job_ref in scoped_job_refs:
                _append_unique_ref(refs, seen, f'{source_label}: {field_label} -> {job_ref}')
    return refs


def _group_job_ref_hits(group_row: dict[str, Any], scoped_job_refs: set[str]) -> list[str]:
    if not scoped_job_refs:
        return []
    dependency_policy = json_object(group_row.get('dependencyPolicy'))
    schedule_policy = json_object(group_row.get('schedulePolicy'))
    release_policy = json_object(group_row.get('releasePolicy'))
    acceptance_binding = json_object(release_policy.get('acceptanceBinding'))
    recovery_policy = json_object(group_row.get('recoveryPolicy'))
    hits: list[str] = []
    for field_label, values in (
        ('dependencyPolicy.orderedJobRefs', json_array(dependency_policy.get('orderedJobRefs'))),
        ('schedulePolicy.jobRefs', json_array(schedule_policy.get('jobRefs'))),
        ('releasePolicy.acceptanceBinding.requiredRunLedgerJobRefs', json_array(acceptance_binding.get('requiredRunLedgerJobRefs'))),
    ):
        if any(_normalize_text(item) in scoped_job_refs for item in values):
            hits.append(field_label)
    for index, item in enumerate(json_array(recovery_policy.get('steps'))):
        if not isinstance(item, dict):
            continue
        if _normalize_text(item.get('jobRef')) in scoped_job_refs:
            hits.append(f'recoveryPolicy.steps[{index}].jobRef')
    return hits


def _group_structured_refs(
    repo_root: Path,
    module_payload: dict[str, Any],
    *,
    registry: dict[str, Any] | None = None,
) -> list[str]:
    scoped_job_refs = set(module_job_refs(module_payload))
    refs: list[str] = []
    seen: set[str] = set()
    group_rows = (registry.get('agentGroups') or []) if isinstance(registry, dict) else []
    for group_row in group_rows:
        if not isinstance(group_row, dict):
            continue
        group_id = _normalize_text(group_row.get('id')) or '<unknown>'
        source_label = _registry_row_source_label(repo_root, group_row, fallback=f'group {group_id}')
        for field_label in _group_job_ref_hits(group_row, scoped_job_refs):
            _append_unique_ref(refs, seen, f'{source_label}: {field_label}')
    return refs


def find_external_module_references(
    repo_root: Path,
    module_payload: dict[str, Any],
    *,
    registry: dict[str, Any] | None = None,
) -> list[str]:
    if not isinstance(registry, dict):
        return []
    refs: list[str] = []
    seen: set[str] = set()
    for bucket in (
        _module_owned_path_refs(repo_root, module_payload, registry=registry),
        _module_structured_identifier_refs(repo_root, module_payload, registry=registry),
        _job_structured_refs(repo_root, module_payload, registry=registry),
        _group_structured_refs(repo_root, module_payload, registry=registry),
    ):
        for ref in bucket:
            _append_unique_ref(refs, seen, ref)
    return refs
