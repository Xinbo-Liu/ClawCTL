#!/usr/bin/env python3
"""Structured checks for repository paths that must stay absent."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openclaw.lib.repo.contracts import repo_contract_path
from openclaw.lib.repo.layout import resolve_repo_root


ROOT_DIR = resolve_repo_root(Path(__file__))
ABSENT_SURFACES_PATH = repo_contract_path('governance.absent_surfaces')


class AbsentSurfaceError(RuntimeError):
    """Raised when the absent-surface manifest is malformed."""


@dataclass(frozen=True)
class AbsentSurface:
    id: str
    reason: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class AbsentSurfaceViolation:
    kind: str
    target: str
    reason: str
    surface_id: str


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise AbsentSurfaceError(f'缺少 absent surfaces 真源：{path}') from exc
    except Exception as exc:
        raise AbsentSurfaceError(f'absent surfaces 真源无法解析：{path} ({exc})') from exc
    if not isinstance(payload, dict):
        raise AbsentSurfaceError('absent surfaces 顶层必须为对象')
    return payload


def _unique_text_list(raw: Any, *, label: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise AbsentSurfaceError(f'{label} 必须为非空数组')
    result: list[str] = []
    seen: set[str] = set()
    for idx, item in enumerate(raw):
        value = str(item or '').strip().replace('\\', '/')
        if not value:
            raise AbsentSurfaceError(f'{label}[{idx}] 不能为空')
        if value.startswith('/') or '..' in Path(value).parts:
            raise AbsentSurfaceError(f'{label}[{idx}] 必须是仓库内相对路径：{value}')
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)


def load_absent_surfaces(path: Path = ABSENT_SURFACES_PATH) -> tuple[AbsentSurface, ...]:
    payload = _read_json_object(path)
    raw_surfaces = payload.get('surfaces')
    if not isinstance(raw_surfaces, list) or not raw_surfaces:
        raise AbsentSurfaceError('absent surfaces 必须提供 surfaces 非空数组')
    surfaces: list[AbsentSurface] = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for idx, row in enumerate(raw_surfaces):
        if not isinstance(row, dict):
            raise AbsentSurfaceError(f'surfaces[{idx}] 必须为对象')
        surface_id = str(row.get('id') or '').strip()
        if not surface_id:
            raise AbsentSurfaceError(f'surfaces[{idx}].id 不能为空')
        if surface_id in seen_ids:
            raise AbsentSurfaceError(f'surfaces[{idx}].id 重复：{surface_id}')
        seen_ids.add(surface_id)
        reason = str(row.get('reason') or '').strip()
        if not reason:
            raise AbsentSurfaceError(f'surfaces[{idx}].reason 不能为空')
        paths = _unique_text_list(row.get('paths'), label=f'surfaces[{idx}].paths')
        duplicated_paths = sorted(seen_paths & set(paths))
        if duplicated_paths:
            raise AbsentSurfaceError(f'surfaces[{idx}].paths 重复登记：{", ".join(duplicated_paths)}')
        seen_paths.update(paths)
        surfaces.append(AbsentSurface(id=surface_id, reason=reason, paths=paths))
    return tuple(surfaces)


def resolve_absent_surface_path(repo_root: Path, rel_path: str) -> Path:
    resolved_root = repo_root.resolve()
    target = (resolved_root / rel_path).resolve()
    try:
        target.relative_to(resolved_root)
    except ValueError as exc:
        raise AbsentSurfaceError(f'absent surface 路径越出仓库范围：{rel_path}') from exc
    return target


def validate_absent_surfaces(
    repo_root: Path = ROOT_DIR,
    surfaces: tuple[AbsentSurface, ...] | None = None,
) -> tuple[AbsentSurfaceViolation, ...]:
    rows = load_absent_surfaces() if surfaces is None else surfaces
    violations: list[AbsentSurfaceViolation] = []
    for surface in rows:
        for rel_path in surface.paths:
            if resolve_absent_surface_path(repo_root, rel_path).exists():
                violations.append(
                    AbsentSurfaceViolation(
                        kind='unexpected_path',
                        target=rel_path,
                        reason=surface.reason,
                        surface_id=surface.id,
                    )
                )
    return tuple(violations)
