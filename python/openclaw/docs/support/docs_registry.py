#!/usr/bin/env python3
"""统一文档注册表；校验 pages 结构、路径唯一性与登记页面存在性，并向其他检查器提供注册表访问。"""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from openclaw.control_plane.governance_surfaces import DOCS_REGISTRY_PATH, load_docs_registry
from openclaw.lib.cli.output import stderr_write, stdout_write
from openclaw.lib.repo.layout import DEFAULT_RUNTIME_CONTROL_PLANE_SERVICE_CONFIG_REL_PATH, resolve_default_runtime_control_plane_service_config_path, resolve_repo_root

ROOT_DIR = resolve_repo_root(Path(__file__))

REGISTRY_PATH = DOCS_REGISTRY_PATH
_REPOSITORY_SCOPE: ContextVar[bool] = ContextVar('documentation_repository_scope', default=False)


def usage() -> str:
    return '\n'.join([
        '用法：',
        '  bash ./scripts/docs/check_docs_registry_sync.sh',
        '  bash ./scripts/docs/check_docs_registry_sync.sh --stdout',
        '  bash ./scripts/lib/run_static_python.sh -- -m openclaw.docs.support.docs_registry --dump-json [--config-path <path>]',
        '',
        '说明：',
        '  docs registry 由基座 docs_registry.json 与 enabled extension 的 docs fragment additive merge 组成；',
        '  本工具负责校验 pages 结构、路径唯一性与登记页面存在性。',
    ])


@lru_cache(maxsize=8)
def load_registry(config_path: Path | None = None, *, root_dir: Path | None = None) -> dict[str, Any]:
    """读取指定运行 profile 的文档注册表，不启用无关扩展。

    参数：
        config_path（Path | None）：service 配置；为空时按控制面环境选择配置。
        root_dir（Path | None）：检查的仓库根与显式配置访问边界；None 使用模块所在仓库的注册表真源。
    返回：
        dict[str, Any]：基座与当前启用扩展合并的注册表。

    异常：
        ValueError：显式 service 越过检查根目录，或仓库合同路径无效。

    副作用：
        读取指定根目录的文档真源并缓存合并结果；不修改运行 profile 的启用集合。
    """
    resolved_config = None if config_path is None else Path(config_path).resolve()
    registry_path = REGISTRY_PATH
    if root_dir is not None:
        from openclaw.lib.repo.contracts import repo_contract_path
        root = Path(root_dir).resolve()
        resolved_config = resolved_config or resolve_default_runtime_control_plane_service_config_path(root)
        if not resolved_config.is_relative_to(root):
            raise ValueError('文档检查的 service 配置必须位于当前仓库根目录内')
        registry_path = repo_contract_path('governance.docs_registry', root_dir=root)
    return load_docs_registry(registry_path, config_path=resolved_config)


@lru_cache(maxsize=8)
def load_repository_registry(root_dir: Path = ROOT_DIR) -> dict[str, Any]:
    """从平台和受管扩展的 manifest 动态汇总全仓文档注册表。

    参数：
        root_dir（Path）：被检查仓库根目录，缓存按该根目录隔离。
    返回：
        dict[str, Any]：基座及受管扩展 fragment 合并后带页面 owner 的注册表。
    异常：
        ValueError：同一扩展在不同 profile 下指向不同文档 fragment。
    副作用：
        只读 service、manifest 与文档 fragment，不读取部署 env 或运行状态。
    """
    from openclaw.control_plane.extensions.fragment_descriptors import DOCS_REGISTRY_DESCRIPTOR, load_fragment_payload
    from openclaw.control_plane.registry_loader.config import load_registry_service_context
    from openclaw.lib.repo.contracts import repo_contract_path
    from openclaw.lib.repo.managed_extensions import managed_explicit_extensions

    root = Path(root_dir).resolve()
    # 全仓范围从固定平台 service 开始，避免 ambient profile 指向仓库外配置。
    configs = [root / DEFAULT_RUNTIME_CONTROL_PLANE_SERVICE_CONFIG_REL_PATH]
    configs.extend(row.default_service_config_path for row in managed_explicit_extensions(root))
    extensions: dict[str, dict[str, Any]] = {}
    for config in dict.fromkeys(Path(path).resolve() for path in configs):
        if not config.is_relative_to(root):
            raise ValueError('全仓文档检查的 service 配置越过当前仓库根目录')
        for extension in load_registry_service_context(config)['extensions']:
            extension_id = str(extension['id'])
            prior = extensions.get(extension_id)
            if prior is not None and prior.get('governanceSurfaces') != extension.get('governanceSurfaces'):
                raise ValueError(f'扩展 {extension_id} 的文档治理 fragment 在不同 profile 中不一致')
            extensions[extension_id] = extension
    return load_fragment_payload(
        DOCS_REGISTRY_DESCRIPTOR,
        path=repo_contract_path('governance.docs_registry', root_dir=root),
        extensions=[extensions[key] for key in sorted(extensions)],
    )


@contextmanager
def repository_registry_scope() -> Iterator[None]:
    """让同进程批次使用全仓注册表，并在批次退出时恢复运行 profile 语义。

    返回：
        Iterator[None]：供 with 使用的上下文。
    副作用：
        最外层批次刷新文档注册表快照；嵌套检查共享同一快照，退出时恢复原范围。
    """
    if not _REPOSITORY_SCOPE.get():
        load_repository_registry.cache_clear()
        load_registry.cache_clear()
    token = _REPOSITORY_SCOPE.set(True)
    try:
        yield
    finally:
        _REPOSITORY_SCOPE.reset(token)


def load_check_registry(config_path: Path, *, root_dir: Path = ROOT_DIR, explicit_config: bool = False) -> dict[str, Any]:
    """选择检查使用的注册表；显式 service 配置始终只检查其启用扩展。

    参数：
        config_path（Path）：当前 service 配置路径。
        root_dir（Path）：全仓及选定 profile 模式下的仓库真源与 service 边界。
        explicit_config（bool）：是否由调用方显式选择 service。
    返回：
        dict[str, Any]：当前检查范围对应的注册表。
    """
    if _REPOSITORY_SCOPE.get() and not explicit_config:
        return load_repository_registry(Path(root_dir).resolve())
    return load_registry(config_path, root_dir=Path(root_dir).resolve())


def require_pages(registry: dict[str, Any], *, validate_metadata: bool = False) -> list[dict[str, Any]]:
    """验证注册页结构，按需验证身份字段，并返回唯一的页面列表。

    参数：
        registry（dict[str, Any]）：已合并文档注册表。
        validate_metadata（bool）：是否同时验证治理身份、布尔字段和源模式。
    返回：
        list[dict[str, Any]]：顺序保持不变的页面条目。
    异常：
        SystemExit：页面结构、路径或身份不满足合同。
    """
    pages = registry.get('pages')
    if not isinstance(pages, list):
        raise SystemExit('[docs_registry][FAIL] pages 顶层必须为数组')
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(pages):
        if not isinstance(item, dict):
            raise SystemExit(f'[docs_registry][FAIL] pages[{index}] 必须为对象')
        path = str(item.get('path') or '').strip()
        if not path:
            raise SystemExit(f'[docs_registry][FAIL] pages[{index}].path 不能为空')
        parsed = PurePosixPath(path)
        if '\\' in path or parsed.is_absolute() or '..' in parsed.parts or ':' in path or str(parsed) != path:
            raise SystemExit(f'[docs_registry][FAIL] pages[{index}].path 必须是规范仓内相对路径：{path}')
        if path in seen:
            raise SystemExit(f'[docs_registry][FAIL] pages.path 不能重复：{path}')
        seen.add(path)
        if validate_metadata:
            for key, allowed in (
                ('role', {'navigation', 'contract', 'task', 'reference', 'local'}),
                ('entryLevel', {'root', 'L0', 'L1', 'L2', 'local'}),
                ('sourceMode', {'manual', 'generated'}),
            ):
                if item.get(key) not in allowed:
                    raise SystemExit(f'[docs_registry][FAIL] {path}.{key} 不属于支持的文档身份')
            for key in ('formalEntry', 'localOnly'):
                if not isinstance(item.get(key), bool):
                    raise SystemExit(f'[docs_registry][FAIL] {path}.{key} 必须是 boolean')
            if not str(item.get('ownerDomain') or '').strip():
                raise SystemExit(f'[docs_registry][FAIL] {path}.ownerDomain 不能为空')
            if item['localOnly'] and item['formalEntry']:
                raise SystemExit(f'[docs_registry][FAIL] {path} 的局部文档不能声明 formalEntry=true')
        result.append(item)
    return result


def documentation_entrypoint_entries(registry: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for page in require_pages(registry):
        contract = page.get('entrypointContract')
        if not isinstance(contract, dict):
            continue
        item: dict[str, Any] = {'path': str(page['path'])}
        for key in ('requiredRefs', 'forbiddenRefs'):
            raw_refs = contract.get(key)
            if isinstance(raw_refs, list) and raw_refs:
                item[key] = list(raw_refs)
        entries.append(item)
    return entries


def documentation_boundary_rules(registry: dict[str, Any]) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for page in require_pages(registry):
        contract = page.get('boundaryContract')
        if not isinstance(contract, dict):
            continue
        item: dict[str, Any] = {'path': str(page['path'])}
        for key in ('requiredRefs', 'forbiddenRefs'):
            raw_refs = contract.get(key)
            if isinstance(raw_refs, list) and raw_refs:
                item[key] = list(raw_refs)
        rules.append(item)
    return rules


def page_presence_errors(registry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for page in require_pages(registry):
        rel_path = str(page['path'])
        if not (ROOT_DIR / rel_path).exists():
            errors.append(f'{rel_path} 不存在')
    return errors


def main(argv: list[str] | None = None) -> int:
    """验证登记页身份与存在性，或输出合并后的注册表 JSON。

    参数：
        argv（list[str] | None）：支持 --stdout、--dump-json 与显式 --config-path。
    返回：
        int：通过或帮助为 0，登记合同失败为 1，参数错误为 2。
    副作用：
        只读 service、manifest、注册表和页面存在性，向标准输出或标准错误写入诊断。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    stdout = False
    dump_json = False
    config_path: Path | None = None
    idx = 0
    while idx < len(args):
        arg = args[idx]
        if arg == '--stdout':
            stdout = True
        elif arg == '--dump-json':
            dump_json = True
        elif arg == '--config-path':
            idx += 1
            if idx >= len(args):
                stderr_write('[docs_registry][FAIL] --config-path 缺少路径参数\n')
                stderr_write(f'{usage()}\n')
                return 2
            config_path = Path(args[idx]).resolve()
        elif arg in {'-h', '--help'}:
            stdout_write(f'{usage()}\n')
            return 0
        else:
            stderr_write(f'[docs_registry][FAIL] 未知参数：{arg}\n')
            stderr_write(f'{usage()}\n')
            return 2
        idx += 1

    resolved_config = config_path or resolve_default_runtime_control_plane_service_config_path(ROOT_DIR)
    try:
        registry = load_check_registry(resolved_config, explicit_config=config_path is not None)
        pages = require_pages(registry, validate_metadata=True)
    except (Exception, SystemExit) as exc:
        stderr_write(f'[docs_registry][FAIL] {exc}\n')
        return 1

    if dump_json:
        stdout_write(json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=False) + '\n')
        return 0

    errors = page_presence_errors(registry)
    if stdout:
        config_label = str(resolved_config)
        try:
            config_label = str(resolved_config.relative_to(ROOT_DIR))
        except ValueError:
            pass
        stdout_write(
            f'[docs_registry] registry={REGISTRY_PATH.relative_to(ROOT_DIR)} config={config_label} pages={len(pages)}\n'
        )
        for page in pages:
            stdout_write(f'- {page["path"]} role={page.get("role")} entryLevel={page.get("entryLevel")}\n')
    if errors:
        stderr_write('[docs_registry] 同步校验失败：\n')
        for error in errors:
            stderr_write(f'- {error}\n')
        return 1
    stdout_write('[docs_registry] 已通过\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
