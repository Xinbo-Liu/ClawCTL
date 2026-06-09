#!/usr/bin/env python3
"""文档职责边界检查器。"""
from __future__ import annotations

import sys

from openclaw.lib.cli.output import stdout_write, stderr_write
from pathlib import Path

from openclaw.lib.repo.layout import resolve_repo_root
from typing import Any

from openclaw.docs.support.docs_registry import REGISTRY_PATH, documentation_boundary_rules, load_registry
from openclaw.docs.support.text_contracts import check_text_contract

ROOT_DIR = resolve_repo_root(Path(__file__))


def usage() -> str:
    return '\n'.join([
        '用法：',
        '  bash ./scripts/docs/check_documentation_boundaries.sh',
        '  bash ./scripts/docs/check_documentation_boundaries.sh --stdout',
        '',
        '说明：',
        '  校验项目级入口、导航页与专题页是否越权重复维护不该出现的部署/验收/操作内容。',
    ])


def check_rule(rule: dict[str, Any]) -> dict[str, Any]:
    rel_path = str(rule['path'])
    file_path = ROOT_DIR / rel_path
    errors: list[str] = []
    if not file_path.exists():
        errors.append(f'{rel_path} 不存在')
        return {'file_path': file_path, 'errors': errors}
    content = file_path.read_text(encoding='utf-8')
    errors.extend(
        check_text_contract(
            rel_path=rel_path,
            content=content,
            contract=rule,
            missing_label='缺少必须文本',
            forbidden_label='出现越权文本',
        )
    )
    return {'file_path': file_path, 'errors': errors}


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
            stderr_write(f'[check_documentation_boundaries][FAIL] 未知参数：{arg}\n')
            stderr_write(f'{usage()}\n')
            return 2
    try:
        registry = load_registry()
    except Exception as exc:
        stderr_write(f'[check_documentation_boundaries][FAIL] {exc}\n')
        return 1
    rules = documentation_boundary_rules(registry)
    results = [check_rule(rule) for rule in rules]
    errors = [error for item in results for error in item['errors']]
    if stdout:
        stdout_write(f'[check_documentation_boundaries] registry={REGISTRY_PATH.relative_to(ROOT_DIR)} count={len(rules)}\n')
        for item in results:
            stdout_write(f'- {item["file_path"].relative_to(ROOT_DIR)} errors={len(item["errors"])}\n')
    if errors:
        stderr_write('[check_documentation_boundaries] 文档职责边界校验失败：\n')
        for error in errors:
            stderr_write(f'- {error}\n')
        return 1
    stdout_write('[check_documentation_boundaries] 已通过\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
