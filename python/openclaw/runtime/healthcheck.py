#!/usr/bin/env python3
"""运行面 healthcheck 统一入口。"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from openclaw.lib.io.json_access import json_object


def fail(message: str, exit_code: int = 1) -> int:
    """输出运行态 healthcheck 的统一失败消息并退出。"""
    sys.stderr.write(f"[runtime_healthcheck][FAIL] {message}\n")
    return exit_code


def command_http_ready(argv: list[str]) -> int:
    """执行 HTTP ready 检查。"""
    parser = argparse.ArgumentParser(prog='runtime healthcheck http-ready', description='校验 HTTP 健康接口全部返回 200。')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--path', dest='paths', action='append', required=True)
    parser.add_argument('--timeout-seconds', type=float, default=5.0)
    args = parser.parse_args(argv)

    for item in args.paths:
        url = f'http://{args.host}:{args.port}{item}'
        try:
            with urllib.request.urlopen(url, timeout=args.timeout_seconds) as response:
                if int(response.status) != 200:
                    return fail(f'{item} status {response.status}')
        except urllib.error.URLError as error:
            return fail(f'{item} request failed: {error}')
    return 0


def load_dispatch_contract(preflight_path: str, status_path: str) -> tuple[dict, dict]:
    """加载 dispatch contract。"""
    preflight = json.loads(Path(preflight_path).read_text(encoding='utf-8'))
    status = json.loads(Path(status_path).read_text(encoding='utf-8'))
    return preflight, status


def build_dispatch_summary(preflight: dict, status: dict) -> dict:
    """构建 dispatch 运行摘要。"""
    preflight_validation = json_object(preflight.get('validation'))
    validation = json_object(status.get('validation'))
    formal = json_object(status.get('formal_dispatch'))
    blocking_issue = json_object(preflight.get('blocking_issue') or preflight_validation.get('blocking_issue'))
    preflight_status = str(preflight.get('status') or '').lower()
    preflight_ready = (
        preflight.get('ok') is True
        or preflight.get('ready') is True
        or (preflight_status in {'ok', 'ready'} and preflight_validation.get('ok') is not False)
    )
    return {
        'preflight_ok': preflight_ready,
        'dispatch_ready': preflight.get('dispatch_ready') is True or preflight.get('ready') is True,
        'validation_ok': validation.get('ok') is True,
        'formal_dispatch_ready': formal.get('ready') is True,
        'formal_dispatch_status': formal.get('status'),
        'queue_total': int(formal.get('queue_total_count') or 0),
        'queue_due': int(formal.get('queue_due_count') or 0),
        'latest_run_id': formal.get('latest_run_id'),
        'current_push_run_id': formal.get('current_push_run_id'),
        'missing_target_count': int(formal.get('missing_target_count') or 0),
        'failed_target_count': int(formal.get('failed_count') or 0),
        'blocking_issue_code': (blocking_issue.get('code') or blocking_issue.get('reason') or formal.get('primary_issue')),
    }


def command_dispatch_json(argv: list[str]) -> int:
    """输出 dispatch JSON 视图。"""
    parser = argparse.ArgumentParser(prog='runtime healthcheck dispatch-json', description='校验 dispatcher healthcheck 生成的 JSON 摘要。')
    parser.add_argument('--preflight', required=True)
    parser.add_argument('--status', required=True)
    args = parser.parse_args(argv)

    preflight, status = load_dispatch_contract(args.preflight, args.status)
    preflight_validation = json_object(preflight.get('validation'))
    target_summary = json_object(preflight.get('target_summary'))
    validation = json_object(status.get('validation'))
    formal_dispatch = json_object(status.get('formal_dispatch'))
    paths = json_object(preflight.get('paths'))
    preflight_ready_known = (
        isinstance(preflight.get('dispatch_ready'), bool)
        or isinstance(preflight.get('ready'), bool)
        or str(preflight.get('status') or '').lower() in {'ok', 'ready', 'blocked', 'failed'}
    )
    preflight_target_surface_present = bool(paths.get('targets_path')) or bool(target_summary) or isinstance(preflight.get('runtime_state'), dict)
    ok = (
        preflight.get('stage') == 'dispatch'
        and preflight_ready_known
        and preflight_target_surface_present
        and (not preflight_validation or isinstance(preflight_validation.get('ok'), bool))
        and isinstance(validation.get('ok'), bool)
        and isinstance(formal_dispatch.get('ready'), bool)
        and isinstance(formal_dispatch.get('issues'), list)
        and isinstance(formal_dispatch.get('queue_total_count'), int)
        and isinstance(formal_dispatch.get('queue_due_count'), int)
        and isinstance(formal_dispatch.get('missing_target_count'), int)
        and isinstance(formal_dispatch.get('failed_count'), int)
    )
    return 0 if ok else fail('dispatcher preflight/status contract invalid')


def command_dispatch_summary(argv: list[str]) -> int:
    """输出 dispatch 摘要视图。"""
    parser = argparse.ArgumentParser(prog='runtime healthcheck dispatch-summary', description='读取 dispatcher preflight/status 并输出统一摘要。')
    parser.add_argument('--preflight', required=True)
    parser.add_argument('--status', required=True)
    args = parser.parse_args(argv)
    preflight, status = load_dispatch_contract(args.preflight, args.status)
    if command_dispatch_json(['--preflight', args.preflight, '--status', args.status]) != 0:
        return 1
    sys.stdout.write(json.dumps(build_dispatch_summary(preflight, status), ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    """运行态 healthcheck 命令入口。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return fail('缺少子命令；当前支持 http-ready / dispatch-json / dispatch-summary', 2)
    command = args.pop(0)
    if command == 'http-ready':
        return command_http_ready(args)
    if command == 'dispatch-json':
        return command_dispatch_json(args)
    if command == 'dispatch-summary':
        return command_dispatch_summary(args)
    return fail(f'未知子命令：{command}', 2)


if __name__ == '__main__':
    raise SystemExit(main())
