#!/usr/bin/env python3
"""setup / full test 失败分流控制面。"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, NoReturn

from openclaw.control_plane.governance_surfaces import load_setup_failures_surface
from openclaw.lib.cli.examples import canonical_cli_command, usage_block
from openclaw.lib.repo.layout import resolve_repo_root

ROOT_DIR = resolve_repo_root(Path(__file__))


def fail(message: str, code: int = 2) -> NoReturn:
    sys.stderr.write(f'[setup_failure_surface][FAIL] {message}\n')
    raise SystemExit(code)


def load_config() -> dict[str, Any]:
    payload = load_setup_failures_surface()
    if not isinstance(payload, dict):
        fail('governance/docs/setup_failures.json 顶层必须为对象')
    return payload


def entries() -> dict[str, dict[str, Any]]:
    raw = load_config().get('entries') or {}
    if not isinstance(raw, dict):
        fail('entries 必须为对象')
    return {str(key): value for key, value in raw.items() if isinstance(value, dict)}


def entry_info(entry_id: str) -> dict[str, Any]:
    info = entries().get(entry_id)
    if info is None:
        fail(f'未知 entry_id：{entry_id}')
    return info


def scenarios(entry_id: str) -> dict[str, dict[str, Any]]:
    raw = entry_info(entry_id).get('scenarios') or {}
    if not isinstance(raw, dict):
        fail(f'{entry_id}.scenarios 必须为对象')
    return {str(key): value for key, value in raw.items() if isinstance(value, dict)}


def scenario_info(entry_id: str, scenario_id: str) -> dict[str, Any]:
    info = scenarios(entry_id).get(scenario_id)
    if info is None:
        fail(f'{entry_id} 缺少 scenario：{scenario_id}')
    return info


def list_str(info: dict[str, Any], key: str) -> list[str]:
    value = info.get(key) or []
    if not isinstance(value, list):
        fail(f'{key} 必须为数组')
    return [str(item).strip() for item in value if str(item).strip()]


def parse_args(argv: list[str]) -> tuple[str, dict[str, Any]]:
    if not argv or argv[0] in {'-h', '--help'}:
        return 'help', {}
    command = argv[0]
    opts: dict[str, Any] = {'entry': '', 'scenario': '', 'format': 'text'}
    index = 1
    while index < len(argv):
        arg = argv[index]
        if arg in {'-h', '--help'}:
            return 'help', {}
        if arg == '--entry':
            index += 1
            if index >= len(argv):
                fail('--entry 缺少参数值')
            opts['entry'] = argv[index]
        elif arg == '--scenario':
            index += 1
            if index >= len(argv):
                fail('--scenario 缺少参数值')
            opts['scenario'] = argv[index]
        elif arg == '--format':
            index += 1
            if index >= len(argv):
                fail('--format 缺少参数值')
            opts['format'] = argv[index]
        else:
            fail(f'未知参数：{arg}')
        index += 1
    return command, opts


def usage() -> str:
    return usage_block(
        canonical_cli_command('setup', 'surface', 'failures', 'show-index'),
        canonical_cli_command('setup', 'surface', 'failures', 'show-entry') + ' --entry <id>',
        canonical_cli_command('setup', 'surface', 'failures', 'show-next-steps') + ' --entry <id> --scenario <scenario_id>',
        canonical_cli_command('setup', 'surface', 'failures', 'show-next-steps') + ' --entry <id> --scenario <scenario_id> --format json',
        title='用法：',
    )


def render_index() -> str:
    lines = [str(load_config().get('title') or 'setup / full test 失败分流参考').strip(), '']
    for entry_id, info in entries().items():
        lines.append(f'- {entry_id}: {str(info.get("title") or entry_id).strip()}')
        for scenario_id, scenario in scenarios(entry_id).items():
            lines.append(f'  - {scenario_id}: {str(scenario.get("title") or scenario_id).strip()}')
    return '\n'.join(lines)


def render_entry(entry_id: str) -> str:
    info = entry_info(entry_id)
    lines = [
        f'id: {entry_id}',
        f'title: {str(info.get("title") or entry_id).strip()}',
        f'command: {str(info.get("command") or "").strip()}',
        'scenarios:',
    ]
    for scenario_id, scenario in scenarios(entry_id).items():
        lines.append(f'  - {scenario_id}: {str(scenario.get("title") or scenario_id).strip()}')
    return '\n'.join(lines)


def render_next_steps(entry_id: str, scenario_id: str) -> str:
    scenario = scenario_info(entry_id, scenario_id)
    return '\n'.join(list_str(scenario, 'commands'))


def render_next_steps_json(entry_id: str, scenario_id: str) -> str:
    entry = entry_info(entry_id)
    scenario = scenario_info(entry_id, scenario_id)
    payload = {
        'entry_id': entry_id,
        'entry_title': str(entry.get('title') or entry_id).strip(),
        'scenario_id': scenario_id,
        'scenario_title': str(scenario.get('title') or scenario_id).strip(),
        'when': str(scenario.get('when') or '').strip(),
        'commands': list_str(scenario, 'commands'),
        'notes': list_str(scenario, 'notes'),
        'references': list_str(scenario, 'references'),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> int:
    command, opts = parse_args(list(sys.argv[1:] if argv is None else argv))
    if command == 'help':
        sys.stdout.write(usage())
        return 0
    if command == 'show-index':
        sys.stdout.write(render_index() + '\n')
        return 0
    if command == 'show-entry':
        if not opts['entry']:
            fail('show-entry 缺少 --entry')
        sys.stdout.write(render_entry(opts['entry']) + '\n')
        return 0
    if command == 'show-next-steps':
        if not opts['entry']:
            fail('show-next-steps 缺少 --entry')
        if not opts['scenario']:
            fail('show-next-steps 缺少 --scenario')
        if opts['format'] == 'json':
            sys.stdout.write(render_next_steps_json(opts['entry'], opts['scenario']) + '\n')
            return 0
        if opts['format'] != 'text':
            fail(f'不支持的 format：{opts["format"]}')
        sys.stdout.write(render_next_steps(opts['entry'], opts['scenario']) + '\n')
        return 0
    fail(f'未知子命令：{command}')

if __name__ == '__main__':
    raise SystemExit(main())
