#!/usr/bin/env python3
"""dispatch target 注册表加载共享常量与基础校验工具。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.static_truth import dispatch_provider_registry_paths, dispatch_target_registry_paths


ROOT_DIR = resolve_repo_root(Path(__file__))
DEFAULT_REGISTRY_PATH: Path | Sequence[Path] | None = None
DEFAULT_SCHEMA_PATH = ROOT_DIR / 'config' / 'control_plane' / 'schemas' / 'dispatch_target_registry.schema.json'
DEFAULT_PROVIDER_REGISTRY_PATH: Path | Sequence[Path] | None = None


def _default_registry_paths() -> list[Path]:
    """从仓库静态真源解析默认 target 注册表路径。"""
    return dispatch_target_registry_paths(ROOT_DIR)


def _default_provider_registry_paths() -> list[Path]:
    """从仓库静态真源解析默认 provider 注册表路径。"""
    return dispatch_provider_registry_paths(ROOT_DIR)


class DispatchRegistryValidationError(RuntimeError):
    """dispatch target 注册表错误。"""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise DispatchRegistryValidationError(f'dispatch target 注册表文件不存在：{path}') from exc
    except Exception as exc:
        raise DispatchRegistryValidationError(f'dispatch target 注册表 JSON 无法解析：{path} ({exc})') from exc
    if not isinstance(payload, dict):
        raise DispatchRegistryValidationError(f'dispatch target 注册表顶层必须为对象：{path}')
    return payload


def load_dispatch_registry_schema(schema_path: Path | None = None) -> dict[str, Any]:
    return _read_json(schema_path or DEFAULT_SCHEMA_PATH)


def _require_keys(payload: dict[str, Any], keys: list[str], *, label: str) -> None:
    missing = [key for key in keys if key not in payload]
    if missing:
        raise DispatchRegistryValidationError(f'{label} 缺少字段：{", ".join(missing)}')


def _require_non_empty_text(value: Any, *, label: str) -> str:
    text = str(value or '').strip()
    if not text:
        raise DispatchRegistryValidationError(f'{label} 必须为非空字符串')
    return text


def _require_bool(value: Any, *, label: str) -> bool:
    if not isinstance(value, bool):
        raise DispatchRegistryValidationError(f'{label} 必须为布尔值')
    return value


def _require_int(value: Any, *, label: str, minimum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise DispatchRegistryValidationError(f'{label} 必须为整数')
    if minimum is not None and value < minimum:
        raise DispatchRegistryValidationError(f'{label} 必须 >= {minimum}')
    return value


def _require_list(value: Any, *, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise DispatchRegistryValidationError(f'{label} 必须为数组')
    return value


def _require_object(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DispatchRegistryValidationError(f'{label} 必须为对象')
    return value


def _require_unique_text_list(
    value: Any,
    *,
    label: str,
    allowed: set[str] | None = None,
    allow_empty: bool = False,
) -> list[str]:
    items = _require_list(value, label=label)
    result: list[str] = []
    for idx, item in enumerate(items):
        text = _require_non_empty_text(item, label=f'{label}[{idx}]')
        if allowed is not None and text not in allowed:
            raise DispatchRegistryValidationError(f'{label}[{idx}] 取值非法：{text}；允许值：{", ".join(sorted(allowed))}')
        if text in result:
            raise DispatchRegistryValidationError(f'{label} 不允许重复：{text}')
        result.append(text)
    if not allow_empty and not result:
        raise DispatchRegistryValidationError(f'{label} 至少需要 1 个条目')
    return result


def _index_by_id(rows: list[dict[str, Any]], *, label: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for idx, row in enumerate(rows):
        obj = _require_object(row, label=f'{label}[{idx}]')
        row_id = _require_non_empty_text(obj.get('id'), label=f'{label}[{idx}].id')
        if row_id in out:
            raise DispatchRegistryValidationError(f'{label} 中存在重复 id：{row_id}')
        out[row_id] = obj
    return out


def _normalize_registry_paths(registry_path: Path | Sequence[Path] | None) -> list[Path]:
    if registry_path is None:
        candidate = DEFAULT_REGISTRY_PATH or _default_registry_paths()
        if isinstance(candidate, Path):
            return [candidate]
        return [item.resolve() if isinstance(item, Path) else item for item in candidate]
    if isinstance(registry_path, Path):
        return [registry_path]
    result: list[Path] = []
    seen: set[str] = set()
    for item in registry_path:
        if not isinstance(item, Path):
            raise DispatchRegistryValidationError(f'dispatch target 注册表路径类型非法：{item!r}')
        resolved = item.resolve()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        result.append(resolved)
    if not result:
        raise DispatchRegistryValidationError('dispatch target 注册表路径不能为空')
    return result


TARGET_MANAGED_ENV_FIELDS = (
    'enabledEnv',
    'endpointEnv',
    'secretEnv',
    'titleEnv',
    'atAllEnv',
    'formatEnv',
    'silenceEnabledEnv',
    'silenceMinDeltaEnv',
    'allowedReleaseLevelsEnv',
)


DISPATCH_GLOBAL_RUNTIME_ENV_NAMES = (
    'DISPATCH_LEDGER_TREND_SERIES_DAYS',
    'DISPATCH_LEDGER_TREND_RECENT_DAYS',
    'DISPATCH_LEDGER_TREND_BASELINE_DAYS',
    'DISPATCH_DEDUPE_WINDOW_HOURS',
    'DISPATCH_MAX_ATTEMPTS',
    'DISPATCH_BACKOFF_SECONDS',
    'DISPATCH_TARGET_MIN_INTERVAL_MS',
    'DISPATCH_TARGET_MAX_PER_SECOND',
    'DISPATCH_TARGET_MAX_PER_MINUTE',
    'DISPATCH_TARGET_RATE_LIMIT_STATE_TTL_SECONDS',
)
