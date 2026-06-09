#!/usr/bin/env python3
"""维护事实总览生成文档渲染器。"""
from __future__ import annotations

import sys
from pathlib import Path

from openclaw.control_plane.facts import (
    MAINTENANCE_MAP_DOC,
    build_overview_payload,
    render_overview_markdown,
)
from openclaw.lib.cli.examples import canonical_cli_command
from openclaw.lib.repo.layout import (
    DEFAULT_RUNTIME_CONTROL_PLANE_PROFILE_ID,
    resolve_repo_root,
    resolve_selected_control_plane_config_path,
)

ROOT_DIR = resolve_repo_root(Path(__file__))
RENDER_MAINTENANCE_MAP_CMD = canonical_cli_command('docs', 'render-maintenance-map')


def usage() -> str:
    return '\n'.join(
        [
            '用法：',
            f'  {RENDER_MAINTENANCE_MAP_CMD} [--control-plane-profile <profile> | --config-path <path>]',
            f'  {RENDER_MAINTENANCE_MAP_CMD} --check [--control-plane-profile <profile> | --config-path <path>]',
            f'  {RENDER_MAINTENANCE_MAP_CMD} --stdout [--control-plane-profile <profile> | --config-path <path>]',
            '',
        ]
    )


def render_doc(*, config_path: Path | None = None) -> str:
    payload = build_overview_payload(
        config_path=config_path,
        control_plane_profile=None if config_path is not None else DEFAULT_RUNTIME_CONTROL_PLANE_PROFILE_ID,
        probe_local=False,
        include_all_profiles=True,
        include_profile_runtime_services=False,
        include_profile_evidence_paths=False,
        root_dir=ROOT_DIR,
    )
    return render_overview_markdown(payload, redact_managed_extensions=True)


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
                sys.stderr.write('[render_maintenance_map] --config-path 缺少路径参数\n')
                sys.stderr.write(f'{usage()}\n')
                return 2
            config_path = args[index]
        elif arg.startswith('--config-path='):
            value = arg.split('=', 1)[1].strip()
            if not value:
                sys.stderr.write('[render_maintenance_map] --config-path 缺少路径参数\n')
                sys.stderr.write(f'{usage()}\n')
                return 2
            config_path = value
        elif arg == '--control-plane-profile':
            index += 1
            if index >= len(args):
                sys.stderr.write('[render_maintenance_map] --control-plane-profile 缺少 profile 参数\n')
                sys.stderr.write(f'{usage()}\n')
                return 2
            control_plane_profile = str(args[index] or '').strip()
            if not control_plane_profile:
                sys.stderr.write('[render_maintenance_map] --control-plane-profile 缺少 profile 参数\n')
                sys.stderr.write(f'{usage()}\n')
                return 2
        elif arg.startswith('--control-plane-profile='):
            control_plane_profile = arg.split('=', 1)[1].strip()
            if not control_plane_profile:
                sys.stderr.write('[render_maintenance_map] --control-plane-profile 缺少 profile 参数\n')
                sys.stderr.write(f'{usage()}\n')
                return 2
        elif arg in {'-h', '--help'}:
            sys.stdout.write(f'{usage()}\n')
            return 0
        else:
            sys.stderr.write(f'[render_maintenance_map] 未知参数：{arg}\n')
            sys.stderr.write(f'{usage()}\n')
            return 2
        index += 1

    if config_path is not None and control_plane_profile:
        sys.stderr.write('[render_maintenance_map] --config-path 与 --control-plane-profile 不能同时使用\n')
        sys.stderr.write(f'{usage()}\n')
        return 2
    resolved_config_path = None
    if config_path is not None or control_plane_profile:
        resolved_config_path = resolve_selected_control_plane_config_path(
            config_path,
            control_plane_profile=control_plane_profile or None,
            start_path=ROOT_DIR,
            default_profile=DEFAULT_RUNTIME_CONTROL_PLANE_PROFILE_ID,
        )
    target_rel = MAINTENANCE_MAP_DOC
    target_path = ROOT_DIR / target_rel
    content = render_doc(config_path=resolved_config_path)
    existing = target_path.read_text(encoding='utf-8') if target_path.exists() else None
    if mode == 'stdout':
        sys.stdout.write(content)
        return 0 if existing == content else 1
    if mode == 'check':
        if existing == content:
            sys.stdout.write('[render_maintenance_map] 已同步\n')
            return 0
        sys.stderr.write(f'[render_maintenance_map] 文档未同步：{target_rel}\n')
        return 1
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content, encoding='utf-8', newline='\n')
    sys.stdout.write(f'[render_maintenance_map] 已写入 {target_rel}\n')
    return 0


def main(argv: list[str] | None = None) -> int:
    return render_entry(argv)


if __name__ == '__main__':
    raise SystemExit(main())
