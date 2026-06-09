#!/usr/bin/env python3
"""检查受管显式扩展包模块 bin 下是否残留未被任何真源引用的孤儿脚本。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.local_workspace_policy import rel_path_is_same_or_child, workspace_target_paths
from openclaw.lib.repo.managed_extensions import managed_extension_module_roots

ROOT_DIR = resolve_repo_root(Path(__file__))
SKIP_PARTS = {'__pycache__', '.git'}


def _policy_target_paths(root_dir: Path) -> tuple[str, ...]:
    try:
        return workspace_target_paths(root_dir=root_dir)
    except (FileNotFoundError, OSError, ValueError):
        return ()


def candidate_scripts(
    repo_root: Path | None = None,
    *,
    extension_id: str | None = None,
) -> tuple[list[Path], list[str]]:
    root_dir = ROOT_DIR if repo_root is None else Path(repo_root).resolve()
    result: list[Path] = []
    scan_roots: list[str] = []
    for module_root in managed_extension_module_roots(root_dir, extension_id=extension_id):
        scan_roots.append(module_root.relative_to(root_dir).as_posix())
        for path in sorted(module_root.rglob('bin/*')):
            if not path.is_file():
                continue
            if any(part in SKIP_PARTS for part in path.parts):
                continue
            result.append(path)
    return result, scan_roots


def repo_text_files(repo_root: Path | None = None) -> list[Path]:
    root_dir = ROOT_DIR if repo_root is None else Path(repo_root).resolve()
    files: list[Path] = []
    policy_targets = _policy_target_paths(root_dir)
    for path in root_dir.rglob('*'):
        if not path.is_file():
            continue
        rel_path = path.relative_to(root_dir).as_posix()
        if any(rel_path_is_same_or_child(rel_path, target) for target in policy_targets):
            continue
        if any(part in SKIP_PARTS for part in path.parts):
            continue
        if path.suffix in {'.pyc', '.png', '.jpg', '.jpeg', '.webp', '.zip', '.gz', '.pdf'}:
            continue
        files.append(path)
    return files


def external_references(target: Path, *, corpus: list[Path], repo_root: Path) -> list[str]:
    rel = target.relative_to(repo_root).as_posix()
    basename = target.name
    refs: list[str] = []
    for path in corpus:
        if path.resolve() == target.resolve():
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        if rel not in text and basename not in text:
            continue
        refs.append(path.relative_to(repo_root).as_posix())
    return refs


def build_orphan_report(
    repo_root: Path | None = None,
    *,
    extension_id: str | None = None,
) -> dict[str, object]:
    root_dir = ROOT_DIR if repo_root is None else Path(repo_root).resolve()
    scripts, scan_roots = candidate_scripts(root_dir, extension_id=extension_id)
    corpus = repo_text_files(root_dir)
    items: list[dict[str, object]] = []
    for script in scripts:
        refs = external_references(script, corpus=corpus, repo_root=root_dir)
        items.append({
            'path': script.relative_to(root_dir).as_posix(),
            'referenceCount': len(refs),
            'references': refs[:20],
            'ok': bool(refs),
        })
    orphan_scripts = [item['path'] for item in items if not bool(item.get('ok'))]
    return {
        'ok': not orphan_scripts,
        'extensionId': str(extension_id or '').strip(),
        'scanRootCount': len(scan_roots),
        'scanRoots': scan_roots,
        'count': len(items),
        'orphanCount': len(orphan_scripts),
        'orphanScripts': orphan_scripts,
        'items': items,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='检查受管显式扩展包模块 bin 下的孤儿脚本')
    parser.add_argument('--extension', help='只检查指定受管显式扩展包')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    ns = parse_args(sys.argv[1:] if argv is None else argv)
    payload = build_orphan_report(ROOT_DIR, extension_id=ns.extension)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not bool(payload.get('orphanScripts')) else 1


if __name__ == '__main__':
    raise SystemExit(main())
