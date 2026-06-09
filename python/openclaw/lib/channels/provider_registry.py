#!/usr/bin/env python3
"""渠道 provider adapter 注册表。"""
from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from pathlib import Path

from openclaw.lib.repo.layout import resolve_repo_root
from typing import Any, Callable, Sequence

from openclaw.lib.repo.static_truth import dispatch_provider_registry_paths
from openclaw.lib.runtime.execution import validate_callable_reference

ROOT_DIR = resolve_repo_root(Path(__file__))
DEFAULT_PROVIDER_REGISTRY_PATH: Path | Sequence[Path] | None = None


def _default_provider_registry_paths() -> list[Path]:
    return dispatch_provider_registry_paths(ROOT_DIR)


class ChannelProviderRegistryError(RuntimeError):
    """渠道 provider adapter 注册表错误。"""


@dataclass(frozen=True)
class ChannelProviderAdapterSpec:
    adapter_id: str
    title: str
    description: str
    transport: str
    module: str
    endpoint_validator: str
    payload_builder: str
    response_evaluator: str


AdapterFunc = Callable[..., Any]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ChannelProviderRegistryError(f"渠道 provider adapter 注册表文件不存在：{path}") from exc
    except Exception as exc:
        raise ChannelProviderRegistryError(f"渠道 provider adapter 注册表 JSON 无法解析：{path} ({exc})") from exc
    if not isinstance(payload, dict):
        raise ChannelProviderRegistryError(f"渠道 provider adapter 注册表顶层必须为对象：{path}")
    return payload


def _require_non_empty_text(value: Any, *, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ChannelProviderRegistryError(f"{label} 必须为非空字符串")
    return text


def _require_list(value: Any, *, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ChannelProviderRegistryError(f"{label} 必须为数组")
    return value


def _normalize_registry_paths(path: Path | Sequence[Path] | None) -> list[Path]:
    if path is None:
        candidate = DEFAULT_PROVIDER_REGISTRY_PATH or _default_provider_registry_paths()
        if isinstance(candidate, Path):
            return [candidate]
        return [item.resolve() if isinstance(item, Path) else item for item in candidate]
    if isinstance(path, Path):
        return [path]
    result: list[Path] = []
    seen: set[str] = set()
    for item in path:
        if not isinstance(item, Path):
            raise ChannelProviderRegistryError(f'渠道 provider adapter 注册表路径类型非法：{item!r}')
        resolved = item.resolve()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        result.append(resolved)
    if not result:
        raise ChannelProviderRegistryError('渠道 provider adapter 注册表路径不能为空')
    return result


def _merge_channel_provider_registry_payloads(paths: list[Path]) -> dict[str, Any]:
    payloads = [_read_json(item) for item in paths]
    version = 0
    adapters: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for payload in payloads:
        current_version = payload.get('version')
        if not isinstance(current_version, int) or current_version < 1:
            raise ChannelProviderRegistryError('渠道 provider adapter 注册表 version 必须为正整数')
        version = max(version, current_version)
        rows = _require_list(payload.get('adapters'), label='渠道 provider adapter 注册表.adapters')
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ChannelProviderRegistryError(f'渠道 provider adapter 注册表.adapters[{idx}] 必须为对象')
            adapter_id = _require_non_empty_text(row.get('id'), label=f'渠道 provider adapter 注册表.adapters[{idx}].id')
            transport = _require_non_empty_text(row.get('transport'), label=f'渠道 provider adapter 注册表.adapters[{idx}].transport')
            normalized = {
                'id': adapter_id,
                'title': _require_non_empty_text(row.get('title'), label=f'渠道 provider adapter 注册表.adapters[{idx}].title'),
                'description': _require_non_empty_text(row.get('description'), label=f'渠道 provider adapter 注册表.adapters[{idx}].description'),
                'transport': transport,
                'module': _require_non_empty_text(row.get('module'), label=f'渠道 provider adapter 注册表.adapters[{idx}].module'),
                'endpointValidator': _require_non_empty_text(row.get('endpointValidator'), label=f'渠道 provider adapter 注册表.adapters[{idx}].endpointValidator'),
                'payloadBuilder': _require_non_empty_text(row.get('payloadBuilder'), label=f'渠道 provider adapter 注册表.adapters[{idx}].payloadBuilder'),
                'responseEvaluator': _require_non_empty_text(row.get('responseEvaluator'), label=f'渠道 provider adapter 注册表.adapters[{idx}].responseEvaluator'),
            }
            key = (adapter_id, transport)
            existing = seen.get(key)
            if existing is not None and existing != normalized:
                raise ChannelProviderRegistryError(f'渠道 provider adapter 注册表存在冲突 provider/transport：{adapter_id}/{transport}')
            if existing is None:
                seen[key] = normalized
                adapters.append(normalized)
    return {'version': version, 'adapters': adapters}


def load_channel_provider_registry(path: Path | Sequence[Path] | None = None) -> dict[str, Any]:
    payload = _merge_channel_provider_registry_payloads(_normalize_registry_paths(path))
    for row in payload.get('adapters') or []:
        adapter_id = str(row.get('id') or '').strip()
        transport = str(row.get('transport') or '').strip()
        module_name = str(row.get('module') or '').strip()
        for field_name, attr_name in (
            ('endpointValidator', str(row.get('endpointValidator') or '').strip()),
            ('payloadBuilder', str(row.get('payloadBuilder') or '').strip()),
            ('responseEvaluator', str(row.get('responseEvaluator') or '').strip()),
        ):
            validate_callable_reference(
                module_name,
                attr_name,
                ChannelProviderRegistryError,
                f'渠道 provider adapter {adapter_id}/{transport}.{field_name}',
            )
    return payload


def channel_provider_adapter_specs(path: Path | Sequence[Path] | None = None) -> dict[tuple[str, str], ChannelProviderAdapterSpec]:
    payload = load_channel_provider_registry(path)
    specs: dict[tuple[str, str], ChannelProviderAdapterSpec] = {}
    for row in payload.get("adapters") or []:
        spec = ChannelProviderAdapterSpec(
            adapter_id=str(row.get("id") or "").strip(),
            title=str(row.get("title") or "").strip(),
            description=str(row.get("description") or "").strip(),
            transport=str(row.get("transport") or "").strip(),
            module=str(row.get("module") or "").strip(),
            endpoint_validator=str(row.get("endpointValidator") or "").strip(),
            payload_builder=str(row.get("payloadBuilder") or "").strip(),
            response_evaluator=str(row.get("responseEvaluator") or "").strip(),
        )
        specs[(spec.adapter_id, spec.transport)] = spec
    return specs


def resolve_channel_provider_adapter(provider: str, transport: str, path: Path | Sequence[Path] | None = None) -> ChannelProviderAdapterSpec | None:
    key = (str(provider or "").strip(), str(transport or "").strip())
    if not key[0] or not key[1]:
        return None
    return channel_provider_adapter_specs(path).get(key)


def _load_callable(spec: ChannelProviderAdapterSpec, attr_name: str) -> AdapterFunc:
    module = importlib.import_module(spec.module)
    attr = getattr(spec, attr_name)
    func = getattr(module, attr, None)
    if not callable(func):
        raise ChannelProviderRegistryError(f"渠道 provider adapter {spec.adapter_id}/{spec.transport} 缺少可调用成员：{spec.module}.{attr}")
    return func


def endpoint_validator(spec: ChannelProviderAdapterSpec) -> AdapterFunc:
    return _load_callable(spec, "endpoint_validator")


def payload_builder(spec: ChannelProviderAdapterSpec) -> AdapterFunc:
    return _load_callable(spec, "payload_builder")


def response_evaluator(spec: ChannelProviderAdapterSpec) -> AdapterFunc:
    return _load_callable(spec, "response_evaluator")
