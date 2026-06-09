#!/usr/bin/env python3
"""Workspace 模板同步与漂移校验。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Tuple

from openclaw.control_plane.surfaces import load_workspace_templates_manifest
from openclaw.docs.support.reference_specs import render_workspace_user_targets
from openclaw.lib.repo.contracts import repo_contract_path
from openclaw.lib.repo.layout import resolve_default_runtime_control_plane_service_config_path, resolve_repo_root
from openclaw.lib.runtime.path_resolver import PathResolver
from openclaw.runtime.generated_paths.rendering import render_generated_outputs

ROOT_DIR = resolve_repo_root(Path(__file__))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='同步或校验 workspace 模板与运行态副本')
    parser.add_argument('--repo-root', default=None)
    parser.add_argument('--config-path', default=None)
    parser.add_argument('--check', action='store_true')
    return parser.parse_args(argv)


def load_manifest(repo_root: Path, *, config_path: Path | None = None) -> dict:
    return load_workspace_templates_manifest(
        repo_contract_path('workspace_templates.manifest', root_dir=repo_root),
        config_path=config_path,
    )


def manifest_pairs(repo_root: Path, resolver: PathResolver, manifest: dict) -> List[Tuple[str, Path, Path]]:
    tpl_root = repo_root / 'config' / 'workspace_templates'
    pairs: List[Tuple[str, Path, Path]] = []
    for item in manifest.get('control_plane', []):
        target_entry = str(item.get('target_entry') or '').strip()
        if not target_entry or target_entry not in resolver.entries:
            continue
        pairs.append((f"control_plane:{item['template']}", tpl_root / item['template'], resolver.absolute_host_path(target_entry)))
    return pairs


def _expected_tree(src: Path, *, rendered_overrides: dict[Path, str] | None = None) -> dict[str, bytes]:
    expected: dict[str, bytes] = {}
    for source_file in sorted(path for path in src.rglob('*') if path.is_file()):
        rel = source_file.relative_to(src).as_posix()
        if rendered_overrides and source_file in rendered_overrides:
            expected[rel] = rendered_overrides[source_file].encode('utf-8')
        else:
            expected[rel] = source_file.read_bytes()
    return expected


def _remove_empty_dirs(root: Path) -> None:
    for path in sorted((candidate for candidate in root.rglob('*') if candidate.is_dir()), key=lambda item: len(item.parts), reverse=True):
        try:
            next(path.iterdir())
        except StopIteration:
            path.rmdir()


def sync_tree(src: Path, dst: Path, *, rendered_overrides: dict[Path, str] | None = None) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.mkdir(parents=True, exist_ok=True)
    expected = _expected_tree(src, rendered_overrides=rendered_overrides)
    for rel, content in expected.items():
        target_file = dst / rel
        target_file.parent.mkdir(parents=True, exist_ok=True)
        if not target_file.exists() or target_file.read_bytes() != content:
            target_file.write_bytes(content)
    existing_files = {path.relative_to(dst).as_posix(): path for path in dst.rglob('*') if path.is_file()}
    extra_files = sorted(set(existing_files) - set(expected))
    for rel in extra_files:
        existing_files[rel].unlink()
    _remove_empty_dirs(dst)


def compare_dirs(src: Path, dst: Path, *, rendered_overrides: dict[Path, str] | None = None) -> List[str]:
    issues: List[str] = []
    if not dst.exists():
        issues.append(f'missing destination: {dst}')
        return issues
    expected = _expected_tree(src, rendered_overrides=rendered_overrides)
    dst_files = {p.relative_to(dst).as_posix(): p for p in dst.rglob('*') if p.is_file()}
    missing = sorted(set(expected) - set(dst_files))
    extra = sorted(set(dst_files) - set(expected))
    if missing:
        issues.append(f'missing files: {missing}')
    if extra:
        issues.append(f'extra files: {extra}')
    for rel, expected_bytes in expected.items():
        target = dst_files.get(rel)
        if target is None:
            continue
        if expected_bytes != target.read_bytes():
            issues.append(f'content mismatch: {rel}')
    return issues


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(args.repo_root).resolve() if args.repo_root else ROOT_DIR
    config_path = Path(args.config_path).resolve() if args.config_path else resolve_default_runtime_control_plane_service_config_path(repo_root)
    try:
        manifest = load_manifest(repo_root, config_path=config_path)
    except ValueError as exc:
        print(f'[workspace_sync][FAIL] structure drift: {exc}', file=sys.stderr)
        return 1
    resolver = PathResolver.from_repo_root(repo_root, config_path=config_path)
    render_generated_outputs(repo_root, resolver)
    pairs = manifest_pairs(repo_root, resolver, manifest)
    rendered_targets = render_workspace_user_targets(repo_root, config_path=config_path)
    if args.check:
        failures: List[str] = []
        for label, src, dst in pairs:
            failures.extend(f'{label}: {msg}' for msg in compare_dirs(src, dst, rendered_overrides=rendered_targets))
        if failures:
            for item in failures:
                print(f'[workspace_sync][FAIL] {item}', file=sys.stderr)
            return 1
        print('[workspace_sync] 已通过')
        return 0

    for _, src, dst in pairs:
        sync_tree(src, dst, rendered_overrides=rendered_targets)
    print(f'[workspace_sync] 已同步 {len(pairs)} 组模板')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
