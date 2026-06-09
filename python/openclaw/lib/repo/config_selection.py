#!/usr/bin/env python3
"""Config selection and repository path helpers."""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from .profiles import (
    CONTROL_PLANE_AGENT_PLATFORM_PROFILE_ID,
    CONTROL_PLANE_BASE_PROFILE_ID,
    resolve_control_plane_profile_service_config_path,
    control_plane_profile_id_for_config_path,
)
from .repo_root import CONTROL_PLANE_CONTAINER_REPO_ROOT, resolve_repo_root


CONTROL_PLANE_CONFIG_ENV = 'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH'
CONTROL_PLANE_PROFILE_ENV = 'OPENCLAW_CONTROL_PLANE_PROFILE'


def _normalize_selected_config_path(value: str | Path, *, start_path: Path | None = None) -> Path:
    text = str(value or '').strip().replace('\\', '/')
    container_root = str(CONTROL_PLANE_CONTAINER_REPO_ROOT).rstrip('/')
    if text == container_root:
        return resolve_repo_root(start_path).resolve()
    prefix = f'{container_root}/'
    if text.startswith(prefix):
        return (resolve_repo_root(start_path) / text[len(prefix):]).resolve()
    return Path(value).resolve()


def resolve_selected_control_plane_config_path(
    config_path: str | Path | None = None,
    *,
    control_plane_profile: str | None = None,
    start_path: Path | None = None,
    default_profile: str | None = None,
    default_to_base: bool = False,
) -> Path:
    requested_path = str(config_path or '').strip()
    requested_profile = str(control_plane_profile or '').strip()
    if requested_path and requested_profile:
        raise ValueError('--config-path 与 --control-plane-profile 不能同时使用')
    if requested_path:
        return _normalize_selected_config_path(requested_path, start_path=start_path)
    if requested_profile:
        return resolve_control_plane_profile_service_config_path(requested_profile, start_path=start_path)
    env_path = str(os.environ.get(CONTROL_PLANE_CONFIG_ENV) or '').strip()
    env_profile = str(os.environ.get(CONTROL_PLANE_PROFILE_ENV) or '').strip()
    if env_path:
        if env_profile:
            path_profile = control_plane_profile_id_for_config_path(_normalize_selected_config_path(env_path, start_path=start_path), start_path=start_path)
            if path_profile != env_profile:
                raise ValueError(
                    f'{CONTROL_PLANE_CONFIG_ENV} 与 {CONTROL_PLANE_PROFILE_ENV} 不一致：'
                    f'{CONTROL_PLANE_CONFIG_ENV} -> {path_profile}, {CONTROL_PLANE_PROFILE_ENV}={env_profile}'
                )
        return _normalize_selected_config_path(env_path, start_path=start_path)
    if env_profile:
        return resolve_control_plane_profile_service_config_path(env_profile, start_path=start_path)
    if default_profile:
        return resolve_control_plane_profile_service_config_path(default_profile, start_path=start_path)
    if default_to_base:
        return resolve_control_plane_profile_service_config_path(CONTROL_PLANE_BASE_PROFILE_ID, start_path=start_path)
    raise ValueError(
        f'未指定 control-plane 配置；请传入 --config-path / --control-plane-profile，'
        f'或设置环境变量 {CONTROL_PLANE_PROFILE_ENV} / {CONTROL_PLANE_CONFIG_ENV}。'
    )


def resolve_control_plane_service_config_path(start_path: Path | None = None) -> Path:
    return resolve_selected_control_plane_service_config_path(start_path=start_path, default_to_base=True)


def resolve_default_runtime_control_plane_service_config_path(start_path: Path | None = None) -> Path:
    return resolve_selected_runtime_control_plane_service_config_path(start_path=start_path, default_to_runtime=True)


def resolve_selected_control_plane_service_config_path(
    config_path: str | Path | None = None,
    *,
    start_path: Path | None = None,
    default_to_base: bool = False,
) -> Path:
    return resolve_selected_control_plane_config_path(
        config_path,
        start_path=start_path,
        default_to_base=default_to_base,
    )


def resolve_selected_runtime_control_plane_service_config_path(
    config_path: str | Path | None = None,
    *,
    start_path: Path | None = None,
    default_to_runtime: bool = False,
) -> Path:
    return resolve_selected_control_plane_config_path(
        config_path,
        start_path=start_path,
        default_profile=CONTROL_PLANE_AGENT_PLATFORM_PROFILE_ID if default_to_runtime else None,
    )


def resolve_selected_control_plane_profile_id(
    config_path: str | Path | None = None,
    *,
    control_plane_profile: str | None = None,
    start_path: Path | None = None,
    default_profile: str | None = None,
    default_to_base: bool = False,
) -> str | None:
    requested_profile = str(control_plane_profile or '').strip()
    if str(config_path or '').strip() and requested_profile:
        raise ValueError('--config-path 与 --control-plane-profile 不能同时使用')
    if requested_profile:
        return requested_profile
    resolved = resolve_selected_control_plane_config_path(
        config_path,
        control_plane_profile=control_plane_profile,
        start_path=start_path,
        default_profile=default_profile,
        default_to_base=default_to_base,
    )
    return control_plane_profile_id_for_config_path(resolved, start_path=start_path)


def repo_mounted_container_path_for_host_path(
    host_path: str | Path,
    *,
    start_path: Path | None = None,
    repo_mount_root: str | PurePosixPath = CONTROL_PLANE_CONTAINER_REPO_ROOT,
) -> PurePosixPath:
    repo_mount_root_path = PurePosixPath(str(repo_mount_root))
    normalized_text = str(host_path or '').strip().replace('\\', '/')
    repo_mount_prefix = f'{repo_mount_root_path.as_posix().rstrip("/")}/'
    if normalized_text == repo_mount_root_path.as_posix() or normalized_text.startswith(repo_mount_prefix):
        return PurePosixPath(normalized_text)

    repo_root = resolve_repo_root(start_path).resolve()
    resolved_host_path = Path(host_path).resolve()
    try:
        relative_path = resolved_host_path.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError(f'host config path is outside repo mount: {resolved_host_path}') from exc
    return repo_mount_root_path.joinpath(*PurePosixPath(relative_path.as_posix()).parts)


def resolve_selected_control_plane_container_config_path(
    config_path: str | Path | None = None,
    *,
    control_plane_profile: str | None = None,
    start_path: Path | None = None,
    default_profile: str | None = None,
    default_to_base: bool = False,
    repo_mount_root: str | PurePosixPath = CONTROL_PLANE_CONTAINER_REPO_ROOT,
) -> PurePosixPath:
    selected_path = resolve_selected_control_plane_config_path(
        config_path,
        control_plane_profile=control_plane_profile,
        start_path=start_path,
        default_profile=default_profile,
        default_to_base=default_to_base,
    )
    return repo_mounted_container_path_for_host_path(
        selected_path,
        start_path=start_path,
        repo_mount_root=repo_mount_root,
    )
