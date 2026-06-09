#!/usr/bin/env python3
"""控制平面 service/profile 的合并加载器。"""
from __future__ import annotations

import json
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

from openclaw.control_plane.manifest_models import ControlPlaneServiceConfigModel
from openclaw.lib.repo.path_contracts import resolve_path_contract
from openclaw.lib.repo.layout import (
    RepoRootResolutionError,
    resolve_control_plane_service_config_path,
    resolve_repo_root,
)


class ControlPlaneConfigError(RuntimeError):
    """控制平面 service/profile 链无效时抛出的错误。"""


_PATH_FIELDS: tuple[tuple[str, ...], ...] = (
    ('registry', 'jobsDir'),
    ('registry', 'modelsDir'),
    ('registry', 'targetsDir'),
    ('schemas', 'jobsSchema'),
    ('schemas', 'modelsSchema'),
    ('schemas', 'targetsSchema'),
)
_LIST_PATH_FIELDS: tuple[tuple[str, ...], ...] = (
    ('extensions', 'manifestsDirs'),
)
_SERVICE_SCHEMA_RELATIVE_PATH = ('config', 'control_plane', 'schemas', 'service.schema.json')


@lru_cache(maxsize=256)
def _read_json_cached(path_text: str, mtime_ns: int, size: int) -> dict[str, Any]:
    _ = (mtime_ns, size)
    path = Path(path_text)
    try:
        payload = json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError as exc:
        raise ControlPlaneConfigError(f'控制平面配置不存在：{path}') from exc
    except Exception as exc:
        # 配置入口只暴露路径级诊断，避免底层编码/解析异常泄漏成多种 CLI 形态。
        raise ControlPlaneConfigError(f'控制平面配置无法解析：{path} ({exc})') from exc
    if not isinstance(payload, dict):
        raise ControlPlaneConfigError(f'控制平面配置顶层必须为对象：{path}')
    return payload


def _read_json(path: Path) -> dict[str, Any]:
    """读取控制平面 JSON 配置并校验顶层对象。"""
    resolved = Path(path).resolve()
    try:
        stat = resolved.stat()
    except FileNotFoundError:
        return _read_json_cached(str(resolved), -1, -1)
    return deepcopy(_read_json_cached(str(resolved), int(stat.st_mtime_ns), int(stat.st_size)))


def _relative_to_base(path: Path, *, base_dir: Path) -> str:
    """把路径转换为相对指定基目录的 POSIX 路径。"""
    try:
        relative = os.path.relpath(path, base_dir)
    except ValueError:
        return str(path)
    return Path(relative).as_posix()


def _path_is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _repo_root_or_none(path: Path) -> Path | None:
    try:
        return resolve_repo_root(path)
    except RepoRootResolutionError:
        return None


def _ensure_repo_path(path: Path, *, repo_root: Path | None, label: str) -> None:
    if repo_root is None:
        return
    if not _path_is_relative_to(path, repo_root):
        raise ControlPlaneConfigError(f'配置字段 {label} 必须留在仓库内：{path}')


def _rebase_path_field(
    payload: dict[str, Any],
    keys: tuple[str, ...],
    *,
    source_base: Path,
    dest_base: Path,
    repo_root: Path | None = None,
) -> None:
    """重写单个路径字段，使其相对最终配置目录。"""
    current: Any = payload
    for key in keys[:-1]:
        if not isinstance(current, dict):
            return
        current = current.get(key)
    if not isinstance(current, dict):
        return
    leaf = keys[-1]
    value = str(current.get(leaf) or '').strip()
    if not value:
        return
    resolved = resolve_path_contract(value, base_dir=source_base, start_path=source_base, repo_root=repo_root)
    if resolved is None:
        return
    _ensure_repo_path(resolved, repo_root=repo_root, label='.'.join(keys))
    current[leaf] = _relative_to_base(resolved, base_dir=dest_base)


def _rebase_path_list_field(
    payload: dict[str, Any],
    keys: tuple[str, ...],
    *,
    source_base: Path,
    dest_base: Path,
    repo_root: Path | None = None,
) -> None:
    """重写路径数组字段，使其相对最终配置目录。"""
    current: Any = payload
    for key in keys[:-1]:
        if not isinstance(current, dict):
            return
        current = current.get(key)
    if not isinstance(current, dict):
        return
    leaf = keys[-1]
    rows = current.get(leaf)
    if rows in (None, ''):
        return
    if not isinstance(rows, list):
        raise ControlPlaneConfigError(f'配置字段 {".".join(keys)} 必须为数组')
    rebased: list[str] = []
    for idx, item in enumerate(rows):
        value = str(item or '').strip()
        if not value:
            raise ControlPlaneConfigError(f'配置字段 {".".join(keys)}[{idx}] 不能为空')
        resolved = resolve_path_contract(value, base_dir=source_base, start_path=source_base, repo_root=repo_root)
        if resolved is None:
            raise ControlPlaneConfigError(f'配置字段 {".".join(keys)}[{idx}] 不能为空')
        _ensure_repo_path(resolved, repo_root=repo_root, label=f'{".".join(keys)}[{idx}]')
        rebased.append(_relative_to_base(resolved, base_dir=dest_base))
    current[leaf] = rebased


def _rebase_payload_paths(payload: dict[str, Any], *, source_base: Path, dest_base: Path) -> dict[str, Any]:
    """对整个配置载荷中的路径字段执行 rebasing。"""
    rebased = deepcopy(payload)
    repo_root = _repo_root_or_none(source_base)
    for keys in _PATH_FIELDS:
        _rebase_path_field(rebased, keys, source_base=source_base, dest_base=dest_base, repo_root=repo_root)
    for keys in _LIST_PATH_FIELDS:
        _rebase_path_list_field(rebased, keys, source_base=source_base, dest_base=dest_base, repo_root=repo_root)
    return rebased


def _merge_values(base: Any, override: Any) -> Any:
    """按字典深合并规则合并配置值。"""
    if isinstance(base, dict) and isinstance(override, dict):
        merged: dict[str, Any] = {str(key): deepcopy(value) for key, value in base.items()}
        for key, value in override.items():
            key_text = str(key)
            if key_text in merged:
                merged[key_text] = _merge_values(merged.get(key_text), value)
            else:
                merged[key_text] = deepcopy(value)
        return merged
    return deepcopy(override)


def _load_config_chain(path: Path, *, stack: tuple[Path, ...] = ()) -> list[tuple[Path, dict[str, Any]]]:
    """沿 profile.extends 链加载全部配置文件。"""
    resolved = path.resolve()
    if resolved in stack:
        cycle = ' -> '.join(item.as_posix() for item in (*stack, resolved))
        raise ControlPlaneConfigError(f'控制平面 profile.extends 存在循环继承：{cycle}')
    payload = _read_json(resolved)
    repo_root = _repo_root_or_none(resolved)
    extends = payload.get('extends')
    chain: list[tuple[Path, dict[str, Any]]] = []
    if extends not in (None, ''):
        parent_text = str(extends).strip()
        if not parent_text:
            raise ControlPlaneConfigError(f'控制平面 profile.extends 不能为空：{resolved}')
        parent_path = resolve_path_contract(parent_text, base_dir=resolved.parent, start_path=resolved, repo_root=repo_root)
        if parent_path is None:
            raise ControlPlaneConfigError(f'控制平面 profile.extends 不能为空：{resolved}')
        _ensure_repo_path(parent_path, repo_root=repo_root, label='extends')
        chain.extend(_load_config_chain(parent_path, stack=(*stack, resolved)))
    chain.append((resolved, payload))
    return chain


def control_plane_service_schema_path(config_path: Path | None = None) -> Path:
    """解析控制平面 service schema 的仓库路径。"""
    base = resolve_repo_root(Path(__file__) if config_path is None else Path(config_path).resolve())
    return base.joinpath(*_SERVICE_SCHEMA_RELATIVE_PATH).resolve()


def load_control_plane_service_payload(config_path: Path | None = None) -> tuple[Path, dict[str, Any]]:
    """加载并合并控制平面 service/profile 配置。"""
    requested = resolve_control_plane_service_config_path(Path(__file__)) if config_path is None else Path(config_path).resolve()
    chain = _load_config_chain(requested)
    final_base = requested.parent.resolve()
    merged: dict[str, Any] = {}
    for item_path, payload in chain:
        rebased = _rebase_payload_paths(payload, source_base=item_path.parent.resolve(), dest_base=final_base)
        rebased.pop('extends', None)
        merged = _merge_values(merged, rebased)
    return requested, ControlPlaneServiceConfigModel.from_payload(merged).to_payload()
