#!/usr/bin/env python3
"""CLI helpers for the full-test surface control plane."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from openclaw.lib.testing.full_test.acceptance import (
    build_acceptance_status,
    check_catalog,
    execution_order,
    normalize_check_csv,
    parse_bool,
    render_acceptance_kv_lines,
    render_acceptance_shell,
    selectable_groups,
    validate_group_name,
    write_acceptance_state,
)
from openclaw.lib.testing.full_test.io import fail, read_json, read_manifest, read_surface
from openclaw.lib.testing.full_test.render import render_doc, render_text, write_summary


def usage() -> str:
    surface = read_surface()
    commands = [str(item).strip() for item in (surface.get('control_plane_commands') or []) if str(item).strip()]
    lines = ['用法：']
    lines.extend([f'  {item}' for item in commands])
    lines.append('')
    return '\n'.join(lines)


def parse_args(argv: list[str]) -> dict[str, Any]:
    opts: dict[str, Any] = {
        'format': 'text',
        'summaryJson': '',
        'outJson': '',
        'outMd': '',
        'generatedAt': '',
        'envFile': '',
        'group': 'all',
        'only': '',
        'skip': '',
        'strict': False,
        'quiet': False,
        'jsonStdout': False,
        'returnCode': 0,
        'resultLinesFile': '',
        'nextActionsFile': '',
        'acceptanceState': '',
        'requiredAcceptanceIds': '',
        'csv': '',
        'flagName': '',
        'groupName': '',
    }
    index = 0
    while index < len(argv):
        arg = argv[index]
        if not arg.startswith('--'):
            fail(f'未知参数：{arg}')
        if arg in {'--group-name', '--value'}:
            if index + 1 >= len(argv):
                fail(f'{arg} 缺少参数值')
            opts['groupName'] = argv[index + 1]
            index += 2
            continue
        if index + 1 >= len(argv):
            fail(f'{arg} 缺少参数值')
        value = argv[index + 1]
        if arg == '--format':
            opts['format'] = value
        elif arg == '--summary-json':
            opts['summaryJson'] = str(Path(value).resolve())
        elif arg == '--out-json':
            opts['outJson'] = str(Path(value).resolve())
        elif arg == '--out-md':
            opts['outMd'] = str(Path(value).resolve())
        elif arg == '--generated-at':
            opts['generatedAt'] = value
        elif arg == '--env-file':
            opts['envFile'] = value
        elif arg == '--group':
            opts['group'] = value
        elif arg == '--only':
            opts['only'] = value
        elif arg == '--skip':
            opts['skip'] = value
        elif arg == '--strict':
            opts['strict'] = parse_bool(value, '--strict')
        elif arg == '--quiet':
            opts['quiet'] = parse_bool(value, '--quiet')
        elif arg == '--json-stdout':
            opts['jsonStdout'] = parse_bool(value, '--json-stdout')
        elif arg == '--return-code':
            opts['returnCode'] = int(value)
        elif arg == '--result-lines-file':
            opts['resultLinesFile'] = str(Path(value).resolve())
        elif arg == '--next-actions-file':
            opts['nextActionsFile'] = str(Path(value).resolve())
        elif arg == '--acceptance-state':
            opts['acceptanceState'] = str(Path(value).resolve())
        elif arg == '--required-acceptance-ids':
            opts['requiredAcceptanceIds'] = value
        elif arg == '--csv':
            opts['csv'] = value
        elif arg == '--flag':
            opts['flagName'] = value
        else:
            fail(f'未知参数：{arg}')
        index += 2
    return opts


def write_scalar_list(items: list[str], fmt: str) -> None:
    if fmt == 'json':
        sys.stdout.write(json.dumps(items, ensure_ascii=False, indent=2) + '\n')
        return
    if fmt == 'csv':
        sys.stdout.write(','.join(items) + '\n')
        return
    sys.stdout.write('\n'.join(items))
    if items:
        sys.stdout.write('\n')


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {'-h', '--help'}:
        sys.stdout.write(usage())
        return 0
    command = args.pop(0)
    options = parse_args(args)
    if command == 'doc':
        sys.stdout.write(render_doc())
        return 0
    if command == 'json':
        sys.stdout.write(json.dumps(read_manifest(), ensure_ascii=False, indent=2) + '\n')
        return 0
    if command == 'groups':
        write_scalar_list(selectable_groups(read_manifest()), options['format'])
        return 0
    if command == 'group-order':
        write_scalar_list(execution_order(read_manifest(), options['groupName'] or options['group']), options['format'])
        return 0
    if command == 'check-ids':
        write_scalar_list([item['id'] for item in check_catalog(read_manifest())], options['format'])
        return 0
    if command == 'normalize-check-csv':
        sys.stdout.write(normalize_check_csv(options['csv'], options['flagName'] or '--csv', read_manifest()) + '\n')
        return 0
    if command == 'validate-group':
        group_name = options['groupName'] or options['group']
        validate_group_name(group_name, read_manifest())
        sys.stdout.write(group_name + '\n')
        return 0
    if command == 'acceptance-status':
        status = build_acceptance_status(options, read_manifest())
        if options['format'] == 'json':
            sys.stdout.write(json.dumps(status, ensure_ascii=False, indent=2) + '\n')
        elif options['format'] == 'shell':
            sys.stdout.write(render_acceptance_shell(status) + '\n')
        elif options['format'] == 'kv-lines':
            sys.stdout.write(render_acceptance_kv_lines(status) + '\n')
        else:
            sys.stdout.write(f"eligible={status['eligible']} accepted={status['accepted']} contract_status={status['contract']['status']}\n")
        return 0
    if command == 'write-acceptance-state':
        write_acceptance_state(options)
        return 0
    if command == 'write-summary':
        write_summary(options)
        return 0
    if command == 'print-summary':
        if not options['summaryJson']:
            fail('print-summary 缺少 --summary-json')
        summary = read_json(options['summaryJson'])
        if options['format'] == 'json':
            sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
        else:
            sys.stdout.write(render_text(summary))
        return 0
    fail(f'未知命令：{command}')
