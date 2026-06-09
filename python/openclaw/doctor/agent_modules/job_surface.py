#!/usr/bin/env python3
"""检查 control-plane job manifest 是否仍保留可派生字段。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from openclaw.control_plane.jobs.surface import inspect_job_surface
from openclaw.control_plane.registry import load_registry
from openclaw.lib.cli import CliError, FlagSpec, parse_typed_flag_args
from openclaw.lib.repo.layout import resolve_repo_root, resolve_selected_control_plane_config_path

ROOT_DIR = resolve_repo_root(Path(__file__))


def usage() -> str:
    return '\n'.join([
        '用法:',
        '  python -m openclaw.doctor.agent_modules.job_surface [--config-path <path>] [--control-plane-profile <profile_id>]',
        '',
        '说明:',
        '  默认检查 base control-plane。',
        '  base profile 可能没有业务 job，此时 jobCount=0 是配置范围提示，不代表任何受管显式扩展已检查。',
        '  使用 OPENCLAW_CONTROL_PLANE_PROFILE 或 --control-plane-profile 切换配置；显式合同服务排查使用 --config-path。',
    ])


def parse_args(argv: list[str]) -> tuple[Path | None, str]:
    if any(arg in {'-h', '--help'} for arg in argv):
        sys.stdout.write(f'{usage()}\n')
        raise SystemExit(0)
    try:
        values, positionals = parse_typed_flag_args(
            argv,
            specs={
                'config-path': FlagSpec(kind='path', dest='config_path'),
                'control-plane-profile': FlagSpec(kind='str', dest='control_plane_profile'),
            },
        )
    except CliError as exc:
        sys.stderr.write(f'[check_agent_job_surface][FAIL] {exc}\n')
        sys.stderr.write(f'{usage()}\n')
        raise SystemExit(2) from exc
    if positionals:
        sys.stderr.write(f'[check_agent_job_surface][FAIL] 未知参数: {" ".join(positionals)}\n')
        sys.stderr.write(f'{usage()}\n')
        raise SystemExit(2)
    return values['config_path'], values['control_plane_profile'] or ''


def resolve_config_path(
    config_path: Path | None = None,
    *,
    control_plane_profile: str = '',
) -> Path:
    return resolve_selected_control_plane_config_path(
        config_path,
        control_plane_profile=control_plane_profile,
        start_path=ROOT_DIR,
        default_to_base=True,
    )


def main(argv: list[str] | None = None) -> int:
    requested_config_path, control_plane_profile = parse_args(list(sys.argv[1:] if argv is None else argv))
    config_path = resolve_config_path(
        requested_config_path,
        control_plane_profile=control_plane_profile,
    )
    registry = load_registry(config_path)
    items: list[dict[str, object]] = []
    for job_payload in registry.get('jobs', []):
        if not isinstance(job_payload, dict):
            continue
        inspection = inspect_job_surface(job_payload, registry=registry)
        items.append({
            'jobId': str(inspection.get('jobId') or ''),
            'agentRef': str(inspection.get('agentRef') or ''),
            'moduleRef': str(inspection.get('moduleRef') or ''),
            'path': str(Path(str(inspection.get('sourcePath') or '')).relative_to(ROOT_DIR)),
            'driftPaths': list(inspection.get('driftPaths') or []),
            'ok': bool(inspection.get('ok')),
        })
    offenders = [item for item in items if not bool(item.get('ok'))]
    payload = {
        'ok': not offenders,
        'jobCount': len(items),
        'offenderCount': len(offenders),
        'offenders': offenders,
        'items': items,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not offenders else 1


if __name__ == '__main__':
    raise SystemExit(main())
