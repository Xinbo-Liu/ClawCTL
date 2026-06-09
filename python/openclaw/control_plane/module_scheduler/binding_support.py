#!/usr/bin/env python3
"""Agent module scheduler binding shared helpers."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.control_plane.modules.change_set import read_json_object
from openclaw.control_plane.jobs.defaults import agent_capabilities
from openclaw.control_plane.registry import CliError
from openclaw.lib.io.json_access import json_object


_COLLECTION_KEY_TO_EXTENSION_REGISTRY_KEY = {
    'jobs': 'jobsDirs',
    'models': 'modelsDirs',
    'targets': 'targetsDirs',
    'agentGroups': 'agentGroupsDirs',
    'agentModules': 'agentModulesDirs',
}


def ensure_known_module(registry: dict[str, Any], module_ref: str) -> dict[str, Any]:
    """确认 moduleRef 在 registry 中已注册。"""
    module = json_object(registry.get('agentModulesById')).get(module_ref)
    if not isinstance(module, dict):
        raise CliError(f'未注册的 moduleRef：{module_ref}', 2)
    return module


def ensure_known_operation(module_payload: dict[str, Any], *, module_ref: str, operation_ref: str) -> dict[str, Any]:
    """确认 operationRef 在 module 中已注册。"""
    operations = json_object(module_payload.get('operations'))
    operation = operations.get(operation_ref)
    if not isinstance(operation, dict):
        raise CliError(f'module {module_ref} 未注册 operationRef：{operation_ref}', 2)
    return operation


def module_agent_ref(module_payload: dict[str, Any], *, module_ref: str) -> str:
    """解析 module 对应的 agentRef。"""
    agent_ref = str(module_payload.get('agentRef') or module_ref).strip()
    if not agent_ref:
        raise CliError(f'module {module_ref} 缺少 agentRef', 2)
    return agent_ref


def module_capabilities(module_payload: dict[str, Any]) -> dict[str, Any]:
    """提取 module 的 agent capabilities。"""
    control_plane = json_object(module_payload.get('controlPlane'))
    agent_cfg = json_object(control_plane.get('agent'))
    return {
        **agent_capabilities(agent_cfg),
        'defaultModelProfileRef': str(agent_cfg.get('defaultModelProfileRef') or '').strip(),
    }


def module_binding_refs(module_payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    """汇总 module operations 中引用的 job / target 绑定。"""
    operations = json_object(module_payload.get('operations'))
    job_refs: list[str] = []
    target_refs: list[str] = []
    for operation in operations.values():
        if not isinstance(operation, dict):
            continue
        job_bindings = json_object(operation.get('jobBindings'))
        for job_ref, binding in job_bindings.items():
            normalized_job_ref = str(job_ref or '').strip()
            if normalized_job_ref and normalized_job_ref not in job_refs:
                job_refs.append(normalized_job_ref)
            if isinstance(binding, dict):
                target_binding_ref = str(binding.get('targetBindingRef') or '').strip()
                if target_binding_ref and target_binding_ref not in target_refs:
                    target_refs.append(target_binding_ref)
    return job_refs, target_refs


def path_is_relative_to(path: Path, base: Path) -> bool:
    """判断路径是否位于指定基目录之下。"""
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def configured_registry_collection_dirs(registry: dict[str, Any], *, key: str) -> list[Path]:
    """读取配置中的 registry collection 目录列表。"""
    return [Path(str(item)).resolve() for item in list(json_object(registry.get('registryPaths')).get(key) or []) if str(item).strip()]


def single_registry_collection_dir(registry: dict[str, Any], *, key: str, label: str) -> Path:
    """断言指定 collection 只有唯一写入目录。"""
    rows = configured_registry_collection_dirs(registry, key=key)
    if not rows:
        raise CliError(f'{label} 注册目录未配置', 2)
    if len(rows) > 1:
        joined = ', '.join(str(item) for item in rows)
        raise CliError(f'{label} 注册目录必须唯一；当前配置了多个目录：{joined}', 2)
    resolved = rows[0]
    if resolved.is_dir():
        return resolved
    raise CliError(f'{label} 注册目录不存在：{resolved}', 2)


def extension_owned_registry_collection_dir(
    registry: dict[str, Any],
    *,
    key: str,
    label: str,
    source_path: Path,
    source_payload: dict[str, Any] | None = None,
) -> Path | None:
    """按 sourcePath 推断 extension 归属的写入目录。"""
    registry_key = _COLLECTION_KEY_TO_EXTENSION_REGISTRY_KEY.get(key)
    if not registry_key:
        return None
    resolved_source_path = Path(source_path).resolve()
    source_object = read_json_object(resolved_source_path) if source_payload is None else dict(source_payload)
    source_activation = json_object(source_object.get('activation'))
    active_extension_ids = [
        str(extension.get('id') or '').strip()
        for extension in list(registry.get('extensions') or [])
        if str(extension.get('id') or '').strip() in {
            str(item).strip()
            for item in (source_activation.get('enabledExtensionIds') or [])
            if str(item).strip()
        }
    ]
    def _collect_matches(*, filter_active_ids: bool) -> list[Path]:
        matches: list[Path] = []
        for extension in list(registry.get('extensions') or []):
            if not isinstance(extension, dict):
                continue
            extension_id = str(extension.get('id') or '').strip() or '<unknown>'
            if filter_active_ids and active_extension_ids and extension_id not in active_extension_ids:
                continue
            extension_registry = json_object(extension.get('registry'))
            module_dirs = [
                Path(str(item)).resolve()
                for item in list(extension_registry.get('agentModulesDirs') or [])
                if str(item).strip()
            ]
            if module_dirs and not any(path_is_relative_to(resolved_source_path, module_dir) for module_dir in module_dirs):
                continue
            rows = [
                Path(str(item)).resolve()
                for item in list(extension_registry.get(registry_key) or [])
                if str(item).strip()
            ]
            if not rows:
                continue
            if len(rows) > 1:
                joined = ', '.join(str(item) for item in rows)
                raise CliError(f'{label} extension {extension_id} 注册目录必须唯一：{joined}', 2)
            matches.append(rows[0])
        return matches

    matches = _collect_matches(filter_active_ids=True)
    if not matches and active_extension_ids:
        matches = _collect_matches(filter_active_ids=False)
    deduped: list[Path] = []
    seen: set[str] = set()
    for item in matches:
        normalized = str(item)
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(item)
    if not deduped:
        return None
    if len(deduped) > 1:
        joined = ', '.join(str(item) for item in deduped)
        raise CliError(f'{label} 写入目录来源不唯一；当前 sourcePath={resolved_source_path} 命中多个扩展目录：{joined}', 2)
    resolved = deduped[0]
    if resolved.is_dir():
        return resolved
    raise CliError(f'{label} 注册目录不存在：{resolved}', 2)


def registry_collection_dir_for_write(
    registry: dict[str, Any],
    *,
    key: str,
    label: str,
    source_path: Path | None = None,
    source_payload: dict[str, Any] | None = None,
) -> Path:
    """解析 attach/detach 写入时应使用的 registry 目录。"""
    rows = configured_registry_collection_dirs(registry, key=key)
    if not rows:
        raise CliError(f'{label} 注册目录未配置', 2)
    if len(rows) == 1:
        return single_registry_collection_dir(registry, key=key, label=label)
    if source_path is not None:
        owned = extension_owned_registry_collection_dir(
            registry,
            key=key,
            label=label,
            source_path=source_path,
            source_payload=source_payload,
        )
        if owned is not None:
            return owned
    joined = ', '.join(str(item) for item in rows)
    raise CliError(
        f'{label} 注册目录必须唯一；当前配置了多个目录：{joined}。请使用只启用单一写入面的 profile，或让对象来源明确归属某个 extension。',
        2,
    )


def validate_attach_group_options(
    *,
    group_ref: str,
    group_placement: str,
    recovery_after_minutes: int | None,
    recovery_action_kind: str,
) -> tuple[str, str]:
    """校验 attach 时的 group 选项组合。"""
    normalized_group_ref = str(group_ref or '').strip()
    normalized_group_placement = str(group_placement or 'none').strip() or 'none'
    if normalized_group_placement not in {'none', 'ordered', 'recovery'}:
        raise CliError(f'--group-placement 仅允许 none / ordered / recovery：{normalized_group_placement}', 2)
    if normalized_group_placement == 'none' and normalized_group_ref:
        raise CliError('指定 --group-ref 时必须同时指定 --group-placement ordered 或 recovery', 2)
    if normalized_group_placement != 'none' and not normalized_group_ref:
        raise CliError('group attach 必须提供 --group-ref', 2)
    if normalized_group_placement != 'recovery' and recovery_after_minutes is not None:
        raise CliError('--recovery-after-minutes 仅允许与 --group-placement recovery 一起使用', 2)
    if normalized_group_placement != 'recovery' and str(recovery_action_kind or '').strip() not in {'', 'retry'}:
        raise CliError('--recovery-action-kind 仅允许与 --group-placement recovery 一起使用', 2)
    return normalized_group_ref, normalized_group_placement


def resolve_attach_module_state(
    registry: dict[str, Any],
    *,
    module_ref: str,
    operation_ref: str,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    """解析 attach 操作所需的 module 上下文。"""
    module_row = ensure_known_module(registry, module_ref)
    module_path = Path(str(module_row.get('sourcePath') or '')).resolve()
    module_payload = read_json_object(module_path)
    ensure_known_operation(module_payload, module_ref=module_ref, operation_ref=operation_ref)
    return module_path, module_payload, module_capabilities(module_payload)


def collect_existing_job_bindings(registry: dict[str, Any]) -> dict[str, str]:
    """收集当前 registry 中已有的 job 绑定。"""
    all_job_bindings: dict[str, str] = {}
    for current_module_ref, current_module in json_object(registry.get('agentModulesById')).items():
        if not isinstance(current_module, dict):
            continue
        raw_module = read_json_object(Path(str(current_module.get('sourcePath') or '')).resolve())
        operations = json_object(raw_module.get('operations'))
        for current_operation_ref, current_operation in operations.items():
            if not isinstance(current_operation, dict):
                continue
            job_bindings = json_object(current_operation.get('jobBindings'))
            for current_job_ref in job_bindings:
                current_job_id = str(current_job_ref or '').strip()
                if current_job_id:
                    all_job_bindings[current_job_id] = f'{current_module_ref}.{current_operation_ref}'
    return all_job_bindings


def ensure_attach_job_available(registry: dict[str, Any], *, job_id: str) -> None:
    """确保待写入的 jobId 当前可用。"""
    if job_id in json_object(registry.get('jobsById')):
        raise CliError(f'jobId 已存在，attach 当前仅支持创建新 job：{job_id}', 2)
    all_job_bindings = collect_existing_job_bindings(registry)
    if job_id in all_job_bindings:
        raise CliError(f'jobId 已被其他 module operation 占用：{job_id} -> {all_job_bindings[job_id]}', 2)
