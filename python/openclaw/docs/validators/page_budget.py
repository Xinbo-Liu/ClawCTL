#!/usr/bin/env python3
"""检查任务页/导航页是否满足页面预算。"""
from __future__ import annotations

import sys

from openclaw.lib.cli.output import stdout_write, stderr_write
from typing import Any

from openclaw.docs.support.docs_registry import ROOT_DIR, load_registry, require_pages


def usage() -> str:
    return '\n'.join([
        '用法：',
        '  bash ./scripts/docs/check_documentation_page_budget.sh',
        '  bash ./scripts/docs/check_documentation_page_budget.sh --stdout',
        '',
        '说明：',
        '  校验 task 页必须声明 pageBudget，且所有声明 pageBudget 的页面仍处于预算内，避免入口页再次膨胀。',
    ])


def line_count(text: str) -> int:
    return len(text.splitlines())


def check_page(page: dict[str, Any]) -> list[str]:
    rel_path = str(page['path'])
    budget = page.get('pageBudget')
    if str(page.get('role') or '').strip() == 'task' and not isinstance(budget, dict):
        return [f"{rel_path} 作为任务页必须声明 pageBudget"]
    if not isinstance(budget, dict):
        return []
    file_path = ROOT_DIR / rel_path
    if not file_path.exists():
        return [f'{rel_path} 不存在']
    max_lines = int(budget.get('maxLines') or 0)
    if max_lines <= 0:
        return []
    current = line_count(file_path.read_text(encoding='utf-8'))
    if current > max_lines:
        return [f'{rel_path} 超出页面预算：允许最多 {max_lines} 行，当前 {current} 行']
    return []


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
            stderr_write(f'[check_documentation_page_budget][FAIL] 未知参数：{arg}\n')
            stderr_write(f'{usage()}\n')
            return 2
    try:
        registry = load_registry()
        pages = [page for page in require_pages(registry) if isinstance(page.get('pageBudget'), dict) or str(page.get('role') or '').strip() == 'task']
    except Exception as exc:
        stderr_write(f'[check_documentation_page_budget][FAIL] {exc}\n')
        return 1
    errors: list[str] = []
    for page in pages:
        errors.extend(check_page(page))
    if stdout:
        stdout_write(f'[check_documentation_page_budget] count={len(pages)}\n')
        for page in pages:
            stdout_write(f"- {page['path']} maxLines={page['pageBudget'].get('maxLines')}\n")
    if errors:
        stderr_write('[check_documentation_page_budget] 页面预算校验失败：\n')
        for error in errors:
            stderr_write(f'- {error}\n')
        return 1
    stdout_write('[check_documentation_page_budget] 已通过\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
