#!/usr/bin/env python3
"""运行态统一入口参考文档渲染器。"""
from __future__ import annotations

import sys
from pathlib import Path

from openclaw.lib.repo.layout import resolve_repo_root
from typing import Any

from openclaw.docs.support.doc_targets import require_nested_str
from openclaw.docs.support.markdown_tables import format_markdown_tables
from openclaw.docs.renderers.runtime_surface_support import loaders as runtime_loaders
from openclaw.docs.renderers.runtime_surface_support import pages as runtime_pages
from openclaw.lib.cli.examples import canonical_cli_command, host_wrapper_command
from openclaw.lib.repo.layout import (
    DEFAULT_RUNTIME_CONTROL_PLANE_PROFILE_ID,
    resolve_control_plane_profile_service_config_path,
    resolve_selected_control_plane_config_path,
)
from openclaw.lib.repo.static_truth import (
    read_repo_contract_json,
    service_registry_targets,
)

ROOT_DIR = resolve_repo_root(Path(__file__))
RENDER_RUNTIME_SURFACE_CMD = canonical_cli_command('docs', 'render-runtime-surface')
ACCEPTANCE_SUMMARY_CMD = host_wrapper_command('runtime', 'acceptance', 'acceptance-summary')
AGENT_GROUP_ACCEPTANCE_BINDINGS_CMD = host_wrapper_command('control-plane', 'evidence', 'agent-group-acceptance-bindings')


def load_testing_manifest(*, config_path: Path | None = None) -> dict[str, Any]:
    return runtime_loaders.load_testing_manifest(config_path=config_path)


def load_runtime_surface_manifest() -> dict[str, Any]:
    return runtime_loaders.load_runtime_surface_manifest()


def load_json_object(path: Path) -> dict[str, Any]:
    return runtime_loaders.load_json_object(path)


def read_manifest(*, config_path: Path | None = None) -> dict[str, Any]:
    return runtime_loaders.read_manifest(
        ROOT_DIR,
        config_path=config_path,
        resolve_config_path_fn=lambda root_dir: resolve_selected_control_plane_config_path(
            start_path=root_dir,
            control_plane_profile=DEFAULT_RUNTIME_CONTROL_PLANE_PROFILE_ID,
        ),
        load_runtime_surface_manifest_fn=load_runtime_surface_manifest,
        service_registry_targets_fn=service_registry_targets,
        load_testing_manifest_fn=load_testing_manifest,
        read_repo_contract_json_fn=read_repo_contract_json,
    )


def managed_note() -> str:
    return ''


def render_doc(manifest: dict[str, Any]) -> str:
    return runtime_pages.render_doc(
        manifest,
        managed_note_text=managed_note(),
        acceptance_summary_cmd=ACCEPTANCE_SUMMARY_CMD,
        agent_group_acceptance_bindings_cmd=AGENT_GROUP_ACCEPTANCE_BINDINGS_CMD,
        format_markdown_tables_fn=format_markdown_tables,
    )


def _default_runtime_config_path() -> Path:
    return resolve_control_plane_profile_service_config_path(
        start_path=ROOT_DIR,
        profile_id=DEFAULT_RUNTIME_CONTROL_PLANE_PROFILE_ID,
    ).resolve()


def _is_default_runtime_config_path(path: Path) -> bool:
    return path.resolve() == _default_runtime_config_path()


def usage() -> str:
    return '\n'.join([
        '用法：',
        f'  {RENDER_RUNTIME_SURFACE_CMD} [--control-plane-profile <profile> | --config-path <path>]',
        f'  {RENDER_RUNTIME_SURFACE_CMD} --check [--control-plane-profile <profile> | --config-path <path>]',
        f'  {RENDER_RUNTIME_SURFACE_CMD} --stdout [--control-plane-profile <profile> | --config-path <path>]',
        '',
    ])


def render_entry(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    mode = 'write'
    config_path: str | Path | None = None
    control_plane_profile = ''
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == '--check':
            mode = 'check'
        elif arg == '--stdout':
            mode = 'stdout'
        elif arg == '--config-path':
            index += 1
            if index >= len(args):
                sys.stderr.write('[render_runtime_surface_docs] --config-path 缺少路径参数\n')
                sys.stderr.write(f'{usage()}\n')
                return 2
            config_path = args[index]
        elif arg.startswith('--config-path='):
            value = arg.split('=', 1)[1].strip()
            if not value:
                sys.stderr.write('[render_runtime_surface_docs] --config-path 缺少路径参数\n')
                sys.stderr.write(f'{usage()}\n')
                return 2
            config_path = value
        elif arg == '--control-plane-profile':
            index += 1
            if index >= len(args):
                sys.stderr.write('[render_runtime_surface_docs] --control-plane-profile 缺少 profile 参数\n')
                sys.stderr.write(f'{usage()}\n')
                return 2
            control_plane_profile = str(args[index] or '').strip()
            if not control_plane_profile:
                sys.stderr.write('[render_runtime_surface_docs] --control-plane-profile 缺少 profile 参数\n')
                sys.stderr.write(f'{usage()}\n')
                return 2
        elif arg.startswith('--control-plane-profile='):
            control_plane_profile = arg.split('=', 1)[1].strip()
            if not control_plane_profile:
                sys.stderr.write('[render_runtime_surface_docs] --control-plane-profile 缺少 profile 参数\n')
                sys.stderr.write(f'{usage()}\n')
                return 2
        elif arg in {'-h', '--help'}:
            sys.stdout.write(f'{usage()}\n')
            return 0
        else:
            sys.stderr.write(f'[render_runtime_surface_docs] 未知参数：{arg}\n')
            sys.stderr.write(f'{usage()}\n')
            return 2
        index += 1
    if config_path is not None and control_plane_profile:
        sys.stderr.write('[render_runtime_surface_docs] --config-path 与 --control-plane-profile 不能同时使用\n')
        sys.stderr.write(f'{usage()}\n')
        return 2
    resolved_config_path = resolve_selected_control_plane_config_path(
        config_path,
        control_plane_profile=control_plane_profile or (DEFAULT_RUNTIME_CONTROL_PLANE_PROFILE_ID if config_path is None else None),
        start_path=ROOT_DIR,
        default_profile=DEFAULT_RUNTIME_CONTROL_PLANE_PROFILE_ID,
    )
    if mode != 'stdout' and not _is_default_runtime_config_path(resolved_config_path):
        sys.stderr.write(
            '[render_runtime_surface_docs] canonical runtime-service-reference 只允许使用默认 runtime profile '
            f'{DEFAULT_RUNTIME_CONTROL_PLANE_PROFILE_ID} 写入或检查；非默认 profile 请使用 --stdout 查看，'
            '避免同一生成文档在不同 profile 间来回漂移。\n'
        )
        return 2
    manifest = read_manifest(config_path=resolved_config_path)
    target_rel = require_nested_str(manifest, ['generated_doc'], prefix='render_runtime_surface_docs', label='runtime_entrypoints.generated_doc')
    target_path = ROOT_DIR / target_rel
    content = render_doc(manifest)
    existing = target_path.read_text(encoding='utf-8') if target_path.exists() else None
    if mode == 'stdout':
        sys.stdout.write(content)
        return 0 if existing == content else 1
    if mode == 'check':
        if existing == content:
            sys.stdout.write('[render_runtime_surface_docs] 已同步\n')
            return 0
        sys.stderr.write(f'[render_runtime_surface_docs] 文档未同步：{target_rel}\n')
        return 1
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content, encoding='utf-8', newline='\n')
    sys.stdout.write(f'[render_runtime_surface_docs] 已写入 {target_rel}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(render_entry())
