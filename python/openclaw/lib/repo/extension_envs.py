#!/usr/bin/env python3
"""受管扩展 venv（虚拟环境）运行态 helper，负责离线 wheelhouse、依赖锁和 active manifest。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tomllib
from typing import Any, Mapping
import venv

from openclaw.lib.repo.bootstrap import bootstrap_env_defaults, bootstrap_path_entries
from openclaw.lib.repo.managed_extensions import (
    ManagedExtensionRow,
    managed_explicit_extensions,
    managed_extension_for_agent_ref,
    managed_extension_for_config_path,
)
from openclaw.lib.runtime.resolver_loader import require_path_resolver
from openclaw.runtime.path_view import normalize_runtime_path_view


ACTIVE_MANIFEST_NAME = 'active.json'
ENV_MANIFEST_NAME = 'openclaw_extension_env.json'
LOCK_FILE_NAME = 'requirements.lock'
REPO_WHEELHOUSE_DIR_NAME = 'offline_wheelhouse'
WHEELHOUSE_MANIFEST_NAME = 'manifest.json'
SCHEMA_VERSION = 1
ACTIVE_MANIFEST_SCHEMA_VERSION = 2
DEPENDENCY_HASH_PREFIX = 'openclaw-extension-env-v1'
_PIP_TIMEOUT_SECONDS = 300
_PYTHON_PROBE_TIMEOUT_SECONDS = 20
_ENV_NAME_RE = re.compile(r'^[A-Z_][A-Z0-9_]*$')
_LOCKED_REQUIREMENT_HEAD_RE = re.compile(r'^\s*([A-Za-z0-9_.-]+)==([^\s;]+)')
_REQUIREMENT_HASH_RE = re.compile(r'--hash=sha256:([0-9a-fA-F]{64})', re.IGNORECASE)
_PRESERVED_ENV_KEYS = {
    'ALL_PROXY',
    'APPDATA',
    'COMSPEC',
    'HOME',
    'HTTPS_PROXY',
    'HTTP_PROXY',
    'LANG',
    'LC_ALL',
    'LOCALAPPDATA',
    'NO_PROXY',
    'PATH',
    'PATHEXT',
    'REQUESTS_CA_BUNDLE',
    'SSL_CERT_FILE',
    'SYSTEMROOT',
    'TEMP',
    'TMP',
    'TZ',
    'USER',
    'USERNAME',
    'USERPROFILE',
    'WINDIR',
}
_CONTROL_ENV_KEYS = {
    'OPENCLAW_AGENT_MODEL_MODE',
    'OPENCLAW_CONTROL_PLANE_MODEL_PROFILE_REF',
    'OPENCLAW_CONTROL_PLANE_PROFILE',
    'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH',
    'OPENCLAW_INTERNAL_API_TOKEN',
    'OPENCLAW_REPO_ROOT',
    'OPENCLAW_RUNTIME_PATH_VIEW',
    'OPENCLAW_STATE_DIR',
    'OPENCLAW_TOOLS_ROOT',
}
_PYTHON_ENV_CLEAN_KEYS = {'PYTHONPATH', 'PYTHONHOME', 'PYTHONUSERBASE', 'VIRTUAL_ENV', 'PIP_REQUIRE_VIRTUALENV'}
_MANIFEST_RUNTIME_VIEWS = ('host', 'scheduler')


class ExtensionEnvError(RuntimeError):
    """扩展运行环境缺失、过期或校验失败时抛出的领域异常。"""


@dataclass(frozen=True)
class ExtensionDependencySnapshot:
    """扩展依赖快照，记录 pyproject、requirements.lock、Python 平台标签与派生 venv 名称。"""

    extension_id: str
    pyproject_path: Path
    lock_path: Path
    pyproject_hash: str
    lock_hash: str
    lock_present: bool
    lock_has_requirements: bool
    direct_dependencies: tuple[str, ...]
    dependency_hash: str
    python_tag: str
    platform_tag: str
    env_dir_name: str


@dataclass(frozen=True)
class LockedWheelRequirement:
    """requirements.lock 中单个离线 wheel 依赖的标准化表示。"""

    package: str
    normalized_package: str
    version: str
    hashes: tuple[str, ...]


@dataclass(frozen=True)
class RepoWheelRecord:
    """扩展仓库 offline_wheelhouse 下单个 wheel 文件的校验记录。"""

    filename: str
    path: Path
    package: str
    normalized_package: str
    version: str
    sha256: str
    size: int

    def to_json(self) -> dict[str, Any]:
        """返回 wheel 文件的可序列化摘要，不暴露本地绝对路径。"""
        return {
            'filename': self.filename,
            'package': self.package,
            'version': self.version,
            'sha256': self.sha256,
            'size': self.size,
        }


@dataclass(frozen=True)
class ExtensionEnvStatus:
    """扩展运行态 venv 的当前状态，包括期望路径、实际 manifest 和阻断问题。"""

    extension_id: str
    ok: bool
    dependency_hash: str
    envs_dir: Path
    wheelhouse_dir: Path
    expected_env_path: Path
    active_manifest_path: Path
    env_path: Path | None
    python_executable: Path | None
    issues: tuple[str, ...]
    manifest: dict[str, Any] | None

    def to_json(self) -> dict[str, Any]:
        """返回扩展 venv 状态 JSON，供 doctor、CLI 和 release evidence 消费。"""
        return {
            'extensionId': self.extension_id,
            'ok': self.ok,
            'dependencyHash': self.dependency_hash,
            'envsDir': str(self.envs_dir),
            'wheelhouseDir': str(self.wheelhouse_dir),
            'expectedEnvPath': str(self.expected_env_path),
            'activeManifestPath': str(self.active_manifest_path),
            'envPath': str(self.env_path) if self.env_path is not None else None,
            'pythonExecutable': str(self.python_executable) if self.python_executable is not None else None,
            'issues': list(self.issues),
            'manifest': self.manifest,
        }


@dataclass(frozen=True)
class PreparedExtensionEnv:
    """已准备好的扩展 venv，供 agent runtime 子进程启动时注入 Python 路径。"""

    row: ManagedExtensionRow
    env_path: Path
    python_executable: Path
    manifest: dict[str, Any]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _slug(value: str) -> str:
    text = re.sub(r'[^a-zA-Z0-9_.-]+', '-', str(value or '').strip().lower())
    return text.strip('-') or 'unknown'


def _path_is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes()) if path.is_file() else ''


def _read_pyproject_payload(pyproject_path: Path) -> dict[str, Any]:
    try:
        payload = tomllib.loads(pyproject_path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise ExtensionEnvError(f'扩展缺少 pyproject.toml：{pyproject_path}') from exc
    except tomllib.TOMLDecodeError as exc:
        raise ExtensionEnvError(f'扩展 pyproject.toml 无法解析：{pyproject_path} ({exc})') from exc
    if not isinstance(payload, dict):
        raise ExtensionEnvError(f'扩展 pyproject.toml 根节点必须为对象：{pyproject_path}')
    return payload


def _direct_dependencies(pyproject_path: Path) -> tuple[str, ...]:
    project = _read_pyproject_payload(pyproject_path).get('project')
    if not isinstance(project, dict):
        return ()
    dependencies = project.get('dependencies') or []
    if not isinstance(dependencies, list):
        raise ExtensionEnvError(f'扩展 pyproject.toml project.dependencies 必须为数组：{pyproject_path}')
    result: list[str] = []
    for idx, item in enumerate(dependencies):
        text = str(item or '').strip()
        if not text:
            raise ExtensionEnvError(f'扩展 pyproject.toml project.dependencies[{idx}] 不能为空：{pyproject_path}')
        result.append(text)
    return tuple(result)


def _requirement_content_lines(lock_path: Path) -> tuple[str, ...]:
    if not lock_path.is_file():
        return ()
    rows: list[str] = []
    continuation = ''
    for raw in lock_path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if continuation:
            line = continuation + ' ' + line
            continuation = ''
        if line.endswith('\\'):
            continuation = line[:-1].rstrip()
            continue
        rows.append(line)
    if continuation:
        rows.append(continuation)
    return tuple(rows)


def _normalize_distribution_name(name: str) -> str:
    return re.sub(r'[-_.]+', '-', str(name or '').strip()).lower()


def _locked_wheel_requirements(lock_path: Path) -> tuple[LockedWheelRequirement, ...]:
    grouped: dict[tuple[str, str], set[str]] = {}
    labels: dict[tuple[str, str], str] = {}
    for line in _requirement_content_lines(lock_path):
        match = _LOCKED_REQUIREMENT_HEAD_RE.match(line)
        if match is None:
            raise ExtensionEnvError(f'{lock_path} 只能包含 name==version + --hash=sha256:<hash> 的离线 wheel 依赖：{line}')
        package = match.group(1).strip()
        version = match.group(2).strip()
        hashes = {item.lower() for item in _REQUIREMENT_HASH_RE.findall(line)}
        if not hashes:
            raise ExtensionEnvError(f'{lock_path} 依赖缺少 --hash=sha256:<hash>：{line}')
        key = (_normalize_distribution_name(package), version)
        labels.setdefault(key, package)
        grouped.setdefault(key, set()).update(hashes)
    return tuple(
        LockedWheelRequirement(
            package=labels[key],
            normalized_package=key[0],
            version=key[1],
            hashes=tuple(sorted(hashes)),
        )
        for key, hashes in sorted(grouped.items())
    )


def _parse_wheel_record(path: Path) -> RepoWheelRecord:
    if path.suffix != '.whl':
        raise ExtensionEnvError(f'离线依赖包必须是 .whl 文件：{path}')
    parts = path.name[:-4].split('-')
    if len(parts) < 5:
        raise ExtensionEnvError(f'离线 wheel 文件名不符合 PEP 427：{path.name}')
    package = parts[0]
    version = parts[1]
    return RepoWheelRecord(
        filename=path.name,
        path=path.resolve(),
        package=package,
        normalized_package=_normalize_distribution_name(package),
        version=version,
        sha256=_file_sha256(path),
        size=path.stat().st_size,
    )


def runtime_python_tag() -> str:
    """返回当前解释器的稳定 Python 标签，参与扩展 venv 目录名计算。"""
    return _slug(str(getattr(sys.implementation, 'cache_tag', '') or f'py{sys.version_info.major}{sys.version_info.minor}'))


def runtime_platform_tag() -> str:
    """返回当前操作系统和架构标签，参与扩展 venv 目录名计算。"""
    return _slug(f'{platform.system()}-{platform.machine()}')


def dependency_snapshot(row: ManagedExtensionRow) -> ExtensionDependencySnapshot:
    """基于扩展 pyproject 与 requirements.lock 生成依赖快照和内容 hash。"""
    pyproject_path = (row.root_dir / 'pyproject.toml').resolve()
    lock_path = (row.root_dir / LOCK_FILE_NAME).resolve()
    pyproject_hash = _file_sha256(pyproject_path)
    if not pyproject_hash:
        raise ExtensionEnvError(f'扩展缺少 pyproject.toml：{pyproject_path}')
    lock_hash = _file_sha256(lock_path)
    direct_dependencies = _direct_dependencies(pyproject_path)
    lock_has_requirements = bool(_requirement_content_lines(lock_path))
    python_tag = runtime_python_tag()
    platform_tag = runtime_platform_tag()
    hasher = hashlib.sha256()
    for value in (
        DEPENDENCY_HASH_PREFIX,
        row.id,
        pyproject_hash,
        lock_hash if lock_hash else '<missing-lock>',
        '\n'.join(direct_dependencies),
        python_tag,
        platform_tag,
    ):
        hasher.update(str(value).encode('utf-8'))
        hasher.update(b'\0')
    dependency_hash = hasher.hexdigest()
    env_dir_name = f'{python_tag}-{platform_tag}-{dependency_hash[:16]}'
    return ExtensionDependencySnapshot(
        extension_id=row.id,
        pyproject_path=pyproject_path,
        lock_path=lock_path,
        pyproject_hash=pyproject_hash,
        lock_hash=lock_hash,
        lock_present=lock_path.is_file(),
        lock_has_requirements=lock_has_requirements,
        direct_dependencies=direct_dependencies,
        dependency_hash=dependency_hash,
        python_tag=python_tag,
        platform_tag=platform_tag,
        env_dir_name=env_dir_name,
    )


def _env_map(env: Mapping[str, str] | None = None) -> dict[str, str]:
    return dict(os.environ if env is None else env)


def _remove_env_key_casefold(env: dict[str, str], key: str) -> None:
    target = key.upper()
    for existing in list(env):
        if existing.upper() == target:
            env.pop(existing, None)


def _env_value_casefold(env: Mapping[str, str], key: str) -> str:
    target = key.upper()
    for existing, value in env.items():
        if existing.upper() == target:
            return str(value)
    return ''


def _add_env_ref_names(value: Any, result: set[str]) -> None:
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key or '').strip()
            if key.lower().endswith('env') and isinstance(child, str):
                name = child.strip()
                if _ENV_NAME_RE.match(name):
                    result.add(name)
                continue
            _add_env_ref_names(child, result)
        return
    if isinstance(value, list):
        for child in value:
            _add_env_ref_names(child, result)
        return
    if isinstance(value, str):
        text = value.strip()
        if text.lower().startswith('env:'):
            name = text.split(':', 1)[1].strip()
            if _ENV_NAME_RE.match(name):
                result.add(name)


@lru_cache(maxsize=32)
def _declared_runtime_env_names(config_path_text: str) -> tuple[str, ...]:
    if not config_path_text:
        return ()
    from openclaw.control_plane.manifest_fields import (
        DISPATCH_PROVIDER_REGISTRY_PATHS_KEY,
        DISPATCH_TARGET_REGISTRY_PATHS_KEY,
    )
    from openclaw.control_plane.registry import load_registry

    registry = load_registry(Path(config_path_text).resolve())
    names: set[str] = set()
    _add_env_ref_names(registry, names)
    registry_paths = registry.get('registryPaths') if isinstance(registry.get('registryPaths'), dict) else {}
    for key in (DISPATCH_TARGET_REGISTRY_PATHS_KEY, DISPATCH_PROVIDER_REGISTRY_PATHS_KEY):
        raw_paths = registry_paths.get(key)
        if not isinstance(raw_paths, list):
            continue
        for raw_path in raw_paths:
            path_text = str(raw_path or '').strip()
            if not path_text:
                continue
            path = Path(path_text)
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                continue
            _add_env_ref_names(payload, names)
    return tuple(sorted(names))


@lru_cache(maxsize=32)
def _runtime_path_env_names(repo_root_text: str, config_path_text: str) -> tuple[str, ...]:
    resolver = require_path_resolver(
        repo_root=Path(repo_root_text).resolve(),
        config_path=Path(config_path_text).resolve() if config_path_text else None,
    )
    names: set[str] = set()
    for entry_id in resolver.entries:
        entry = resolver.resolve_entry(entry_id)
        env_names = entry.get('env_names') if isinstance(entry.get('env_names'), dict) else {}
        for value in env_names.values():
            name = str(value or '').strip()
            if _ENV_NAME_RE.match(name):
                names.add(name)
    return tuple(sorted(names))


@lru_cache(maxsize=32)
def _allowed_extension_subprocess_env_names(repo_root_text: str, config_path_text: str) -> tuple[str, ...]:
    names = set(_CONTROL_ENV_KEYS)
    names.update(_declared_runtime_env_names(config_path_text))
    names.update(_runtime_path_env_names(repo_root_text, config_path_text))
    return tuple(sorted(names))


def _extension_subprocess_source_env(
    source_env: Mapping[str, str],
    *,
    repo_root: Path,
    config_path: str | Path | None = None,
) -> dict[str, str]:
    config_path_text = str(Path(config_path).resolve()) if config_path else ''
    allowed_names = {
        name.upper()
        for name in _allowed_extension_subprocess_env_names(str(Path(repo_root).resolve()), config_path_text)
    }
    result: dict[str, str] = {}
    for raw_key, raw_value in source_env.items():
        key = str(raw_key)
        upper_key = key.upper()
        if upper_key in _PYTHON_ENV_CLEAN_KEYS:
            continue
        if (
            upper_key in _PRESERVED_ENV_KEYS
            or upper_key in allowed_names
        ):
            result[key] = str(raw_value)
    return result


def _runtime_view(env: Mapping[str, str] | None = None) -> str:
    return normalize_runtime_path_view(_env_map(env).get('OPENCLAW_RUNTIME_PATH_VIEW'), fallback='host')


def _env_for_runtime_view(env: Mapping[str, str] | None, view: str) -> dict[str, str]:
    env_map = _env_map(env)
    env_map['OPENCLAW_RUNTIME_PATH_VIEW'] = view
    return env_map


def _control_plane_state_root_for_view(resolver: Any, view: str, env_map: Mapping[str, str]) -> Path:
    if view == 'host':
        return Path(resolver.resolve_path('control_plane_host_state_dir', 'host', env=dict(env_map))).resolve()
    if view == 'scheduler':
        return Path(resolver.resolve_path('state_root', 'scheduler', env=dict(env_map))).resolve()
    raise ExtensionEnvError(f'扩展 venv 不支持 runtime path view：{view}')


def _resolve_extension_state_path(
    entry_id: str,
    *,
    repo_root: Path,
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    env_map = _env_map(env)
    view = _runtime_view(env_map)
    resolver = require_path_resolver(repo_root=repo_root, config_path=Path(config_path).resolve() if config_path else None)
    resolved = Path(resolver.resolve_path(entry_id, view, env=env_map)).resolve()
    control_plane_root = _control_plane_state_root_for_view(resolver, view, env_map)
    if not _path_is_relative_to(resolved, control_plane_root):
        raise ExtensionEnvError(f'{entry_id} 必须位于 control-plane runtime state 内：{resolved}')
    return resolved


def extension_envs_dir(
    *,
    repo_root: Path,
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """解析扩展 venv 根目录；repo_root 是仓库根，config_path/env 决定 host 或 scheduler 视角。"""
    return _resolve_extension_state_path('extension_envs_dir', repo_root=repo_root, config_path=config_path, env=env)


def extension_wheelhouse_dir(
    *,
    repo_root: Path,
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """解析运行态扩展 wheelhouse 根目录，用于部署链同步离线依赖。"""
    return _resolve_extension_state_path('extension_wheelhouse_dir', repo_root=repo_root, config_path=config_path, env=env)


def extension_repo_wheelhouse_dir(row: ManagedExtensionRow) -> Path:
    """返回扩展仓库内随代码提交的 offline_wheelhouse 目录。"""
    return (row.root_dir / REPO_WHEELHOUSE_DIR_NAME).resolve()


def extension_active_manifest_path(row: ManagedExtensionRow, *, envs_dir: Path) -> Path:
    """返回指定扩展当前 active manifest 路径。"""
    return (envs_dir / row.id / ACTIVE_MANIFEST_NAME).resolve()


def extension_env_path(row: ManagedExtensionRow, snapshot: ExtensionDependencySnapshot, *, envs_dir: Path) -> Path:
    """按依赖快照派生指定扩展的不可变 venv 路径。"""
    return (envs_dir / row.id / snapshot.env_dir_name).resolve()


def extension_python_executable(env_path: Path) -> Path:
    """返回扩展 venv 内 Python 可执行文件路径，兼容 Windows 与 POSIX 布局。"""
    if os.name == 'nt':
        return (env_path / 'Scripts' / 'python.exe').resolve()
    return (env_path / 'bin' / 'python').resolve()


def _manifest_runtime_path_views(
    row: ManagedExtensionRow,
    *,
    snapshot: ExtensionDependencySnapshot,
    repo_root: Path,
    config_path: str | Path | None,
    env: Mapping[str, str] | None,
) -> dict[str, dict[str, str]]:
    """生成 active manifest 的多运行视角路径，避免 host/scheduler 互相覆盖。"""
    result: dict[str, dict[str, str]] = {}
    for view in _MANIFEST_RUNTIME_VIEWS:
        view_env = _env_for_runtime_view(env, view)
        view_envs_dir = extension_envs_dir(repo_root=repo_root, config_path=config_path, env=view_env)
        view_wheelhouse_dir = extension_wheelhouse_dir(repo_root=repo_root, config_path=config_path, env=view_env)
        view_env_path = extension_env_path(row, snapshot, envs_dir=view_envs_dir)
        result[view] = {
            'envsDir': str(view_envs_dir),
            'envPath': str(view_env_path),
            'pythonExecutable': str(extension_python_executable(view_env_path)),
            'wheelhouseDir': str(view_wheelhouse_dir),
            'activeManifestPath': str(extension_active_manifest_path(row, envs_dir=view_envs_dir)),
        }
    return result


def _read_manifest(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except Exception as exc:
        raise ExtensionEnvError(f'扩展 active manifest 无法读取：{path} ({exc})') from exc
    if not isinstance(payload, dict):
        raise ExtensionEnvError(f'扩展 active manifest 根节点必须为对象：{path}')
    return payload


def _manifest_declared_env_paths(manifest: dict[str, Any] | None) -> set[Path]:
    paths: set[Path] = set()
    if not isinstance(manifest, dict):
        return paths
    runtime_views = manifest.get('runtimePathViews')
    if isinstance(runtime_views, dict):
        for view_payload in runtime_views.values():
            if not isinstance(view_payload, dict):
                continue
            env_path_text = str(view_payload.get('envPath') or '').strip()
            if env_path_text:
                paths.add(Path(env_path_text).resolve())
    legacy_env_path = str(manifest.get('envPath') or '').strip()
    if legacy_env_path:
        paths.add(Path(legacy_env_path).resolve())
    return paths


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f'.{path.name}.tmp-{os.getpid()}')
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    os.replace(tmp_path, path)


def _no_bytecode_subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return env


def _remove_extension_env_bytecode(root: Path) -> None:
    if not root.exists():
        return
    for pattern in ('*.pyc', '*.pyo'):
        for path in sorted(root.rglob(pattern), reverse=True):
            if path.is_file():
                path.unlink(missing_ok=True)
    for path in sorted(root.rglob('__pycache__'), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)


def _probe_python_tag(python_executable: Path) -> str:
    result = subprocess.run(
        [
            str(python_executable),
            '-c',
            'import sys; print(getattr(sys.implementation, "cache_tag", "") or f"py{sys.version_info.major}{sys.version_info.minor}")',
        ],
        check=False,
        capture_output=True,
        env=_no_bytecode_subprocess_env(),
        text=True,
        timeout=_PYTHON_PROBE_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or '').strip()
        raise ExtensionEnvError(f'venv Python 不可执行：{python_executable} ({stderr})')
    return _slug(result.stdout.strip())


def _manifest_issues(
    row: ManagedExtensionRow,
    *,
    manifest: dict[str, Any] | None,
    snapshot: ExtensionDependencySnapshot,
    active_manifest_path: Path,
    expected_env_path: Path,
    expected_wheelhouse_dir: Path,
    runtime_view: str,
) -> tuple[list[str], Path | None, Path | None]:
    issues: list[str] = []
    if not snapshot.lock_present:
        issues.append(f'扩展缺少 {LOCK_FILE_NAME}：{snapshot.lock_path}')
    if manifest is None:
        issues.append(f'active manifest 缺失：{active_manifest_path}')
        return issues, None, None
    if int(manifest.get('schemaVersion') or 0) != ACTIVE_MANIFEST_SCHEMA_VERSION:
        issues.append('active manifest schemaVersion 不匹配')
        return issues, None, None
    if str(manifest.get('extensionId') or '').strip() != row.id:
        issues.append('active manifest extensionId 不匹配')
    if str(manifest.get('dependencyHash') or '').strip() != snapshot.dependency_hash:
        issues.append('active manifest dependencyHash 不匹配；请重新 prepare')
    if str(manifest.get('pythonTag') or '').strip() != snapshot.python_tag:
        issues.append('active manifest Python 版本不匹配；请重新 prepare')
    if str(manifest.get('platformTag') or '').strip() != snapshot.platform_tag:
        issues.append('active manifest platform 不匹配；请重新 prepare')
    runtime_views = manifest.get('runtimePathViews')
    if not isinstance(runtime_views, dict):
        issues.append('active manifest 缺少 runtimePathViews')
        return issues, None, None
    missing_views = [view for view in _MANIFEST_RUNTIME_VIEWS if not isinstance(runtime_views.get(view), dict)]
    if missing_views:
        issues.append(f'active manifest runtimePathViews 缺少视角：{", ".join(missing_views)}')
        return issues, None, None
    view_payload = runtime_views.get(runtime_view)
    if not isinstance(view_payload, dict):
        issues.append(f'active manifest 缺少 runtimePathViews.{runtime_view}')
        return issues, None, None
    active_manifest_text = str(view_payload.get('activeManifestPath') or '').strip()
    if active_manifest_text and Path(active_manifest_text).resolve() != active_manifest_path.resolve():
        issues.append(f'active manifest 当前视角 activeManifestPath 不匹配：{active_manifest_text} != {active_manifest_path}')
    wheelhouse_text = str(view_payload.get('wheelhouseDir') or '').strip()
    if wheelhouse_text and Path(wheelhouse_text).resolve() != expected_wheelhouse_dir.resolve():
        issues.append(f'active manifest 当前视角 wheelhouseDir 不匹配：{wheelhouse_text} != {expected_wheelhouse_dir}')
    env_path_text = str(view_payload.get('envPath') or '').strip()
    if not env_path_text:
        issues.append(f'active manifest 缺少 runtimePathViews.{runtime_view}.envPath')
        return issues, None, None
    env_path = Path(env_path_text).resolve()
    if env_path != expected_env_path.resolve():
        issues.append(f'active manifest 当前视角 envPath 不匹配：{env_path} != {expected_env_path}')
    if not _path_is_relative_to(env_path, active_manifest_path.parent):
        issues.append(f'active manifest 当前视角 envPath 越界：{env_path}')
    python_text = str(view_payload.get('pythonExecutable') or '').strip()
    python_executable = Path(python_text).resolve() if python_text else extension_python_executable(env_path)
    if not python_executable.exists():
        issues.append(f'venv Python 不存在：{python_executable}')
    elif not _path_is_relative_to(python_executable, env_path):
        issues.append(f'venv Python 路径越界：{python_executable}')
    else:
        try:
            actual_tag = _probe_python_tag(python_executable)
        except ExtensionEnvError as exc:
            issues.append(str(exc))
        else:
            if actual_tag != snapshot.python_tag:
                issues.append(f'venv Python 版本不匹配：{actual_tag} != {snapshot.python_tag}')
    if not env_path.is_dir():
        issues.append(f'venv 目录不存在：{env_path}')
    return issues, env_path, python_executable


def extension_env_status(
    row: ManagedExtensionRow,
    *,
    repo_root: Path,
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> ExtensionEnvStatus:
    """检查扩展 venv 是否存在、manifest 是否匹配当前依赖快照、Python 是否可执行。"""
    runtime_view = _runtime_view(env)
    envs_dir = extension_envs_dir(repo_root=repo_root, config_path=config_path, env=env)
    wheelhouse_dir = extension_wheelhouse_dir(repo_root=repo_root, config_path=config_path, env=env)
    snapshot = dependency_snapshot(row)
    expected_env_path = extension_env_path(row, snapshot, envs_dir=envs_dir)
    active_manifest_path = extension_active_manifest_path(row, envs_dir=envs_dir)
    try:
        manifest = _read_manifest(active_manifest_path)
        issues, env_path, python_executable = _manifest_issues(
            row,
            manifest=manifest,
            snapshot=snapshot,
            active_manifest_path=active_manifest_path,
            expected_env_path=expected_env_path,
            expected_wheelhouse_dir=wheelhouse_dir,
            runtime_view=runtime_view,
        )
    except ExtensionEnvError as exc:
        manifest = None
        env_path = None
        python_executable = None
        issues = [str(exc)]
    return ExtensionEnvStatus(
        extension_id=row.id,
        ok=not issues,
        dependency_hash=snapshot.dependency_hash,
        envs_dir=envs_dir,
        wheelhouse_dir=wheelhouse_dir,
        expected_env_path=expected_env_path,
        active_manifest_path=active_manifest_path,
        env_path=env_path,
        python_executable=python_executable,
        issues=tuple(issues),
        manifest=manifest,
    )


def _build_manifest(
    row: ManagedExtensionRow,
    *,
    snapshot: ExtensionDependencySnapshot,
    repo_root: Path,
    config_path: str | Path | None,
    env: Mapping[str, str] | None,
) -> dict[str, Any]:
    runtime_view = _runtime_view(env)
    runtime_path_views = _manifest_runtime_path_views(
        row,
        snapshot=snapshot,
        repo_root=repo_root,
        config_path=config_path,
        env=env,
    )
    return {
        'schemaVersion': ACTIVE_MANIFEST_SCHEMA_VERSION,
        'extensionId': row.id,
        'extensionRoot': str(row.root_dir),
        'runtimePathView': runtime_view,
        'runtimePathViews': runtime_path_views,
        'pythonTag': snapshot.python_tag,
        'pythonVersion': f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}',
        'platformTag': snapshot.platform_tag,
        'dependencyHash': snapshot.dependency_hash,
        'pyprojectPath': str(snapshot.pyproject_path),
        'pyprojectHash': snapshot.pyproject_hash,
        'lockPath': str(snapshot.lock_path),
        'lockHash': snapshot.lock_hash,
        'lockPresent': snapshot.lock_present,
        'lockHasRequirements': snapshot.lock_has_requirements,
        'directDependencies': list(snapshot.direct_dependencies),
        'preparedAt': _utc_now_iso(),
    }


def _repo_wheelhouse_manifest_issues(
    *,
    row: ManagedExtensionRow,
    snapshot: ExtensionDependencySnapshot,
    manifest_path: Path,
    records: tuple[RepoWheelRecord, ...],
) -> list[str]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return [f'离线 wheel manifest 缺失：{manifest_path}']
    except Exception as exc:
        return [f'离线 wheel manifest 无法读取：{manifest_path} ({exc})']
    if not isinstance(manifest, dict):
        return [f'离线 wheel manifest 根节点必须为对象：{manifest_path}']

    issues: list[str] = []
    if int(manifest.get('schemaVersion') or 0) != SCHEMA_VERSION:
        issues.append('离线 wheel manifest schemaVersion 不匹配')
    if str(manifest.get('extensionId') or '').strip() != row.id:
        issues.append('离线 wheel manifest extensionId 不匹配')
    if str(manifest.get('lockHash') or '').strip().lower() != snapshot.lock_hash:
        issues.append('离线 wheel manifest lockHash 不匹配；请重新生成 manifest')
    wheels = manifest.get('wheels')
    if not isinstance(wheels, list):
        issues.append('离线 wheel manifest wheels 必须为数组')
        return issues
    manifest_records: dict[str, tuple[str, int]] = {}
    for idx, item in enumerate(wheels):
        if not isinstance(item, dict):
            issues.append(f'离线 wheel manifest wheels[{idx}] 必须为对象')
            continue
        filename = str(item.get('filename') or '').strip()
        sha256 = str(item.get('sha256') or '').strip().lower()
        size = item.get('size')
        if not filename or not sha256 or not isinstance(size, int):
            issues.append(f'离线 wheel manifest wheels[{idx}] 缺少 filename/sha256/size')
            continue
        manifest_records[filename] = (sha256, size)
    actual_records = {record.filename: (record.sha256, record.size) for record in records}
    if manifest_records != actual_records:
        issues.append('离线 wheel manifest wheels 与目录内容不一致；请重新生成 manifest')
    return issues


def validate_extension_repo_wheelhouse(row: ManagedExtensionRow) -> dict[str, Any]:
    """校验扩展仓库内离线 wheelhouse 与 requirements.lock 的包名、版本和 hash 一致。"""
    snapshot = dependency_snapshot(row)
    if not snapshot.lock_present:
        raise ExtensionEnvError(f'{row.id} 缺少 {LOCK_FILE_NAME}：{snapshot.lock_path}')
    locked = _locked_wheel_requirements(snapshot.lock_path)
    locked_by_key = {
        (item.normalized_package, item.version): item
        for item in locked
    }
    repo_dir = extension_repo_wheelhouse_dir(row)
    issues: list[str] = []
    records: list[RepoWheelRecord] = []

    if snapshot.direct_dependencies and not locked:
        issues.append(f'{row.id} 声明了外部依赖，但 {LOCK_FILE_NAME} 没有可安装依赖：{snapshot.lock_path}')
    if locked and not repo_dir.is_dir():
        issues.append(f'扩展离线 wheelhouse 缺失：{repo_dir}')
    if repo_dir.is_dir():
        for wheel_path in sorted(repo_dir.glob('*.whl'), key=lambda item: item.name.lower()):
            try:
                records.append(_parse_wheel_record(wheel_path))
            except ExtensionEnvError as exc:
                issues.append(str(exc))

    records_by_key: dict[tuple[str, str], list[RepoWheelRecord]] = {}
    for record in records:
        records_by_key.setdefault((record.normalized_package, record.version), []).append(record)
        if (record.normalized_package, record.version) not in locked_by_key:
            issues.append(f'离线 wheel 不在 {LOCK_FILE_NAME} 锁定集合中：{record.filename}')

    for key, locked_item in locked_by_key.items():
        matches = records_by_key.get(key, [])
        if not matches:
            issues.append(f'锁定依赖缺少离线 wheel：{locked_item.package}=={locked_item.version}')
            continue
        if len(matches) != 1:
            names = ', '.join(record.filename for record in matches)
            issues.append(f'锁定依赖离线 wheel 必须唯一：{locked_item.package}=={locked_item.version} ({names})')
            continue
        record = matches[0]
        if record.sha256 not in locked_item.hashes:
            issues.append(f'离线 wheel hash 与 {LOCK_FILE_NAME} 不一致：{record.filename}')

    manifest_path = repo_dir / WHEELHOUSE_MANIFEST_NAME
    if locked:
        issues.extend(
            _repo_wheelhouse_manifest_issues(
                row=row,
                snapshot=snapshot,
                manifest_path=manifest_path,
                records=tuple(records),
            )
        )
    elif repo_dir.is_dir() and records:
        issues.append(f'{row.id} 没有锁定依赖，但 {repo_dir} 中存在 .whl 文件')

    if issues:
        raise ExtensionEnvError('; '.join(issues))
    return {
        'extensionId': row.id,
        'ok': True,
        'repoWheelhouseDir': str(repo_dir),
        'manifestPath': str(manifest_path) if manifest_path.is_file() else None,
        'lockPath': str(snapshot.lock_path),
        'lockHash': snapshot.lock_hash,
        'wheels': [record.to_json() for record in records],
    }


def sync_extension_wheelhouse(
    row: ManagedExtensionRow,
    *,
    repo_root: Path,
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    clean: bool = True,
) -> dict[str, Any]:
    """把扩展仓库 offline_wheelhouse 同步到运行态目录，并按 clean 参数清理失效文件。"""
    validation = validate_extension_repo_wheelhouse(row)
    source_dir = extension_repo_wheelhouse_dir(row)
    runtime_root = extension_wheelhouse_dir(repo_root=repo_root, config_path=config_path, env=env)
    target_dir = (runtime_root / row.id).resolve()
    if not _path_is_relative_to(target_dir, runtime_root):
        raise ExtensionEnvError(f'扩展 runtime wheelhouse 路径越界：{target_dir}')

    wheel_names = {str(item['filename']) for item in validation.get('wheels') or []}
    source_files = [source_dir / name for name in sorted(wheel_names)]
    manifest_path = source_dir / WHEELHOUSE_MANIFEST_NAME
    if manifest_path.is_file():
        source_files.append(manifest_path)

    copied: list[str] = []
    unchanged: list[str] = []
    removed: list[str] = []
    target_dir.mkdir(parents=True, exist_ok=True)
    allowed_names = {path.name for path in source_files}
    for source_path in source_files:
        target_path = target_dir / source_path.name
        if target_path.is_file() and _file_sha256(target_path) == _file_sha256(source_path):
            unchanged.append(str(target_path))
            continue
        shutil.copy2(source_path, target_path)
        copied.append(str(target_path))

    if clean:
        for existing in sorted(target_dir.iterdir(), key=lambda item: item.name.lower()):
            if not existing.is_file() or existing.name in allowed_names:
                continue
            if existing.suffix != '.whl' and existing.name != WHEELHOUSE_MANIFEST_NAME:
                continue
            if not _path_is_relative_to(existing, target_dir):
                raise ExtensionEnvError(f'拒绝清理越界 runtime wheelhouse 文件：{existing}')
            existing.unlink()
            removed.append(str(existing))

    return {
        'extensionId': row.id,
        'changed': bool(copied or removed),
        'clean': clean,
        'repoWheelhouseDir': str(source_dir),
        'runtimeWheelhouseDir': str(target_dir),
        'copied': copied,
        'unchanged': unchanged,
        'removed': removed,
        'validation': validation,
    }


def ensure_extension_env(
    row: ManagedExtensionRow,
    *,
    repo_root: Path,
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    offline: bool = True,
    allow_online: bool = False,
    clean_wheelhouse: bool = True,
) -> dict[str, Any]:
    """同步扩展离线 wheelhouse、准备 venv，并返回最终校验结果。"""
    sync_result = sync_extension_wheelhouse(
        row,
        repo_root=repo_root,
        config_path=config_path,
        env=env,
        clean=clean_wheelhouse,
    )
    prepare_result = prepare_extension_env(
        row,
        repo_root=repo_root,
        config_path=config_path,
        env=env,
        offline=offline,
        allow_online=allow_online,
    )
    status = extension_env_status(row, repo_root=repo_root, config_path=config_path, env=env)
    return {
        'extensionId': row.id,
        'ok': bool(status.ok),
        'changed': bool(sync_result.get('changed') or prepare_result.get('changed')),
        'syncWheelhouse': sync_result,
        'prepare': prepare_result,
        'status': status.to_json(),
    }


def _safe_remove_tree(path: Path, *, required_parent: Path) -> None:
    resolved = path.resolve()
    if not _path_is_relative_to(resolved, required_parent):
        raise ExtensionEnvError(f'拒绝删除越界扩展 venv 路径：{resolved}')
    if resolved.exists():
        shutil.rmtree(resolved)


def _pip_install_command(
    *,
    python_executable: Path,
    lock_path: Path,
    wheelhouse_dir: Path,
    extension_id: str,
    offline: bool,
) -> list[str]:
    command = [
        str(python_executable),
        '-m',
        'pip',
        'install',
        '--disable-pip-version-check',
        '--no-compile',
        '--only-binary',
        ':all:',
        '--require-hashes',
    ]
    if offline:
        command.extend([
            '--no-index',
            '--find-links',
            str((wheelhouse_dir / extension_id).resolve()),
            '--find-links',
            str(wheelhouse_dir.resolve()),
        ])
    command.extend(['-r', str(lock_path)])
    return command


def prepare_extension_env(
    row: ManagedExtensionRow,
    *,
    repo_root: Path,
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    offline: bool = True,
    allow_online: bool = False,
) -> dict[str, Any]:
    """创建或复用扩展 venv；offline 为真时只允许从运行态 wheelhouse 安装依赖。"""
    if offline and allow_online:
        raise ExtensionEnvError('--offline 与 --allow-online 不能同时使用')
    install_offline = not allow_online
    envs_dir = extension_envs_dir(repo_root=repo_root, config_path=config_path, env=env)
    wheelhouse_dir = extension_wheelhouse_dir(repo_root=repo_root, config_path=config_path, env=env)
    extension_root = (envs_dir / row.id).resolve()
    snapshot = dependency_snapshot(row)
    if not snapshot.lock_present:
        raise ExtensionEnvError(f'{row.id} 缺少 {LOCK_FILE_NAME}：{snapshot.lock_path}')
    if snapshot.direct_dependencies and not snapshot.lock_has_requirements:
        raise ExtensionEnvError(f'{row.id} 声明了外部依赖，但 {LOCK_FILE_NAME} 没有可安装依赖：{snapshot.lock_path}')

    current_status = extension_env_status(row, repo_root=repo_root, config_path=config_path, env=env)
    if current_status.ok:
        _remove_extension_env_bytecode(extension_root)
        return {
            'extensionId': row.id,
            'changed': False,
            'status': 'ready',
            'envPath': str(current_status.env_path),
            'activeManifestPath': str(current_status.active_manifest_path),
        }

    extension_root.mkdir(parents=True, exist_ok=True)
    env_path = extension_env_path(row, snapshot, envs_dir=envs_dir)
    tmp_path = (extension_root / f'.prepare-{snapshot.env_dir_name}-{os.getpid()}').resolve()
    _safe_remove_tree(tmp_path, required_parent=extension_root)
    try:
        builder = venv.EnvBuilder(with_pip=True, clear=True)
        builder.create(tmp_path)
        python_executable = extension_python_executable(tmp_path)
        if snapshot.lock_has_requirements:
            command = _pip_install_command(
                python_executable=python_executable,
                lock_path=snapshot.lock_path,
                wheelhouse_dir=wheelhouse_dir,
                extension_id=row.id,
                offline=install_offline,
            )
            result = subprocess.run(
                command,
                cwd=str(repo_root),
                check=False,
                capture_output=True,
                env=_no_bytecode_subprocess_env(),
                text=True,
                timeout=_PIP_TIMEOUT_SECONDS,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or '').strip()
                raise ExtensionEnvError(f'{row.id} venv 依赖安装失败：{detail}')
        actual_tag = _probe_python_tag(python_executable)
        if actual_tag != snapshot.python_tag:
            raise ExtensionEnvError(f'{row.id} venv Python 版本不匹配：{actual_tag} != {snapshot.python_tag}')
        _remove_extension_env_bytecode(tmp_path)
        manifest = _build_manifest(
            row,
            snapshot=snapshot,
            repo_root=repo_root,
            config_path=config_path,
            env=env,
        )
        _write_json_atomic(tmp_path / ENV_MANIFEST_NAME, manifest)
        _safe_remove_tree(env_path, required_parent=extension_root)
        tmp_path.replace(env_path)
        _write_json_atomic(extension_active_manifest_path(row, envs_dir=envs_dir), manifest)
    except Exception:
        _safe_remove_tree(tmp_path, required_parent=extension_root)
        raise

    final_status = extension_env_status(row, repo_root=repo_root, config_path=config_path, env=env)
    if not final_status.ok:
        raise ExtensionEnvError(f'{row.id} venv prepare 后校验失败：{"; ".join(final_status.issues)}')
    return {
        'extensionId': row.id,
        'changed': True,
        'status': 'prepared',
        'envPath': str(final_status.env_path),
        'activeManifestPath': str(final_status.active_manifest_path),
    }


def prune_extension_envs(
    row: ManagedExtensionRow,
    *,
    repo_root: Path,
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    keep: int = 2,
) -> dict[str, Any]:
    """清理扩展历史 venv，只保留 active manifest 引用和最近 keep 个候选目录。"""
    envs_dir = extension_envs_dir(repo_root=repo_root, config_path=config_path, env=env)
    extension_root = (envs_dir / row.id).resolve()
    active_paths: set[Path] = set()
    manifest = _read_manifest(extension_active_manifest_path(row, envs_dir=envs_dir))
    status = extension_env_status(row, repo_root=repo_root, config_path=config_path, env=env)
    if status.env_path is not None:
        active_paths.add(status.env_path.resolve())
    active_paths.update(
        path for path in _manifest_declared_env_paths(manifest)
        if _path_is_relative_to(path, extension_root)
    )
    candidates = [
        item.resolve()
        for item in extension_root.iterdir()
        if item.is_dir() and not item.name.startswith('.')
    ] if extension_root.is_dir() else []
    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    retained: set[Path] = set(candidates[: max(0, int(keep))])
    retained.update(active_paths)
    removed: list[str] = []
    for candidate in candidates:
        if candidate in retained:
            continue
        _safe_remove_tree(candidate, required_parent=extension_root)
        removed.append(str(candidate))
    return {
        'extensionId': row.id,
        'kept': [str(item) for item in sorted(retained)],
        'removed': removed,
    }


def select_extension_rows(
    *,
    repo_root: Path,
    extension_id: str | None = None,
    include_all: bool = False,
    include_enabled: bool = False,
    config_path: str | Path | None = None,
) -> tuple[ManagedExtensionRow, ...]:
    """按 extension_id、include_all、include_enabled 或默认 profile 推断要处理的受管扩展。"""
    normalized_id = str(extension_id or '').strip()
    selected_count = sum(1 for selected in (bool(normalized_id), bool(include_all), bool(include_enabled)) if selected)
    if selected_count > 1:
        raise ExtensionEnvError('--extension、--all 与 --enabled 只能选择一个')
    rows = managed_explicit_extensions(repo_root)
    if include_all:
        return rows
    if include_enabled:
        if config_path in (None, ''):
            raise ExtensionEnvError('--enabled 需要 active control-plane config')
        from openclaw.control_plane.registry_loader.config import load_registry_service_context

        context = load_registry_service_context(Path(config_path).resolve())
        enabled_ids = [
            str(item).strip()
            for item in (context.get('enabledExtensionIds') or [])
            if str(item).strip()
        ]
        row_by_id = {row.id: row for row in rows}
        return tuple(row_by_id[extension_id] for extension_id in enabled_ids if extension_id in row_by_id)
    if normalized_id:
        matches = tuple(row for row in rows if row.id == normalized_id)
        if not matches:
            raise ExtensionEnvError(f'未知 managed explicit extension：{normalized_id}')
        return matches
    row = managed_extension_for_config_path(config_path, start_path=repo_root)
    if row is not None:
        return (row,)
    raise ExtensionEnvError('请通过 --extension <id> 或 --all 指定扩展；也可以使用扩展默认 profile 推断')


def managed_extension_for_runtime_agent_ref(agent_ref: str, *, repo_root: Path) -> ManagedExtensionRow | None:
    """根据 agent ref 推断其所属受管扩展；base 或未知引用返回 None。"""
    normalized = str(agent_ref or '').strip()
    if not normalized:
        return None
    if ':' in normalized:
        owner, local_ref = normalized.split(':', 1)
        owner = owner.strip()
        if not owner or owner == 'base':
            return None
        for row in managed_explicit_extensions(repo_root):
            if row.id == owner:
                return row
        return None
    return managed_extension_for_agent_ref(normalized, start_path=repo_root)


def extension_env_for_agent_runtime(
    agent_ref: str,
    *,
    repo_root: Path,
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> PreparedExtensionEnv | None:
    """为 agent runtime 查找已准备好的扩展 venv；未就绪时给出唯一 ensure 命令。"""
    row = managed_extension_for_runtime_agent_ref(agent_ref, repo_root=repo_root)
    if row is None:
        return None
    status = extension_env_status(row, repo_root=repo_root, config_path=config_path, env=env)
    if not status.ok or status.env_path is None or status.python_executable is None or status.manifest is None:
        ensure_command = (
            f'bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane runtime '
            f'--control-plane-profile {row.id} extension-env ensure --extension {row.id} --offline --json'
        )
        detail = '; '.join(status.issues) or 'unknown'
        raise ExtensionEnvError(
            f'agent {agent_ref} 属于扩展 {row.id}，但扩展 venv 未准备或已失效：{detail}；'
            f'请先执行：{ensure_command}'
        )
    return PreparedExtensionEnv(
        row=row,
        env_path=status.env_path,
        python_executable=status.python_executable,
        manifest=status.manifest,
    )


def build_extension_subprocess_env(
    prepared: PreparedExtensionEnv,
    *,
    repo_root: Path,
    base_env: Mapping[str, str] | None = None,
    config_path: str | Path | None = None,
) -> dict[str, str]:
    """构造扩展子进程环境，清理宿主机 Python 变量并注入扩展 venv、PYTHONPATH 与控制面变量。"""
    source_env = dict(os.environ if base_env is None else base_env)
    env = _extension_subprocess_source_env(source_env, repo_root=repo_root, config_path=config_path)
    for key in _PYTHON_ENV_CLEAN_KEYS:
        _remove_env_key_casefold(env, key)
    env.update(bootstrap_env_defaults(repo_root))
    path_entries = [*prepared.row.python_roots, *bootstrap_path_entries(repo_root, config_path=None)]
    seen: set[str] = set()
    normalized: list[str] = []
    for entry in path_entries:
        marker = str(Path(entry).resolve())
        if marker in seen:
            continue
        seen.add(marker)
        normalized.append(marker)
    env['PYTHONPATH'] = os.pathsep.join(normalized)
    env['PYTHONNOUSERSITE'] = '1'
    env['VIRTUAL_ENV'] = str(prepared.env_path)
    venv_bin_dir = prepared.python_executable.parent
    previous_path = _env_value_casefold(env, 'PATH')
    _remove_env_key_casefold(env, 'PATH')
    env['PATH'] = str(venv_bin_dir) + (os.pathsep + previous_path if previous_path else '')
    env['OPENCLAW_EXTENSION_ID'] = prepared.row.id
    env['OPENCLAW_EXTENSION_ENV_PATH'] = str(prepared.env_path)
    return env
