#!/usr/bin/env python3
"""控制面 manifest 的结构化模型，负责把 JSON 真源转换为稳定 Python 对象。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openclaw.control_plane.manifest_fields import (
    DISPATCH_PROVIDER_REGISTRY_PATHS_KEY,
    DISPATCH_TARGET_REGISTRY_PATHS_KEY,
    GOVERNANCE_SURFACE_KEYS,
    GOVERNANCE_SURFACES_FIELD,
    RUNTIME_ADAPTER_REGISTRY_PATHS_KEY,
    SURFACE_FRAGMENT_KEYS,
    SURFACE_FRAGMENTS_FIELD,
    filter_path_mapping,
)


def _text(value: Any) -> str:
    return str(value or '').strip()


def _text_tuple(values: Any) -> tuple[str, ...]:
    if not isinstance(values, list):
        return ()
    return tuple(text for item in values if (text := _text(item)))


def _path_tuple(values: Any) -> tuple[Path, ...]:
    if not isinstance(values, list):
        return ()
    return tuple(item for item in values if isinstance(item, Path))


_SCHEMA_KEYS = (
    'jobsSchema',
    'modelsSchema',
    'targetsSchema',
    'agentsSchema',
    'implementationsSchema',
    'agentGroupsSchema',
    'agentModulesSchema',
    'runtimeAdaptersSchema',
    'skillSetsSchema',
    'permissionPoliciesSchema',
    'toolsetsSchema',
)


@dataclass(frozen=True)
class ControlPlaneRegistryConfigModel:
    """控制面 registry 配置段，记录 jobs/models/targets 等目录入口及保留扩展字段。"""

    jobs_dir: str = ''
    models_dir: str = ''
    targets_dir: str = ''
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Any) -> 'ControlPlaneRegistryConfigModel':
        """从未校验 JSON payload 读取 registry 配置，缺失字段按空值处理。"""
        data = payload if isinstance(payload, dict) else {}
        return cls(
            jobs_dir=_text(data.get('jobsDir')),
            models_dir=_text(data.get('modelsDir')),
            targets_dir=_text(data.get('targetsDir')),
            extra={key: value for key, value in data.items() if key not in {'jobsDir', 'modelsDir', 'targetsDir'}},
        )

    def to_payload(self) -> dict[str, Any]:
        """导出回 JSON 对象，保留未知字段以避免重写时丢失兼容信息。"""
        payload = dict(self.extra)
        if self.jobs_dir or 'jobsDir' in payload:
            payload['jobsDir'] = self.jobs_dir
        if self.models_dir or 'modelsDir' in payload:
            payload['modelsDir'] = self.models_dir
        if self.targets_dir or 'targetsDir' in payload:
            payload['targetsDir'] = self.targets_dir
        return payload


@dataclass(frozen=True)
class ControlPlaneExtensionsConfigModel:
    """控制面 extensions 配置段，描述扩展 manifest 目录与显式启用扩展集合。"""

    manifests_dirs: tuple[str, ...] = ()
    enabled_extension_ids: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Any) -> 'ControlPlaneExtensionsConfigModel':
        """从 JSON payload 读取扩展目录和启用列表，非数组输入按空集合处理。"""
        data = payload if isinstance(payload, dict) else {}
        manifests_dirs = list(_text_tuple(data.get('manifestsDirs')))
        return cls(
            manifests_dirs=tuple(manifests_dirs),
            enabled_extension_ids=_text_tuple(data.get('enabledExtensionIds')),
            extra={key: value for key, value in data.items() if key not in {'manifestsDirs', 'enabledExtensionIds'}},
        )

    def to_payload(self) -> dict[str, Any]:
        """导出可写回 service config 的 extensions 对象。"""
        payload = dict(self.extra)
        if self.manifests_dirs or 'manifestsDirs' in payload:
            payload['manifestsDirs'] = list(self.manifests_dirs)
        if self.enabled_extension_ids or 'enabledExtensionIds' in payload:
            payload['enabledExtensionIds'] = list(self.enabled_extension_ids)
        return payload


@dataclass(frozen=True)
class ControlPlaneSchemasConfigModel:
    """控制面 schema 配置段，记录 jobs/models/targets 等基础 schema 路径。"""

    jobs_schema: str = ''
    models_schema: str = ''
    targets_schema: str = ''
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Any) -> 'ControlPlaneSchemasConfigModel':
        """从 JSON payload 读取 schema 路径，缺失项保持空字符串。"""
        data = payload if isinstance(payload, dict) else {}
        return cls(
            jobs_schema=_text(data.get('jobsSchema')),
            models_schema=_text(data.get('modelsSchema')),
            targets_schema=_text(data.get('targetsSchema')),
            extra={key: value for key, value in data.items() if key not in {'jobsSchema', 'modelsSchema', 'targetsSchema'}},
        )

    def to_payload(self) -> dict[str, Any]:
        """导出 schema 配置对象，保留当前实现尚未理解的扩展字段。"""
        payload = dict(self.extra)
        if self.jobs_schema or 'jobsSchema' in payload:
            payload['jobsSchema'] = self.jobs_schema
        if self.models_schema or 'modelsSchema' in payload:
            payload['modelsSchema'] = self.models_schema
        if self.targets_schema or 'targetsSchema' in payload:
            payload['targetsSchema'] = self.targets_schema
        return payload


@dataclass(frozen=True)
class ControlPlaneServiceConfigModel:
    """完整 control plane（控制面）service config 的结构化视图。"""

    registry: ControlPlaneRegistryConfigModel
    extensions: ControlPlaneExtensionsConfigModel
    schemas: ControlPlaneSchemasConfigModel
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Any) -> 'ControlPlaneServiceConfigModel':
        """把 service config JSON 对象拆成 registry、extensions 与 schemas 三个子模型。"""
        data = payload if isinstance(payload, dict) else {}
        return cls(
            registry=ControlPlaneRegistryConfigModel.from_payload(data.get('registry')),
            extensions=ControlPlaneExtensionsConfigModel.from_payload(data.get('extensions')),
            schemas=ControlPlaneSchemasConfigModel.from_payload(data.get('schemas')),
            extra={key: value for key, value in data.items() if key not in {'registry', 'extensions', 'schemas'}},
        )

    def to_payload(self) -> dict[str, Any]:
        """组合子模型并导出为可序列化 JSON 对象。"""
        payload = dict(self.extra)
        registry_payload = self.registry.to_payload()
        extensions_payload = self.extensions.to_payload()
        schemas_payload = self.schemas.to_payload()
        if registry_payload or 'registry' in payload:
            payload['registry'] = registry_payload
        if extensions_payload or 'extensions' in payload:
            payload['extensions'] = extensions_payload
        if schemas_payload or 'schemas' in payload:
            payload['schemas'] = schemas_payload
        return payload


@dataclass(frozen=True)
class WorkspaceTemplateBindingModel:
    """workspace 模板到目标对象 entry 的绑定关系。"""

    template: str
    target_entry: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any], *, label: str) -> 'WorkspaceTemplateBindingModel':
        """读取单条模板绑定；缺少 template 或 target_entry 时抛出可读错误。"""
        template = _text(payload.get('template'))
        target_entry = _text(payload.get('target_entry'))
        if not template:
            raise ValueError(f'{label} 缺少 template')
        if not target_entry:
            raise ValueError(f'{label} {template} 缺少 target_entry')
        return cls(template=template, target_entry=target_entry)

    def to_payload(self) -> dict[str, str]:
        """导出模板绑定，供 workspace manifest 重渲染使用。"""
        return {'template': self.template, 'target_entry': self.target_entry}


@dataclass(frozen=True)
class WorkspaceTemplatesManifestModel:
    """workspace templates manifest 的结构化表示，包含控制面模板与需清理目录。"""

    control_plane: tuple[WorkspaceTemplateBindingModel, ...]
    stale_dirs: tuple[str, ...]
    module: str = ''
    version: str = ''
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Any, *, label: str) -> 'WorkspaceTemplatesManifestModel':
        """从 manifest JSON 读取模板绑定，忽略非对象行并保留未知字段。"""
        data = payload if isinstance(payload, dict) else {}
        return cls(
            control_plane=tuple(
                WorkspaceTemplateBindingModel.from_payload(row, label=label)
                for row in (data.get('control_plane') or [])
                if isinstance(row, dict)
            ),
            stale_dirs=tuple(text for row in (data.get('stale_dirs') or []) if (text := _text(row))),
            module=_text(data.get('module')),
            version=_text(data.get('version')),
            extra={key: value for key, value in data.items() if key not in {'control_plane', 'stale_dirs', 'module', 'version'}},
        )

    def to_payload(self) -> dict[str, Any]:
        """导出 workspace templates manifest 的 JSON 对象。"""
        payload = dict(self.extra)
        payload['control_plane'] = [row.to_payload() for row in self.control_plane]
        payload['stale_dirs'] = list(self.stale_dirs)
        if self.module:
            payload['module'] = self.module
        if self.version:
            payload['version'] = self.version
        return payload


@dataclass(frozen=True)
class ExtensionRegistryConfigModel:
    """扩展贡献的 registry 路径集合，路径已在加载阶段解析为 Path。"""

    jobs_dirs: tuple[Path, ...] = ()
    models_dirs: tuple[Path, ...] = ()
    targets_dirs: tuple[Path, ...] = ()
    agent_groups_dirs: tuple[Path, ...] = ()
    agent_modules_dirs: tuple[Path, ...] = ()
    runtime_adapter_registry_paths: tuple[Path, ...] = ()
    dispatch_target_registry_paths: tuple[Path, ...] = ()
    dispatch_provider_registry_paths: tuple[Path, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        """导出扩展 registry 路径对象，字段名保持 manifest 合同原样。"""
        return {
            'jobsDirs': list(self.jobs_dirs),
            'modelsDirs': list(self.models_dirs),
            'targetsDirs': list(self.targets_dirs),
            'agentGroupsDirs': list(self.agent_groups_dirs),
            'agentModulesDirs': list(self.agent_modules_dirs),
            RUNTIME_ADAPTER_REGISTRY_PATHS_KEY: list(self.runtime_adapter_registry_paths),
            DISPATCH_TARGET_REGISTRY_PATHS_KEY: list(self.dispatch_target_registry_paths),
            DISPATCH_PROVIDER_REGISTRY_PATHS_KEY: list(self.dispatch_provider_registry_paths),
        }


@dataclass(frozen=True)
class ExtensionJobRunnerModel:
    """扩展声明的 job runner 入口，描述模块、callable 与 agent binding 支持。"""

    id: str
    title: str
    module: str
    callable: str
    handles_agent_bindings: bool

    def to_payload(self) -> dict[str, Any]:
        """导出 job runner manifest 行。"""
        return {
            'id': self.id,
            'title': self.title,
            'module': self.module,
            'callable': self.callable,
            'handlesAgentBindings': self.handles_agent_bindings,
        }


@dataclass(frozen=True)
class ExtensionCliCommandModel:
    """扩展贡献的 CLI 命令到 Python 模块的绑定。"""

    command: str
    module: str

    def to_payload(self) -> dict[str, str]:
        """导出 CLI command manifest 行。"""
        return {'command': self.command, 'module': self.module}


@dataclass(frozen=True)
class ExtensionInternalApiRouteModel:
    """扩展贡献的 internal-api 路由定义。"""

    id: str
    path: str
    module: str
    callable: str
    auth_required: bool

    def to_payload(self) -> dict[str, Any]:
        """导出 internal-api route manifest 行。"""
        return {
            'id': self.id,
            'path': self.path,
            'module': self.module,
            'callable': self.callable,
            'authRequired': self.auth_required,
        }


@dataclass(frozen=True)
class ExtensionReadyCheckModel:
    """扩展 runtime ready check（就绪检查）定义。"""

    id: str
    module: str
    callable: str
    blocking: bool

    def to_payload(self) -> dict[str, Any]:
        """导出 ready check manifest 行。"""
        return {
            'id': self.id,
            'module': self.module,
            'callable': self.callable,
            'blocking': self.blocking,
        }


@dataclass(frozen=True)
class ExtensionDependencyModel:
    """扩展之间的依赖关系声明，支持可选依赖与版本约束文本。"""

    id: str
    version: str = ''
    optional: bool = False

    def to_payload(self) -> dict[str, Any]:
        """导出 dependency manifest 行，空版本和默认 optional 不写出。"""
        payload: dict[str, Any] = {'id': self.id}
        if self.version:
            payload['version'] = self.version
        if self.optional:
            payload['optional'] = True
        return payload


@dataclass(frozen=True)
class ExtensionMigrationModel:
    """扩展迁移任务声明，描述版本范围与可调用入口。"""

    id: str
    from_version: str = ''
    to_version: str = ''
    callable: str = ''

    def to_payload(self) -> dict[str, Any]:
        """导出 migration manifest 行，空字段不写出。"""
        payload: dict[str, Any] = {'id': self.id}
        if self.from_version:
            payload['fromVersion'] = self.from_version
        if self.to_version:
            payload['toVersion'] = self.to_version
        if self.callable:
            payload['callable'] = self.callable
        return payload


@dataclass(frozen=True)
class ExtensionManifestModel:
    """标准化后的扩展 manifest，汇总扩展元信息、registry、schema 与运行入口。"""

    id: str
    title: str
    version: str
    compat: dict[str, Any]
    dependencies: tuple[ExtensionDependencyModel, ...]
    migrations: tuple[ExtensionMigrationModel, ...]
    source_path: str
    base_dir: str
    registry: ExtensionRegistryConfigModel
    schemas: dict[str, Path]
    surface_fragments: dict[str, Path]
    governance_surfaces: dict[str, Path]
    job_runners: tuple[ExtensionJobRunnerModel, ...]
    cli_commands: tuple[ExtensionCliCommandModel, ...]
    internal_api_routes: tuple[ExtensionInternalApiRouteModel, ...]
    ready_checks: tuple[ExtensionReadyCheckModel, ...]

    @classmethod
    def from_normalized_payload(cls, payload: dict[str, Any]) -> 'ExtensionManifestModel':
        """从已标准化 payload 构造扩展模型；输入中的路径字段应已解析为 Path。"""
        registry_payload = payload.get('registry') if isinstance(payload.get('registry'), dict) else {}
        return cls(
            id=_text(payload.get('id')),
            title=_text(payload.get('title')),
            version=_text(payload.get('version')),
            compat=dict(payload.get('compat')) if isinstance(payload.get('compat'), dict) else {},
            dependencies=tuple(
                ExtensionDependencyModel(
                    id=_text(row.get('id')),
                    version=_text(row.get('version')),
                    optional=bool(row.get('optional', False)),
                )
                for row in (payload.get('dependencies') or [])
                if isinstance(row, dict)
            ),
            migrations=tuple(
                ExtensionMigrationModel(
                    id=_text(row.get('id')),
                    from_version=_text(row.get('fromVersion')),
                    to_version=_text(row.get('toVersion')),
                    callable=_text(row.get('callable')),
                )
                for row in (payload.get('migrations') or [])
                if isinstance(row, dict)
            ),
            source_path=_text(payload.get('sourcePath')),
            base_dir=_text(payload.get('baseDir')),
            registry=ExtensionRegistryConfigModel(
                jobs_dirs=_path_tuple(registry_payload.get('jobsDirs')),
                models_dirs=_path_tuple(registry_payload.get('modelsDirs')),
                targets_dirs=_path_tuple(registry_payload.get('targetsDirs')),
                agent_groups_dirs=_path_tuple(registry_payload.get('agentGroupsDirs')),
                agent_modules_dirs=_path_tuple(registry_payload.get('agentModulesDirs')),
                runtime_adapter_registry_paths=_path_tuple(registry_payload.get(RUNTIME_ADAPTER_REGISTRY_PATHS_KEY)),
                dispatch_target_registry_paths=_path_tuple(registry_payload.get(DISPATCH_TARGET_REGISTRY_PATHS_KEY)),
                dispatch_provider_registry_paths=_path_tuple(registry_payload.get(DISPATCH_PROVIDER_REGISTRY_PATHS_KEY)),
            ),
            schemas=filter_path_mapping(payload.get('schemas'), keys=_SCHEMA_KEYS),
            surface_fragments=filter_path_mapping(payload.get(SURFACE_FRAGMENTS_FIELD), keys=SURFACE_FRAGMENT_KEYS),
            governance_surfaces=filter_path_mapping(payload.get(GOVERNANCE_SURFACES_FIELD), keys=GOVERNANCE_SURFACE_KEYS),
            job_runners=tuple(
                ExtensionJobRunnerModel(
                    id=_text(row.get('id')),
                    title=_text(row.get('title')),
                    module=_text(row.get('module')),
                    callable=_text(row.get('callable')),
                    handles_agent_bindings=bool(row.get('handlesAgentBindings')),
                )
                for row in (payload.get('jobRunners') or [])
                if isinstance(row, dict)
            ),
            cli_commands=tuple(
                ExtensionCliCommandModel(command=_text(row.get('command')), module=_text(row.get('module')))
                for row in (payload.get('cliCommands') or [])
                if isinstance(row, dict)
            ),
            internal_api_routes=tuple(
                ExtensionInternalApiRouteModel(
                    id=_text(row.get('id')),
                    path=_text(row.get('path')),
                    module=_text(row.get('module')),
                    callable=_text(row.get('callable')),
                    auth_required=bool(row.get('authRequired', True)),
                )
                for row in (payload.get('internalApiRoutes') or [])
                if isinstance(row, dict)
            ),
            ready_checks=tuple(
                ExtensionReadyCheckModel(
                    id=_text(row.get('id')),
                    module=_text(row.get('module')),
                    callable=_text(row.get('callable')),
                    blocking=bool(row.get('blocking', True)),
                )
                for row in (payload.get('readyChecks') or [])
                if isinstance(row, dict)
            ),
        )

    def to_payload(self) -> dict[str, Any]:
        """导出扩展 manifest JSON 对象，供锁文件、诊断和文档渲染复用。"""
        return {
            'id': self.id,
            'title': self.title,
            'version': self.version,
            'compat': dict(self.compat),
            'dependencies': [row.to_payload() for row in self.dependencies],
            'migrations': [row.to_payload() for row in self.migrations],
            'sourcePath': self.source_path,
            'baseDir': self.base_dir,
            'registry': self.registry.to_payload(),
            'schemas': dict(self.schemas),
            SURFACE_FRAGMENTS_FIELD: dict(self.surface_fragments),
            GOVERNANCE_SURFACES_FIELD: dict(self.governance_surfaces),
            'jobRunners': [row.to_payload() for row in self.job_runners],
            'cliCommands': [row.to_payload() for row in self.cli_commands],
            'internalApiRoutes': [row.to_payload() for row in self.internal_api_routes],
            'readyChecks': [row.to_payload() for row in self.ready_checks],
        }
