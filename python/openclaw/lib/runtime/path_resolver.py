#!/usr/bin/env python3
"""规范运行态路径解析器的实现。"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_INTERNAL_VIEWS = ("host", "gateway", "scheduler")


def _json_object(value: Any) -> dict[str, Any]:
    """把任意值转为对象视图。"""
    return value if isinstance(value, dict) else {}


def _json_array(value: Any) -> list[Any]:
    """把任意值转为数组视图。"""
    return value if isinstance(value, list) else []


def _json_object_map(value: Any) -> dict[str, dict[str, Any]]:
    """把对象映射规范化为对象字典。"""
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items() if isinstance(item, dict)}


def _root_placeholder_values(repo_root: Path) -> dict[str, str]:
    """构建路径模板可用的根占位符值。"""
    values = {'__HOST_STATE_ROOT_DEFAULT__': '<current-host-state-root>'}
    try:
        from openclaw.lib.repo.install_defaults import read_repo_contract_json

        payload = read_repo_contract_json('governance.install_defaults', root_dir=repo_root)
    except (ImportError, OSError, ValueError, KeyError, json.JSONDecodeError, UnicodeDecodeError):
        return values
    defaults = payload.get('defaults') if isinstance(payload, dict) else None
    if isinstance(defaults, dict):
        host_state_root = str(defaults.get('host_state_root') or '').strip()
        if host_state_root:
            values['__HOST_STATE_ROOT_DEFAULT__'] = host_state_root
    return values


def normalize_view(view: str) -> str:
    """规范化 runtime view 名称。"""
    normalized = str(view or 'host').strip().lower()
    return normalized


def _snake_to_upper(name: str) -> str:
    """把 snake_case 转成大写环境变量风格。"""
    return name.upper()


def _state_root_override_candidates(view: str, state_root_env_name: Optional[str]) -> list[str]:
    """生成 state root 的环境变量覆盖候选。"""
    names: list[str] = []
    if state_root_env_name:
        names.append(state_root_env_name)
    if view == 'host' and 'OPENCLAW_STATE_DIR' not in names:
        names.append('OPENCLAW_STATE_DIR')
    return names


def _derive_from_state_root(default_path: Optional[str], default_state_root: Optional[str], overridden_state_root: Optional[str]) -> Optional[str]:
    """从 state root 推导派生路径。"""
    if not default_path or not default_state_root or not overridden_state_root:
        return None
    default = Path(default_path)
    state_root = Path(default_state_root)
    override = Path(overridden_state_root)
    if default == state_root:
        return str(override)
    try:
        relative = default.relative_to(state_root)
    except ValueError:
        return None
    return str(override / relative)


@dataclass
class PathResolver:
    """运行态路径真源解析器。"""
    repo_root: Path
    manifest: Dict[str, Any]
    config_path: Path | None = None

    @classmethod
    def from_repo_root(cls, repo_root: Path, *, config_path: Path | None = None) -> 'PathResolver':
        """从仓库根目录加载路径 surface 并创建解析器。"""
        from openclaw.control_plane.surfaces import load_runtime_paths_manifest
        from openclaw.lib.repo.layout import (
            resolve_default_runtime_control_plane_service_config_path,
            resolve_runtime_paths_manifest_path,
        )

        resolved_config_path = (
            config_path.resolve()
            if isinstance(config_path, Path)
            else resolve_default_runtime_control_plane_service_config_path(repo_root)
        )
        data = load_runtime_paths_manifest(resolve_runtime_paths_manifest_path(repo_root), config_path=resolved_config_path)
        return cls(repo_root=repo_root, manifest=data, config_path=resolved_config_path)

    @property
    def view_contract(self) -> Dict[str, Any]:
        """返回 view 合同定义。"""
        return dict(_json_object(self.manifest.get('view_contract')))

    @property
    def internal_views(self) -> tuple[str, ...]:
        """返回 internal 视图集合。"""
        configured = tuple(str(item) for item in _json_array(self.view_contract.get('internal_view_keys')) if str(item).strip())
        return configured or DEFAULT_INTERNAL_VIEWS

    @property
    def public_view_names(self) -> Dict[str, str]:
        """返回公开 view 名称集合。"""
        configured = {str(key): str(value) for key, value in _json_object(self.view_contract.get('public_view_names')).items() if str(value).strip()}
        defaults = {view: view for view in self.internal_views}
        defaults.update(configured)
        return defaults

    @property
    def gateway_public_label(self) -> str:
        """返回 gateway 对外展示标签。"""
        return str(self.public_view_names.get('gateway') or 'gateway')

    def normalize_view(self, view: str) -> str:
        """规范化传入的 view 名称。"""
        return normalize_view(view)

    @property
    def roots(self) -> Dict[str, str]:
        """返回逻辑根定义。"""
        raw = {str(key): str(value) for key, value in _json_object(self.manifest.get('roots')).items()}
        placeholders = _root_placeholder_values(self.repo_root)
        resolved: Dict[str, str] = {}

        def resolve_root(name: str) -> str:
            """解析 根目录。"""
            if name in resolved:
                return resolved[name]
            value = str(raw[name])
            for token, replacement in placeholders.items():
                value = value.replace(token, replacement)
            replacements = {key: resolve_root(key) for key in raw if key != name and ('{' + key + '}') in value}
            rendered = value.format(**replacements) if replacements else value
            resolved[name] = rendered
            return rendered

        for key in raw:
            resolve_root(key)
        return resolved

    @property
    def entries(self) -> Dict[str, Dict[str, Any]]:
        """返回路径条目定义。"""
        return _json_object_map(self.manifest.get('entries'))

    @property
    def logical_groups(self) -> Dict[str, Dict[str, Any]]:
        """返回逻辑分组定义。"""
        return _json_object_map(self.manifest.get('logical_groups'))

    @property
    def gateway_exec_approvals_spec(self) -> Dict[str, Any]:
        """返回 gateway exec approvals 规格。"""
        return dict(_json_object(self.manifest.get('gateway_exec_approvals')))

    def render_value(self, template: Optional[str]) -> Optional[str]:
        """渲染单个模板值。"""
        if template is None:
            return None
        return str(template).format(**self.roots)

    def resolve_entry(self, entry_id: str) -> Dict[str, Any]:
        """解析单个路径条目。"""
        entry = self.entries[entry_id]
        entry_paths = _json_object(entry.get('paths'))
        paths = {
            view: self.render_value(str(entry_paths.get(view))) if entry_paths.get(view) is not None else None
            for view in self.internal_views
        }
        resolved = dict(entry)
        resolved['id'] = entry_id
        resolved['paths'] = paths
        resolved['env_names'] = {view: self.env_name(entry_id, view) for view in self.internal_views if paths.get(view) is not None}
        logical_group = str(entry.get('logical_group') or '').strip()
        if logical_group:
            resolved['logical_group'] = logical_group
            group_spec = self.logical_groups.get(logical_group)
            if isinstance(group_spec, dict):
                resolved['logical_group_label'] = str(group_spec.get('label') or logical_group)
        return resolved

    def resolve_all(self) -> Dict[str, Dict[str, Any]]:
        """解析全部路径条目。"""
        return {entry_id: self.resolve_entry(entry_id) for entry_id in self.entries}

    def env_name(self, entry_id: str, view: str) -> str:
        """生成路径条目的环境变量名。"""
        view = self.normalize_view(view)
        entry = self.entries[entry_id]
        explicit = str(_json_object(entry.get('env_names')).get(view) or '').strip()
        if explicit:
            return explicit
        base = _snake_to_upper(entry_id)
        if view == 'host':
            return f'HOST_{base}'
        if entry_id == 'state_root' and view in {'gateway', 'scheduler'}:
            return 'OPENCLAW_STATE_DIR'
        return base

    def resolve_path(self, entry_id: str, view: str = 'gateway', env: Optional[dict[str, str]] = None) -> str:
        """解析指定路径名。"""
        view = self.normalize_view(view)
        if view not in self.internal_views:
            raise KeyError(f'unknown view: {view}')
        resolved = self.resolve_entry(entry_id)
        default_path = resolved['paths'].get(view)
        if default_path is None:
            raise KeyError(f'entry {entry_id} has no {view} path')
        env_map = dict(env or {})
        env_name = resolved['env_names'].get(view)
        if env_name and env_map.get(env_name):
            return env_map[env_name]
        state_root = self.resolve_entry('state_root')
        state_root_env = state_root['env_names'].get(view)
        state_root_override = None
        for candidate in _state_root_override_candidates(view, state_root_env):
            if env_map.get(candidate):
                state_root_override = env_map[candidate]
                break
        derived = _derive_from_state_root(default_path, state_root['paths'].get(view), state_root_override)
        if derived:
            return derived
        return default_path

    def workspace_placeholders(self) -> Dict[str, str]:
        """返回 workspace 占位符字典。"""
        result: Dict[str, str] = {}
        for entry_id in self.entries:
            if not entry_id.startswith('workspace_'):
                continue
            placeholder = f'__{entry_id.upper()}__'
            gateway_path = self.resolve_entry(entry_id)['paths'].get('gateway')
            if gateway_path:
                result[placeholder] = gateway_path
        return result

    def build_index(self) -> Dict[str, Any]:
        """构建路径索引。"""
        entries: list[Dict[str, Any]] = []
        for entry_id, entry in self.resolve_all().items():
            rows = {
                'id': entry_id,
                'kind': str(entry.get('kind') or '').strip(),
                'category': str(entry.get('category') or '').strip(),
                'logical_group': str(entry.get('logical_group') or '').strip(),
                'logical_group_label': str(entry.get('logical_group_label') or entry.get('logical_group') or '').strip(),
                'owner': entry.get('owner'),
                'paths': dict(entry.get('paths') or {}),
                'env_names': dict(entry.get('env_names') or {}),
            }
            entries.append(rows)
        logical_groups = []
        for group_id, group_spec in self.logical_groups.items():
            logical_groups.append({
                'id': group_id,
                'label': str(group_spec.get('label') or group_id),
                'description': str(group_spec.get('description') or '').strip(),
            })
        return {
            'module': str(self.manifest.get('module') or 'runtime_paths'),
            'description': str(self.manifest.get('description') or '').strip(),
            'views': [
                {'id': view, 'public_name': self.public_view_names.get(view, view)}
                for view in self.internal_views
            ],
            'logical_groups': logical_groups,
            'entries': entries,
        }

    def absolute_host_path(self, entry_id: str) -> Path:
        """解析 view 下的宿主机绝对路径。"""
        rel = self.resolve_entry(entry_id)['paths']['host']
        if rel is None:
            raise KeyError(f'entry {entry_id} has no host path')
        path = Path(rel)
        return path if path.is_absolute() else (self.repo_root / path).resolve()

    def _repo_host_output_paths(self, spec: Dict[str, Any], label: str) -> Dict[str, Path]:
        """返回仓库内输出路径集合。"""
        repo_output = str(spec.get('repo_output') or '').strip()
        host_output = str(spec.get('host_output') or '').strip()
        if not repo_output or not host_output:
            raise KeyError(f'manifest.{label}.repo_output / host_output is required')
        repo_path = Path(repo_output)
        if not repo_path.is_absolute():
            repo_path = (self.repo_root / repo_path).resolve()
        rendered_host_output = self.render_value(str(host_output)) or str(host_output)
        host_path = Path(rendered_host_output)
        if not host_path.is_absolute():
            host_path = (self.repo_root / host_path).resolve()
        return {'repo_output': repo_path, 'host_output': host_path}

    def gateway_exec_approvals_paths(self) -> Dict[str, Path]:
        """解析 gateway exec approvals 相关路径。"""
        return self._repo_host_output_paths(self.gateway_exec_approvals_spec, 'gateway_exec_approvals')

    def read_gateway_exec_approvals_source(self) -> str:
        """读取 gateway exec approvals 真源文件。"""
        from openclaw.control_plane.surfaces import load_gateway_exec_approvals

        return json.dumps(load_gateway_exec_approvals(config_path=self.config_path), ensure_ascii=False, indent=2) + '\n'


__all__ = ['DEFAULT_INTERNAL_VIEWS', 'PathResolver', 'normalize_view']
