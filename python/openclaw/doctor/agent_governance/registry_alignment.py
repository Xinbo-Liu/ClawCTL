#!/usr/bin/env python3
"""Resolved-registry alignment checks for the agent governance doctor."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.control_plane.registry import load_registry
from openclaw.lib.io.json_access import json_object
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.path_contracts import resolve_extension_root, resolve_path_contract

ROOT_DIR = resolve_repo_root(Path(__file__))
AGENT_DIR = ROOT_DIR / 'agent'

REQUIRED_MODULE_FILES = [
    'README.md',
    'skills.md',
    'permissions.json',
    'tools.json',
]
OPTIONAL_MODULE_FILES = ['AGENTS.md']
REQUIRED_MODULE_DIRS = [
    'bin',
]
OPTIONAL_MODULE_DIRS = [
    'contracts',
    'constraints',
    'docs',
]
REQUIRED_TEST_FILES = ['test_smoke.py']


def ensure(condition: bool, errors: list[str], message: str) -> None:
    if not condition:
        errors.append(message)


def extension_tests_dir_for_module(module_dir: Path) -> Path:
    try:
        extension_root = resolve_extension_root(module_dir)
    except ValueError as exc:
        raise ValueError(f'cannot resolve extension root for module directory: {module_dir}') from exc
    return extension_root / 'tests' / 'modules' / module_dir.name


def module_requires_python_dir(module: dict[str, Any]) -> bool:
    runtime = json_object(module.get('runtime'))
    runtime_adapter_refs = [str(item).strip() for item in (runtime.get('runtimeAdapterRefs') or []) if str(item).strip()]
    if 'python_module' in runtime_adapter_refs:
        return True
    logic = json_object(module.get('logic'))
    source_paths = [str(item).strip() for item in (logic.get('sourcePaths') or []) if str(item).strip()]
    return bool(source_paths)


def _validate_python_sources(module_dir: Path, module: dict[str, Any], errors: list[str]) -> None:
    logic = json_object(module.get('logic'))
    source_paths = [str(item).strip() for item in (logic.get('sourcePaths') or []) if str(item).strip()]
    for source_path in source_paths:
        try:
            target = resolve_path_contract(source_path, base_dir=module_dir, start_path=module_dir)
        except ValueError as exc:
            errors.append(f'模块声明的 Python 真源路径合同无效：{source_path} ({exc})')
            continue
        if target is None:
            continue
        ensure(target.exists(), errors, f'模块声明的 Python 真源不存在：{target.relative_to(ROOT_DIR)}')


def ensure_standard_module_layout(
    module_dir: Path,
    module: dict[str, Any],
    errors: list[str],
    *,
    require_manifest: bool,
    require_python_dir: bool,
) -> None:
    rel_dir = module_dir.relative_to(ROOT_DIR)
    if require_manifest:
        ensure((module_dir / 'module.json').exists(), errors, f'模块缺少 manifest：{rel_dir / "module.json"}')
    for file_name in REQUIRED_MODULE_FILES:
        ensure((module_dir / file_name).exists(), errors, f'模块缺少局部资产：{rel_dir / file_name}')
    for file_name in OPTIONAL_MODULE_FILES:
        target_file = module_dir / file_name
        if target_file.exists():
            ensure(target_file.is_file(), errors, f'模块可选资产必须为文件：{rel_dir / file_name}')
    for dir_name in REQUIRED_MODULE_DIRS:
        target_dir = module_dir / dir_name
        ensure(target_dir.exists() and target_dir.is_dir(), errors, f'模块缺少标准目录：{rel_dir / dir_name}')
    for dir_name in OPTIONAL_MODULE_DIRS:
        target_dir = module_dir / dir_name
        if target_dir.exists():
            ensure(target_dir.is_dir(), errors, f'模块可选目录必须为目录：{rel_dir / dir_name}')
            ensure((target_dir / 'README.md').exists(), errors, f'模块可选目录缺少 README：{rel_dir / dir_name / "README.md"}')
    tests_dir = extension_tests_dir_for_module(module_dir)
    tests_rel_dir = tests_dir.relative_to(ROOT_DIR)
    for file_name in REQUIRED_TEST_FILES:
        ensure((tests_dir / file_name).exists(), errors, f'模块测试目录缺少标准测试文件：{tests_rel_dir / file_name}')
    tests_readme = tests_dir / 'README.md'
    if tests_readme.exists():
        ensure(tests_readme.is_file(), errors, f'模块测试目录 README 必须为文件：{tests_rel_dir / "README.md"}')
    if require_python_dir:
        _validate_python_sources(module_dir, module, errors)


def _module_dir(module_ref: str, module: dict[str, Any]) -> Path:
    source_path = str(module.get('sourcePath') or '').strip()
    if not source_path:
        raise ValueError(f'module {module_ref} is missing resolved sourcePath')
    module_dir = Path(source_path).resolve().parent
    try:
        module_dir.relative_to(ROOT_DIR)
    except ValueError as exc:
        raise ValueError(f'module {module_ref} resolved outside repository: {module_dir}') from exc
    return module_dir


def _module_ref_for_agent(agent: dict[str, Any]) -> str:
    governance = json_object(agent.get('governance'))
    return str(agent.get('resolvedModuleRef') or governance.get('moduleRef') or agent.get('id') or '').strip()


def _validate_module_layouts(
    agent_rows: list[dict[str, Any]],
    modules_by_id: dict[str, dict[str, Any]],
    errors: list[str],
) -> list[str]:
    checked_modules: list[str] = []
    for agent in agent_rows:
        agent_ref = str(agent.get('id') or '').strip()
        module_ref = _module_ref_for_agent(agent)
        module = modules_by_id.get(module_ref)
        if not isinstance(module, dict):
            errors.append(f'agent {agent_ref} 对应模块缺少注册：{module_ref}')
            continue
        checked_modules.append(module_ref)
        try:
            module_dir = _module_dir(module_ref, module)
        except ValueError as exc:
            errors.append(f'agent {agent_ref} 模块目录解析失败：{exc}')
            continue
        ensure_standard_module_layout(
            module_dir,
            module,
            errors,
            require_manifest=True,
            require_python_dir=module_requires_python_dir(module),
        )
    return checked_modules


def _validate_group_bridge_docs(
    groups_by_id: dict[str, dict[str, Any]],
    agents_by_id: dict[str, dict[str, Any]],
    modules_by_id: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    for group_ref, group in groups_by_id.items():
        if not str(group.get('extensionId') or '').strip():
            bridge_dir = AGENT_DIR / 'groups' / group_ref
            bridge_readme = bridge_dir / 'README.md'
            mapping_doc = bridge_dir / 'control-plane-mapping.md'
            ensure(bridge_readme.exists(), errors, f'缺少 group 说明页：{bridge_readme.relative_to(ROOT_DIR)}')
            ensure(mapping_doc.exists(), errors, f'缺少 group 控制平面对齐页：{mapping_doc.relative_to(ROOT_DIR)}')
        members = [str(item).strip() for item in (group.get('resolvedMembers') or []) if str(item).strip()]
        for member in members:
            ensure(member in agents_by_id, errors, f'group {group_ref} 成员不存在：{member}')
            agent = json_object(agents_by_id.get(member))
            module_ref = _module_ref_for_agent(agent) if agent else member
            module = modules_by_id.get(module_ref)
            if not isinstance(module, dict):
                errors.append(f'group {group_ref} 成员缺少模块注册：{module_ref}')
                continue
            try:
                module_readme = _module_dir(module_ref, module) / 'README.md'
            except ValueError as exc:
                errors.append(f'group {group_ref} 成员模块目录解析失败：{exc}')
                continue
            ensure(module_readme.exists(), errors, f'group {group_ref} 成员缺少模块总览页：{module_readme.relative_to(ROOT_DIR)}')


def _merge_registry_indexes(
    registry_payload: dict[str, Any],
    *,
    local_key: str,
    qualified_key: str,
) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for key_name in (local_key, qualified_key):
        for key, value in dict(registry_payload.get(key_name) or {}).items():
            if isinstance(value, dict):
                merged[str(key)] = dict(value)
    return merged


def _registry_views(registry_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        'agents': [dict(item) for item in (registry_payload.get('agents') or []) if isinstance(item, dict)],
        'agentsById': _merge_registry_indexes(registry_payload, local_key='agentsById', qualified_key='agentsByQualifiedId'),
        'implementationsById': _merge_registry_indexes(
            registry_payload,
            local_key='implementationsById',
            qualified_key='implementationsByQualifiedId',
        ),
        'groupsById': _merge_registry_indexes(
            registry_payload,
            local_key='agentGroupsById',
            qualified_key='agentGroupsByQualifiedId',
        ),
        'jobsById': _merge_registry_indexes(registry_payload, local_key='jobsById', qualified_key='jobsByQualifiedId'),
        'skillSetsById': _merge_registry_indexes(
            registry_payload,
            local_key='skillSetsById',
            qualified_key='skillSetsByQualifiedId',
        ),
        'permissionPoliciesById': _merge_registry_indexes(
            registry_payload,
            local_key='permissionPoliciesById',
            qualified_key='permissionPoliciesByQualifiedId',
        ),
        'toolsetsById': _merge_registry_indexes(
            registry_payload,
            local_key='toolsetsById',
            qualified_key='toolsetsByQualifiedId',
        ),
        'modulesById': _merge_registry_indexes(
            registry_payload,
            local_key='agentModulesById',
            qualified_key='agentModulesByQualifiedId',
        ),
    }


def load_resolved_registry_views(resolved_config_path: Path) -> dict[str, Any]:
    return _registry_views(load_registry(resolved_config_path))


def validate_resolved_registry_views(views: dict[str, Any], errors: list[str]) -> list[str]:
    checked_modules = _validate_module_layouts(
        views['agents'],
        views['modulesById'],
        errors,
    )
    _validate_group_bridge_docs(
        views['groupsById'],
        views['agentsById'],
        views['modulesById'],
        errors,
    )
    return checked_modules


def validate_governance_registry_alignment(resolved_config_path: Path, errors: list[str]) -> dict[str, Any]:
    views = load_resolved_registry_views(resolved_config_path)
    checked_modules = validate_resolved_registry_views(views, errors)
    return {
        'views': views,
        'checkedModules': checked_modules,
    }
