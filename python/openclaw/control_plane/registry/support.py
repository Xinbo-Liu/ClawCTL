#!/usr/bin/env python3
"""控制平面 registry 装配与校验的共享辅助。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from openclaw.control_plane.registry.store import read_json
from openclaw.lib.cli.common import CliError
from openclaw.lib.io.json_access import json_object


CONTROL_PLANE_ALLOWED_JOB_STATUSES = {
    'scheduled',
    'running',
    'succeeded',
    'failed',
    'blocked',
    'retry_pending',
}

_AGENT_GROUP_RELEASE_CHECK_IDS = {
    'group_health',
    'member_health',
    'run_ledger',
    'recent_access',
    'required_evidence',
    'acceptance_binding',
}


def _ensure_unique_text_list(value: Any, *, label: str) -> list[str]:
    if not isinstance(value, list):
        raise CliError(f'{label} 必须为数组', 2)
    result: list[str] = []
    for idx, item in enumerate(value):
        text = str(item or '').strip()
        if not text:
            raise CliError(f'{label}[{idx}] 必须为非空字符串', 2)
        if text in result:
            raise CliError(f'{label} 不允许重复：{text}', 2)
        result.append(text)
    return result


def _ensure_text_list(value: Any, *, label: str) -> list[str]:
    if not isinstance(value, list):
        raise CliError(f'{label} 必须为数组', 2)
    result: list[str] = []
    for idx, item in enumerate(value):
        text = str(item or '').strip()
        if not text:
            raise CliError(f'{label}[{idx}] 必须为非空字符串', 2)
        result.append(text)
    return result


def _normalize_entrypoint(entrypoint: Any, *, label: str) -> dict[str, str]:
    if not isinstance(entrypoint, dict):
        raise CliError(f'{label} 必须为对象', 2)
    kind = str(entrypoint.get('kind') or '').strip()
    if not kind:
        raise CliError(f'{label}.kind 不能为空', 2)
    return {'kind': kind}


def _read_required_json_object(path: Path, *, label: str) -> dict[str, Any]:
    payload = read_json(path, None)
    if not isinstance(payload, dict):
        raise CliError(f'{label} JSON 无法解析：{path}', 2)
    return payload


def _path_is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _module_asset_path(module: dict[str, Any], asset_key: str) -> Path:
    source_path = Path(str(module.get('sourcePath') or ''))
    module_dir = source_path.parent
    assets = json_object(module.get('assets'))
    rel = str(assets.get(asset_key) or '').strip()
    if not rel:
        raise CliError(f"agent module {module.get('id')} assets.{asset_key} 不能为空", 2)
    if Path(rel).is_absolute():
        raise CliError(f"agent module {module.get('id')} assets.{asset_key} 必须使用相对路径：{rel}", 2)
    resolved = (module_dir / rel).resolve()
    if not _path_is_relative_to(resolved, module_dir):
        raise CliError(f"agent module {module.get('id')} assets.{asset_key} 必须留在模块目录内：{resolved}", 2)
    return resolved


def _parse_skill_markdown(path: Path, *, label: str) -> list[dict[str, str]]:
    text = path.read_text(encoding='utf-8')
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for idx, raw in enumerate(text.splitlines()):
        line = raw.strip()
        if not line.startswith('- '):
            continue
        match = re.match(r'^-\s+`([^`]+)`[：:](.+?)[；;]?$', line)
        if not match:
            raise CliError(f'{label} 第 {idx + 1} 行格式不合法：{line}', 2)
        skill_id = str(match.group(1) or '').strip()
        summary = str(match.group(2) or '').strip()
        if not skill_id or not summary:
            raise CliError(f'{label} 第 {idx + 1} 行不能为空：{line}', 2)
        if skill_id in seen:
            raise CliError(f'{label} skill 重复：{skill_id}', 2)
        seen.add(skill_id)
        rows.append({'id': skill_id, 'summary': summary})
    if not rows:
        raise CliError(f'{label} 未声明任何 skill：{path}', 2)
    return rows


def _validate_permission_asset(path: Path, *, module_ref: str, label: str) -> dict[str, Any]:
    payload = _read_required_json_object(path, label=label)
    allowed_keys = {'schemaVersion', 'moduleRef', 'allow', 'deny'}
    extra_keys = sorted(set(payload) - allowed_keys)
    if extra_keys:
        raise CliError(f'{label} 存在未注册字段：{", ".join(extra_keys)}', 2)
    if int(payload.get('schemaVersion') or 0) != 1:
        raise CliError(f'{label} schemaVersion 必须为 1', 2)
    if str(payload.get('moduleRef') or '').strip() != module_ref:
        raise CliError(f'{label} moduleRef 必须为 {module_ref}', 2)
    allow = _ensure_unique_text_list(payload.get('allow') or [], label=f'{label}.allow')
    deny = _ensure_unique_text_list(payload.get('deny') or [], label=f'{label}.deny')
    if set(allow) & set(deny):
        conflict = sorted(set(allow) & set(deny))
        raise CliError(f'{label} allow/deny 不允许交叉：{", ".join(conflict)}', 2)
    return {'moduleRef': module_ref, 'allow': allow, 'deny': deny}


def _validate_toolset_asset(path: Path, *, module_ref: str, label: str) -> dict[str, Any]:
    payload = _read_required_json_object(path, label=label)
    if int(payload.get('schemaVersion') or 0) != 1:
        raise CliError(f'{label} schemaVersion 必须为 1', 2)
    if str(payload.get('moduleRef') or '').strip() != module_ref:
        raise CliError(f'{label} moduleRef 必须为 {module_ref}', 2)
    allowed = _ensure_unique_text_list(payload.get('allowedTools') or [], label=f'{label}.allowedTools')
    forbidden = _ensure_unique_text_list(payload.get('forbiddenTools') or [], label=f'{label}.forbiddenTools')
    audit_fields = _ensure_unique_text_list(payload.get('auditFields') or [], label=f'{label}.auditFields')
    if set(allowed) & set(forbidden):
        conflict = sorted(set(allowed) & set(forbidden))
        raise CliError(f'{label} allowedTools/forbiddenTools 不允许交叉：{", ".join(conflict)}', 2)
    return {
        'moduleRef': module_ref,
        'allowedTools': allowed,
        'forbiddenTools': forbidden,
        'auditFields': audit_fields,
    }


def _normalize_operation_args(value: Any, *, label: str) -> list[str]:
    if not isinstance(value, dict):
        raise CliError(f'{label} 必须为对象', 2)
    argv = value.get('argv') if isinstance(value.get('argv'), list) else None
    if not argv:
        raise CliError(f'{label}.argv 必须为非空数组', 2)
    return _ensure_text_list(argv, label=f'{label}.argv')


def _implementation_payload(implementation_row: dict[str, Any], *, label: str) -> dict[str, Any]:
    runtime = json_object(implementation_row.get('runtime'))
    if not runtime:
        raise CliError(f'{label} 缺少 runtime', 2)
    adapter_ref = str(runtime.get('adapterRef') or '').strip()
    if not adapter_ref:
        raise CliError(f'{label}.runtime.adapterRef 不能为空', 2)
    config_raw = runtime.get('config')
    if not isinstance(config_raw, dict):
        raise CliError(f'{label}.runtime.config 必须为对象', 2)
    resolved: dict[str, Any] = {
        'adapterRef': adapter_ref,
        'config': dict(config_raw),
    }
    if runtime.get('defaultArgs') is not None:
        resolved['defaultArgs'] = _ensure_text_list(runtime.get('defaultArgs'), label=f'{label}.runtime.defaultArgs')
    return resolved


def _normalize_executor_contract(executor: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(executor, dict):
        raise CliError(f'{label} 必须为对象', 2)
    kind = str(executor.get('kind') or '').strip()
    if not kind:
        raise CliError(f'{label}.kind 不能为空', 2)
    allowed = {'python_cli', 'shell', 'openclaw_runtime', 'delivery_adapter'}
    if kind not in allowed:
        raise CliError(f'{label}.kind 不受支持：{kind}', 2)
    normalized: dict[str, Any] = {'kind': kind}
    if kind == 'delivery_adapter':
        operation = str(executor.get('operation') or '').strip()
        if not operation:
            raise CliError(f'{label}.operation 不能为空', 2)
        normalized['operation'] = operation
        if executor.get('argv') is not None:
            if not isinstance(executor.get('argv'), list):
                raise CliError(f'{label}.argv 必须为数组', 2)
            normalized['argv'] = _ensure_text_list(executor.get('argv') or [], label=f'{label}.argv')
        else:
            normalized['argv'] = []
        return normalized
    argv = executor.get('argv') if isinstance(executor.get('argv'), list) else None
    if argv is None:
        raise CliError(f'{label}.argv 不能为空', 2)
    normalized['argv'] = _ensure_text_list(argv, label=f'{label}.argv')
    return normalized


def _normalize_input_contract(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CliError(f'{label} 必须为对象', 2)
    normalized = {
        'artifacts': _ensure_unique_text_list(value.get('artifacts') or [], label=f'{label}.artifacts'),
        'runtimeInputs': _ensure_unique_text_list(value.get('runtimeInputs') or [], label=f'{label}.runtimeInputs'),
    }
    if value.get('notes') is not None:
        normalized['notes'] = _ensure_text_list(value.get('notes') or [], label=f'{label}.notes')
    return normalized


def _normalize_output_contract(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CliError(f'{label} 必须为对象', 2)
    normalized = {
        'artifacts': _ensure_unique_text_list(value.get('artifacts') or [], label=f'{label}.artifacts'),
        'statusSignals': _ensure_unique_text_list(value.get('statusSignals') or [], label=f'{label}.statusSignals'),
    }
    if value.get('notes') is not None:
        normalized['notes'] = _ensure_text_list(value.get('notes') or [], label=f'{label}.notes')
    return normalized


def _normalize_contract(value: Any, *, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        raise CliError(f'{label} 必须为对象', 2)
    return {
        'inputs': _normalize_input_contract(value.get('inputs'), label=f'{label}.inputs'),
        'outputs': _normalize_output_contract(value.get('outputs'), label=f'{label}.outputs'),
    }


def _normalize_dependency_specs(depends_on: Any, *, label: str, source: str) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if depends_on is None:
        return normalized
    if not isinstance(depends_on, list):
        raise CliError(f'{label} 必须为数组', 2)
    for idx, dep in enumerate(depends_on):
        if isinstance(dep, str) and dep.strip():
            normalized.append({'jobId': dep.strip(), 'requiredStatuses': ['succeeded'], 'maxAgeMinutes': 240, 'source': source})
            continue
        if isinstance(dep, dict):
            dep_job = str(dep.get('jobId') or '').strip()
            if not dep_job:
                raise CliError(f'{label}[{idx}].jobId 不能为空', 2)
            statuses = _ensure_unique_text_list(dep.get('requiredStatuses'), label=f'{label}[{idx}].requiredStatuses')
            unknown = sorted(set(statuses) - CONTROL_PLANE_ALLOWED_JOB_STATUSES)
            if unknown:
                raise CliError(f'{label}[{idx}].requiredStatuses 存在未注册状态：{", ".join(unknown)}', 2)
            max_age = int(dep.get('maxAgeMinutes') or 0)
            if max_age < 1:
                raise CliError(f'{label}[{idx}].maxAgeMinutes 必须 >= 1', 2)
            normalized.append({'jobId': dep_job, 'requiredStatuses': statuses, 'maxAgeMinutes': max_age, 'source': source})
            continue
        raise CliError(f'{label}[{idx}] 必须为字符串或对象', 2)
    return normalized


def _normalized_dependencies(job: dict[str, Any]) -> list[dict[str, Any]]:
    job_id = str(job.get('id') or '')
    return _normalize_dependency_specs(job.get('dependsOn'), label=f'job {job_id} dependsOn', source='job')


def _validate_resolved_dependencies(
    *,
    job_id: str,
    order: int,
    dependencies: list[dict[str, Any]],
    jobs_by_id: dict[str, dict[str, Any]],
    resolved_orders: dict[str, int],
) -> None:
    """校验 job 依赖列表与 resolvedOrder 约束。"""
    dep_ids: set[str] = set()
    for dep in dependencies:
        dep_id = str(dep.get('jobId') or '').strip() if isinstance(dep, dict) else ''
        if not dep_id:
            raise CliError(f'job {job_id} dependsOn.jobId cannot be empty', 2)
        if dep_id == job_id:
            raise CliError(f'job {job_id} cannot depend on itself', 2)
        if dep_id not in jobs_by_id:
            raise CliError(f'job {job_id} depends on unknown job {dep_id}', 2)
        if dep_id in dep_ids:
            raise CliError(f'job {job_id} has duplicate dependency {dep_id}', 2)
        dep_ids.add(dep_id)
        dep_order = int(resolved_orders.get(dep_id) or 0)
        if dep_order >= order:
            raise CliError(f'job {job_id} must only depend on earlier resolvedOrder jobs', 2)
