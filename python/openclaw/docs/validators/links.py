"""检查注册表内全部文档的真实本地链接、图片与 Markdown 锚点。"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from openclaw.docs.support.docs_registry import ROOT_DIR, require_pages, repository_registry_scope
from openclaw.docs.support.markdown_links import local_link_errors
from openclaw.docs.validators.registry_context import load_validator_context
from openclaw.docs.support.shared_cache import read_text
from openclaw.lib.cli.output import stderr_write, stdout_write


def check_pages(registry: dict[str, Any], *, root_dir: Path) -> list[str]:
    """检查登记页本地链接，所有读取均限制在当前仓库根目录。

    参数：
        registry（dict[str, Any]）：当前检查范围的唯一文档注册表。
        root_dir（Path）：文件与链接目标允许访问的仓库根目录。
    返回：
        list[str]：登记页缺失或越界诊断包含文档路径，链接诊断另含一基行号。
    """
    root = Path(root_dir).resolve()
    errors: list[str] = []
    for page in require_pages(registry):
        path = (root / page['path']).resolve()
        if not path.is_relative_to(root):
            errors.append(f"登记页越过仓库边界：{page['path']}")
        elif not path.is_file():
            errors.append(f"登记页不存在：{page['path']}")
        else:
            errors.extend(local_link_errors(path, read_text(path), root_dir=root))
    return errors


def main(argv: list[str] | None = None) -> int:
    """执行本地文档链接检查并输出可定位的诊断。

    参数：
        argv（list[str] | None）：支持 --stdout 与 --config-path。
    返回：
        int：通过为 0，链接或读文件失败为 1，参数错误为 2。
    副作用：
        只读仓内 Markdown 与图片存在性，不请求外部 URL。
    """
    with repository_registry_scope():
        context = load_validator_context(list(sys.argv[1:] if argv is None else argv), usage_text='用法：文档 links [--stdout] [--config-path <service>]', error_prefix='[documentation_links][FAIL]', root_dir=ROOT_DIR)
    if isinstance(context, int):
        return context
    try:
        errors = check_pages(context.registry, root_dir=ROOT_DIR)
    except (Exception, SystemExit) as exc:
        stderr_write(f'[documentation_links][FAIL] {exc}\n')
        return 1
    if context.stdout:
        stdout_write(f'[documentation_links] pages={len(require_pages(context.registry))}\n')
    for error in errors:
        stderr_write(f'[documentation_links][FAIL] {error}\n')
    if not errors:
        stdout_write('[documentation_links] 已通过\n')
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
