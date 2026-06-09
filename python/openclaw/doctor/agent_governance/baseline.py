#!/usr/bin/env python3
"""Agent governance baseline checker."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from openclaw.lib.cli import CliError, FlagSpec, parse_typed_flag_args
from openclaw.doctor.agent_governance.baseline_support import run_governance_baseline_check
from openclaw.lib.repo.layout import resolve_selected_control_plane_config_path


def usage() -> str:
    return '\n'.join([
        '用法:',
        '  bash ./scripts/doctor/check_agent_governance_baseline.sh',
        '  python3 -m openclaw.doctor.agent_governance.baseline [--config-path <path>] [--control-plane-profile <profile_id>]',
        '',
        '说明:',
        '  校验 agent 统一治理目录、模块总览页、group 映射页、active profile 下的 agent/module/group 一致性，',
        '  以及 module.contract / module.logic.implementationRef 单真源、group membership / topology / recoveryPolicy 解析关系。',
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
        sys.stderr.write(f'[check_agent_governance_baseline][FAIL] {exc}\n')
        sys.stderr.write(f'{usage()}\n')
        raise SystemExit(2) from exc
    if positionals:
        sys.stderr.write(f'[check_agent_governance_baseline][FAIL] 未知参数: {" ".join(positionals)}\n')
        sys.stderr.write(f'{usage()}\n')
        raise SystemExit(2)
    return values['config_path'], values['control_plane_profile'] or ''


def resolve_requested_config_path(
    config_path: Path | None,
    *,
    control_plane_profile: str,
) -> Path:
    return resolve_selected_control_plane_config_path(
        config_path,
        control_plane_profile=control_plane_profile,
        start_path=Path(__file__),
        default_to_base=True,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        config_path, control_plane_profile = parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as exc:
        return int(exc.code)
    payload = run_governance_baseline_check(
        resolve_requested_config_path(
            config_path,
            control_plane_profile=control_plane_profile,
        ),
    )
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    return 0 if not payload.get('errors') else 1


if __name__ == '__main__':
    raise SystemExit(main())
