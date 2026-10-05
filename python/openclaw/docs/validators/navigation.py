#!/usr/bin/env python3
"""检查导航页结构、正式入口闭环与本地 Markdown 链接。"""
from __future__ import annotations

import sys

from openclaw.lib.cli.output import stdout_write, stderr_write
from pathlib import Path
from typing import Any

from openclaw.docs.support.docs_registry import ROOT_DIR, require_pages
from openclaw.docs.support.markdown_links import local_link_errors, parse_markdown, resolve_local_link
from openclaw.docs.validators.registry_context import load_validator_context
from openclaw.docs.support.shared_cache import read_text
from openclaw.lib.repo.layout import relative_path_within_root

def usage() -> str:
    return '\n'.join([
        '用法：',
        '  bash ./scripts/docs/check_documentation_navigation.sh',
        '  bash ./scripts/docs/check_documentation_navigation.sh --stdout',
        '  bash ./scripts/docs/check_documentation_navigation.sh --config-path <control-plane-config-path>',
        '',
        '说明：',
        '  校验导航页是否包含最小任务分流结构；避免目录页重新变成无序长索引。',
    ])


def link_count(content: str) -> int:
    """统计真实 Markdown 链接与图片，排除代码示例中的伪链接。

    参数：
        content（str）：导航页面正文。
    返回：
        int：解析得到的实际链接数量。
    """
    return len(parse_markdown(content).links)


def _linked_local_paths(file_path: Path, content: str, *, root_dir: Path) -> set[str]:
    """收集导航页中已存在的仓库内本地链接。

    参数：
        file_path（Path）：当前 Markdown 文件路径，作为相对链接解析基准。
        content（str）：当前 Markdown 文件正文。
        root_dir（Path）：仓库根目录边界。

    返回：
        set[str]：返回已存在链接目标的仓库相对路径集合；缺失目标和外部链接不进入集合。
    """
    paths: set[str] = set()
    for link in parse_markdown(content).links:
        if not link.target or link.target.startswith('#'):
            continue
        try:
            resolved, _ = resolve_local_link(file_path, link.target, root_dir=root_dir)
        except ValueError:
            continue
        if resolved is None or not resolved.exists():
            continue
        paths.add(relative_path_within_root(resolved, root_dir).as_posix())
    return paths


def check_page(page: dict[str, Any], *, root_dir: Path = ROOT_DIR) -> tuple[Path, list[str]]:
    """校验单个导航页的区块、链接数量和本地链接目标。

    参数：
        page（dict[str, Any]）：docs registry 中的页面条目，读取 path 与 navigationContract。
        root_dir（Path）：仓库根目录，作为页面存在性和链接目标检查边界。

    返回：
        tuple[Path, list[str]]：返回页面绝对路径和该页面的中文错误列表；错误列表为空代表该页通过。
    """
    rel_path = str(page['path'])
    file_path = root_dir / rel_path
    errors: list[str] = []
    contract = page.get('navigationContract')
    if not isinstance(contract, dict):
        return file_path, errors
    if not file_path.exists():
        return file_path, [f'{rel_path} 不存在']
    content = read_text(file_path)
    for token in contract.get('requiredTokens') or []:
        if str(token) not in content:
            errors.append(f'{rel_path} 缺少导航区块：{token}')
    min_links = int(contract.get('minLinks') or 0)
    link_total = link_count(content)
    if min_links and link_total < min_links:
        errors.append(f'{rel_path} 有效链接数量不足：需要至少 {min_links} 个，当前 {link_total} 个')
    errors.extend(local_link_errors(file_path, content, root_dir=root_dir))
    return file_path, errors


def _navigation_links(rel_path: str, *, root_dir: Path) -> set[str]:
    """读取一个导航页实际指向的仓库内本地链接集合。

    参数：
        rel_path（str）：导航页仓库相对路径。
        root_dir（Path）：仓库根目录，作为页面读取和链接解析边界。

    返回：
        set[str]：返回该导航页中存在的本地链接目标集合；导航页不存在时返回空集合。
    """
    file_path = root_dir / rel_path
    if not file_path.is_file():
        return set()
    return _linked_local_paths(file_path, read_text(file_path), root_dir=root_dir)


def check_formal_entry_links(registry: dict[str, Any], *, root_dir: Path = ROOT_DIR) -> list[str]:
    """校验 L1 正式入口同时进入项目导航和所属目录导航。

    参数：
        registry（dict[str, Any]）：已合并的 docs registry，提供 formalEntry 与 entryLevel 规则。
        root_dir（Path）：仓库根目录，作为 docs/README.md 与目录 README 的链接解析边界。

    返回：
        list[str]：返回未闭合正式入口的中文错误列表；空列表代表所有 L1 正式页均可从导航抵达。

    副作用：
        读取 docs/README.md 和所属目录 README 的 Markdown 正文，不写入文件系统。
    """
    docs_readme_links = _navigation_links('docs/README.md', root_dir=root_dir)
    owner_cache: dict[str, set[str]] = {}
    errors: list[str] = []
    for page in require_pages(registry):
        rel_path = str(page.get('path') or '').strip()
        if (
            not rel_path
            or page.get('localOnly') is True
            or page.get('formalEntry') is not True
            or str(page.get('entryLevel') or '') != 'L1'
        ):
            continue
        if not rel_path.startswith('docs/'):
            continue
        if rel_path not in docs_readme_links:
            errors.append(f'{rel_path} 是 L1 正式入口，但 docs/README.md 未链接该页')
        owner_readme = str(Path(rel_path).parent / 'README.md').replace('\\', '/')
        if owner_readme not in owner_cache:
            owner_cache[owner_readme] = _navigation_links(owner_readme, root_dir=root_dir)
        if owner_readme != 'docs/README.md' and rel_path not in owner_cache[owner_readme]:
            errors.append(f'{rel_path} 是 L1 正式入口，但 {owner_readme} 未链接该页')
    return errors


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    context = load_validator_context(
        args,
        usage_text=usage(),
        error_prefix='[check_documentation_navigation][FAIL]',
        root_dir=ROOT_DIR,
    )
    if isinstance(context, int):
        return context
    try:
        pages = [page for page in require_pages(context.registry) if isinstance(page.get('navigationContract'), dict)]
    except Exception as exc:
        stderr_write(f'[check_documentation_navigation][FAIL] {exc}\n')
        return 1
    results = [check_page(page) for page in pages]
    errors = [error for _, item_errors in results for error in item_errors]
    errors.extend(check_formal_entry_links(context.registry))
    if context.stdout:
        stdout_write(f'[check_documentation_navigation] config={context.config_label} count={len(pages)}\n')
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
