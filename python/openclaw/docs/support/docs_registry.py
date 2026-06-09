#!/usr/bin/env python3
"""统一文档注册表；校验 pages 结构、路径唯一性与登记页面存在性，并向其他检查器提供注册表访问。"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from openclaw.control_plane.governance_surfaces import DOCS_REGISTRY_PATH, load_docs_registry
from openclaw.lib.cli.output import stderr_write, stdout_write
from openclaw.lib.repo.layout import resolve_default_runtime_control_plane_service_config_path, resolve_repo_root

ROOT_DIR = resolve_repo_root(Path(__file__))

REGISTRY_PATH = DOCS_REGISTRY_PATH


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


def load_registry(config_path: Path | None = None) -> dict[str, Any]:
    resolved_config = None if config_path is None else Path(config_path).resolve()
    return load_docs_registry(REGISTRY_PATH, config_path=resolved_config)


def require_pages(registry: dict[str, Any]) -> list[dict[str, Any]]:
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
        if path in seen:
            raise SystemExit(f'[docs_registry][FAIL] pages.path 不能重复：{path}')
        seen.add(path)
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
        registry = load_registry(resolved_config)
        pages = require_pages(registry)
    except Exception as exc:
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
