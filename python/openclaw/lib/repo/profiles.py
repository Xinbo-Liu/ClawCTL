#!/usr/bin/env python3
"""Control-plane profile registry helpers."""
from __future__ import annotations

import os
import json
import re
from pathlib import Path, PurePosixPath

from .extension_discovery import discover_extension_profiles, discovered_profile_rel_path
from .repo_root import CONTROL_PLANE_SERVICE_CONFIG_REL_PATH, resolve_repo_root


CONTROL_PLANE_BASE_PROFILE_ID = 'base'
CONTROL_PLANE_AGENT_PLATFORM_PROFILE_ID = 'agent_platform'
DEFAULT_RUNTIME_CONTROL_PLANE_PROFILE_ID = CONTROL_PLANE_AGENT_PLATFORM_PROFILE_ID
CONTROL_PLANE_PROFILES_REL_DIR = 'config/control_plane/profiles'
CONTROL_PLANE_PROFILE_CONFIG_SUFFIX = '.service.json'
CONTROL_PLANE_PROFILE_REGISTRY_REL_PATH = 'config/control_plane/profile_registry.tsv'
CONTROL_PLANE_REPO_COMBINATION_PROFILES_REL_PATH = 'config/control_plane/repo_combination_profiles.json'
CONTROL_PLANE_PROFILE_REGISTRY_ENV = 'OPENCLAW_CONTROL_PLANE_PROFILE_REGISTRY_PATH'
CONTROL_PLANE_EXTENSIONS_DIR_REL_PATH = 'config/control_plane/extensions.d'
CONTROL_PLANE_SCHEMAS_REL_DIR = 'config/control_plane/schemas'
DEFAULT_RUNTIME_CONTROL_PLANE_SERVICE_CONFIG_REL_PATH = '/'.join(
    (
        CONTROL_PLANE_PROFILES_REL_DIR,
        f'{CONTROL_PLANE_AGENT_PLATFORM_PROFILE_ID}{CONTROL_PLANE_PROFILE_CONFIG_SUFFIX}',
    )
)
_CONTROL_PLANE_PROFILE_ID_PATTERN = re.compile(r'^[a-z0-9_]+$')


def control_plane_profile_registry_path(
    start_path: Path | None = None,
    *,
    allow_env_override: bool = True,
) -> Path:
    override = str(os.environ.get(CONTROL_PLANE_PROFILE_REGISTRY_ENV) or '').strip()
    if allow_env_override and override:
        return Path(override).resolve()
    return (resolve_repo_root(start_path) / CONTROL_PLANE_PROFILE_REGISTRY_REL_PATH).resolve()


def _trim_contract_text(value: str) -> str:
    return value.strip().replace('\r', '')


def _repo_combination_profiles_path(start_path: Path | None = None) -> Path:
    return (resolve_repo_root(start_path) / CONTROL_PLANE_REPO_COMBINATION_PROFILES_REL_PATH).resolve()


def control_plane_repo_combination_profile_rows(start_path: Path | None = None) -> tuple[dict[str, object], ...]:
    path = _repo_combination_profiles_path(start_path)
    if not path.is_file():
        return ()
    payload = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(payload, dict):
        raise ValueError(f'repo combination profile config root must be an object: {path}')
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in payload.get('profiles') or []:
        if not isinstance(raw, dict):
            continue
        profile_id = str(raw.get('id') or '').strip()
        config_path = str(raw.get('configPath') or '').strip().replace('\\', '/')
        if not profile_id or not config_path:
            raise ValueError(f'repo combination profile row missing id/configPath: {path}')
        if not _CONTROL_PLANE_PROFILE_ID_PATTERN.match(profile_id):
            raise ValueError(f'repo combination profile id 非法：{profile_id}')
        if profile_id in seen:
            raise ValueError(f'duplicate repo combination profile: {profile_id}')
        seen.add(profile_id)
        enabled_ids = tuple(str(item).strip() for item in raw.get('enabledExtensionIds') or [] if str(item).strip())
        manifest_dirs = tuple(str(item).strip() for item in raw.get('manifestsDirs') or [] if str(item).strip())
        shared_fields = tuple(item for item in raw.get('sharedDeployEnvFields') or [] if isinstance(item, dict))
        rows.append(
            {
                'id': profile_id,
                'configPath': config_path,
                'enabledExtensionIds': enabled_ids,
                'manifestsDirs': manifest_dirs,
                'sharedDeployEnvFields': shared_fields,
            }
        )
    return tuple(rows)


def control_plane_repo_combination_profile_rel_paths(start_path: Path | None = None) -> dict[str, str]:
    return {
        str(row['id']): str(row['configPath'])
        for row in control_plane_repo_combination_profile_rows(start_path)
    }


def control_plane_repo_combination_profile(
    profile_id: str,
    *,
    start_path: Path | None = None,
) -> dict[str, object] | None:
    normalized = str(profile_id or '').strip()
    for row in control_plane_repo_combination_profile_rows(start_path):
        if row.get('id') == normalized:
            return dict(row)
    return None


def control_plane_repo_combination_profile_for_config_path(
    config_path: str | Path,
    *,
    start_path: Path | None = None,
) -> dict[str, object] | None:
    repo_root = resolve_repo_root(start_path or Path(config_path)).resolve()
    resolved = Path(config_path).resolve()
    for row in control_plane_repo_combination_profile_rows(repo_root):
        candidate = (repo_root / str(row.get('configPath') or '')).resolve()
        if candidate == resolved:
            return dict(row)
    return None


def control_plane_repo_combination_shared_deploy_env_owner_sets(
    start_path: Path | None = None,
) -> dict[str, tuple[frozenset[str], ...]]:
    by_key: dict[str, list[frozenset[str]]] = {}
    for row in control_plane_repo_combination_profile_rows(start_path):
        for raw in row.get('sharedDeployEnvFields') or []:
            if not isinstance(raw, dict):
                continue
            owners = frozenset(str(item).strip() for item in raw.get('extensionIds') or [] if str(item).strip())
            if len(owners) < 2:
                continue
            for key in raw.get('keys') or []:
                normalized_key = str(key or '').strip()
                if normalized_key:
                    by_key.setdefault(normalized_key, []).append(owners)
    return {key: tuple(values) for key, values in by_key.items()}


def _expected_control_plane_profile_rel_path(profile_id: str, *, start_path: Path | None = None) -> str:
    if profile_id == CONTROL_PLANE_BASE_PROFILE_ID:
        return CONTROL_PLANE_SERVICE_CONFIG_REL_PATH
    if profile_id == CONTROL_PLANE_AGENT_PLATFORM_PROFILE_ID:
        return DEFAULT_RUNTIME_CONTROL_PLANE_SERVICE_CONFIG_REL_PATH
    combo_path = control_plane_repo_combination_profile_rel_paths(start_path).get(profile_id)
    if combo_path:
        return combo_path
    return (
        f'agent/extensions/{profile_id}/config/control_plane/profiles/'
        f'{profile_id}{CONTROL_PLANE_PROFILE_CONFIG_SUFFIX}'
    )


def _validate_control_plane_profile_registry_rel_path(
    rel_path: str,
    *,
    profile_id: str,
    start_path: Path | None = None,
) -> str:
    if not _CONTROL_PLANE_PROFILE_ID_PATTERN.match(profile_id):
        raise ValueError(f'profile registry profile_id 非法：{profile_id}')
    normalized = _trim_contract_text(rel_path).replace('\\', '/')
    if not normalized:
        raise ValueError(f'profile registry 缺少 config_path：{profile_id}')
    if normalized.startswith('/') or (
        len(normalized) >= 3 and normalized[0].isalpha() and normalized[1] == ':' and normalized[2] == '/'
    ):
        raise ValueError(f'profile registry 路径非法：{profile_id} -> {normalized}')
    if '..' in PurePosixPath(normalized).parts:
        raise ValueError(f'profile registry 路径非法：{profile_id} -> {normalized}')
    expected = _expected_control_plane_profile_rel_path(profile_id, start_path=start_path)
    if normalized != expected:
        raise ValueError(
            f'profile registry 路径必须使用合同路径：{profile_id} -> {normalized} '
            f'(expected {expected})'
        )
    return normalized


def _load_control_plane_profile_registry_rows(
    start_path: Path | None = None,
    *,
    allow_env_override: bool = True,
) -> tuple[tuple[str, str], ...]:
    registry_path = control_plane_profile_registry_path(start_path, allow_env_override=allow_env_override)
    repo_root = resolve_repo_root(start_path).resolve()
    try:
        content = registry_path.read_text(encoding='utf-8')
    except FileNotFoundError as exc:
        raise ValueError(f'missing profile registry: {registry_path}') from exc
    except Exception as exc:
        raise ValueError(f'cannot read profile registry: {registry_path} ({exc})') from exc

    rows: list[tuple[str, str]] = []
    seen_ids: set[str] = set()
    for line_no, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.lstrip('\ufeff') if line_no == 1 else raw_line
        stripped = _trim_contract_text(line)
        if not stripped or stripped.startswith('#'):
            continue
        parts = [_trim_contract_text(part) for part in line.split('\t')]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f'invalid profile registry line {line_no}: {raw_line}')
        profile_id, rel_path = parts
        if profile_id in seen_ids:
            raise ValueError(f'duplicate control-plane profile: {profile_id}')
        normalized_rel_path = _validate_control_plane_profile_registry_rel_path(
            rel_path,
            profile_id=profile_id,
            start_path=start_path,
        )
        resolved_path = (repo_root / normalized_rel_path).resolve()
        try:
            resolved_path.relative_to(repo_root)
        except ValueError as exc:
            raise ValueError(f'profile registry 路径非法：{profile_id} -> {normalized_rel_path}') from exc
        if not resolved_path.is_file() and normalized_rel_path.startswith('agent/extensions/'):
            continue
        if not resolved_path.is_file():
            raise ValueError(f'profile registry 指向不存在的配置文件：{profile_id} -> {normalized_rel_path}')
        rows.append((profile_id, normalized_rel_path))
        seen_ids.add(profile_id)

    if not rows:
        raise ValueError(f'profile registry is empty: {registry_path}')
    if CONTROL_PLANE_BASE_PROFILE_ID not in dict(rows):
        raise ValueError(f'profile registry 缺少 base profile: {registry_path}')
    return tuple(rows)


def control_plane_profile_config_rel_path(
    profile_id: str,
    start_path: Path | None = None,
    *,
    allow_env_override: bool = True,
) -> str:
    normalized = str(profile_id or '').strip()
    if not normalized:
        raise ValueError('control-plane profile id 不能为空')
    try:
        relative_path = control_plane_profile_config_rel_paths(
            start_path,
            allow_env_override=allow_env_override,
        ).get(normalized)
        if not relative_path:
            raise KeyError(normalized)
    except KeyError as exc:
        supported = ', '.join(available_control_plane_profile_ids(start_path, allow_env_override=allow_env_override))
        raise ValueError(f'unknown control-plane profile: {normalized or "<empty>"}; supported: {supported}') from exc
    return relative_path


def control_plane_profile_config_rel_paths(
    start_path: Path | None = None,
    *,
    allow_env_override: bool = True,
) -> dict[str, str]:
    repo_root = resolve_repo_root(start_path).resolve()
    rows = dict(_load_control_plane_profile_registry_rows(start_path, allow_env_override=allow_env_override))
    for discovered in discover_extension_profiles(repo_root, skip_ids=set(rows)):
        if not discovered.valid or discovered.id in rows:
            continue
        rows[discovered.id] = discovered_profile_rel_path(repo_root, discovered)
    return rows


def control_plane_profile_status_rows(
    start_path: Path | None = None,
    *,
    allow_env_override: bool = True,
) -> tuple[dict[str, object], ...]:
    """Return explicit registry profiles plus discovered extension candidates."""
    repo_root = resolve_repo_root(start_path).resolve()
    explicit_rows = dict(_load_control_plane_profile_registry_rows(start_path, allow_env_override=allow_env_override))
    items: list[dict[str, object]] = [
        {
            'id': profile_id,
            'path': rel_path,
            'configPath': rel_path,
            'source': 'registry',
            'status': 'valid',
            'issues': [],
        }
        for profile_id, rel_path in explicit_rows.items()
    ]
    for discovered in discover_extension_profiles(repo_root):
        rel_path = discovered_profile_rel_path(repo_root, discovered)
        if discovered.id in explicit_rows and explicit_rows[discovered.id] == rel_path:
            continue
        issues = list(discovered.issues)
        explicit_rel_path = explicit_rows.get(discovered.id)
        if explicit_rel_path and explicit_rel_path != rel_path:
            issues.append(
                f'discovered profile conflicts with registry entry: {discovered.id} -> {rel_path} '
                f'(registry: {explicit_rel_path})'
            )
        items.append(
            {
                'id': discovered.id,
                'path': rel_path,
                'configPath': rel_path,
                'source': 'discovered',
                'status': 'valid' if not issues else 'invalid',
                'issues': issues,
            }
        )
    return tuple(items)


def available_control_plane_profile_ids(
    start_path: Path | None = None,
    *,
    allow_env_override: bool = True,
) -> tuple[str, ...]:
    return tuple(control_plane_profile_config_rel_paths(start_path, allow_env_override=allow_env_override))


def resolve_control_plane_profile_service_config_path(
    profile_id: str,
    *,
    start_path: Path | None = None,
) -> Path:
    normalized = str(profile_id or '').strip()
    try:
        relative_path = control_plane_profile_config_rel_paths(start_path).get(normalized)
        if not relative_path:
            raise KeyError(normalized)
    except KeyError as exc:
        supported = ', '.join(available_control_plane_profile_ids(start_path))
        raise ValueError(f'unknown control-plane profile: {normalized or "<empty>"}; supported: {supported}') from exc
    return (resolve_repo_root(start_path) / relative_path).resolve()


def control_plane_profile_id_for_config_path(
    config_path: str | Path | None,
    *,
    start_path: Path | None = None,
) -> str | None:
    if config_path is None:
        return None
    resolved = Path(config_path).resolve()
    for profile_id in available_control_plane_profile_ids(start_path):
        if resolved == resolve_control_plane_profile_service_config_path(profile_id, start_path=start_path):
            return profile_id
    return None
