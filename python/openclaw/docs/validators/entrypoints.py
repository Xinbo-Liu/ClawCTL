#!/usr/bin/env python3
"""文档入口边界检查。"""
from __future__ import annotations

import sys

from openclaw.lib.cli.output import stdout_write, stderr_write
from pathlib import Path

from openclaw.lib.repo.layout import resolve_repo_root
from typing import Any

from openclaw.docs.support.docs_registry import REGISTRY_PATH, documentation_entrypoint_entries
from openclaw.docs.support.text_contracts import check_text_contract
from openclaw.docs.validators.registry_context import load_validator_context
from openclaw.docs.support.shared_cache import read_text

ROOT_DIR = resolve_repo_root(Path(__file__))


def usage() -> str:
    return '\n'.join([
        '用法：',
        '  bash ./scripts/docs/check_documentation_entrypoints.sh',
        '  bash ./scripts/docs/check_documentation_entrypoints.sh --stdout',
        '  bash ./scripts/docs/check_documentation_entrypoints.sh --config-path <control-plane-config-path>',
        '',
        '说明：',
        '  检查 entrypointContract 声明的必需文本与禁止文本。',
    ])


def check_entry(entry: dict[str, Any]) -> dict[str, Any]:
    rel_path = str(entry['path'])
    file_path = ROOT_DIR / rel_path
    errors: list[str] = []
    if not file_path.exists():
        errors.append(f'{rel_path} 不存在')
        return {'file_path': file_path, 'errors': errors}
    content = read_text(file_path)
    errors.extend(
        check_text_contract(
            rel_path=rel_path,
            content=content,
            contract=entry,
            missing_label='缺少必须文本',
            forbidden_label='出现禁止文本',
        )
    )
    return {'file_path': file_path, 'errors': errors}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    context = load_validator_context(
        args,
        usage_text=usage(),
        error_prefix='[check_documentation_entrypoints][FAIL]',
        root_dir=ROOT_DIR,
    )
    if isinstance(context, int):
        return context
    entries = documentation_entrypoint_entries(context.registry)
    results = [check_entry(entry) for entry in entries]
    errors = [error for item in results for error in item['errors']]
    if context.stdout:
        stdout_write(
            f'[check_documentation_entrypoints] registry={REGISTRY_PATH.relative_to(ROOT_DIR)} config={context.config_label} '
            f'entries={len(entries)}\n'
        )
        for item in results:
            stdout_write(f'- {item["file_path"].relative_to(ROOT_DIR)} errors={len(item["errors"])}\n')
    if errors:
        stderr_write('[check_documentation_entrypoints] 入口边界校验失败：\n')
        for error in errors:
            stderr_write(f'- {error}\n')
        return 1
    stdout_write('[check_documentation_entrypoints] 已通过\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
