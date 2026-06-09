#!/usr/bin/env python3
"""部署后验收与证据控制面。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from openclaw.lib.testing.acceptance.render import render_acceptance_summary_text, render_doc, usage
from openclaw.lib.testing.acceptance.state import (
    ROOT_DIR,
    fail,
    parse_bool,
    parse_kv_args,
    read_manifest,
    read_surface,
    write_deployment_acceptance_state,
)
from openclaw.lib.testing.acceptance.summary import (
    build_acceptance_summary,
    build_official_cli_summary,
    build_runtime_acceptance_summary,
    build_runtime_evidence_status,
    write_official_cli_summary,
    write_runtime_acceptance_summary,
)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {'-h', '--help'}:
        sys.stdout.write(usage())
        return 0
    command = args.pop(0)
    if command == 'doc':
        if args:
            fail(f'doc 不接受参数：{" ".join(args)}')
        sys.stdout.write(render_doc())
        return 0
    if command == 'json':
        if args:
            fail(f'json 不接受参数：{" ".join(args)}')
        sys.stdout.write(json.dumps(read_manifest(), ensure_ascii=False, indent=2) + '\n')
        return 0
    if command == 'required-checks':
        opts = parse_kv_args(args)
        if '-h' in opts or '--help' in opts:
            sys.stdout.write(usage())
            return 0
        fmt = opts.get('--format', 'lines')
        required_checks = read_manifest().get('required_checks') or []
        if fmt == 'lines':
            sys.stdout.write('\n'.join(required_checks) + ('\n' if required_checks else ''))
            return 0
        if fmt == 'csv':
            sys.stdout.write(','.join(required_checks) + '\n')
            return 0
        if fmt == 'json':
            sys.stdout.write(json.dumps(required_checks, ensure_ascii=False, indent=2) + '\n')
            return 0
        fail(f'未知 --format：{fmt}')
    if command == 'acceptance-summary':
        opts = parse_kv_args(args)
        base_root = Path(opts.get('--root', str(ROOT_DIR))).resolve()
        summary = build_acceptance_summary(base_root)
        fmt = opts.get('--format', 'text')
        if fmt == 'json':
            sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
            return 0
        sys.stdout.write(render_acceptance_summary_text(summary))
        return 0
    if command == 'write-deployment-acceptance-state':
        opts = parse_kv_args(args)
        required = ['--out', '--generated-at', '--env-file', '--eligible', '--accepted', '--required-checks']
        missing = [key for key in required if key not in opts]
        if missing:
            fail(f'write-deployment-acceptance-state 缺少参数：{", ".join(missing)}')
        write_deployment_acceptance_state(
            out=opts['--out'],
            generated_at=opts['--generated-at'],
            suite=opts.get('--suite', 'default'),
            env_file=opts['--env-file'],
            eligible=parse_bool(opts['--eligible'], '--eligible'),
            accepted=parse_bool(opts['--accepted'], '--accepted'),
            required_checks=opts['--required-checks'],
        )
        return 0
    if command == 'write-official-cli-summary':
        opts = parse_kv_args(args)
        required = ['--official-dir', '--out', '--target']
        missing = [key for key in required if key not in opts]
        if missing:
            fail(f"write-official-cli-summary 缺少参数：{', '.join(missing)}")
        write_official_cli_summary(official_dir=opts['--official-dir'], out=opts['--out'], target=opts['--target'])
        return 0
    if command == 'write-runtime-acceptance-summary':
        opts = parse_kv_args(args)
        required = ['--acceptance-state', '--control-plane-summary', '--out']
        missing = [key for key in required if key not in opts]
        if missing:
            fail(f"write-runtime-acceptance-summary 缺少参数：{', '.join(missing)}")
        write_runtime_acceptance_summary(
            acceptance_state=opts['--acceptance-state'],
            control_plane_summary=opts['--control-plane-summary'],
            control_plane_run_ledger=opts.get('--control-plane-run-ledger'),
            control_plane_runtime_summary=opts.get('--control-plane-runtime-summary'),
            out=opts['--out'],
        )
        return 0
    fail(f'未知命令：{command}')


if __name__ == '__main__':
    raise SystemExit(main())
