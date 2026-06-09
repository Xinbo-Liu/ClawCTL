#!/usr/bin/env python3
"""control-plane runtime adapter 注册表。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from openclaw.control_plane.schema import SchemaValidationError, load_schema, validate_payload_against_schema
from openclaw.lib.io.json_access import json_array
from openclaw.lib.runtime.execution import import_callable, validate_callable_reference


class RuntimeAdapterRegistryError(RuntimeError):
    """runtime adapter 注册表错误。"""


@dataclass(frozen=True)
class RuntimeAdapterSpec:
    adapter_id: str
    title: str
    description: str
    module: str
    config_validator: str
    runner: str
    supported_entrypoint_kinds: tuple[str, ...]
    supported_executor_kinds: tuple[str, ...]


RuntimeAdapterFunc = Callable[..., Any]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise RuntimeAdapterRegistryError(f'runtime adapter 注册表不存在：{path}') from exc
    except Exception as exc:
        # 注册表是控制平面入口文件；底层读/解码/解析错误统一成可读诊断。
        raise RuntimeAdapterRegistryError(f'runtime adapter 注册表 JSON 无法解析：{path} ({exc})') from exc
    if not isinstance(payload, dict):
        raise RuntimeAdapterRegistryError(f'runtime adapter 注册表顶层必须为对象：{path}')
    return payload


def _require_non_empty_text(value: Any, *, label: str) -> str:
    text = str(value or '').strip()
    if not text:
        raise RuntimeAdapterRegistryError(f'{label} 必须为非空字符串')
    return text


def _require_unique_text_list(value: Any, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise RuntimeAdapterRegistryError(f'{label} 必须为非空数组')
    rows: list[str] = []
    for idx, item in enumerate(value):
        text = str(item or '').strip()
        if not text:
            raise RuntimeAdapterRegistryError(f'{label}[{idx}] 必须为非空字符串')
        if text in rows:
            raise RuntimeAdapterRegistryError(f'{label} 不允许重复：{text}')
        rows.append(text)
    return tuple(rows)


def load_runtime_adapter_registry(path: Path, *, schema_path: Path | None = None) -> dict[str, Any]:
    payload = _read_json(path)
    if schema_path is not None:
        schema = load_schema(schema_path)
        try:
            validate_payload_against_schema(
                payload,
                schema,
                label=f'runtime adapter 注册表 {path.name}',
                strict_dependency=True,
            )
        except SchemaValidationError as exc:
            raise RuntimeAdapterRegistryError(str(exc)) from exc
    adapters = json_array(payload.get('adapters'))
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, row in enumerate(adapters):
        if not isinstance(row, dict):
            raise RuntimeAdapterRegistryError(f'runtime adapter 注册表.adapters[{idx}] 必须为对象')
        adapter_id = _require_non_empty_text(row.get('id'), label=f'runtime adapter 注册表.adapters[{idx}].id')
        if adapter_id in seen:
            raise RuntimeAdapterRegistryError(f'runtime adapter 注册表存在重复 id：{adapter_id}')
        seen.add(adapter_id)
        normalized.append({
            'id': adapter_id,
            'title': _require_non_empty_text(row.get('title'), label=f'runtime adapter 注册表.adapters[{idx}].title'),
            'description': _require_non_empty_text(row.get('description'), label=f'runtime adapter 注册表.adapters[{idx}].description'),
            'module': _require_non_empty_text(row.get('module'), label=f'runtime adapter 注册表.adapters[{idx}].module'),
            'configValidator': _require_non_empty_text(row.get('configValidator'), label=f'runtime adapter 注册表.adapters[{idx}].configValidator'),
            'runner': _require_non_empty_text(row.get('runner'), label=f'runtime adapter 注册表.adapters[{idx}].runner'),
            'supportedEntrypointKinds': list(_require_unique_text_list(row.get('supportedEntrypointKinds'), label=f'runtime adapter 注册表.adapters[{idx}].supportedEntrypointKinds')),
            'supportedExecutorKinds': list(_require_unique_text_list(row.get('supportedExecutorKinds'), label=f'runtime adapter 注册表.adapters[{idx}].supportedExecutorKinds')),
        })
    payload = dict(payload)
    payload['adapters'] = normalized
    for row in normalized:
        adapter_id = str(row.get('id') or '').strip()
        module_name = str(row.get('module') or '').strip()
        validate_callable_reference(
            module_name,
            str(row.get('configValidator') or '').strip(),
            RuntimeAdapterRegistryError,
            f'runtime adapter {adapter_id}.configValidator',
        )
        validate_callable_reference(
            module_name,
            str(row.get('runner') or '').strip(),
            RuntimeAdapterRegistryError,
            f'runtime adapter {adapter_id}.runner',
        )
    return payload


def runtime_adapter_specs(payload: dict[str, Any]) -> dict[str, RuntimeAdapterSpec]:
    specs: dict[str, RuntimeAdapterSpec] = {}
    for row in payload.get('adapters') or []:
        spec = RuntimeAdapterSpec(
            adapter_id=str(row.get('id') or '').strip(),
            title=str(row.get('title') or '').strip(),
            description=str(row.get('description') or '').strip(),
            module=str(row.get('module') or '').strip(),
            config_validator=str(row.get('configValidator') or '').strip(),
            runner=str(row.get('runner') or '').strip(),
            supported_entrypoint_kinds=tuple(str(item).strip() for item in (row.get('supportedEntrypointKinds') or []) if str(item).strip()),
            supported_executor_kinds=tuple(str(item).strip() for item in (row.get('supportedExecutorKinds') or []) if str(item).strip()),
        )
        specs[spec.adapter_id] = spec
    return specs


def _load_callable(spec: RuntimeAdapterSpec, attr_name: str) -> RuntimeAdapterFunc:
    attr = getattr(spec, attr_name)
    return import_callable(spec.module, attr, RuntimeAdapterRegistryError, f'runtime adapter {spec.adapter_id}')


def config_validator(spec: RuntimeAdapterSpec) -> RuntimeAdapterFunc:
    return _load_callable(spec, 'config_validator')


def runner(spec: RuntimeAdapterSpec) -> RuntimeAdapterFunc:
    return _load_callable(spec, 'runner')
