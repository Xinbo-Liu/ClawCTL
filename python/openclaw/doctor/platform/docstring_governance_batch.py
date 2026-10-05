#!/usr/bin/env python3
"""一次读取并解析生产 Python 文件，同时执行两类 docstring 递进检查。"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

from openclaw.doctor.platform import docstring_governance, semantic_docstring_governance
from openclaw.lib.repo.layout import resolve_repo_root


ROOT_DIR = resolve_repo_root(Path(__file__))


def _parse_repo_prod_files(repo_root: Path) -> dict[Path, ast.Module]:
    """读取并解析生产范围内的 Python 文件，供两类检查共享。

    参数：
        repo_root（Path）：仓库根目录。

    返回：
        dict[Path, ast.Module]：绝对文件路径到已解析 AST 的稳定映射。
    """
    parsed: dict[Path, ast.Module] = {}
    for path in semantic_docstring_governance.iter_scope_files(
        repo_root,
        semantic_docstring_governance.SCOPE_REPO_PROD,
    ):
        relative = path.resolve().relative_to(repo_root.resolve()).as_posix()
        parsed[path.resolve()] = ast.parse(path.read_text(encoding='utf-8'), filename=relative)
    return parsed


def build_report(repo_root: Path = ROOT_DIR) -> dict[str, Any]:
    """复用单次文件读取和 AST 解析，分别生成平台覆盖与生产语义结果。

    参数：
        repo_root（Path）：仓库根目录。

    返回：
        dict[str, Any]：两个保留检查 ID 的状态、摘要、问题列表与总体状态。
    """
    parsed = _parse_repo_prod_files(repo_root)
    platform_parsed = {
        path: tree
        for path, tree in parsed.items()
        if path.resolve().is_relative_to((repo_root / 'python' / 'openclaw').resolve())
        and not docstring_governance._is_excluded(path, repo_root)
    }
    platform_report = docstring_governance.build_report_from_parsed_files(repo_root, platform_parsed)
    semantic_report = semantic_docstring_governance.build_report_from_parsed_files(
        repo_root,
        parsed,
        scope=semantic_docstring_governance.SCOPE_REPO_PROD,
    )
    platform_issues = docstring_governance.compare_with_baseline(
        platform_report,
        docstring_governance.load_baseline(),
    )
    semantic_issues = semantic_docstring_governance.compare_with_baseline(
        semantic_report,
        semantic_docstring_governance.load_baseline(),
    )
    checks = [
        {
            'id': 'platform_docstring_governance',
            'status': 'fail' if platform_issues else 'ok',
            'mode': 'ratchet',
            'summary': platform_report['summary'],
            'issues': platform_issues,
            'issueGroups': docstring_governance.issue_groups(platform_issues),
        },
        {
            'id': 'repo_prod_semantic_docstring_governance',
            'status': 'fail' if semantic_issues else 'ok',
            'mode': 'ratchet',
            'scope': semantic_docstring_governance.SCOPE_REPO_PROD,
            'summary': semantic_report['summary'],
            'issues': semantic_issues[:1000],
            'issueGroups': semantic_docstring_governance.issue_groups(semantic_issues),
        },
    ]
    return {
        'suite': 'docstring_governance',
        'status': 'fail' if platform_issues or semantic_issues else 'ok',
        'checks': checks,
    }


def main(argv: list[str] | None = None) -> int:
    """输出共享扫描报告；当前入口不接受额外参数。

    参数：
        argv（list[str] | None）：显式参数；为 ``None`` 时读取进程参数。

    返回：
        int：两个递进检查均通过时为 0，否则为 1。

    副作用：
        将共享扫描的 JSON 报告写入标准输出；参数错误写入标准错误。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        print(f'[docstring_governance][FAIL] 未知参数：{" ".join(args)}', file=sys.stderr)
        return 2
    payload = build_report()
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload['status'] == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
