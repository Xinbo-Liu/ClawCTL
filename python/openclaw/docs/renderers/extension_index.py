"""生成仓内受管扩展的 README 导航索引。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from openclaw.docs.support.generated_output import add_output_modes, publish_document, read_document
from openclaw.docs.support.markdown_tables import format_markdown_tables
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.managed_extensions import managed_explicit_extensions

ROOT_DIR = resolve_repo_root(Path(__file__))
DOC_PATH = 'agent/extensions/README.md'
BEGIN_MARKER = '<!-- BEGIN managed-extension-index -->'
END_MARKER = '<!-- END managed-extension-index -->'


def render_section(*, root_dir: Path = ROOT_DIR) -> str:
    """列出受管扩展的标识、名称和包内 README。

    参数：
        root_dir（Path）：扩展登记与目录发现的仓库根目录。

    返回：
        str：按扩展 ID 排序的生成区块，包含唯一起止标记。

    异常：
        ManagedExtensionError：显式登记合同无效。
        ValueError：扩展 README 缺失、链接或越出扩展根目录。

    副作用：
        读取扩展登记与发现所需的仓库真源，并检查 README 文件类型；生成文本只在内存中构建。
    """
    rows = sorted(managed_explicit_extensions(root_dir), key=lambda row: row.id)
    lines = [BEGIN_MARKER, '', '| 扩展 ID | 名称 | 包内说明 |', '| --- | --- | --- |']
    for row in rows:
        readme = row.root_dir / 'README.md'
        relative = readme.relative_to(root_dir / 'agent/extensions')
        if not readme.is_file() or readme.is_symlink():
            raise ValueError(f'扩展 {row.id} 缺少普通 README.md，请先补齐扩展说明')
        title = row.title.replace('|', '\\|').replace('\n', ' ').replace('\r', '')
        lines.append(f'| `{row.id}` | {title} | [README]({relative.as_posix()}) |')
    if not rows:
        lines.extend(['', '当前仓库没有可列出的受管扩展。'])
    return format_markdown_tables('\n'.join([*lines, '', END_MARKER]))


def replace_section(content: str, section: str) -> str:
    """只替换唯一标记区块，完整保留人工维护的前后正文。

    参数：
        content（str）：原 README 的 UTF-8 文本。
        section（str）：包含起止标记的完整生成区块。

    返回：
        str：仅标记区块改变后的 README。

    异常：
        ValueError：标记缺失、重复、顺序错误或未独占一行。
    """
    if content.count(BEGIN_MARKER) != 1 or content.count(END_MARKER) != 1:
        raise ValueError('扩展索引起止标记必须各出现一次；请修复 README 标记后重新生成')
    start, end = content.index(BEGIN_MARKER), content.index(END_MARKER)
    if start >= end or BEGIN_MARKER not in content.splitlines() or END_MARKER not in content.splitlines():
        raise ValueError('扩展索引标记必须独占一行且起止顺序正确')
    return content[:start] + section + content[end + len(END_MARKER):]


def render_entry(argv: list[str] | None = None) -> int:
    """按模式生成或检查扩展索引，不改写标记外的 README 正文。

    参数：
        argv（list[str] | None）：显式命令参数；为空时使用进程参数。

    返回：
        int：成功为 0，生成漂移为 1，输入或安全校验失败为 2。

    异常：
        SystemExit：argparse 处理帮助参数或拒绝非法、互斥的参数组合时退出。

    副作用：
        默认模式按读取快照原子提交 README；check/stdout 模式不创建或修改文件。
    """
    parser = argparse.ArgumentParser(prog='docs render-extension-index')
    add_output_modes(parser)
    args = parser.parse_args(argv)
    try:
        target = ROOT_DIR / DOC_PATH
        snapshot = read_document(ROOT_DIR, target)
        if snapshot.text is None:
            raise ValueError('扩展 README 不存在；先建立人工说明和索引标记')
        content = replace_section(snapshot.text, render_section(root_dir=ROOT_DIR))
        return publish_document(ROOT_DIR, target, content, snapshot, check=args.check, stdout=args.stdout, label='extension_index')
    except (ValueError, OSError, RuntimeError) as exc:
        sys.stderr.write(f'[extension_index][FAIL] {exc}\n')
        return 2


if __name__ == '__main__':
    raise SystemExit(render_entry())
