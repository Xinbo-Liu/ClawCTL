#!/usr/bin/env python3
"""检查任务页是否具备固定任务模板区块。"""
from __future__ import annotations

import sys

from openclaw.lib.cli.output import stdout_write, stderr_write
from typing import Any

from openclaw.docs.support.docs_registry import ROOT_DIR, load_registry, require_pages


def usage() -> str:
    return '\n'.join([
        '用法：',
        '  bash ./scripts/docs/check_documentation_task_structure.sh',
        '  bash ./scripts/docs/check_documentation_task_structure.sh --stdout',
        '',
        '说明：',
        '  校验所有 role=task 的任务页都声明 taskContract，且具备固定区块，避免一级/二级任务页重新变成大而全说明书。',
    ])


def check_page(page: dict[str, Any]) -> list[str]:
    rel_path = str(page['path'])
    file_path = ROOT_DIR / rel_path
    if str(page.get('role') or '').strip() != 'task':
        return []
    contract = page.get('taskContract')
    if not isinstance(contract, dict):
        return [f"{rel_path} 作为任务页必须声明 taskContract"]
    if not file_path.exists():
        return [f'{rel_path} 不存在']
    content = file_path.read_text(encoding='utf-8')
    errors: list[str] = []
    for token in contract.get('requiredTokens') or []:
        token_text = str(token).strip()
        if token_text and token_text not in content:
            errors.append(f'{rel_path} 缺少任务页区块：{token_text}')
    return errors


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    stdout = False
    for arg in args:
        if arg == '--stdout':
            stdout = True
        elif arg in {'-h', '--help'}:
            stdout_write(f'{usage()}\n')
            return 0
        else:
            stderr_write(f'[check_documentation_task_structure][FAIL] 未知参数：{arg}\n')
            stderr_write(f'{usage()}\n')
            return 2
    try:
        registry = load_registry()
        pages = [page for page in require_pages(registry) if str(page.get('role') or '').strip() == 'task']
    except Exception as exc:
        stderr_write(f'[check_documentation_task_structure][FAIL] {exc}\n')
        return 1
    errors: list[str] = []
    for page in pages:
        errors.extend(check_page(page))
    if stdout:
        stdout_write(f'[check_documentation_task_structure] count={len(pages)}\n')
        for page in pages:
            stdout_write(f"- {page['path']}\n")
    if errors:
        stderr_write('[check_documentation_task_structure] 任务页结构校验失败：\n')
        for error in errors:
            stderr_write(f'- {error}\n')
        return 1
    stdout_write('[check_documentation_task_structure] 已通过\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
