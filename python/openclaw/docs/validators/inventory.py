"""核对 Git 或交付 BOM 中的 Markdown 文件与文档注册表的双向覆盖和归属。"""
from __future__ import annotations

import base64
import binascii
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
from typing import Any, Mapping

from openclaw.docs.support.docs_registry import ROOT_DIR, require_pages, repository_registry_scope
from openclaw.docs.validators.registry_context import load_validator_context
from openclaw.lib.cli.output import stderr_write, stdout_write
from openclaw.lib.repo.managed_extensions import managed_explicit_extensions


def load_inventory(root_dir: Path, *, environ: Mapping[str, str] | None = None) -> set[str]:
    """读取受管 Markdown 文件清单，不递归扫描部署状态或私有目录。

    参数：
        root_dir（Path）：Git 工作树或交付包解包根目录。
        environ（Mapping[str, str] | None）：清单选择环境；None 使用进程环境。显式 BOM 优先，其次使用绑定仓库根的宿主 NUL 快照，最后读取本地 Git。
    返回：
        set[str]：现存的规范仓内 Markdown 路径；Git 及其宿主快照可包含尚未提交的文件，并忽略已删除项。BOM 声明的文件必须存在。
    异常：
        ValueError：清单缺失、编码错误、越界，或 BOM 结构无效。
        OSError：显式 BOM 文件不可读取。
    副作用：
        可执行只读 git ls-files，或读取显式指定的交付 BOM。
    """
    root = Path(root_dir).resolve()
    env = os.environ if environ is None else environ
    encoded = env.get('OPENCLAW_DOCS_TRACKED_FILES_B64', '')
    bom_path = env.get('OPENCLAW_DOCS_INVENTORY_BOM_PATH', '')
    using_bom = bool(bom_path)
    if using_bom:
        payload = json.loads(Path(bom_path).resolve().read_text(encoding='utf-8-sig'))
        rows = payload.get('files') if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not all(isinstance(row, dict) and isinstance(row.get('path'), str) for row in rows):
            raise ValueError('交付 BOM 必须包含 files[].path；请传入既有 bundle .bom.json')
        raw_paths = [row['path'] for row in rows]
    elif 'OPENCLAW_DOCS_TRACKED_FILES_B64' in env:
        bound_root = env.get('OPENCLAW_DOCS_TRACKED_FILES_ROOT', '').strip()
        if not bound_root:
            raise ValueError('宿主文档清单缺少 OPENCLAW_DOCS_TRACKED_FILES_ROOT 根目录绑定')
        if Path(bound_root).resolve() != root:
            raise ValueError('宿主文档清单绑定的根目录与当前仓库不一致')
        try:
            raw_paths = base64.b64decode(encoded, validate=True).decode('utf-8').split('\0')
        except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
            raise ValueError('宿主传入的文档 NUL 清单不是有效 UTF-8 base64') from exc
    elif (root / '.git').exists():
        try:
            result = subprocess.run(
                ['git', '-C', str(root), 'ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', ':(icase)*.md'],
                check=True, capture_output=True, timeout=15,
            )
            raw_paths = result.stdout.decode('utf-8').split('\0')
        except (OSError, subprocess.SubprocessError, UnicodeDecodeError) as exc:
            raise ValueError('无法读取 Git 文档清单；请由宿主 wrapper 传入 NUL base64 清单') from exc
    else:
        raise ValueError('当前目录没有 .git；请用 OPENCLAW_DOCS_INVENTORY_BOM_PATH 指定该交付包已有的 .bom.json')
    paths: set[str] = set()
    for raw in raw_paths:
        if not raw or PurePosixPath(raw).suffix.lower() != '.md':
            continue
        parsed = PurePosixPath(raw)
        if parsed.is_absolute() or '..' in parsed.parts or '\\' in raw or ':' in raw or parsed.as_posix() != raw:
            raise ValueError(f'文档清单含非规范仓内路径：{raw}')
        try:
            (root / raw).resolve().relative_to(root)
        except ValueError as exc:
            raise ValueError(f'文档清单通过符号链接越过仓库边界：{raw}') from exc
        if (root / raw).is_file():
            paths.add(raw)
        elif using_bom:
            raise ValueError(f'交付 BOM 声明的 Markdown 文件不存在：{raw}')
    return paths


def inventory_errors(registry: dict[str, Any], inventory: set[str], *, root_dir: Path) -> list[str]:
    """检查文件登记双向覆盖与扩展 owner，不升级文档的正式入口身份。

    参数：
        registry（dict[str, Any]）：全仓或选定 profile 的文档注册表。
        inventory（set[str]）：受管 Markdown 文件路径集合。
        root_dir（Path）：路径及动态扩展发现边界。
    返回：
        list[str]：漏登记、残留登记、缺文件、越界或 owner 不一致的诊断。
    异常：
        SystemExit：注册表身份或路径合同无效。
    副作用：
        只读文档存在性和受管扩展索引，不读取运行态目录。
    """
    root = Path(root_dir).resolve()
    pages = require_pages(registry, validate_metadata=True)
    declared = {page['path'] for page in pages}
    errors = [f'受管 Markdown 未登记：{path}' for path in sorted(inventory - declared)]
    errors.extend(f'登记页不在受管 Markdown 清单中：{path}' for path in sorted(declared - inventory))
    extensions = managed_explicit_extensions(root)
    roots = {row.id: row.root_dir.resolve() for row in extensions}
    for page in pages:
        path = (root / page['path']).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            errors.append(f"登记页越过仓库边界：{page['path']}")
            continue
        if not path.is_file():
            errors.append(f"登记页不是现存文件：{page['path']}")
        expected = None
        for extension_id, extension_root in roots.items():
            if path.is_relative_to(extension_root):
                expected = extension_id
                break
        owner = str(page.get('extensionId') or '').strip()
        if expected and owner != expected:
            errors.append(f"{page['path']} 的 extensionId 应为 {expected}，当前为 {owner or '<未声明>'}")
        elif owner and owner != 'agent_platform' and owner != expected:
            errors.append(f"{page['path']} 的 extensionId={owner} 不属于该页面路径")
    return errors


def main(argv: list[str] | None = None) -> int:
    """执行受管文档覆盖检查，标准输出保持普通单项 validator 约定。

    参数：
        argv（list[str] | None）：支持 --stdout 与 --config-path。
    返回：
        int：通过为 0，事实或覆盖失败为 1，参数错误为 2。
    副作用：
        仅读清单、注册表和扩展规格，输出诊断。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    with repository_registry_scope():
        context = load_validator_context(args, usage_text='用法：文档 inventory [--stdout] [--config-path <service>]', error_prefix='[documentation_inventory][FAIL]', root_dir=ROOT_DIR)
    if isinstance(context, int):
        return context
    try:
        inventory = load_inventory(ROOT_DIR)
        # 显式 profile 检查仅要求其已登记页面受管；全仓批次负责反向漏登记检查。
        if any(arg == '--config-path' or arg.startswith('--config-path=') for arg in args):
            inventory &= {page['path'] for page in require_pages(context.registry)}
        errors = inventory_errors(context.registry, inventory, root_dir=ROOT_DIR)
    except (Exception, SystemExit) as exc:
        stderr_write(f'[documentation_inventory][FAIL] {exc}\n')
        return 1
    if context.stdout:
        stdout_write(f'[documentation_inventory] files={len(inventory)} pages={len(require_pages(context.registry))}\n')
    for error in errors:
        stderr_write(f'[documentation_inventory][FAIL] {error}\n')
    if not errors:
        stdout_write('[documentation_inventory] 已通过\n')
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
