#!/usr/bin/env python3
"""模块 scaffold 的请求标准化、布局、payload 与写入计划构建器。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from openclaw.control_plane.modules.scaffold_models import (
    PlannedWrite,
    ScaffoldLayout,
    ScaffoldPayloads,
    ScaffoldRequest,
    display_repo_posix_path,
    normalize_owner_domain,
    python_main_template,
    relative_source_path,
    test_template_python,
    validate_module_ref,
    write_json,
    write_text,
)
from openclaw.control_plane.modules.scaffold_support import (
    DEFAULT_CHANGE_CONTROL_DOC_TARGETS,
    agents_md_template as _agents_md_template,
    build_module_payload,
    constraints_readme_template,
    contracts_readme_template,
    docs_readme_template,
    managed_extension_launcher_template as _managed_extension_launcher_template,
    module_readme_template as _module_readme_template,
    skills_template as _skills_template,
    tests_readme_template,
)
from openclaw.control_plane.registry import CliError, load_registry
from openclaw.control_plane.runtime.adapter_registry import RuntimeAdapterSpec, runtime_adapter_specs
from openclaw.lib.repo.layout import (
    CONTROL_PLANE_PROFILES_REL_DIR,
    resolve_repo_root,
    resolve_selected_control_plane_service_config_path,
)
from openclaw.lib.repo.managed_extensions import ManagedExtensionError, managed_extension_layout_for_config_path
from openclaw.lib.repo.path_contracts import extension_anchored_path, is_repo_anchored_path

RuntimeSpecsBuilder = Callable[[Path, Path | None], dict[str, RuntimeAdapterSpec]]


def resolve_config_path(repo_root: Path, config_path: Path | None = None) -> Path:
    try:
        return resolve_selected_control_plane_service_config_path(
            config_path,
            start_path=repo_root,
            default_to_base=False,
        )
    except ValueError as exc:
        raise CliError(str(exc), 2) from exc


def runtime_adapter_specs_for(repo_root: Path, config_path: Path | None = None) -> dict[str, RuntimeAdapterSpec]:
    config_path = resolve_config_path(repo_root, config_path)
    try:
        registry = load_registry(config_path)
    except CliError:
        raise
    except Exception as exc:  # pragma: no cover - defensive wrapper for CLI diagnostics
        # registry 加载异常统一转成 CLI 错误，避免 scaffold 命令暴露内部 traceback。
        raise CliError(str(exc), 2) from exc
    adapters = registry.get('runtimeAdapters') if isinstance(registry.get('runtimeAdapters'), list) else []
    return runtime_adapter_specs({'adapters': adapters})


def normalize_scaffold_request(
    *,
    repo_root: Path | None,
    config_path: Path | None,
    module_ref: str,
    title: str,
    owner_domain: str,
    module_kind: str = 'worker',
    entrypoint_kind: str = 'python_cli',
    runtime_adapter_ref: str = 'python_module',
    executor_kind: str | None = None,
    operation_ref: str = 'run_default',
    description: str = '',
    version: str = 'v1',
    network: bool = False,
    model_required: bool = False,
    external_dispatch: bool = False,
    filesystem_write: list[str] | None = None,
    with_agents_doc: bool = False,
    with_optional_dirs: bool = False,
    force: bool = False,
) -> ScaffoldRequest:
    resolved_repo_root = resolve_repo_root(Path(__file__)) if repo_root is None else Path(repo_root).resolve()
    normalized_module_ref = validate_module_ref(module_ref, label='module_ref')
    normalized_owner_domain = normalize_owner_domain(owner_domain)
    normalized_operation_ref = validate_module_ref(operation_ref, label='operation_ref')
    normalized_module_kind = str(module_kind or '').strip()
    if normalized_module_kind not in {'worker', 'control_check'}:
        raise CliError(f'--module-kind 只允许 worker / control_check：{normalized_module_kind or "<empty>"}', 2)
    normalized_title = str(title or '').strip()
    if not normalized_title:
        raise CliError('--title 不能为空', 2)
    normalized_entrypoint_kind = str(entrypoint_kind or '').strip()
    normalized_runtime_adapter_ref = str(runtime_adapter_ref or '').strip()
    normalized_executor_kind = str(executor_kind or entrypoint_kind).strip()
    normalized_version = str(version or '').strip()
    if not normalized_version or not re.match(r'^v[0-9]+$', normalized_version):
        raise CliError(f'--version 必须匹配 ^v[0-9]+$：{normalized_version or "<empty>"}', 2)
    normalized_description = str(description or '').strip() or f'通过脚手架注册的 {normalized_title}。'
    normalized_filesystem_write = tuple(
        str(item).strip()
        for item in (filesystem_write or [])
        if str(item).strip()
    )
    return ScaffoldRequest(
        repo_root=resolved_repo_root,
        config_path=None if config_path is None else Path(config_path).resolve(),
        module_ref=normalized_module_ref,
        title=normalized_title,
        owner_domain=normalized_owner_domain,
        module_kind=normalized_module_kind,
        entrypoint_kind=normalized_entrypoint_kind,
        runtime_adapter_ref=normalized_runtime_adapter_ref,
        executor_kind=normalized_executor_kind,
        operation_ref=normalized_operation_ref,
        description=normalized_description,
        version=normalized_version,
        network=bool(network),
        model_required=bool(model_required),
        external_dispatch=bool(external_dispatch),
        filesystem_write=normalized_filesystem_write,
        with_agents_doc=bool(with_agents_doc),
        with_optional_dirs=bool(with_optional_dirs),
        force=bool(force),
    )


def resolve_scaffold_layout(
    request: ScaffoldRequest,
    *,
    runtime_specs_builder: RuntimeSpecsBuilder = runtime_adapter_specs_for,
) -> ScaffoldLayout:
    config_path = resolve_config_path(request.repo_root, request.config_path)
    try:
        managed_layout = managed_extension_layout_for_config_path(config_path, start_path=request.repo_root)
    except ManagedExtensionError as exc:
        raise CliError(str(exc), 2) from exc
    if managed_layout is None:
        raise CliError(
            'scaffold-agent-module 只支持受管显式扩展包配置；请传入 '
            f'agent/extensions/<extension-id>/{CONTROL_PLANE_PROFILES_REL_DIR}/<extension-id>.service.json',
            2,
        )

    specs = runtime_specs_builder(request.repo_root, config_path)
    runtime_spec = specs.get(request.runtime_adapter_ref)
    if runtime_spec is None:
        raise CliError(f'未注册的 runtime adapter：{request.runtime_adapter_ref}', 2)
    if request.entrypoint_kind not in set(runtime_spec.supported_entrypoint_kinds):
        raise CliError(
            f'runtime adapter {request.runtime_adapter_ref} 不支持 entrypointKind={request.entrypoint_kind}',
            2,
        )
    if request.executor_kind not in set(runtime_spec.supported_executor_kinds):
        raise CliError(
            f'runtime adapter {request.runtime_adapter_ref} 不支持 executorKind={request.executor_kind}',
            2,
        )

    module_dir = managed_layout.module_root / request.module_ref
    if module_dir.exists() and not request.force:
        raise CliError(f'模块目录已存在：{module_dir.relative_to(request.repo_root)}', 2)
    python_module_dir = managed_layout.python_package_dir / 'modules' / request.module_ref
    if request.runtime_adapter_ref == 'python_module' and python_module_dir.exists() and not request.force:
        raise CliError(f'Python 模块目录已存在：{python_module_dir.relative_to(request.repo_root)}', 2)

    implementation_ref = f'{request.module_ref}_impl'
    launcher_path = module_dir / 'bin' / request.module_ref
    change_control_docs: list[str] = []
    for rel_path in DEFAULT_CHANGE_CONTROL_DOC_TARGETS:
        if is_repo_anchored_path(rel_path):
            change_control_docs.append(rel_path)
        else:
            change_control_docs.append(relative_source_path(module_dir, request.repo_root / rel_path))

    module_dir_display = display_repo_posix_path(module_dir, request.repo_root) + '/'
    module_manifest_display = display_repo_posix_path(module_dir / 'module.json', request.repo_root)
    launcher_display = display_repo_posix_path(launcher_path, request.repo_root)
    group_display = (
        display_repo_posix_path(managed_layout.row.root_dir / 'agent' / 'control_plane' / 'groups', request.repo_root)
        + '/*.json'
    )
    shared_objects_display = (
        display_repo_posix_path(
            managed_layout.python_package_dir / 'domains' / request.owner_domain / 'shared',
            request.repo_root,
        )
        + '/'
    )

    if request.runtime_adapter_ref != 'python_module':
        raise CliError(f'脚手架暂不支持 runtime adapter：{request.runtime_adapter_ref}', 2)

    python_main_path = python_module_dir / 'main.py'
    test_dir = managed_layout.row.root_dir / 'tests' / 'modules' / request.module_ref
    python_module_name = f'{managed_layout.python_package_dir.name}.modules.{request.module_ref}.main'
    implementation_source_display = display_repo_posix_path(python_main_path, request.repo_root)
    logic_source_paths = (
        extension_anchored_path(
            display_repo_posix_path(python_main_path, request.repo_root).removeprefix(
                f'{display_repo_posix_path(managed_layout.row.root_dir, request.repo_root)}/'
            )
        ),
    )
    implementation_runtime = {
        'adapterRef': 'python_module',
        'config': {
            'module': python_module_name,
        },
    }
    smoke_test_text = test_template_python(
        request.module_ref,
        request.operation_ref,
        module_import=python_module_name,
        config_path_repo_rel=display_repo_posix_path(
            managed_layout.row.default_service_config_path,
            request.repo_root,
        ),
    )
    return ScaffoldLayout(
        repo_root=request.repo_root,
        config_path=config_path,
        managed_layout=managed_layout,
        runtime_spec=runtime_spec,
        module_dir=module_dir,
        python_module_dir=python_module_dir,
        implementation_ref=implementation_ref,
        launcher_path=launcher_path,
        test_dir=test_dir,
        change_control_docs=tuple(change_control_docs),
        module_dir_display=module_dir_display,
        module_manifest_display=module_manifest_display,
        launcher_display=launcher_display,
        group_display=group_display,
        shared_objects_display=shared_objects_display,
        python_module_name=python_module_name,
        implementation_source_display=implementation_source_display,
        logic_source_paths=logic_source_paths,
        implementation_runtime=implementation_runtime,
        smoke_test_text=smoke_test_text,
    )


def build_scaffold_payloads(request: ScaffoldRequest, layout: ScaffoldLayout) -> ScaffoldPayloads:
    executor: dict[str, Any] = {'kind': request.executor_kind}
    if request.executor_kind == 'delivery_adapter':
        executor['operation'] = 'send'
    else:
        executor['argv'] = [request.operation_ref]

    module_payload = build_module_payload(
        module_ref=request.module_ref,
        title=request.title,
        owner_domain=request.owner_domain,
        module_kind=request.module_kind,
        entrypoint_kind=request.entrypoint_kind,
        runtime_adapter_ref=request.runtime_adapter_ref,
        implementation_ref=layout.implementation_ref,
        logic_source_paths=list(layout.logic_source_paths),
        activation_extension_ids=[layout.managed_layout.row.id],
        change_control_doc_paths=list(layout.change_control_docs),
        operations={
            request.operation_ref: {
                'summary': f'执行 {request.title} 的默认运行流程。',
                'executor': executor,
                'jobBindings': {},
            }
        },
        contract={
            'inputs': {
                'artifacts': [],
                'runtimeInputs': [],
                'notes': [
                    '补齐本模块实际输入、运行时依赖与校验说明。',
                ],
            },
            'outputs': {
                'artifacts': [],
                'statusSignals': [f'{request.module_ref}_completed'],
                'notes': [
                    '补齐本模块实际输出、工件与状态信号说明。',
                ],
            },
        },
        control_plane_agent={
            'title': f'{request.title} Agent',
            'entrypointKind': request.entrypoint_kind,
            'description': request.description,
            'capabilities': {
                'network': request.network,
                'filesystemWrite': list(request.filesystem_write),
                'modelRequired': request.model_required,
                'externalDispatch': request.external_dispatch,
            },
        },
        control_plane_implementation={
            'title': f'{request.title} Agent 实现',
            'runtime': layout.implementation_runtime,
        },
        version=request.version,
        with_agents_doc=request.with_agents_doc,
    )
    permissions_payload = {
        'schemaVersion': 1,
        'moduleRef': request.module_ref,
        'allow': [],
        'deny': [],
    }
    tools_payload = {
        'schemaVersion': 1,
        'moduleRef': request.module_ref,
        'allowedTools': [
            'run_agent_entrypoint',
            'openclaw.control_plane.run_agent_runtime',
        ],
        'forbiddenTools': [],
        'auditFields': [
            'agent_ref',
            'run_id',
        ],
    }
    return ScaffoldPayloads(
        module_payload=module_payload,
        permissions_payload=permissions_payload,
        tools_payload=tools_payload,
        module_readme_text=_module_readme_template(
            request.module_ref,
            request.title,
            request.owner_domain,
            request.module_kind,
            request.entrypoint_kind,
            request.runtime_adapter_ref,
            request.operation_ref,
            module_dir_display=layout.module_dir_display,
            module_manifest_display=layout.module_manifest_display,
            implementation_source_display=layout.implementation_source_display,
            launcher_display=layout.launcher_display,
            group_display=layout.group_display,
            shared_objects_display=layout.shared_objects_display,
        ),
        skills_text=_skills_template(request.module_ref, request.title),
        launcher_text=_managed_extension_launcher_template(request.module_ref, layout.managed_layout.row.id),
        tests_readme_text=tests_readme_template(request.module_ref),
        agents_text=(
            _agents_md_template(request.module_ref, launcher_display_path=layout.launcher_display)
            if request.with_agents_doc
            else None
        ),
        contracts_text=contracts_readme_template(request.module_ref) if request.with_optional_dirs else None,
        constraints_text=constraints_readme_template(request.module_ref) if request.with_optional_dirs else None,
        docs_text=docs_readme_template(request.module_ref) if request.with_optional_dirs else None,
        python_main_text=python_main_template(
            request.module_ref,
            request.title,
            request.operation_ref,
            module_import=layout.python_module_name,
        ),
    )


def build_scaffold_write_plan(
    layout: ScaffoldLayout,
    payloads: ScaffoldPayloads,
) -> tuple[PlannedWrite, ...]:
    writes: list[PlannedWrite] = [
        PlannedWrite(layout.module_dir / 'module.json', payloads.module_payload),
        PlannedWrite(layout.module_dir / 'README.md', payloads.module_readme_text),
        PlannedWrite(layout.module_dir / 'skills.md', payloads.skills_text),
        PlannedWrite(layout.module_dir / 'permissions.json', payloads.permissions_payload),
        PlannedWrite(layout.module_dir / 'tools.json', payloads.tools_payload),
        PlannedWrite(layout.launcher_path, payloads.launcher_text, executable=True),
        PlannedWrite(layout.test_dir / 'README.md', payloads.tests_readme_text),
        PlannedWrite(layout.test_dir / 'test_smoke.py', layout.smoke_test_text),
        PlannedWrite(layout.python_module_dir / '__init__.py', ''),
        PlannedWrite(layout.python_module_dir / 'main.py', payloads.python_main_text, executable=True),
    ]
    if payloads.agents_text is not None:
        writes.append(PlannedWrite(layout.module_dir / 'AGENTS.md', payloads.agents_text))
    if payloads.contracts_text is not None:
        writes.append(PlannedWrite(layout.module_dir / 'contracts' / 'README.md', payloads.contracts_text))
    if payloads.constraints_text is not None:
        writes.append(PlannedWrite(layout.module_dir / 'constraints' / 'README.md', payloads.constraints_text))
    if payloads.docs_text is not None:
        writes.append(PlannedWrite(layout.module_dir / 'docs' / 'README.md', payloads.docs_text))
    return tuple(writes)


def execute_write_plan(plan: tuple[PlannedWrite, ...]) -> list[str]:
    written_paths: list[str] = []
    for item in plan:
        if item.is_json:
            written_paths.append(write_json(item.path, item.content))
            continue
        written_paths.append(write_text(item.path, str(item.content), executable=item.executable))
    return written_paths
