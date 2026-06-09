#!/usr/bin/env python3
"""Control-plane service scope classification and boundary validation."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openclaw.lib.repo.path_contracts import resolve_path_contract

from .profiles import (
    CONTROL_PLANE_AGENT_PLATFORM_PROFILE_ID,
    CONTROL_PLANE_BASE_PROFILE_ID,
    DEFAULT_RUNTIME_CONTROL_PLANE_SERVICE_CONFIG_REL_PATH,
    control_plane_repo_combination_profile,
    control_plane_repo_combination_profile_for_config_path,
    control_plane_repo_combination_profile_rel_paths,
)
from .repo_root import CONTROL_PLANE_SERVICE_CONFIG_REL_PATH, resolve_repo_root


CONTROL_PLANE_SERVICE_SCOPE_BASE = 'base'
CONTROL_PLANE_SERVICE_SCOPE_PLATFORM = 'platform'
CONTROL_PLANE_SERVICE_SCOPE_REPO_COMBINATION = 'repo_combination'
CONTROL_PLANE_SERVICE_SCOPE_MANAGED_EXTENSION = 'managed_extension'
CONTROL_PLANE_SERVICE_SCOPE_CUSTOM = 'custom'
_PLATFORM_EXTENSION_ID = CONTROL_PLANE_AGENT_PLATFORM_PROFILE_ID
_ROOT_PROFILE_DIR_REL_PATH = Path('config/control_plane/profiles')
_MANAGED_EXTENSION_ROOT_REL_PATH = Path('agent/extensions')
_EXTENSION_PROFILE_PARTS = ('config', 'control_plane', 'profiles')
_EXTENSION_SERVICE_SUFFIX = '.service.json'
_EXTENSION_ID_PATTERN = re.compile(r'^[a-z0-9_]+$')


@dataclass(frozen=True)
class ControlPlaneServiceScope:
    """Resolved role for one control-plane service config path."""

    kind: str
    profile_id: str
    extension_id: str = ''
    issues: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, str]:
        return {
            'kind': self.kind,
            'profileId': self.profile_id,
            'extensionId': self.extension_id,
        }


def _path_is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _resolved_repo_path(repo_root: Path, relative_path: str | Path) -> Path:
    return (repo_root / Path(relative_path)).resolve()


def _extension_contract_path(repo_root: Path, extension_id: str) -> Path:
    return (
        repo_root
        / _MANAGED_EXTENSION_ROOT_REL_PATH
        / extension_id
        / 'config'
        / 'control_plane'
        / 'profiles'
        / f'{extension_id}{_EXTENSION_SERVICE_SUFFIX}'
    ).resolve()


def _extension_manifest_dir(repo_root: Path, extension_id: str) -> Path:
    return (
        repo_root
        / _MANAGED_EXTENSION_ROOT_REL_PATH
        / extension_id
        / 'config'
        / 'control_plane'
        / 'extensions.d'
    ).resolve()


def _repo_combination_manifest_dir(repo_root: Path, value: str) -> Path:
    candidate = resolve_path_contract(
        value,
        base_dir=repo_root,
        start_path=repo_root,
        repo_root=repo_root,
    )
    return (candidate or (repo_root / value)).resolve()


def _extension_scope_from_repo_relative_path(repo_root: Path, path: Path) -> ControlPlaneServiceScope | None:
    try:
        rel_parts = path.resolve().relative_to(repo_root.resolve()).parts
    except ValueError:
        return None
    if len(rel_parts) < 3 or tuple(rel_parts[:2]) != tuple(_MANAGED_EXTENSION_ROOT_REL_PATH.parts):
        return None

    extension_id = rel_parts[2]
    extension_root = repo_root.joinpath(*rel_parts[:3]).resolve()
    expected_path = _extension_contract_path(repo_root, extension_id)
    if not _EXTENSION_ID_PATTERN.match(extension_id):
        return ControlPlaneServiceScope(
            kind=CONTROL_PLANE_SERVICE_SCOPE_CUSTOM,
            profile_id='custom',
            issues=(f'扩展 service 路径中的 extension id 非法：{extension_id}',),
        )
    if path.resolve() == expected_path:
        return ControlPlaneServiceScope(
            kind=CONTROL_PLANE_SERVICE_SCOPE_MANAGED_EXTENSION,
            profile_id=extension_id,
            extension_id=extension_id,
        )

    if len(rel_parts) >= 7 and tuple(rel_parts[3:6]) == _EXTENSION_PROFILE_PARTS and path.name.endswith(_EXTENSION_SERVICE_SUFFIX):
        return ControlPlaneServiceScope(
            kind=CONTROL_PLANE_SERVICE_SCOPE_CUSTOM,
            profile_id='custom',
            issues=(f'扩展 service 配置必须使用自身合同路径：{expected_path}',),
        )

    if path.name.endswith(_EXTENSION_SERVICE_SUFFIX) and _path_is_relative_to(path, extension_root):
        return ControlPlaneServiceScope(
            kind=CONTROL_PLANE_SERVICE_SCOPE_CUSTOM,
            profile_id='custom',
            issues=(f'扩展目录内的 service 配置必须使用自身合同路径：{expected_path}',),
        )
    return None


def classify_control_plane_service_scope(config_path: str | Path) -> ControlPlaneServiceScope:
    """Classify a service config as base, platform, extension default, or custom."""
    path = Path(config_path).resolve()
    repo_root = resolve_repo_root(path).resolve()
    if path == _resolved_repo_path(repo_root, CONTROL_PLANE_SERVICE_CONFIG_REL_PATH):
        return ControlPlaneServiceScope(
            kind=CONTROL_PLANE_SERVICE_SCOPE_BASE,
            profile_id=CONTROL_PLANE_BASE_PROFILE_ID,
        )
    if path == _resolved_repo_path(repo_root, DEFAULT_RUNTIME_CONTROL_PLANE_SERVICE_CONFIG_REL_PATH):
        return ControlPlaneServiceScope(
            kind=CONTROL_PLANE_SERVICE_SCOPE_PLATFORM,
            profile_id=CONTROL_PLANE_AGENT_PLATFORM_PROFILE_ID,
        )
    repo_combination = control_plane_repo_combination_profile_for_config_path(path, start_path=repo_root)
    if repo_combination is not None:
        return ControlPlaneServiceScope(
            kind=CONTROL_PLANE_SERVICE_SCOPE_REPO_COMBINATION,
            profile_id=str(repo_combination.get('id') or ''),
        )

    extension_scope = _extension_scope_from_repo_relative_path(repo_root, path)
    if extension_scope is not None:
        return extension_scope

    root_profile_dir = _resolved_repo_path(repo_root, _ROOT_PROFILE_DIR_REL_PATH)
    if path.name.endswith(_EXTENSION_SERVICE_SUFFIX) and _path_is_relative_to(path, root_profile_dir):
        platform_path = _resolved_repo_path(repo_root, DEFAULT_RUNTIME_CONTROL_PLANE_SERVICE_CONFIG_REL_PATH)
        repo_combination_paths = [
            _resolved_repo_path(repo_root, rel_path)
            for rel_path in control_plane_repo_combination_profile_rel_paths(repo_root).values()
        ]
        allowed_paths = ', '.join(str(item) for item in (platform_path, *repo_combination_paths))
        return ControlPlaneServiceScope(
            kind=CONTROL_PLANE_SERVICE_SCOPE_CUSTOM,
            profile_id='custom',
            issues=(f'基座 profile 目录只保留受控 profile 服务：{allowed_paths}',),
        )

    return ControlPlaneServiceScope(kind=CONTROL_PLANE_SERVICE_SCOPE_CUSTOM, profile_id='custom')


def _enabled_extension_ids(payload: dict[str, Any]) -> tuple[str, ...]:
    extensions = payload.get('extensions') if isinstance(payload.get('extensions'), dict) else {}
    values = extensions.get('enabledExtensionIds') or []
    if not isinstance(values, list):
        return ()
    return tuple(str(item).strip() for item in values if str(item).strip())


def _resolved_manifest_dirs(config_path: Path, payload: dict[str, Any], *, repo_root: Path) -> tuple[Path, ...]:
    extensions = payload.get('extensions') if isinstance(payload.get('extensions'), dict) else {}
    values = extensions.get('manifestsDirs') or []
    if not isinstance(values, list):
        return ()
    resolved: list[Path] = []
    for value in values:
        candidate = resolve_path_contract(
            value,
            base_dir=config_path.parent,
            start_path=config_path,
            repo_root=repo_root,
        )
        if candidate is not None:
            resolved.append(candidate.resolve())
    return tuple(resolved)


def _append_expected_enabled_ids_issue(
    issues: list[str],
    *,
    label: str,
    actual: tuple[str, ...],
    expected: tuple[str, ...],
) -> None:
    if actual == expected:
        return
    issues.append(
        f'{label} extensions.enabledExtensionIds 必须精确为 [{", ".join(expected)}]，当前为 [{", ".join(actual)}]'
    )


def _append_expected_manifest_dirs_issue(
    issues: list[str],
    *,
    label: str,
    actual: tuple[Path, ...],
    expected: tuple[Path, ...],
) -> None:
    if actual == expected:
        return
    issues.append(
        f'{label} extensions.manifestsDirs 必须精确为 [{", ".join(str(item) for item in expected)}]，'
        f'当前为 [{", ".join(str(item) for item in actual)}]'
    )


def validate_control_plane_service_boundary(config_path: str | Path, payload: dict[str, Any]) -> tuple[str, ...]:
    """Validate service-level module boundaries for contract service paths."""
    path = Path(config_path).resolve()
    repo_root = resolve_repo_root(path).resolve()
    scope = classify_control_plane_service_scope(path)
    issues = list(scope.issues)
    if scope.issues:
        return tuple(issues)
    if scope.kind == CONTROL_PLANE_SERVICE_SCOPE_CUSTOM:
        return ()

    enabled_ids = _enabled_extension_ids(payload)
    manifest_dirs = _resolved_manifest_dirs(path, payload, repo_root=repo_root)
    platform_manifest_dir = _resolved_repo_path(repo_root, 'config/control_plane/extensions.d')

    if scope.kind == CONTROL_PLANE_SERVICE_SCOPE_BASE:
        _append_expected_enabled_ids_issue(
            issues,
            label='base service',
            actual=enabled_ids,
            expected=(),
        )
        _append_expected_manifest_dirs_issue(
            issues,
            label='base service',
            actual=manifest_dirs,
            expected=(platform_manifest_dir,),
        )
    elif scope.kind == CONTROL_PLANE_SERVICE_SCOPE_PLATFORM:
        _append_expected_enabled_ids_issue(
            issues,
            label='agent_platform service',
            actual=enabled_ids,
            expected=(_PLATFORM_EXTENSION_ID,),
        )
        _append_expected_manifest_dirs_issue(
            issues,
            label='agent_platform service',
            actual=manifest_dirs,
            expected=(platform_manifest_dir,),
        )
    elif scope.kind == CONTROL_PLANE_SERVICE_SCOPE_REPO_COMBINATION:
        repo_combination = control_plane_repo_combination_profile(scope.profile_id, start_path=repo_root) or {}
        expected_extension_ids = tuple(
            str(item).strip()
            for item in repo_combination.get('enabledExtensionIds') or ()
            if str(item).strip()
        )
        _append_expected_enabled_ids_issue(
            issues,
            label=f'{scope.profile_id} service',
            actual=enabled_ids,
            expected=expected_extension_ids,
        )
        expected_manifest_dirs = tuple(
            _repo_combination_manifest_dir(repo_root, str(item))
            for item in repo_combination.get('manifestsDirs') or ()
            if str(item).strip()
        )
        _append_expected_manifest_dirs_issue(
            issues,
            label=f'{scope.profile_id} service',
            actual=manifest_dirs,
            expected=expected_manifest_dirs,
        )
    elif scope.kind == CONTROL_PLANE_SERVICE_SCOPE_MANAGED_EXTENSION:
        extension_id = scope.extension_id
        _append_expected_enabled_ids_issue(
            issues,
            label=f'extension service {extension_id}',
            actual=enabled_ids,
            expected=(_PLATFORM_EXTENSION_ID, extension_id),
        )
        _append_expected_manifest_dirs_issue(
            issues,
            label=f'extension service {extension_id}',
            actual=manifest_dirs,
            expected=(platform_manifest_dir, _extension_manifest_dir(repo_root, extension_id)),
        )
    return tuple(issues)
