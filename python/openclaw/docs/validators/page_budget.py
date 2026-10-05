#!/usr/bin/env python3
"""检查任务页/导航页是否满足页面预算。"""
from __future__ import annotations

import sys

from openclaw.lib.cli.output import stdout_write, stderr_write
from typing import Any

from openclaw.docs.support.docs_registry import ROOT_DIR, require_pages
from openclaw.docs.validators.registry_context import load_validator_context
from openclaw.docs.support.shared_cache import read_text


def usage() -> str:
    return '\n'.join([
        '用法：',
        '  bash ./scripts/docs/check_documentation_page_budget.sh',
        '  bash ./scripts/docs/check_documentation_page_budget.sh --stdout',
        '  bash ./scripts/docs/check_documentation_page_budget.sh --config-path <control-plane-config-path>',
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
    current = line_count(read_text(file_path))
    if current > max_lines:
        return [f'{rel_path} 超出页面预算：允许最多 {max_lines} 行，当前 {current} 行']
    return []


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    context = load_validator_context(
        args,
        usage_text=usage(),
        error_prefix='[check_documentation_page_budget][FAIL]',
        root_dir=ROOT_DIR,
    )
    if isinstance(context, int):
        return context
    try:
        pages = [
            page
            for page in require_pages(context.registry)
            if isinstance(page.get('pageBudget'), dict) or str(page.get('role') or '').strip() == 'task'
        ]
    except Exception as exc:
        stderr_write(f'[check_documentation_page_budget][FAIL] {exc}\n')
        return 1
    errors: list[str] = []
    for page in pages:
        errors.extend(check_page(page))
    if context.stdout:
        stdout_write(f'[check_documentation_page_budget] config={context.config_label} count={len(pages)}\n')
        for page in pages:
            budget = page.get('pageBudget') if isinstance(page.get('pageBudget'), dict) else {}
            stdout_write(f"- {page['path']} maxLines={budget.get('maxLines')}\n")
    if errors:
        stderr_write('[check_documentation_page_budget] 页面预算校验失败：\n')
        for error in errors:
            stderr_write(f'- {error}\n')
        return 1
    stdout_write('[check_documentation_page_budget] 已通过\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
