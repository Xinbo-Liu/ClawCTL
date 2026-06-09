#!/usr/bin/env python3
"""检查手工页是否与真实实现对齐。"""
from __future__ import annotations

import sys

from openclaw.lib.cli.output import stdout_write, stderr_write
from typing import Any

from openclaw.docs.support.docs_registry import ROOT_DIR, load_registry, require_pages
from openclaw.docs.support.text_contracts import check_text_contract
from openclaw.docs.validators.object_closure import (
    has_any_token,
    load_documentation_runtime_resolver,
    normalized_token_candidates,
    resolve_runtime_path_ref,
    resolve_string_list_ref,
    resolve_string_ref,
)
from openclaw.lib.runtime.resolver_loader import PathResolverInstance


def usage() -> str:
    return "\n".join([
        "用法：",
        "  bash ./scripts/docs/check_documentation_implementation_alignment.sh",
        "  bash ./scripts/docs/check_documentation_implementation_alignment.sh --stdout",
        "",
        "说明：",
        "  校验声明 implementationContract 的页面是否引用实现真源，并保持键、路径与层级表述与现状一致。",
    ])


def _render_token(spec: dict[str, Any], value: str) -> str:
    render = str(spec.get("render") or "").strip()
    return render.format(value=value) if render else value


def check_page(page: dict[str, Any], resolver: PathResolverInstance) -> list[str]:
    rel_path = str(page["path"])
    contract = page.get("implementationContract")
    if not isinstance(contract, dict):
        return []
    file_path = ROOT_DIR / rel_path
    if not file_path.exists():
        return [f"{rel_path} 不存在"]
    content = file_path.read_text(encoding="utf-8")
    errors: list[str] = []

    errors.extend(
        check_text_contract(
            rel_path=rel_path,
            content=content,
            contract=contract,
            missing_label="缺少实现对齐固定事实",
            forbidden_label="仍保留已失效实现事实",
            resolver=resolver,
        )
    )

    for spec in contract.get("stringRefs") or []:
        if not isinstance(spec, dict):
            raise SystemExit("[check_documentation_implementation_alignment][FAIL] implementationContract.stringRefs 项必须为对象")
        label, token = resolve_string_ref(spec)
        candidates = normalized_token_candidates(_render_token(spec, token))
        if not has_any_token(content, candidates):
            errors.append(f"{rel_path} 缺少实现真源引用：{label} -> {' | '.join(candidates)}")

    for spec in contract.get("stringListRefs") or []:
        if not isinstance(spec, dict):
            raise SystemExit("[check_documentation_implementation_alignment][FAIL] implementationContract.stringListRefs 项必须为对象")
        label, tokens = resolve_string_list_ref(spec)
        for token in tokens:
            candidates = normalized_token_candidates(_render_token(spec, token))
            if not has_any_token(content, candidates):
                errors.append(f"{rel_path} 缺少实现真源列表引用：{label} -> {' | '.join(candidates)}")

    for spec in contract.get("runtimePathRefs") or []:
        if not isinstance(spec, dict):
            raise SystemExit("[check_documentation_implementation_alignment][FAIL] implementationContract.runtimePathRefs 项必须为对象")
        label, candidates = resolve_runtime_path_ref(spec, resolver)
        if not any(token in content for token in candidates):
            errors.append(f"{rel_path} 缺少实现路径真源引用：{label} -> {' | '.join(candidates)}")

    return errors


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    stdout = False
    for arg in args:
        if arg == "--stdout":
            stdout = True
        elif arg in {"-h", "--help"}:
            stdout_write(f"{usage()}\n")
            return 0
        else:
            stderr_write(f"[check_documentation_implementation_alignment][FAIL] 未知参数：{arg}\n")
            stderr_write(f"{usage()}\n")
            return 2
    try:
        registry = load_registry()
        pages = [page for page in require_pages(registry) if isinstance(page.get("implementationContract"), dict)]
        resolver = load_documentation_runtime_resolver()
    except Exception as exc:
        stderr_write(f"[check_documentation_implementation_alignment][FAIL] {exc}\n")
        return 1

    errors: list[str] = []
    for page in pages:
        errors.extend(check_page(page, resolver))

    if stdout:
        stdout_write(f"[check_documentation_implementation_alignment] count={len(pages)}\n")
        for page in pages:
            stdout_write(f"- {page['path']}\n")

    if errors:
        stderr_write("[check_documentation_implementation_alignment] 实现对齐校验失败：\n")
        for error in errors:
            stderr_write(f"- {error}\n")
        return 1

    stdout_write("[check_documentation_implementation_alignment] 已通过\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
