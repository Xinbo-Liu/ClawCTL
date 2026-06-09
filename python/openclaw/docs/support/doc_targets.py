"""文档输出目标真源解析。"""
from __future__ import annotations

import json
from pathlib import Path

from openclaw.lib.repo.layout import resolve_repo_root
from typing import Any

ROOT_DIR = resolve_repo_root(Path(__file__))


def fail(prefix: str, message: str) -> "NoReturn":
    raise SystemExit(f'[{prefix}][FAIL] {message}')


def read_json_object(config_path: Path, *, prefix: str) -> dict[str, Any]:
    payload = json.loads(config_path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        fail(prefix, f'{config_path.relative_to(ROOT_DIR)} 顶层必须为对象')
    return payload


def require_nested_str(payload: dict[str, Any], key_path: list[str], *, prefix: str, label: str) -> str:
    current: Any = payload
    walked: list[str] = []
    for segment in key_path:
        walked.append(segment)
        if not isinstance(current, dict):
            fail(prefix, f'{label} 缺少字段：{".".join(walked)}')
        current = current.get(segment)
    value = str(current or '').strip()
    if not value:
        fail(prefix, f'{label} 不能为空：{".".join(key_path)}')
    return value


def resolve_target_from_config(config_rel_path: str, key_path: list[str], *, prefix: str, label: str) -> tuple[Path, str]:
    config_path = ROOT_DIR / config_rel_path
    payload = read_json_object(config_path, prefix=prefix)
    rel_path = require_nested_str(payload, key_path, prefix=prefix, label=label)
    return ROOT_DIR / rel_path, rel_path


def resolve_payload_and_target_from_config(config_rel_path: str, key_path: list[str], *, prefix: str, label: str) -> tuple[dict[str, Any], Path, str]:
    config_path = ROOT_DIR / config_rel_path
    payload = read_json_object(config_path, prefix=prefix)
    rel_path = require_nested_str(payload, key_path, prefix=prefix, label=label)
    return payload, ROOT_DIR / rel_path, rel_path
