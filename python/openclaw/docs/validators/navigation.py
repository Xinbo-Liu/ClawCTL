#!/usr/bin/env python3
"""检查导航/路由页面是否具备最小任务分流结构。"""
from __future__ import annotations

import re
import sys

from openclaw.lib.cli.output import stdout_write, stderr_write
from pathlib import Path
from typing import Any

from openclaw.docs.support.docs_registry import ROOT_DIR, load_registry, require_pages

LINK_PATTERN = re.compile(r'\[[^\]]+\]\(([^)]+)\)')


def usage() -> str:
    return '\n'.join([
        '用法：',
        '  bash ./scripts/docs/check_documentation_navigation.sh',
        '  bash ./scripts/docs/check_documentation_navigation.sh --stdout',
        '',
        '说明：',
        '  校验导航页是否包含最小任务分流结构；避免目录页重新变成无序长索引。',
    ])


def link_count(content: str) -> int:
    return len(LINK_PATTERN.findall(content))


def check_page(page: dict[str, Any]) -> tuple[Path, list[str]]:
    rel_path = str(page['path'])
    file_path = ROOT_DIR / rel_path
    errors: list[str] = []
    contract = page.get('navigationContract')
    if not isinstance(contract, dict):
        return file_path, errors
    if not file_path.exists():
        return file_path, [f'{rel_path} 不存在']
    content = file_path.read_text(encoding='utf-8')
    for token in contract.get('requiredTokens') or []:
        if str(token) not in content:
            errors.append(f'{rel_path} 缺少导航区块：{token}')
    min_links = int(contract.get('minLinks') or 0)
    link_total = link_count(content)
    if min_links and link_total < min_links:
        errors.append(f'{rel_path} 有效链接数量不足：需要至少 {min_links} 个，当前 {link_total} 个')
    return file_path, errors


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
            stderr_write(f'[check_documentation_navigation][FAIL] 未知参数：{arg}\n')
            stderr_write(f'{usage()}\n')
            return 2
    try:
        registry = load_registry()
        pages = [page for page in require_pages(registry) if isinstance(page.get('navigationContract'), dict)]
    except Exception as exc:
        stderr_write(f'[check_documentation_navigation][FAIL] {exc}\n')
        return 1
    results = [check_page(page) for page in pages]
    errors = [error for _, item_errors in results for error in item_errors]
    if stdout:
        stdout_write(f'[check_documentation_navigation] count={len(pages)}\n')
        for file_path, item_errors in results:
            stdout_write(f'- {file_path.relative_to(ROOT_DIR)} errors={len(item_errors)}\n')
    if errors:
        stderr_write('[check_documentation_navigation] 导航结构校验失败：\n')
        for error in errors:
            stderr_write(f'- {error}\n')
        return 1
    stdout_write('[check_documentation_navigation] 已通过\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
