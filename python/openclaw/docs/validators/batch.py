#!/usr/bin/env python3
"""在单个进程中执行文档注册表与实现契约检查。"""
from __future__ import annotations

import io
import json
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from typing import Callable

from openclaw.docs.support import docs_registry
from openclaw.docs.validators import (
    boundaries,
    entrypoints,
    implementation_alignment,
    inventory,
    links,
    local_document_identity,
    navigation,
    object_closure,
    page_budget,
    task_structure,
)


CHECKS: tuple[tuple[str, Callable[[list[str] | None], int]], ...] = (
    ('docs_registry_sync', docs_registry.main),
    ('documentation_inventory', inventory.main),
    ('documentation_links', links.main),
    ('documentation_entrypoints', entrypoints.main),
    ('documentation_boundaries', boundaries.main),
    ('documentation_navigation', navigation.main),
    ('documentation_task_structure', task_structure.main),
    ('documentation_page_budget', page_budget.main),
    ('documentation_implementation_alignment', implementation_alignment.main),
    ('documentation_object_closure', object_closure.main),
    ('local_document_identity', local_document_identity.main),
)


def _run_check(check_id: str, entry: Callable[[list[str] | None], int]) -> dict[str, object]:
    """隔离捕获一个文档 validator 的输出和退出状态。

    参数：
        check_id（str）：对外保持稳定的文档检查标识。
        entry（Callable[[list[str] | None], int]）：validator 的 Python 入口。

    返回：
        dict[str, object]：状态、退出码、耗时和诊断文本组成的子检查结果。

    副作用：
        在调用期间重定向标准输出和标准错误，随后恢复原输出流。
    """
    started = time.monotonic()
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = int(entry([]))
    except SystemExit as exc:
        exit_code = int(exc.code or 0) if isinstance(exc.code, int) else 1
        if exc.code and not isinstance(exc.code, int):
            stderr.write(str(exc.code))
    except Exception as exc:
        # 单页读文件或配置解析失败属于子检查失败，不能中断其他诊断和 JSON 报告。
        exit_code = 1
        stderr.write(f'{type(exc).__name__}: {exc}\n')
    detail = '\n'.join(part.strip() for part in (stdout.getvalue(), stderr.getvalue()) if part.strip())
    return {
        'id': check_id,
        'status': 'PASS' if exit_code == 0 else 'FAIL',
        'exitCode': exit_code,
        'timedOut': False,
        'durationSeconds': round(time.monotonic() - started, 3),
        'detail': detail,
    }


def main(argv: list[str] | None = None) -> int:
    """在全仓注册表范围内执行文档检查，汇总各子检查状态和诊断为 JSON。

    参数：
        argv（list[str] | None）：当前入口不接受额外参数。

    返回：
        int：全部检查通过为 0，存在检查失败为 1，传入不支持的参数为 2。

    副作用：
        在标准输出写入单个 JSON 结果；参数错误写入标准错误。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        print(f'[documentation_validators][FAIL] 未知参数：{" ".join(args)}', file=sys.stderr)
        return 2
    with docs_registry.repository_registry_scope():
        checks = [_run_check(check_id, entry) for check_id, entry in CHECKS]
    payload = {
        'suite': 'documentation_validators',
        'status': 'PASS' if all(item['status'] == 'PASS' for item in checks) else 'FAIL',
        'checks': checks,
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
