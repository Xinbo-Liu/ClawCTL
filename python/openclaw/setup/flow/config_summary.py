#!/usr/bin/env python3
"""one_click_config failure summary surface."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, NoReturn

from openclaw.lib.summary.io import relative_or_self, utc_now_iso, write_json
from openclaw.setup.surface import failure as failure_surface


ENTRY_ID = 'one_click_config'


def fail(message: str, code: int = 2) -> NoReturn:
    sys.stderr.write(f'[config_summary_control_plane][FAIL] {message}\n')
    raise SystemExit(code)


def parse_bool(raw: object, name: str) -> bool:
    value = str(raw).strip().lower()
    if value in {'1', 'true', 'yes'}:
        return True
    if value in {'0', 'false', 'no', ''}:
        return False
    fail(f'{name} 只接受 true/false/1/0，收到：{raw}')
    raise AssertionError('unreachable')


def parse_args(argv: list[str]) -> dict[str, Any]:
    opts: dict[str, Any] = {
        'format': 'text',
        'generated_at': '',
        'output_path': '',
        'failed_stage': '',
        'exit_code': 1,
        'failure_message': '',
        'dry_run': False,
        'out_json': '',
    }
    index = 0
    while index < len(argv):
        arg = argv[index]
        if not arg.startswith('--'):
            fail(f'未知参数：{arg}')
        index += 1
        if index >= len(argv):
            fail(f'{arg} 缺少参数值')
        value = argv[index]
        index += 1
        match arg:
            case '--format':
                opts['format'] = value
            case '--generated-at':
                opts['generated_at'] = value
            case '--output-path':
                opts['output_path'] = str(Path(value).resolve())
            case '--failed-stage':
                opts['failed_stage'] = value
            case '--exit-code':
                opts['exit_code'] = int(value)
            case '--failure-message':
                opts['failure_message'] = value
            case '--dry-run':
                opts['dry_run'] = parse_bool(value, '--dry-run')
            case '--out-json':
                opts['out_json'] = str(Path(value).resolve())
            case _:
                fail(f'未知参数：{arg}')
    if not opts['failed_stage']:
        fail('--failed-stage 缺少阶段名')
    return opts


def scenario_for_stage(stage: str) -> str:
    match stage:
        case 'effective_compose_render':
            return 'effective_compose_render_failed'
        case 'render_control_plane' | 'final_emit':
            return 'render_failed'
        case _:
            return 'preflight_failed'


def build_summary(options: dict[str, Any]) -> dict[str, Any]:
    scenario_id = scenario_for_stage(str(options['failed_stage']))
    scenario = failure_surface.scenario_info(ENTRY_ID, scenario_id)
    return {
        'schema_version': 1,
        'generated_at': str(options.get('generated_at') or utc_now_iso()),
        'output_path': str(options.get('output_path') or ''),
        'status': scenario_id,
        'required_manual_keys': [],
        'unresolved_required_count': 0,
        'auth_mode': 'official_gateway_token',
        'ingress_mode': 'private_https_ingress',
        'tls_mode': 'unknown',
        'generator': {
            'mode': 'python_surface',
            'reason': 'config_summary_control_plane',
        },
        'failed_stage': str(options['failed_stage']),
        'exit_code': int(options.get('exit_code') or 1),
        'scenario': scenario_id,
        'scenario_title': str(scenario.get('title') or scenario_id).strip(),
        'failure_message': str(options.get('failure_message') or '').strip(),
        'when': str(scenario.get('when') or '').strip(),
        'reference_doc': str(failure_surface.load_config().get('generated_artifacts', {}).get('setup_failure_doc') or '').strip(),
        'dry_run': bool(options.get('dry_run')),
        'next_actions': failure_surface.list_str(scenario, 'commands'),
    }


def render_text(summary: dict[str, Any]) -> str:
    lines = [
        '=== one_click_config 汇总 ===',
        'status: FAIL',
        f"failed_stage: {summary['failed_stage']}",
        f"exit_code: {summary['exit_code']}",
        f"output_path: {summary['output_path']}",
    ]
    if summary.get('failure_message'):
        lines.extend(['', f"[detail] {summary['failure_message']}"])
    if summary.get('scenario_title'):
        lines.append(f"[detail] setup 主链失败分流：{summary['scenario_title']} ({summary['scenario']})")
    if summary.get('when'):
        lines.append(f"[detail] 适用条件：{summary['when']}")
    if summary.get('reference_doc'):
        lines.append(f"[detail] 统一参考：{summary['reference_doc']}")
    if summary.get('next_actions'):
        lines.extend(['', '下一步动作:'])
        lines.extend([f'{index + 1}. {item}' for index, item in enumerate(summary['next_actions'])])
    return '\n'.join(lines) + '\n'


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        fail('缺少命令')
    command = args.pop(0)
    options = parse_args(args)
    summary = build_summary(options)
    if command == 'summary':
        if options['format'] == 'json':
            sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
        else:
            sys.stdout.write(render_text(summary))
        return 0
    if command == 'write-summary':
        if not options['out_json']:
            fail('write-summary 缺少 --out-json')
        write_json(options['out_json'], summary)
        return 0
    fail(f'未知命令：{command}')


if __name__ == '__main__':
    raise SystemExit(main())
