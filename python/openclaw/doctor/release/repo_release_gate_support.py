#!/usr/bin/env python3
"""仓库发布门禁的检查项装配、命令渲染和输出格式化辅助模块。"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

from openclaw.control_plane.surfaces import load_testing_manifest
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.managed_extensions import ManagedExtensionRow, managed_explicit_extensions
from openclaw.lib.repo.profiles import control_plane_profile_config_rel_paths
from openclaw.lib.repo.verification_tiers import release_gate_usage_lines

ROOT_DIR = resolve_repo_root(Path(__file__))
STRICT_MODE = 'strict'
STATIC_LANE = 'static'
INTEGRATION_LANE = 'integration'
EXHAUSTIVE_LANE = 'exhaustive'
RELEASE_LANES = (STATIC_LANE, INTEGRATION_LANE, EXHAUSTIVE_LANE)
DEFAULT_STATIC_TIMEOUT_SECONDS = 120.0
DEFAULT_INTEGRATION_TIMEOUT_SECONDS = 300.0
DEFAULT_EXHAUSTIVE_TIMEOUT_SECONDS = 240.0
GENERATED_DOCS_SYNC_CHECK_ID = 'generated_docs_sync'
GENERATED_DOCS_SYNC_TITLE = '生成文档同步检查'
GENERATED_DOCS_SYNC_COMMAND_TEXT = 'bash ./scripts/docs/check_generated_docs_sync.sh'
GENERATED_DOCS_INSERT_AFTER_CHECK_ID = 'documentation_implementation_alignment'
DOCUMENTATION_BATCH_CHECK_IDS = (
    'docs_registry_sync',
    'documentation_inventory',
    'documentation_links',
    'documentation_entrypoints',
    'documentation_boundaries',
    'documentation_navigation',
    'documentation_task_structure',
    'documentation_page_budget',
    'documentation_implementation_alignment',
    'documentation_object_closure',
    'local_document_identity',
)
AGENT_GOVERNANCE_BATCH_CHECK_ID = 'agent_extension_governance'
AGENT_GOVERNANCE_BATCH_SCRIPT_NAMES = {
    'check_agent_runtime_script_orphans.sh',
    'check_agent_governance_baseline.sh',
    'check_agent_module_optional_surface.sh',
    'check_agent_job_surface.sh',
}


@dataclass(frozen=True)
class CheckSpec:
    """单个发布门禁检查的静态定义、执行 lane 与内层时间边界。"""

    check_id: str
    title: str
    command_text: str
    command: Sequence[str]
    lane: str = STATIC_LANE
    timeout_seconds: float = DEFAULT_STATIC_TIMEOUT_SECONDS


@dataclass(frozen=True)
class CheckResult:
    """单个发布门禁检查的执行结果，供文本和 JSON 输出复用。"""

    check_id: str
    title: str
    command_text: str
    status: str
    detail: str
    mode: str = STRICT_MODE
    lane: str = STATIC_LANE
    duration_seconds: float = 0.0
    exit_code: int | None = None
    timed_out: bool = False


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
    return [resolve_bash_executable(), str(ROOT_DIR / script_rel), *args]


def generated_docs_check_spec() -> CheckSpec:
    return CheckSpec(
        GENERATED_DOCS_SYNC_CHECK_ID,
        GENERATED_DOCS_SYNC_TITLE,
        GENERATED_DOCS_SYNC_COMMAND_TEXT,
        bash_command('scripts/docs/check_generated_docs_sync.sh'),
        lane=STATIC_LANE,
        timeout_seconds=DEFAULT_STATIC_TIMEOUT_SECONDS,
    )


def documentation_batch_check_spec() -> CheckSpec:
    """返回一次执行全部文档 validator 的内部批处理检查定义。

    返回：
        CheckSpec：共享文档扫描的静态 lane、命令和内层时限。
    """
    return CheckSpec(
        'documentation_validators',
        '文档注册表与实现契约共享扫描',
        'bash ./scripts/docs/check_documentation_validators.sh',
        bash_command('scripts/docs/check_documentation_validators.sh'),
        lane=STATIC_LANE,
        timeout_seconds=DEFAULT_STATIC_TIMEOUT_SECONDS,
    )


def agent_governance_batch_check_spec() -> CheckSpec:
    """返回共享受管扩展 profile 与 registry 的内部批处理定义。

    返回：
        CheckSpec：扩展静态治理批处理的命令、lane 与内层时限。
    """
    return CheckSpec(
        AGENT_GOVERNANCE_BATCH_CHECK_ID,
        '受管扩展静态治理共享扫描',
        'bash ./scripts/doctor/check_agent_extension_governance.sh',
        bash_command('scripts/doctor/check_agent_extension_governance.sh'),
        lane=STATIC_LANE,
        timeout_seconds=DEFAULT_STATIC_TIMEOUT_SECONDS,
    )


def is_agent_governance_batch_spec(spec: CheckSpec) -> bool:
    """判断发布检查是否属于可共享上下文的扩展静态治理集合。

    参数：
        spec（CheckSpec）：待分类的发布检查定义。

    返回：
        bool：命令指向四类受管扩展静态治理脚本时为真。
    """
    command = list(spec.command)
    if spec.lane != STATIC_LANE or len(command) < 2:
        return False
    return Path(str(command[1])).name in AGENT_GOVERNANCE_BATCH_SCRIPT_NAMES


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
    lane = str(row.get('lane') or '').strip()
    if not lane:
        raise ValueError(f'release gate check {check_id} 必须显式声明 lane')
    if lane not in RELEASE_LANES:
        raise ValueError(f'release gate check {check_id} lane 不受支持：{lane}')
    default_timeout = {
        STATIC_LANE: DEFAULT_STATIC_TIMEOUT_SECONDS,
        INTEGRATION_LANE: DEFAULT_INTEGRATION_TIMEOUT_SECONDS,
        EXHAUSTIVE_LANE: DEFAULT_EXHAUSTIVE_TIMEOUT_SECONDS,
    }[lane]
    if 'timeoutSeconds' not in row:
        raise ValueError(f'release gate check {check_id} 必须显式声明 timeoutSeconds')
    raw_timeout = row.get('timeoutSeconds', default_timeout)
    if isinstance(raw_timeout, bool) or not isinstance(raw_timeout, (int, float)) or float(raw_timeout) <= 0:
        raise ValueError(f'release gate check {check_id} timeoutSeconds 必须是正数')
    return CheckSpec(
        check_id,
        title,
        f'bash ./{script_rel}{command_suffix}',
        bash_command(script_rel, *args),
        lane=lane,
        timeout_seconds=float(raw_timeout),
    )


@lru_cache(maxsize=1)
def _managed_extension_release_checks_cached() -> tuple[CheckSpec, ...]:
    """装配并缓存受管扩展的发布检查定义。

    返回：
        tuple[CheckSpec, ...]：按扩展注册顺序排列的不可变检查定义。

    异常：
        ValueError：manifest 缺失、检查 ID 重复或声明不合法时抛出。
    """
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
    return tuple(specs)


def managed_extension_release_checks() -> list[CheckSpec]:
    """返回受管扩展 release checks 的稳定副本，并复用注册表与 manifest 解析。

    返回：
        list[CheckSpec]：可由调用方安全修改的检查定义列表。
    """
    return list(_managed_extension_release_checks_cached())


def managed_extension_release_summary() -> str:
    entries: list[str] = []
    for extension_row in managed_explicit_extensions(ROOT_DIR):
        count = len(_release_gate_rows_for_extension(extension_row))
        if count:
            entries.append(f'{extension_row.id}（{count} 项）')
    return '、'.join(entries) if entries else '无'


def base_checks() -> list[CheckSpec]:
    return [
        CheckSpec(
            'host_python_governance',
            '宿主机 Python 执行面治理检查',
            'bash ./scripts/doctor/check_host_python_governance.sh',
            bash_command('scripts/doctor/check_host_python_governance.sh'),
        ),
        CheckSpec(
            'docstring_governance',
            '生产 Python 中文注释共享扫描检查',
            'bash ./scripts/doctor/check_docstring_governance.sh',
            bash_command('scripts/doctor/check_docstring_governance.sh'),
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
            'import_closure',
            '批量模块导入闭包检查',
            'bash ./scripts/doctor/check_cold_start_imports.sh --mode closure',
            bash_command('scripts/doctor/check_cold_start_imports.sh', '--mode', 'closure'),
            lane=STATIC_LANE,
            timeout_seconds=DEFAULT_EXHAUSTIVE_TIMEOUT_SECONDS,
        ),
        CheckSpec(
            'cold_start_imports',
            '独立冷启动模块导入检查',
            'bash ./scripts/doctor/check_cold_start_imports.sh --mode isolated',
            bash_command('scripts/doctor/check_cold_start_imports.sh', '--mode', 'isolated'),
            lane=EXHAUSTIVE_LANE,
            timeout_seconds=DEFAULT_EXHAUSTIVE_TIMEOUT_SECONDS,
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
            'documentation_inventory',
            '受管文档登记与归属检查',
            'bash ./scripts/docs/check_documentation_validators.sh',
            bash_command('scripts/docs/check_documentation_validators.sh'),
        ),
        CheckSpec(
            'documentation_links',
            '文档本地链接与锚点检查',
            'bash ./scripts/docs/check_documentation_validators.sh',
            bash_command('scripts/docs/check_documentation_validators.sh'),
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
        *managed_extension_release_checks(),
        CheckSpec(
            'local_document_identity',
            '局部文档身份检查',
            'bash ./scripts/docs/check_local_document_identity.sh',
            bash_command('scripts/docs/check_local_document_identity.sh'),
        ),
    ]


def ordered_check_specs(lanes: Sequence[str] | None = None) -> list[CheckSpec]:
    """按稳定顺序返回全部检查，或返回调用方选定 lane 的检查。

    参数：
        lanes（Sequence[str] | None）：待保留的 lane；为 ``None`` 时返回全部检查。

    返回：
        list[CheckSpec]：保持完整门禁相对顺序的检查定义。

    异常：
        ValueError：调用方传入未登记的 lane 时抛出。
    """
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
    if lanes is None:
        return ordered
    selected = set(lanes)
    unknown = sorted(selected - set(RELEASE_LANES))
    if unknown:
        raise ValueError(f'unsupported release lane: {", ".join(unknown)}')
    return [spec for spec in ordered if spec.lane in selected]


def generated_docs_steps() -> list[tuple[str, Sequence[str]]]:
    return [
        (GENERATED_DOCS_SYNC_CHECK_ID, generated_docs_check_spec().command),
    ]


def usage() -> str:
    check_lines = [f'    {index}. {spec.title}' for index, spec in enumerate(ordered_check_specs(), start=1)]
    return '\n'.join([
        '用法：',
        '  bash ./scripts/doctor/run_repo_release_gate.sh [--with-docker-sock] [--quiet] [--json] [--lane <static|integration|exhaustive>]...',
        '',
        '说明：',
        '  推荐仓库级检查顺序：',
        '    1. bash ./scripts/testing/check_repo_test_readiness.sh',
        '    2. bash ./scripts/doctor/run_repo_release_gate.sh [--with-docker-sock] [--quiet] [--json] [--lane <lane>]...',
        '',
        '  可独立于完整 release gate 运行的前置检查入口：',
        '    - bash ./scripts/testing/check_repo_test_readiness.sh',
        '    - bash ./scripts/doctor/check_host_python_governance.sh',
        '    - bash ./scripts/doctor/check_platform_docstring_governance.sh --mode report',
        '    - bash ./scripts/doctor/check_repo_prod_docstring_governance.sh --scope repo-prod --mode report',
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
        '  - --lane 可重复传入；选择多个 lane 或不指定时并发执行 lane，每个 lane 内仍保持声明顺序。',
        '',
        '边界：',
        '  - 只覆盖仓库静态治理与生成产物同步；',
        '  - 目标机实机验收链：',
        '    prepare_docker_host -> check_docker_host_readiness -> prepare_control_plane_medium -> one_click_config -> apply_ingress_boundary_rules -> fix_permissions -> one_click_test_basic -> one_click_deploy（默认自动执行 full test 与 runtime evidence 导出）',
        '  - 若真源刚被修改且尚未重渲染，先同步生成文档再执行本门禁。',
    ])


def render_json(
    results: list[CheckResult],
    *,
    duration_seconds: float | None = None,
) -> str:
    """渲染发布门禁机器报告，并区分墙钟总耗时与单项耗时。

    参数：
        results（list[CheckResult]）：按稳定声明顺序排列的检查结果。
        duration_seconds（float | None）：完整执行的墙钟秒数；未提供时兼容使用单项耗时之和。

    返回：
        str：保持既有字段并包含耗时、lane 与超时状态的 JSON 文本。
    """
    total_duration = round(
        sum(item.duration_seconds for item in results)
        if duration_seconds is None
        else max(float(duration_seconds), 0.0),
        3,
    )
    slowest = max(results, key=lambda item: (item.duration_seconds, item.check_id), default=None)
    summary = {
        'pass': sum(1 for item in results if item.status == 'PASS'),
        'strict_pass': sum(1 for item in results if item.status == 'PASS' and item.mode == STRICT_MODE),
        'fail': sum(1 for item in results if item.status == 'FAIL'),
        'total': len(results),
        'durationSeconds': total_duration,
        'slowestCheck': None if slowest is None else {
            'id': slowest.check_id,
            'durationSeconds': slowest.duration_seconds,
        },
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
                'lane': item.lane,
                'durationSeconds': item.duration_seconds,
                'exitCode': item.exit_code,
                'timedOut': item.timed_out,
                'detail': item.detail,
            }
            for item in results
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def safe_print(text: str, *, err: bool = False) -> None:
    stream = sys.stderr if err else sys.stdout
    payload = f'{text}\n'
    if hasattr(stream, 'buffer'):
        encoding = getattr(stream, 'encoding', None) or 'utf-8'
        stream.buffer.write(payload.encode(encoding, errors='replace'))
        stream.flush()
        return
    stream.write(payload)
