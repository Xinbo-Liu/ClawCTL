#!/usr/bin/env python3
"""Plan builders for module prune/drop lifecycle operations."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openclaw.control_plane.modules.change_set import (
    apply_staged_writes,
    build_write,
    read_json_object,
    relative_to_repo,
    summarize_files,
)
from openclaw.control_plane.modules.lifecycle_filesystem import cleanup_empty_dirs, collect_drop_files
from openclaw.control_plane.modules.lifecycle_optional_surfaces import inspect_module_optional_surfaces
from openclaw.control_plane.modules.lifecycle_references import (
    find_external_module_references,
    module_job_refs,
    module_target_binding_refs,
)
from openclaw.control_plane.registry import CliError, load_registry
from openclaw.control_plane.registry.owners import resolve_collection_ref
from openclaw.lib.repo.layout import resolve_repo_root


def resolve_modules(registry: dict[str, Any], *, module_ref: str) -> list[dict[str, Any]]:
    normalized_module_ref = str(module_ref or '').strip()
    if normalized_module_ref:
        module = resolve_collection_ref(registry, 'agentModules', normalized_module_ref, label='moduleRef')
        return [module]
    return [dict(item) for item in (registry.get('agentModules') or []) if isinstance(item, dict)]


def build_prune_plan(
    *,
    config_path: Path,
    repo_root: Path | None,
    module_ref: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], set[Path], Path, Path]:
    registry = load_registry(Path(config_path).resolve())
    effective_repo_root = resolve_repo_root(Path(__file__)) if repo_root is None else Path(repo_root).resolve()
    writes: list[dict[str, Any]] = []
    cleanup_dirs: set[Path] = set()
    module_items: list[dict[str, Any]] = []

    for module_row in resolve_modules(registry, module_ref=module_ref):
        source_path = Path(str(module_row.get('sourcePath') or '')).resolve()
        module_payload = read_json_object(source_path)
        module_dir = source_path.parent
        surfaces = inspect_module_optional_surfaces({**module_payload, 'sourcePath': str(source_path)})
        boilerplate_surfaces = [item for item in surfaces if bool(item.get('boilerplate'))]
        if not boilerplate_surfaces:
            module_items.append({
                'moduleRef': str(module_payload.get('id') or ''),
                'surfaceMode': 'minimal',
                'optionalSurfacePaths': [item['relPath'] for item in surfaces],
                'boilerplateOptionalPaths': [],
                'updatedAssets': [],
            })
            continue
        next_module_payload = json.loads(json.dumps(module_payload, ensure_ascii=False))
        next_assets = next_module_payload.get('assets') if isinstance(next_module_payload.get('assets'), dict) else {}
        updated_assets: list[str] = []
        for item in boilerplate_surfaces:
            if str(item.get('assetKey') or '') == 'agentsMdPath' and str(next_assets.get('agentsMdPath') or '').strip() == str(item.get('relPath') or '').strip():
                next_assets.pop('agentsMdPath', None)
                if 'agentsMdPath' not in updated_assets:
                    updated_assets.append('agentsMdPath')
            writes.append(build_write(Path(item['path']), action='delete', summary=f'删除 {item["moduleRef"]} 的模板化可选面 {item["relPath"]}'))
            cleanup_dirs.add(Path(item['path']).resolve().parent)
        if updated_assets:
            next_module_payload['assets'] = next_assets
            writes.append(build_write(source_path, action='update', payload=next_module_payload, summary=f'收紧 {module_payload.get("id")} 的 assets 可选面引用'))
        module_items.append({
            'moduleRef': str(module_payload.get('id') or ''),
            'surfaceMode': 'extended',
            'optionalSurfacePaths': [item['relPath'] for item in surfaces],
            'boilerplateOptionalPaths': [item['relPath'] for item in boilerplate_surfaces],
            'updatedAssets': updated_assets,
            'moduleDir': relative_to_repo(effective_repo_root, module_dir),
        })

    plan = {
        'status': 'ok',
        'mode': 'plan',
        'moduleRef': str(module_ref or '').strip() or None,
        'moduleCount': len(module_items),
        'prunableFileCount': len([item for item in writes if str(item.get('action') or '') == 'delete']),
        'moduleUpdates': len([item for item in writes if str(item.get('action') or '') == 'update']),
        'modules': module_items,
        'files': summarize_files(effective_repo_root, writes),
    }
    return plan, writes, cleanup_dirs, effective_repo_root, Path(config_path).resolve()


def build_drop_plan(
    *,
    config_path: Path,
    repo_root: Path | None,
    module_ref: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], set[Path], Path, Path]:
    normalized_module_ref = str(module_ref or '').strip()
    if not normalized_module_ref:
        raise CliError('--module-ref 不能为空', 2)
    registry = load_registry(Path(config_path).resolve())
    effective_repo_root = resolve_repo_root(Path(__file__)) if repo_root is None else Path(repo_root).resolve()
    module_row = resolve_collection_ref(registry, 'agentModules', normalized_module_ref, label='moduleRef')
    source_path = Path(str(module_row.get('sourcePath') or '')).resolve()
    module_payload = read_json_object(source_path)

    job_refs = module_job_refs(module_payload)
    if job_refs:
        raise CliError(f'module {normalized_module_ref} 仍绑定 job：{", ".join(job_refs)}；请先 detach', 2)
    group_refs = [str(item).strip() for item in (module_row.get('resolvedGroupRefs') or []) if str(item).strip()]
    if group_refs:
        raise CliError(f'module {normalized_module_ref} 仍归属 group：{", ".join(group_refs)}；请先清理 group 绑定', 2)
    target_refs = module_target_binding_refs(module_payload)
    if target_refs:
        raise CliError(f'module {normalized_module_ref} 仍声明 targetBindingRef：{", ".join(target_refs)}；请先 detach', 2)

    external_refs = find_external_module_references(
        effective_repo_root,
        {**module_payload, 'sourcePath': str(source_path)},
        registry=registry,
    )
    if external_refs:
        preview = ', '.join(external_refs[:8])
        suffix = '' if len(external_refs) <= 8 else ' ...'
        raise CliError(f'module {normalized_module_ref} 在仓库其他位置仍有外部引用，先清理再 drop：{preview}{suffix}', 2)

    drop_files, cleanup_dirs = collect_drop_files(
        effective_repo_root,
        {**module_payload, 'sourcePath': str(source_path)},
        registry=registry,
    )
    if not drop_files:
        raise CliError(f'module {normalized_module_ref} 未找到可删除的本地文件面', 2)
    writes = [
        build_write(path, action='delete', summary=f'删除 {normalized_module_ref} 的本地面 {path.name}')
        for path in drop_files
    ]
    plan = {
        'status': 'ok',
        'mode': 'plan',
        'moduleRef': normalized_module_ref,
        'agentRef': str(module_payload.get('agentRef') or normalized_module_ref),
        'bindingMode': 'standalone',
        'ownedFileCount': len(drop_files),
        'moduleDir': relative_to_repo(effective_repo_root, source_path.parent),
        'files': summarize_files(effective_repo_root, writes),
    }
    return plan, writes, cleanup_dirs, effective_repo_root, Path(config_path).resolve()


def plan_agent_module_prune(**kwargs: Any) -> dict[str, Any]:
    plan, _, _, _, _ = build_prune_plan(**kwargs)
    return plan


def apply_agent_module_prune(**kwargs: Any) -> dict[str, Any]:
    plan, writes, cleanup_dirs, effective_repo_root, config_path = build_prune_plan(**kwargs)
    if writes:
        apply_staged_writes(writes=writes, config_path=config_path)
    removed_dirs = cleanup_empty_dirs(effective_repo_root, cleanup_dirs)
    return {**plan, 'mode': 'apply', 'removedEmptyDirs': removed_dirs}


def plan_agent_module_drop(**kwargs: Any) -> dict[str, Any]:
    plan, _, _, _, _ = build_drop_plan(**kwargs)
    return plan


def apply_agent_module_drop(**kwargs: Any) -> dict[str, Any]:
    plan, writes, cleanup_dirs, effective_repo_root, config_path = build_drop_plan(**kwargs)
    apply_staged_writes(writes=writes, config_path=config_path)
    removed_dirs = cleanup_empty_dirs(effective_repo_root, cleanup_dirs)
    return {**plan, 'mode': 'apply', 'removedEmptyDirs': removed_dirs}
