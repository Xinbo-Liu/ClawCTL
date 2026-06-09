#!/usr/bin/env python3
"""检查局部说明页是否明确降级为非正式入口。"""
from __future__ import annotations

import sys

from openclaw.lib.cli.output import stdout_write, stderr_write
from pathlib import Path
from typing import Any

from openclaw.docs.support.docs_registry import ROOT_DIR, load_registry, require_pages


def usage() -> str:
    return '\n'.join([
        '用法：',
        '  bash ./scripts/docs/check_local_document_identity.sh',
        '  bash ./scripts/docs/check_local_document_identity.sh --stdout',
        '',
        '说明：',
        '  校验 workspace / tool 局部文档是否带有非正式入口声明并回链 docs/README.md。',
    ])


def check_page(page: dict[str, Any]) -> tuple[Path, list[str]]:
    rel_path = str(page['path'])
    file_path = ROOT_DIR / rel_path
    errors: list[str] = []
    contract = page.get('localIdentity')
    if not isinstance(contract, dict):
        return file_path, errors
    if not file_path.exists():
        return file_path, [f'{rel_path} 不存在']
    content = file_path.read_text(encoding='utf-8')
    for token in contract.get('requiredTokens') or []:
        if str(token) not in content:
            errors.append(f'{rel_path} 缺少局部文档声明：{token}')
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
            stderr_write(f'[check_local_document_identity][FAIL] 未知参数：{arg}\n')
            stderr_write(f'{usage()}\n')
            return 2
    try:
        registry = load_registry()
        pages = [page for page in require_pages(registry) if isinstance(page.get('localIdentity'), dict)]
    except Exception as exc:
        stderr_write(f'[check_local_document_identity][FAIL] {exc}\n')
        return 1
    results = [check_page(page) for page in pages]
    errors = [error for _, item_errors in results for error in item_errors]
    if stdout:
        stdout_write(f'[check_local_document_identity] count={len(pages)}\n')
        for file_path, item_errors in results:
            stdout_write(f'- {file_path.relative_to(ROOT_DIR)} errors={len(item_errors)}\n')
    if errors:
        stderr_write('[check_local_document_identity] 局部文档身份校验失败：\n')
        for error in errors:
            stderr_write(f'- {error}\n')
        return 1
    stdout_write('[check_local_document_identity] 已通过\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
