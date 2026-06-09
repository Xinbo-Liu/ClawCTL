#!/usr/bin/env python3
"""bootstrap 阶段的运行态派生产物渲染器。"""
from __future__ import annotations

import filecmp
import json
import os
import shutil
from pathlib import Path
from typing import Any, Iterable, NoReturn

from openclaw.control_plane.surfaces import load_gateway_readonly_manifest
from openclaw.lib.cli import CliError, FlagSpec, parse_typed_flag_args
from openclaw.lib.repo.contracts import repo_contract_path
from openclaw.lib.repo.layout import resolve_default_runtime_control_plane_service_config_path, resolve_repo_root
from openclaw.lib.repo.static_truth import runtime_paths_host_entry as truth_runtime_paths_host_entry
from openclaw.lib.runtime.resolver_loader import require_path_resolver
from openclaw.runtime.generated_paths.rendering import render_generated_outputs
from openclaw.setup.deploy_env import dispatch_registry as dispatch_registry_lib
from openclaw.setup.deploy_env.render_validate import DEFAULT_OUTPUT_PATH


ROOT_DIR = resolve_repo_root(Path(__file__))
DEFAULT_RUNTIME_SCHEDULER_APP_ENV_PATH = ROOT_DIR / truth_runtime_paths_host_entry('runtime_scheduler_app_env')
DEFAULT_RUNTIME_INTERNAL_API_APP_ENV_PATH = ROOT_DIR / truth_runtime_paths_host_entry('runtime_internal_api_app_env')
DEFAULT_DISPATCH_RUNTIME_OUTPUT_PATH = ROOT_DIR / truth_runtime_paths_host_entry('dispatch_targets_json')
DEFAULT_DISPATCH_RUNTIME_SUMMARY_PATH = ROOT_DIR / truth_runtime_paths_host_entry('dispatch_runtime_summary_json')
DEFAULT_RUNTIME_INTERNAL_API_BIND = str(os.environ.get('OPENCLAW_RENDER_RUNTIME_INTERNAL_API_BIND') or '').strip() or '0.0.0.0'


def _fail(message: str, exit_code: int = 2) -> NoReturn:
    raise SystemExit(f'[bootstrap_runtime][FAIL] {message}')


def _is_under(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _resolve_repo_path(repo_root: Path, value: str) -> Path:
    if not value or Path(value).is_absolute():
        _fail(f'manifest 不允许使用绝对 source 路径：{value}', 3)
    resolved = (repo_root / value).resolve()
    if not _is_under(resolved, repo_root):
        _fail(f'manifest source 越界：{value}', 3)
    return resolved


def _resolve_output_path(output_dir: Path, value: str) -> Path:
    if not value or Path(value).is_absolute():
        _fail(f'manifest 不允许使用绝对 target 路径：{value}', 3)
    resolved = (output_dir / value).resolve(strict=False)
    if not _is_under(resolved, output_dir):
        _fail(f'manifest target 越界：{value}', 3)
    return resolved


def _iter_source_files(source: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(source, followlinks=False):
        dirnames[:] = sorted(name for name in dirnames if not (Path(dirpath) / name).is_symlink())
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            if path.is_file() and not path.is_symlink():
                yield path


def _load_readonly_manifest(
    manifest_path: Path,
    *,
    repo_root: Path,
    config_path: Path | None,
) -> dict[str, Any]:
    try:
        canonical_path = repo_contract_path('gateway.readonly_manifest', root_dir=repo_root).resolve()
    except (KeyError, ValueError, FileNotFoundError):
        canonical_path = None
    if canonical_path is not None and manifest_path.resolve() == canonical_path:
        return load_gateway_readonly_manifest(manifest_path, config_path=config_path)
    payload = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    if not isinstance(payload, dict):
        _fail('gateway readonly manifest 顶层必须为对象', 3)
    return payload


def _expected_local_ro_files(
    *,
    repo_root: Path,
    output_dir: Path,
    manifest_path: Path,
    config_path: Path | None,
) -> dict[Path, Path]:
    payload = _load_readonly_manifest(manifest_path, repo_root=repo_root, config_path=config_path)
    entries = payload.get('entries') or []
    if not isinstance(entries, list):
        _fail('manifest entries 必须为数组', 3)
    expected: dict[Path, Path] = {}
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            _fail(f'entry[{index}] 必须为对象', 3)
        entry_type = str(entry.get('type') or '').strip()
        source = str(entry.get('source') or '').strip()
        target = str(entry.get('target') or '').strip()
        if entry_type not in {'file', 'dir'}:
            _fail(f'entry[{index}] type 非法：{entry_type!r}', 3)
        source_abs = _resolve_repo_path(repo_root, source)
        target_abs = _resolve_output_path(output_dir, target)
        if not source_abs.exists():
            _fail(f'manifest source 不存在：{source}', 3)
        if entry_type == 'file':
            if not source_abs.is_file():
                _fail(f'manifest source 不是文件：{source}', 3)
            if target_abs in expected and expected[target_abs] != source_abs:
                _fail(f'manifest target 重复：{target}', 3)
            expected[target_abs] = source_abs
            continue
        if not source_abs.is_dir():
            _fail(f'manifest source 不是目录：{source}', 3)
        for source_file in _iter_source_files(source_abs):
            resolved_target = (target_abs / source_file.relative_to(source_abs)).resolve(strict=False)
            if resolved_target in expected and expected[resolved_target] != source_file:
                _fail(f'manifest target 重复：{resolved_target.relative_to(output_dir)}', 3)
            expected[resolved_target] = source_file
    return expected


def _chmod_local_ro(output_dir: Path) -> None:
    if not output_dir.exists():
        return
    for dirpath, dirnames, filenames in os.walk(output_dir, topdown=False, followlinks=False):
        current_dir = Path(dirpath)
        for filename in filenames:
            path = current_dir / filename
            if path.is_file() and not path.is_symlink():
                path.chmod(0o600)
        for dirname in dirnames:
            path = current_dir / dirname
            if path.is_dir() and not path.is_symlink():
                path.chmod(0o700)
        current_dir.chmod(0o700)


def render_local_ro_mirror(
    *,
    manifest_path: Path,
    output_dir: Path,
    label: str,
    repo_root: Path = ROOT_DIR,
    config_path: Path | None = None,
    check_only: bool = False,
) -> int:
    manifest_path = manifest_path.resolve()
    output_dir = output_dir.resolve(strict=False)
    gateway_root = require_path_resolver(repo_root=repo_root, config_path=config_path).absolute_host_path('gateway_host_state_dir').resolve(strict=False)
    if not _is_under(output_dir, gateway_root) or output_dir == gateway_root:
        _fail(f'输出目录必须位于 gateway state root 的子目录：{gateway_root}；当前={output_dir}', 3)
    if not manifest_path.is_file():
        _fail(f'缺少 manifest：{manifest_path}', 2)

    expected = _expected_local_ro_files(
        repo_root=repo_root.resolve(),
        output_dir=output_dir,
        manifest_path=manifest_path,
        config_path=config_path,
    )
    if check_only:
        if not output_dir.is_dir():
            _fail(f'输出目录不存在：{output_dir}', 4)
        for target, source in expected.items():
            if not target.is_file():
                _fail(f'缺少输出文件：{target.relative_to(output_dir)}', 4)
            if not filecmp.cmp(source, target, shallow=False):
                _fail(f'输出文件与真源不一致：{target.relative_to(output_dir)}', 4)
        for actual in sorted(path for path in output_dir.rglob('*') if path.is_file() or path.is_symlink()):
            if actual.resolve() not in expected:
                _fail(f'输出目录存在未声明文件：{actual.relative_to(output_dir)}', 4)
        print(f'[{label}] check passed: {output_dir}')
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    for target, source in expected.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and filecmp.cmp(source, target, shallow=False):
            continue
        if target.exists() or target.is_symlink():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        shutil.copy2(source, target)
    for actual in sorted((path for path in output_dir.rglob('*') if path.is_file() or path.is_symlink()), reverse=True):
        if actual.resolve() not in expected:
            actual.unlink()
    for actual_dir in sorted((path for path in output_dir.rglob('*') if path.is_dir()), key=lambda item: len(item.parts), reverse=True):
        try:
            actual_dir.rmdir()
        except OSError:
            pass
    _chmod_local_ro(output_dir)
    print(f'[{label}] rendered: {output_dir}')
    return 0


def render_local_ro_mirror_cli(argv: list[str]) -> int:
    try:
        values, positionals = parse_typed_flag_args(
            argv,
            specs={
                'manifest': FlagSpec(kind='path', dest='manifest_path', default=None),
                'output-dir': FlagSpec(kind='path', dest='output_dir', default=None),
                'label': FlagSpec(kind='str', dest='label', default='render_local_ro_mirror'),
                'config-path': FlagSpec(kind='path', dest='config_path', default=None),
                'check': FlagSpec(kind='bool', dest='check_only', default=False),
            },
        )
    except CliError as exc:
        _fail(str(exc), 2)
    if positionals:
        _fail(f'未知参数：{positionals[0]}', 2)
    if not values['manifest_path']:
        _fail('必须通过 --manifest 指定 manifest', 2)
    if not values['output_dir']:
        _fail('必须通过 --output-dir 指定输出目录', 2)
    config_path = values['config_path'] or resolve_default_runtime_control_plane_service_config_path(ROOT_DIR)
    return render_local_ro_mirror(
        manifest_path=values['manifest_path'],
        output_dir=values['output_dir'],
        label=values['label'],
        config_path=config_path,
        check_only=values['check_only'],
    )


def bootstrap_runtime(argv: list[str]) -> int:
    try:
        values, positionals = parse_typed_flag_args(
            argv,
            specs={
                'repo-root': FlagSpec(kind='path', dest='repo_root', default=ROOT_DIR),
                'env-file': FlagSpec(kind='path', dest='env_file', default=DEFAULT_OUTPUT_PATH),
                'config-path': FlagSpec(kind='path', dest='config_path', default=None),
                'dispatch-output': FlagSpec(kind='path', dest='dispatch_output', default=DEFAULT_DISPATCH_RUNTIME_OUTPUT_PATH),
                'dispatch-registry-summary-json': FlagSpec(kind='path', dest='dispatch_registry_summary_json', default=None),
                'dispatch-summary-json': FlagSpec(kind='path', dest='dispatch_summary_json', default=DEFAULT_DISPATCH_RUNTIME_SUMMARY_PATH),
                'scheduler-output': FlagSpec(kind='path', dest='scheduler_output', default=DEFAULT_RUNTIME_SCHEDULER_APP_ENV_PATH),
                'internal-api-output': FlagSpec(kind='path', dest='internal_api_output', default=DEFAULT_RUNTIME_INTERNAL_API_APP_ENV_PATH),
                'internal-api-bind': FlagSpec(kind='str', dest='internal_api_bind', default=DEFAULT_RUNTIME_INTERNAL_API_BIND),
                'gateway-readonly-manifest': FlagSpec(kind='path', dest='gateway_readonly_manifest', default=repo_contract_path('gateway.readonly_manifest')),
                'gateway-local-ro-output': FlagSpec(kind='path', dest='gateway_local_ro_output', default=None),
            },
        )
    except CliError as exc:
        _fail(str(exc), 2)
    if positionals:
        _fail(f'未知参数：{positionals[0]}', 2)
    repo_root = values['repo_root'].resolve()
    config_path = values['config_path'] or resolve_default_runtime_control_plane_service_config_path(repo_root)
    resolver = require_path_resolver(repo_root=repo_root, config_path=config_path)

    render_generated_outputs(repo_root, resolver, config_path)
    print(
        '[runtime_path_surface] rendered runtime path artifacts under '
        f"{resolver.absolute_host_path('gateway_host_state_dir')} / "
        f"{resolver.absolute_host_path('control_plane_host_state_dir')}"
    )
    dispatch_registry_lib.validate_dispatch_registry([
        '--env-file',
        str(values['env_file']),
        '--config-path',
        str(config_path),
        '--summary-json',
        str(values['dispatch_registry_summary_json'] or values['dispatch_summary_json']),
    ])
    dispatch_registry_lib.render_dispatch_runtime([
        '--env-file',
        str(values['env_file']),
        '--config-path',
        str(config_path),
        '--output',
        str(values['dispatch_output']),
        '--summary-json',
        str(values['dispatch_summary_json']),
    ], default_env_file=values['env_file'], default_output=values['dispatch_output'], default_summary_json=values['dispatch_summary_json'])
    dispatch_registry_lib.render_runtime_service_envs([
        '--env-file',
        str(values['env_file']),
        '--config-path',
        str(config_path),
        '--scheduler-output',
        str(values['scheduler_output']),
        '--internal-api-output',
        str(values['internal_api_output']),
        '--internal-api-bind',
        str(values['internal_api_bind']),
    ], default_env_file=values['env_file'], default_scheduler_output=values['scheduler_output'], default_internal_api_output=values['internal_api_output'], default_config_path=config_path, default_internal_api_bind=values['internal_api_bind'])
    local_ro_output = values['gateway_local_ro_output'] or resolver.absolute_host_path('gateway_local_ro_dir')
    render_local_ro_mirror(
        manifest_path=values['gateway_readonly_manifest'],
        output_dir=local_ro_output,
        label='render_gateway_local_ro',
        repo_root=repo_root,
        config_path=config_path,
    )
    return 0
