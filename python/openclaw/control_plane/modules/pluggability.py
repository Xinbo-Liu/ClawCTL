#!/usr/bin/env python3
"""Agent module 可插拔性摘要。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.control_plane.modules.lifecycle import find_external_module_references, inspect_module_optional_surfaces
from openclaw.lib.io.json_access import json_array, json_object
from openclaw.lib.repo.layout import resolve_repo_root

REQUIRED_MODULE_ASSET_KEYS = ('readmePath', 'binPath', 'skillsPath', 'permissionsPath', 'toolsPath')
OPTIONAL_MODULE_ASSET_KEYS = ('agentsMdPath',)


def _relative_to_repo(repo_root: Path, value: str | Path | None) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    path = Path(text)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except (OSError, RuntimeError, ValueError):
        return str(path.resolve())


def _module_job_refs(module: dict[str, Any]) -> list[str]:
    operations = json_object(module.get('operations'))
    result: list[str] = []
    for payload in operations.values():
        if not isinstance(payload, dict):
            continue
        job_bindings = json_object(payload.get('jobBindings'))
        declared_job_refs = json_array(payload.get('jobRefs'))
        job_refs = [str(item).strip() for item in job_bindings.keys()] or [str(item).strip() for item in declared_job_refs if str(item).strip()]
        for job_ref in job_refs:
            if job_ref and job_ref not in result:
                result.append(job_ref)
    return result


def _module_target_binding_refs(module: dict[str, Any]) -> list[str]:
    operations = json_object(module.get('operations'))
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


def _module_operation_refs(module: dict[str, Any]) -> list[str]:
    operations = json_object(module.get('operations'))
    return sorted(str(ref).strip() for ref in operations if str(ref).strip())


def _module_capabilities(module: dict[str, Any]) -> dict[str, Any]:
    control_plane = json_object(module.get('controlPlane'))
    agent_cfg = json_object(control_plane.get('agent'))
    capabilities = json_object(agent_cfg.get('capabilities'))
    return {
        'network': bool(capabilities.get('network', False)),
        'filesystemWrite': [str(item).strip() for item in json_array(capabilities.get('filesystemWrite')) if str(item).strip()],
        'modelRequired': bool(capabilities.get('modelRequired', False)),
        'externalDispatch': bool(capabilities.get('externalDispatch', False)),
    }


def build_module_pluggability_summary(registry: dict[str, Any], *, module_ref: str = '') -> dict[str, Any]:
    repo_root = resolve_repo_root(Path(__file__))
    normalized_module_ref = str(module_ref or '').strip()
    items: list[dict[str, Any]] = []
    for module in registry.get('agentModules', []):
        if not isinstance(module, dict):
            continue
        current_module_ref = str(module.get('id') or '').strip()
        if normalized_module_ref and current_module_ref != normalized_module_ref:
            continue
        runtime = json_object(module.get('runtime'))
        logic = json_object(module.get('logic'))
        assets = json_object(module.get('assets'))
        assembly = json_object(module.get('assembly'))
        source_path = Path(str(module.get('sourcePath') or '')).resolve() if str(module.get('sourcePath') or '').strip() else None
        module_dir = Path(str(module.get('moduleDir') or '')).resolve() if str(module.get('moduleDir') or '').strip() else None
        required_assets = {
            key: str(assets.get(key) or '').strip()
            for key in REQUIRED_MODULE_ASSET_KEYS
            if str(assets.get(key) or '').strip()
        }
        optional_assets = {
            key: str(assets.get(key) or '').strip()
            for key in OPTIONAL_MODULE_ASSET_KEYS
            if str(assets.get(key) or '').strip()
        }
        optional_surface_items = inspect_module_optional_surfaces(module)
        optional_surface_paths = [str(item.get('relPath') or '').strip() for item in optional_surface_items if str(item.get('relPath') or '').strip()]
        boilerplate_optional_paths = [str(item.get('relPath') or '').strip() for item in optional_surface_items if bool(item.get('boilerplate')) and str(item.get('relPath') or '').strip()]
        job_refs = _module_job_refs(module)
        target_binding_refs = _module_target_binding_refs(module)
        group_refs = [str(item).strip() for item in (module.get('resolvedGroupRefs') or []) if str(item).strip()]
        capabilities = _module_capabilities(module)
        scheduler_bound = bool(job_refs)
        external_refs = find_external_module_references(repo_root, module, registry=registry)
        items.append({
            'id': current_module_ref,
            'agentRef': str(module.get('agentRef') or ''),
            'title': str(module.get('title') or ''),
            'version': str(module.get('version') or ''),
            'ownerDomain': str(module.get('ownerDomain') or ''),
            'moduleKind': str(module.get('moduleKind') or 'worker'),
            'runtime': {
                'implementationRef': str(logic.get('implementationRef') or ''),
                'entrypointKinds': [str(item).strip() for item in (runtime.get('entrypointKinds') or []) if str(item).strip()],
                'runtimeAdapterRefs': [str(item).strip() for item in (runtime.get('runtimeAdapterRefs') or []) if str(item).strip()],
            },
            'surface': {
                'moduleDir': _relative_to_repo(repo_root, module_dir) if module_dir else '',
                'manifestPath': _relative_to_repo(repo_root, source_path) if source_path else '',
                'requiredAssets': required_assets,
                'optionalAssets': optional_assets,
                'optionalSurfacePaths': optional_surface_paths,
                'boilerplateOptionalPaths': boilerplate_optional_paths,
                'surfaceMode': 'minimal' if not optional_surface_paths else 'extended',
                'logicSourcePaths': [str(item).strip() for item in (logic.get('sourcePaths') or []) if str(item).strip()],
                'operationRefs': _module_operation_refs(module),
            },
            'assembly': {
                'skillSetRef': str(assembly.get('skillSetRef') or ''),
                'permissionPolicyRef': str(assembly.get('permissionPolicyRef') or ''),
                'toolsetRef': str(assembly.get('toolsetRef') or ''),
            },
            'bindings': {
                'jobRefs': job_refs,
                'groupRefs': group_refs,
                'targetBindingRefs': target_binding_refs,
            },
            'capabilities': capabilities,
            'pluggability': {
                'bindingMode': 'scheduler_bound' if scheduler_bound else 'standalone',
                'dropInRegistration': not scheduler_bound,
                'dropInRemoval': (not scheduler_bound) and (not group_refs),
                'dropInDeletionReady': (not scheduler_bound) and (not group_refs) and (not external_refs),
                'deletionBlockers': external_refs[:10],
                'hasOperatorGuide': bool(optional_assets.get('agentsMdPath')),
                'hasThinLauncher': bool(required_assets.get('binPath')),
                'requiresCoordinatedRemoval': bool(job_refs or group_refs or target_binding_refs or capabilities.get('externalDispatch') or external_refs),
            },
        })
    return {
        'status': 'ok',
        'service': str((registry.get('service') or {}).get('name') or 'openclaw-control-plane'),
        'configPath': str(registry.get('configPath') or ''),
        'moduleRef': normalized_module_ref or None,
        'count': len(items),
        'items': items,
    }
