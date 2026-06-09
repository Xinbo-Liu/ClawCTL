"""Control-plane registry 与运行时 env 读取。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

from openclaw.control_plane.registry_loader import load_registry_from_path

from .shared import _json_object, _json_object_rows, _line_text

def _deploy_env_values(repo_root: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    env_path = repo_root / 'deploy' / '.env'
    try:
        lines = env_path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return values
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        if not key:
            continue
        values[key] = value.strip().strip('"').strip("'")
    return values


def _runtime_value(env_values: Dict[str, str], name: str) -> str:
    key = _line_text(name)
    if not key:
        return ''
    return _line_text(os.environ.get(key)) or _line_text(env_values.get(key))


def _is_unresolved_runtime_value(value: str) -> bool:
    text = _line_text(value)
    return (
        not text
        or text == '__REQUIRED__'
        or text.startswith('<')
        or text.endswith('>')
        or '${' in text
        or text.endswith('__REQUIRED_MODEL__')
    )


def _registry_rows(registry: Dict[str, Any], name: str) -> List[Dict[str, Any]]:
    return _json_object_rows(registry.get(name))


def _registry_owned_index(registry: Dict[str, Any], name: str) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    ambiguous_ids = set(_json_object(registry.get(f'{name}AmbiguousIds')).keys())
    for row in _registry_rows(registry, name):
        qualified_id = _line_text(row.get('qualifiedId'))
        local_id = _line_text(row.get('id'))
        if qualified_id:
            index[qualified_id] = row
        if local_id and local_id not in ambiguous_ids and local_id not in index:
            index[local_id] = row
    for key, row in _json_object(registry.get(f'{name}ByQualifiedId')).items():
        if isinstance(row, dict) and _line_text(key):
            index[_line_text(key)] = row
    for key, row in _json_object(registry.get(f'{name}ById')).items():
        if isinstance(row, dict) and _line_text(key):
            index.setdefault(_line_text(key), row)
    return index


def _load_registry(config_path: Path | None) -> Dict[str, Any]:
    if config_path is None:
        return {}
    return load_registry_from_path(config_path)
