#!/usr/bin/env python3
"""仓库发布门禁的检查项装配、命令渲染和输出格式化辅助模块。"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from openclaw.control_plane.surfaces import load_testing_manifest
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.managed_extensions import ManagedExtensionRow, managed_explicit_extensions
from openclaw.lib.repo.profiles import control_plane_profile_config_rel_paths
from openclaw.lib.repo.verification_tiers import release_gate_usage_lines

ROOT_DIR = resolve_repo_root(Path(__file__))
STRICT_MODE = 'strict'
GENERATED_DOCS_SYNC_CHECK_ID = 'generated_docs_sync'
GENERATED_DOCS_SYNC_TITLE = '生成文档同步检查'
GENERATED_DOCS_SYNC_COMMAND_TEXT = 'bash ./scripts/docs/check_generated_docs_sync.sh'
GENERATED_DOCS_INSERT_AFTER_CHECK_ID = 'documentation_implementation_alignment'


@dataclass(frozen=True)
class CheckSpec:
    """单个发布门禁检查的静态定义，包含展示文本和实际执行命令。"""

    check_id: str
    title: str
    command_text: str
    command: Sequence[str]


@dataclass(frozen=True)
class CheckResult:
    """单个发布门禁检查的执行结果，供文本和 JSON 输出复用。"""

    check_id: str
    title: str
    command_text: str
    status: str
    detail: str
    mode: str = STRICT_MODE


def _git_bash_candidates(git_executable: str) -> list[str]:
    raw = str(git_executable or '').strip()
    if not raw:
        return []
    candidates: list[str] = []
    for candidate_root in Path(raw).resolve().parents:
        for relative_path in ('bin/bash.exe', 'usr/bin/bash.exe'):
            bash_path = candidate_root / relative_path
            if bash_path.exists():
                candidates.append(str(bash_path))
    return candidates


def resolve_bash_executable() -> str:
    """解析发布门禁执行 shell 脚本时使用的 bash，可通过环境变量覆盖。"""
    configured = str(os.environ.get('OPENCLAW_BASH_BIN') or '').strip()
    if configured:
        return configured
    if os.name == 'nt':
        git_executable = shutil.which('git')
        if git_executable:
            for candidate in _git_bash_candidates(git_executable):
                if os.path.exists(candidate):
                    return candidate
        bash_executable = shutil.which('bash')
        if bash_executable:
            lowered = bash_executable.replace('/', '\\').lower()
            if not lowered.endswith(r'\windows\system32\bash.exe') and not lowered.endswith(r'\windowsapps\bash.exe'):
                return bash_executable
    return shutil.which('bash') or 'bash'


def bash_command(script_rel: str, *args: str) -> list[str]:
    """把仓库内 shell 脚本相对路径转换为可执行命令数组。"""
    return [resolve_bash_executable(), str(ROOT_DIR / script_rel), *args]


def generated_docs_check_spec() -> CheckSpec:
    """返回生成文档同步检查的固定 CheckSpec，供顺序插入逻辑复用。"""
    return CheckSpec(
        GENERATED_DOCS_SYNC_CHECK_ID,
        GENERATED_DOCS_SYNC_TITLE,
        GENERATED_DOCS_SYNC_COMMAND_TEXT,
        bash_command('scripts/docs/check_generated_docs_sync.sh'),
    )


def _repo_relative_path(path: Path) -> str:
    return path.resolve().relative_to(ROOT_DIR.resolve()).as_posix()


def _profile_id_for_managed_extension(row: ManagedExtensionRow) -> str:
    extension_config_path = row.default_service_config_path.resolve()
    for profile_id, rel_path in control_plane_profile_config_rel_paths(ROOT_DIR, allow_env_override=False).items():
        if (ROOT_DIR / rel_path).resolve() == extension_config_path:
            return profile_id
    raise ValueError(
        f'受管扩展缺少 profile_registry.tsv 登记：{row.id} -> {_repo_relative_path(extension_config_path)}'
    )


def _release_gate_rows_for_extension(row: ManagedExtensionRow) -> list[dict[str, Any]]:
    payload = load_testing_manifest(config_path=row.default_service_config_path)
    rows: list[dict[str, Any]] = []
    for item in payload.get('release_gate_checks') or []:
        if not isinstance(item, dict):
            continue
        if str(item.get('extensionId') or '').strip() == row.id:
            rows.append(dict(item))
    return rows


def _render_release_gate_template(value: str, *, extension_id: str, profile_id: str, config_path: Path) -> str:
    replacements = {
        'extension_id': extension_id,
        'profile_id': profile_id,
        'config_path': _repo_relative_path(config_path),
    }
    rendered = str(value)
    for key, replacement in replacements.items():
        rendered = rendered.replace('{' + key + '}', replacement)
    return rendered


def _normalize_release_gate_script(value: object, *, check_id: str) -> str:
    script_rel = str(value or '').strip().replace('\\', '/')
    parts = [part for part in script_rel.split('/') if part]
    if not script_rel or script_rel.startswith('/') or '..' in parts:
        raise ValueError(f'release gate check {check_id} command.script 必须是仓库内相对路径')
    script_path = (ROOT_DIR / script_rel).resolve()
    try:
        script_path.relative_to(ROOT_DIR.resolve())
    except ValueError as exc:
        raise ValueError(f'release gate check {check_id} command.script 越过仓库边界：{script_rel}') from exc
    if not script_path.is_file():
        raise ValueError(f'release gate check {check_id} command.script 不存在：{script_rel}')
    return script_rel


def _release_gate_check_spec(
    row: dict[str, Any],
    *,
    extension_id: str,
    profile_id: str,
    config_path: Path,
) -> CheckSpec:
    check_id = str(row.get('id') or '').strip()
    if not check_id:
        raise ValueError(f'release_gate_checks item for {extension_id} is missing id')
    title = str(row.get('title') or check_id).strip()
    command = row.get('command') if isinstance(row.get('command'), dict) else {}
    script_rel = _normalize_release_gate_script(command.get('script'), check_id=check_id)
    raw_args = command.get('args') or []
    if not isinstance(raw_args, list):
        raise ValueError(f'release gate check {check_id} command.args 必须是数组')
    args = [
        _render_release_gate_template(str(item), extension_id=extension_id, profile_id=profile_id, config_path=config_path)
        for item in raw_args
    ]
    command_suffix = '' if not args else ' ' + ' '.join(shlex.quote(item) for item in args)
    return CheckSpec(
        check_id,
        title,
        f'bash ./{script_rel}{command_suffix}',
        bash_command(script_rel, *args),
    )


def managed_extension_release_checks() -> list[CheckSpec]:
    """从受管扩展 testing manifest 收集扩展贡献的 release gate 检查。"""
    specs: list[CheckSpec] = []
    seen_ids: set[str] = set()
    for extension_row in managed_explicit_extensions(ROOT_DIR):
        profile_id = _profile_id_for_managed_extension(extension_row)
        release_checks = _release_gate_rows_for_extension(extension_row)
        if not release_checks:
            raise ValueError(f'受管扩展未在 testing manifest 声明 release_gate_checks：{extension_row.id}')
        for release_check in release_checks:
            spec = _release_gate_check_spec(
                release_check,
                extension_id=extension_row.id,
                profile_id=profile_id,
                config_path=extension_row.default_service_config_path,
            )
            if spec.check_id in seen_ids:
                raise ValueError(f'duplicate managed extension release gate check id: {spec.check_id}')
            seen_ids.add(spec.check_id)
            specs.append(spec)
    return specs


def managed_extension_release_summary() -> str:
    """返回受管扩展 release gate 覆盖摘要，用于帮助文本展示。"""
    entries: list[str] = []
    for extension_row in managed_explicit_extensions(ROOT_DIR):
        count = len(_release_gate_rows_for_extension(extension_row))
        if count:
            entries.append(f'{extension_row.id}（{count} 项）')
    return '、'.join(entries) if entries else '无'


def base_checks() -> list[CheckSpec]:
    """返回平台基座固定 release gate 检查列表，不含生成文档插入项。"""
    return [
        CheckSpec(
            'host_python_governance',
            '宿主机 Python 执行面治理检查',
            'bash ./scripts/doctor/check_host_python_governance.sh',
            bash_command('scripts/doctor/check_host_python_governance.sh'),
        ),
        CheckSpec(
            'platform_docstring_governance',
            '平台 Python 中文注释递进检查',
            'bash ./scripts/doctor/check_platform_docstring_governance.sh',
            bash_command('scripts/doctor/check_platform_docstring_governance.sh'),
        ),
        CheckSpec(
            'keyword_gate_inventory',
            '关键词门禁静态治理检查',
            'openclaw guards keyword-gate-inventory',
            [sys.executable, '-m', 'openclaw.doctor.platform.keyword_gate_inventory'],
        ),
        CheckSpec(
            'centos7_host_shell_guard',
            'CentOS 7 宿主机入口静态安全检查',
            'openclaw guards centos7-host-shell-guard',
            [sys.executable, '-m', 'openclaw.doctor.platform.centos7_host_shell_guard'],
        ),
        CheckSpec(
            'architecture_import_guards',
            'Python 包导入边界与目录布局检查',
            'bash ./scripts/doctor/check_architecture_import_guards.sh',
            bash_command('scripts/doctor/check_architecture_import_guards.sh'),
        ),
        CheckSpec(
            'cold_start_imports',
            '冷启动单模块导入检查',
            'bash ./scripts/doctor/check_cold_start_imports.sh',
            bash_command('scripts/doctor/check_cold_start_imports.sh'),
        ),
        CheckSpec(
            'shell_pythonpath_contract',
            'Shell Python 路径合同检查',
            'bash ./scripts/doctor/check_shell_pythonpath_contract.sh',
            bash_command('scripts/doctor/check_shell_pythonpath_contract.sh'),
        ),
        CheckSpec(
            'stack_lock_verify',
            'Stack lock 严格发布来源检查',
            'openclaw control-plane stack verify --strict-release --json',
            [sys.executable, '-m', 'openclaw.cli', 'control-plane', 'stack', 'verify', '--strict-release', '--json'],
        ),
        CheckSpec(
            'docs_registry_sync',
            'docs_registry 同步检查',
            'bash ./scripts/docs/check_docs_registry_sync.sh',
            bash_command('scripts/docs/check_docs_registry_sync.sh'),
        ),
        CheckSpec(
            'documentation_entrypoints',
            '文档入口检查',
            'bash ./scripts/docs/check_documentation_entrypoints.sh',
            bash_command('scripts/docs/check_documentation_entrypoints.sh'),
        ),
        CheckSpec(
            'documentation_boundaries',
            '文档职责边界检查',
            'bash ./scripts/docs/check_documentation_boundaries.sh',
            bash_command('scripts/docs/check_documentation_boundaries.sh'),
        ),
        CheckSpec(
            'documentation_navigation',
            '文档导航结构检查',
            'bash ./scripts/docs/check_documentation_navigation.sh',
            bash_command('scripts/docs/check_documentation_navigation.sh'),
        ),
        CheckSpec(
            'documentation_task_structure',
            '文档任务页模板检查',
            'bash ./scripts/docs/check_documentation_task_structure.sh',
            bash_command('scripts/docs/check_documentation_task_structure.sh'),
        ),
        CheckSpec(
            'documentation_page_budget',
            '文档页面预算检查',
            'bash ./scripts/docs/check_documentation_page_budget.sh',
            bash_command('scripts/docs/check_documentation_page_budget.sh'),
        ),
        CheckSpec(
            'documentation_implementation_alignment',
            '文档实现对齐检查',
            'bash ./scripts/docs/check_documentation_implementation_alignment.sh',
            bash_command('scripts/docs/check_documentation_implementation_alignment.sh'),
        ),
        CheckSpec(
            'documentation_object_closure',
            '文档对象闭环检查',
            'bash ./scripts/docs/check_documentation_object_closure.sh',
            bash_command('scripts/docs/check_documentation_object_closure.sh'),
        ),
        CheckSpec(
            'delivery_cleanliness',
            '交付说明洁净度检查',
            'bash ./scripts/doctor/check_delivery_cleanliness.sh',
            bash_command('scripts/doctor/check_delivery_cleanliness.sh'),
        ),
        *managed_extension_release_checks(),
        CheckSpec(
            'local_document_identity',
            '局部文档身份检查',
            'bash ./scripts/docs/check_local_document_identity.sh',
            bash_command('scripts/docs/check_local_document_identity.sh'),
        ),
    ]


def ordered_check_specs() -> list[CheckSpec]:
    """返回实际执行顺序，将生成文档同步检查插入到实现对齐之后。"""
    ordered: list[CheckSpec] = []
    generated_inserted = False
    generated_spec = generated_docs_check_spec()
    for spec in base_checks():
        ordered.append(spec)
        if spec.check_id == GENERATED_DOCS_INSERT_AFTER_CHECK_ID:
            ordered.append(generated_spec)
            generated_inserted = True
    if not generated_inserted:
        ordered.append(generated_spec)
    return ordered


def generated_docs_steps() -> list[tuple[str, Sequence[str]]]:
    """返回生成文档同步步骤，供外层 runner 单独补跑或展示。"""
    return [
        (GENERATED_DOCS_SYNC_CHECK_ID, generated_docs_check_spec().command),
    ]


def usage() -> str:
    """渲染 release gate 帮助文本，说明入口边界、检查清单和执行前提。"""
    check_lines = [f'    {index}. {spec.title}' for index, spec in enumerate(ordered_check_specs(), start=1)]
    return '\n'.join([
        '用法：',
        '  bash ./scripts/doctor/run_repo_release_gate.sh [--with-docker-sock] [--quiet] [--json]',
        '',
        '说明：',
        '  推荐仓库级检查顺序：',
        '    1. bash ./scripts/testing/check_repo_test_readiness.sh',
        '    2. bash ./scripts/doctor/run_repo_release_gate.sh [--with-docker-sock] [--quiet] [--json]',
        '',
        '  可独立于完整 release gate 运行的前置检查入口：',
        '    - bash ./scripts/testing/check_repo_test_readiness.sh',
        '    - bash ./scripts/doctor/check_host_python_governance.sh',
        '    - bash ./scripts/doctor/check_platform_docstring_governance.sh --mode report',
        '    注：除 --help 外，静态 Python 检查仍固定要求 Docker 与控制面执行介质。',
        '',
        *release_gate_usage_lines(ROOT_DIR),
        '',
        '  统一执行当前仓库的静态发布门禁，覆盖：',
        *check_lines,
        '',
        '受管扩展覆盖：',
        '  - 仓库内受管扩展 profile 从 config/control_plane/profile_registry.tsv 登记；',
        '  - 扩展包内部能力检查由各扩展 testing manifest 的 release_gate_checks 声明，仓库门禁只负责发现、渲染与执行；',
        f'  - 当前受管扩展声明覆盖：{managed_extension_release_summary()}；',
        '  - agent 模块结构检查使用扩展声明的 --control-plane-profile / --extension 参数，不依赖默认 agent_platform 空业务面。',
        '',
        '模式：',
        '  - 默认模式：所有检查统一保持 strict；除 host_python_governance 外，其余检查执行面固定复用控制面容器，宿主机 Python 不属于支持路径。',
        '',
        '边界：',
        '  - 只覆盖仓库静态治理与生成产物同步；',
        '  - 目标机实机验收链：',
        '    prepare_docker_host -> check_docker_host_readiness -> prepare_control_plane_medium -> one_click_config -> apply_ingress_boundary_rules -> fix_permissions -> one_click_test_basic -> one_click_deploy（默认自动执行 full test 与 runtime evidence 导出）',
        '  - 若真源刚被修改且尚未重渲染，先同步生成文档再执行本门禁。',
    ])


def render_json(results: list[CheckResult]) -> str:
    """把门禁结果列表序列化为稳定 JSON，便于 CI 或外部工具消费。"""
    summary = {
        'pass': sum(1 for item in results if item.status == 'PASS'),
        'strict_pass': sum(1 for item in results if item.status == 'PASS' and item.mode == STRICT_MODE),
        'fail': sum(1 for item in results if item.status == 'FAIL'),
        'total': len(results),
    }
    payload = {
        'suite': 'repo_release_gate',
        'summary': summary,
        'checks': [
            {
                'id': item.check_id,
                'title': item.title,
                'command': item.command_text,
                'status': item.status,
                'mode': item.mode,
                'detail': item.detail,
            }
            for item in results
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def safe_print(text: str, *, err: bool = False) -> None:
    """按当前输出流编码安全打印文本，避免 Windows 控制台编码异常中断门禁。"""
    stream = sys.stderr if err else sys.stdout
    payload = f'{text}\n'
    if hasattr(stream, 'buffer'):
        encoding = getattr(stream, 'encoding', None) or 'utf-8'
        stream.buffer.write(payload.encode(encoding, errors='replace'))
        stream.flush()
        return
    stream.write(payload)
