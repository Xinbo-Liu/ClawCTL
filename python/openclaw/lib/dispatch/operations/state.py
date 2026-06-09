from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, NoReturn

from openclaw.control_plane.extensions.ownership import filter_rows_by_extension, resolve_owned_row
from openclaw.control_plane.governance_surfaces import load_dispatch_operations_surface
from openclaw.lib.repo.config_selection import (
    CONTROL_PLANE_CONFIG_ENV,
    CONTROL_PLANE_PROFILE_ENV,
    resolve_selected_control_plane_config_path,
)
from openclaw.lib.repo.layout import (
    control_plane_profile_id_for_config_path,
    resolve_control_plane_profile_service_config_path,
    resolve_repo_root,
)
from openclaw.lib.repo.static_truth import parse_env_file


ROOT_DIR = resolve_repo_root(Path(__file__))


def fail(message: str, code: int = 2) -> NoReturn:
    sys.stderr.write(f'[dispatch_operations_surface][FAIL] {message}\n')
    raise SystemExit(code)


def load_config(*, config_path: Path | None = None) -> dict[str, Any]:
    payload = load_dispatch_operations_surface(config_path=config_path)
    if not isinstance(payload, dict):
        fail('dispatch_operations_surface.json top-level payload must be an object')
    return payload


def entries(*, config_path: Path | None = None, extension_id: str | None = None) -> list[dict[str, Any]]:
    rows = load_config(config_path=config_path).get('entries') or []
    if not isinstance(rows, list):
        fail('entries must be a list')
    return filter_rows_by_extension([row for row in rows if isinstance(row, dict)], extension_id)


def entry_info(entry_id: str, *, config_path: Path | None = None, extension_id: str | None = None) -> dict[str, Any]:
    try:
        return resolve_owned_row(
            [row for row in load_config(config_path=config_path).get('entries') or [] if isinstance(row, dict)],
            entry_id,
            extension_id=extension_id,
            id_key='id',
            label='dispatch operation entry',
        )
    except KeyError as exc:
        fail(str(exc))
    except ValueError as exc:
        fail(str(exc))


def string_list(value: Any) -> list[str]:
    return [str(item).strip() for item in list(value or []) if str(item).strip()]


def example_rows(value: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in list(value or []):
        if not isinstance(item, dict):
            continue
        title = str(item.get('title') or '').strip()
        command = str(item.get('command') or '').rstrip()
        if not title or not command:
            continue
        rows.append({'title': title, 'command': command})
    return rows


def config_path_from_opts(opts: dict[str, Any]) -> Path | None:
    value = str(opts.get('config_path') or '').strip()
    profile_id = str(opts.get('control_plane_profile') or '').strip()
    if value and profile_id:
        fail('--config-path 与 --control-plane-profile 不能同时使用')
    if value:
        try:
            return resolve_selected_control_plane_config_path(value, start_path=ROOT_DIR)
        except ValueError as exc:
            fail(str(exc))
    if profile_id:
        try:
            return resolve_control_plane_profile_service_config_path(profile_id, start_path=ROOT_DIR)
        except ValueError as exc:
            fail(str(exc))

    gate_env_file = str(opts.get('gate_env_file') or '').strip()
    if not gate_env_file:
        return None
    env_path = Path(gate_env_file).resolve()
    if not env_path.exists():
        fail(f'--gate-env-file 不存在：{env_path}')
    env_values = parse_env_file(env_path)
    env_config_path = str(env_values.get(CONTROL_PLANE_CONFIG_ENV) or '').strip()
    env_profile = str(env_values.get(CONTROL_PLANE_PROFILE_ENV) or '').strip()
    if env_config_path:
        try:
            resolved_path = resolve_selected_control_plane_config_path(env_config_path, start_path=ROOT_DIR)
        except ValueError as exc:
            fail(str(exc))
        if env_profile:
            resolved_profile = control_plane_profile_id_for_config_path(resolved_path, start_path=ROOT_DIR)
            if resolved_profile != env_profile:
                fail(
                    f'{env_path} 中的 {CONTROL_PLANE_CONFIG_ENV} 与 {CONTROL_PLANE_PROFILE_ENV} 不一致：'
                    f'{CONTROL_PLANE_CONFIG_ENV} -> {resolved_profile}, {CONTROL_PLANE_PROFILE_ENV}={env_profile}'
                )
        return resolved_path
    if env_profile:
        try:
            return resolve_control_plane_profile_service_config_path(env_profile, start_path=ROOT_DIR)
        except ValueError as exc:
            fail(str(exc))
    return None
