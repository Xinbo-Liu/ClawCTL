#!/usr/bin/env python3
"""Repo-baseline checks for the agent governance doctor."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.control_plane.surfaces import load_runtime_paths_manifest, load_workspace_templates_manifest
from openclaw.lib.repo.contracts import repo_contract_path
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.managed_extensions import validate_managed_explicit_extension_index
from openclaw.lib.repo.profiles import control_plane_profile_config_rel_paths

ROOT_DIR = resolve_repo_root(Path(__file__))
AGENT_DIR = ROOT_DIR / 'agent'
WORKSPACE_TEMPLATE_ROOT_DIR = ROOT_DIR / 'config' / 'workspace_templates'
WORKSPACE_TEMPLATE_MANIFEST_PATH = WORKSPACE_TEMPLATE_ROOT_DIR / 'manifest.json'
RUNTIME_PATH_MANIFEST_PATH = ROOT_DIR / 'config' / 'runtime' / 'paths.json'

FORMAL_GOVERNANCE_FILES = [
    AGENT_DIR / 'governance' / 'baseline.md',
    AGENT_DIR / 'governance' / 'directory-standard.md',
    AGENT_DIR / 'governance' / 'module-governance.md',
    AGENT_DIR / 'governance' / 'group-governance.md',
    AGENT_DIR / 'governance' / 'lifecycle-governance.md',
    AGENT_DIR / 'governance' / 'source-of-truth-matrix.md',
    AGENT_DIR / 'governance' / 'job-operation-bridge.md',
    AGENT_DIR / 'governance' / 'job-contract-governance.md',
    AGENT_DIR / 'governance' / 'implementation-binding-governance.md',
    AGENT_DIR / 'governance' / 'group-membership-governance.md',
    AGENT_DIR / 'governance' / 'group-topology-governance.md',
    AGENT_DIR / 'governance' / 'group-recovery-governance.md',
]
STANDARD_WORKSPACE_TEMPLATE_FILES = ['USER.md', 'TOOLS.md', 'AGENTS.md', 'IDENTITY.md', 'SOUL.md', 'HEARTBEAT.md']
AGENT_CONTROL_PLANE_README_PATH = AGENT_DIR / 'control_plane' / 'README.md'
AGENT_CONTROL_PLANE_DIRECTORY_FACTS = ['registries', 'runtime']


def ensure(condition: bool, errors: list[str], message: str) -> None:
    if not condition:
        errors.append(message)


def has_uppercase_agent_dir(root_dir: Path) -> bool:
    try:
        return any(path.is_dir() and path.name == 'Agent' for path in root_dir.iterdir())
    except FileNotFoundError:
        return False


def ensure_workspace_template_registry(errors: list[str], config_path: Path | None = None) -> dict[str, Any]:
    ensure(WORKSPACE_TEMPLATE_MANIFEST_PATH.exists(), errors, f'缺少工作区模板基座清单：{WORKSPACE_TEMPLATE_MANIFEST_PATH.relative_to(ROOT_DIR)}')
    ensure(RUNTIME_PATH_MANIFEST_PATH.exists(), errors, f'缺少 runtime path manifest：{RUNTIME_PATH_MANIFEST_PATH.relative_to(ROOT_DIR)}')
    if not WORKSPACE_TEMPLATE_MANIFEST_PATH.exists() or not RUNTIME_PATH_MANIFEST_PATH.exists():
        return {'templateRefs': [], 'runtimeEntries': []}

    manifest = load_workspace_templates_manifest(WORKSPACE_TEMPLATE_MANIFEST_PATH, config_path=config_path)
    runtime_manifest = load_runtime_paths_manifest(RUNTIME_PATH_MANIFEST_PATH, config_path=config_path)
    runtime_entries = runtime_manifest.get('entries') if isinstance(runtime_manifest.get('entries'), dict) else {}

    control_plane = manifest.get('control_plane') if isinstance(manifest.get('control_plane'), list) else []
    template_refs: list[str] = []
    runtime_entry_refs: list[str] = []
    seen_templates: set[str] = set()
    seen_runtime_entries: set[str] = set()

    for idx, row in enumerate(control_plane):
        if not isinstance(row, dict):
            errors.append(f'merged workspace template surface control_plane[{idx}] 必须为对象')
            continue
        template_ref = str(row.get('template') or '').strip()
        target_entry = str(row.get('target_entry') or '').strip()
        if not template_ref:
            errors.append(f'merged workspace template surface control_plane[{idx}] 缺少 template')
            continue
        if template_ref in seen_templates:
            errors.append(f'merged workspace template surface 存在重复 template：{template_ref}')
            continue
        seen_templates.add(template_ref)
        template_refs.append(template_ref)

        template_dir = WORKSPACE_TEMPLATE_ROOT_DIR / template_ref
        ensure(template_dir.exists() and template_dir.is_dir(), errors, f'工作区模板目录缺失：{template_dir.relative_to(ROOT_DIR)}')
        for file_name in STANDARD_WORKSPACE_TEMPLATE_FILES:
            ensure((template_dir / file_name).exists(), errors, f'工作区模板缺少标准文件：{template_dir.relative_to(ROOT_DIR) / file_name}')

        ensure(bool(target_entry), errors, f'工作区模板 {template_ref} 缺少 target_entry')
        if not target_entry:
            continue
        if target_entry in seen_runtime_entries:
            errors.append(f'merged workspace template surface 存在重复 target_entry：{target_entry}')
            continue
        seen_runtime_entries.add(target_entry)
        runtime_entry_refs.append(target_entry)
        runtime_entry = runtime_entries.get(target_entry) if isinstance(runtime_entries, dict) else None
        ensure(isinstance(runtime_entry, dict), errors, f'工作区模板 {template_ref} 绑定的 runtime entry 未注册：{target_entry}')
        if isinstance(runtime_entry, dict):
            ensure(str(runtime_entry.get('kind') or '').strip() == 'workspace_dir', errors, f'工作区模板 {template_ref} 的 runtime entry.kind 必须为 workspace_dir：{target_entry}')
            ensure(str(runtime_entry.get('category') or '').strip() == 'workspace', errors, f'工作区模板 {template_ref} 的 runtime entry.category 必须为 workspace：{target_entry}')

    actual_dirs = sorted(path.name for path in WORKSPACE_TEMPLATE_ROOT_DIR.iterdir() if path.is_dir())
    missing_dirs = sorted(set(template_refs) - set(actual_dirs))
    ensure(not missing_dirs, errors, f'workspace template surface 引用了不存在的目录：{", ".join(missing_dirs)}')

    stale_dirs_raw = manifest.get('stale_dirs')
    stale_dirs = stale_dirs_raw if isinstance(stale_dirs_raw, list) else []
    for idx, raw in enumerate(stale_dirs):
        ensure(bool(str(raw).strip()), errors, f'merged workspace template surface stale_dirs[{idx}] 不得为空')

    return {'templateRefs': template_refs, 'runtimeEntries': runtime_entry_refs}


def load_object_fact_drift_rules() -> tuple[dict[str, str], ...]:
    return (
        {'id': 'docs_registry_pages_exist', 'source': 'config/governance/docs/docs_registry.json'},
        {'id': 'profile_registry_paths_exist', 'source': 'config/control_plane/profile_registry.tsv'},
        {'id': 'managed_extension_index_layout', 'source': 'agent/extensions/index.json'},
    )


def _read_json_object(path: Path) -> dict[str, Any]:
    import json

    payload = json.loads(path.read_text(encoding='utf-8'))
    return payload if isinstance(payload, dict) else {}


def _validate_docs_registry_pages(errors: list[str]) -> None:
    docs_registry_path = repo_contract_path('governance.docs_registry', root_dir=ROOT_DIR)
    if not docs_registry_path.is_file():
        errors.append(f'缺少 docs registry：{docs_registry_path.relative_to(ROOT_DIR)}')
        return
    payload = _read_json_object(docs_registry_path)
    pages = payload.get('pages')
    if not isinstance(pages, list) or not pages:
        errors.append('docs registry pages 必须为非空数组')
        return
    seen_paths: set[str] = set()
    for index, row in enumerate(pages):
        if not isinstance(row, dict):
            errors.append(f'docs registry pages[{index}] 必须为对象')
            continue
        rel_path = str(row.get('path') or '').strip().replace('\\', '/')
        if not rel_path:
            errors.append(f'docs registry pages[{index}] 缺少 path')
            continue
        if rel_path in seen_paths:
            errors.append(f'docs registry pages path 重复：{rel_path}')
            continue
        seen_paths.add(rel_path)
        if not (ROOT_DIR / rel_path).is_file():
            errors.append(f'docs registry 指向不存在的页面：{rel_path}')


def _validate_profile_registry_paths(errors: list[str]) -> None:
    try:
        rows = control_plane_profile_config_rel_paths(ROOT_DIR)
    except ValueError as exc:
        errors.append(str(exc))
        return
    if 'base' not in rows:
        errors.append('profile registry 缺少 base profile')
    if 'agent_platform' not in rows:
        errors.append('profile registry 缺少 agent_platform profile')
    for profile_id, rel_path in rows.items():
        if not (ROOT_DIR / rel_path).is_file():
            errors.append(f'profile registry 指向不存在的配置：{profile_id} -> {rel_path}')


def _validate_managed_extension_facts(errors: list[str]) -> None:
    for issue in validate_managed_explicit_extension_index(ROOT_DIR):
        errors.append(issue)


def validate_object_fact_surfaces(errors: list[str]) -> None:
    _validate_docs_registry_pages(errors)
    _validate_profile_registry_paths(errors)
    _validate_managed_extension_facts(errors)


def validate_agent_control_plane_directory_facts(errors: list[str]) -> None:
    if not AGENT_CONTROL_PLANE_README_PATH.exists():
        errors.append(f'缺少 agent control-plane 目录说明：{AGENT_CONTROL_PLANE_README_PATH.relative_to(ROOT_DIR)}')
        return
    content = AGENT_CONTROL_PLANE_README_PATH.read_text(encoding='utf-8')
    for dir_name in AGENT_CONTROL_PLANE_DIRECTORY_FACTS:
        token = f'`{dir_name}/`'
        if token in content and not (AGENT_DIR / 'control_plane' / dir_name).is_dir():
            errors.append(f'agent/control_plane/README.md 引用了不存在的目录：{dir_name}/')


def validate_governance_repo_baseline(resolved_config_path: Path, errors: list[str]) -> dict[str, Any]:
    ensure(not has_uppercase_agent_dir(ROOT_DIR), errors, '仓库根目录不得存在额外的 Agent/ 大写目录')
    ensure(AGENT_DIR.exists(), errors, '缺少统一治理目录 agent/')
    for path in FORMAL_GOVERNANCE_FILES:
        ensure(path.exists(), errors, f'缺少治理基线文件：{path.relative_to(ROOT_DIR)}')
    workspace_registry = ensure_workspace_template_registry(errors, resolved_config_path)
    validate_object_fact_surfaces(errors)
    validate_agent_control_plane_directory_facts(errors)
    return workspace_registry
