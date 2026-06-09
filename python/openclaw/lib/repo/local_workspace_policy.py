#!/usr/bin/env python3
"""Shared truth and helpers for local workspace residue policy."""
from __future__ import annotations

import fnmatch
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openclaw.lib.repo.contracts import repo_contract_relpath
from openclaw.lib.repo.layout import resolve_repo_root

ROOT_DIR = resolve_repo_root(Path(__file__))
POLICY_REL_PATH = Path(repo_contract_relpath('governance.local_workspace_policy'))
INSTALL_DEFAULTS_REL_PATH = Path(repo_contract_relpath('governance.install_defaults'))
POLICY_PATH = (ROOT_DIR / POLICY_REL_PATH).resolve()
ALLOWED_TRUTH_REFS = {'host_state_root'}
ALLOWED_CLASSES = {
    'disposable_local',
    'disposable_runtime_cache',
    'managed_runtime_state',
    'managed_input_cache',
    'managed_export_output',
}
GLOB_CHARS = set('*?[]')
CLI_EXIT_CODE = 97


@dataclass(frozen=True)
class LocalWorkspaceTarget:
    id: str
    path: str
    target_class: str
    cleanup_by_default: bool
    truth_ref: str = ''

    def bundle_exclude(self) -> str:
        return f'{self.path.rstrip("/")}/**'

    def gitignore_pattern(self) -> str:
        return f'/{self.path.rstrip("/")}/'


@dataclass(frozen=True)
class LocalWorkspacePolicy:
    schema_version: int
    targets: tuple[LocalWorkspaceTarget, ...]
    derived_globs: tuple[str, ...]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def _support_path(root_dir: Path, rel_path: Path) -> Path:
    return (Path(root_dir).resolve() / rel_path).resolve()


def _normalize_rel_path(path: str, *, root_dir: Path, error_factory: type[Exception]) -> str:
    normalized = str(path or '').strip().replace('\\', '/').strip('/')
    if not normalized:
        raise error_factory('local_workspace_policy 路径不能为空')
    if normalized.startswith('../') or '/../' in normalized or normalized == '..':
        raise error_factory(f'local_workspace_policy 路径越界：{path}')
    resolved = (root_dir / normalized).resolve()
    try:
        resolved.relative_to(root_dir.resolve())
    except ValueError as exc:
        raise error_factory(f'local_workspace_policy 路径越界：{path}') from exc
    return normalized


def _resolve_truth_ref(ref: str, *, root_dir: Path, error_factory: type[Exception]) -> str:
    if ref not in ALLOWED_TRUTH_REFS:
        raise error_factory(f'local_workspace_policy.truthRef 非法：{ref}')
    if ref == 'host_state_root':
        return _normalize_rel_path(_host_state_root_default(root_dir, error_factory=error_factory), root_dir=root_dir, error_factory=error_factory)
    raise error_factory(f'local_workspace_policy.truthRef 未实现：{ref}')


def _host_state_root_default(root_dir: Path, *, error_factory: type[Exception]) -> str:
    install_defaults_path = _support_path(root_dir, INSTALL_DEFAULTS_REL_PATH)
    payload = _read_json(install_defaults_path)
    defaults = payload.get('defaults') if isinstance(payload, dict) else None
    value = defaults.get('host_state_root') if isinstance(defaults, dict) else None
    normalized = str(value or '').strip()
    if not normalized:
        raise error_factory('install_defaults 缺少必填真源：host_state_root')
    return normalized


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or '').strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _normalize_rel_path_for_match(path: str) -> str:
    return str(path or '').strip().replace('\\', '/').strip('/')


def rel_path_is_same_or_child(rel_path: str, parent_path: str) -> bool:
    child = _normalize_rel_path_for_match(rel_path)
    parent = _normalize_rel_path_for_match(parent_path)
    return bool(child and parent and (child == parent or child.startswith(f'{parent}/')))


def host_state_root_path(*, root_dir: Path = ROOT_DIR, error_factory: type[Exception] = ValueError) -> str:
    return _resolve_truth_ref('host_state_root', root_dir=root_dir, error_factory=error_factory)


def local_workspace_policy_path(*, root_dir: Path = ROOT_DIR) -> Path:
    return _support_path(root_dir, POLICY_REL_PATH)


def load_local_workspace_policy(
    *,
    root_dir: Path = ROOT_DIR,
    path: Path | None = None,
    error_factory: type[Exception] = ValueError,
) -> LocalWorkspacePolicy:
    resolved_path = local_workspace_policy_path(root_dir=root_dir) if path is None else Path(path).resolve()
    payload = _read_json(resolved_path)
    if not isinstance(payload, dict):
        raise error_factory('local_workspace_policy 顶层必须为对象')
    schema_version = payload.get('schemaVersion')
    if not isinstance(schema_version, int):
        raise error_factory('local_workspace_policy.schemaVersion 必须为整数')
    raw_targets = payload.get('targets')
    if not isinstance(raw_targets, list) or not raw_targets:
        raise error_factory('local_workspace_policy.targets 必须为非空数组')
    raw_derived = payload.get('derivedGlobs')
    if not isinstance(raw_derived, list):
        raise error_factory('local_workspace_policy.derivedGlobs 必须为数组')

    targets: list[LocalWorkspaceTarget] = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for index, item in enumerate(raw_targets):
        if not isinstance(item, dict):
            raise error_factory(f'local_workspace_policy.targets[{index}] 必须为对象')
        target_id = str(item.get('id') or '').strip()
        target_class = str(item.get('class') or '').strip()
        cleanup_by_default = item.get('cleanupByDefault')
        raw_path = item.get('path')
        truth_ref = str(item.get('truthRef') or '').strip()
        if not target_id:
            raise error_factory(f'local_workspace_policy.targets[{index}].id 不能为空')
        if target_id in seen_ids:
            raise error_factory(f'local_workspace_policy.targets.id 重复：{target_id}')
        if target_class not in ALLOWED_CLASSES:
            raise error_factory(f'local_workspace_policy.targets[{index}].class 非法：{target_class}')
        if not isinstance(cleanup_by_default, bool):
            raise error_factory(f'local_workspace_policy.targets[{index}].cleanupByDefault 必须为布尔值')
        has_path = isinstance(raw_path, str) and bool(str(raw_path).strip())
        has_truth_ref = bool(truth_ref)
        if has_path == has_truth_ref:
            raise error_factory(f'local_workspace_policy.targets[{index}] 必须且只能声明 path 或 truthRef')
        resolved_path = (
            _normalize_rel_path(str(raw_path), root_dir=root_dir, error_factory=error_factory)
            if has_path
            else _resolve_truth_ref(truth_ref, root_dir=root_dir, error_factory=error_factory)
        )
        if resolved_path in seen_paths:
            raise error_factory(f'local_workspace_policy.targets.path 重复：{resolved_path}')
        seen_ids.add(target_id)
        seen_paths.add(resolved_path)
        targets.append(
            LocalWorkspaceTarget(
                id=target_id,
                path=resolved_path,
                target_class=target_class,
                cleanup_by_default=cleanup_by_default,
                truth_ref=truth_ref,
            )
        )

    derived_globs: list[str] = []
    for index, item in enumerate(raw_derived):
        normalized = str(item or '').strip().replace('\\', '/')
        if not normalized:
            raise error_factory(f'local_workspace_policy.derivedGlobs[{index}] 不能为空')
        if normalized.startswith('/'):
            raise error_factory(f'local_workspace_policy.derivedGlobs[{index}] 不能以 / 开头：{normalized}')
        derived_globs.append(normalized)

    return LocalWorkspacePolicy(
        schema_version=schema_version,
        targets=tuple(targets),
        derived_globs=tuple(_dedupe_preserve_order(derived_globs)),
    )


def default_cleanup_targets(
    policy: LocalWorkspacePolicy | None = None,
    *,
    root_dir: Path = ROOT_DIR,
    error_factory: type[Exception] = ValueError,
) -> tuple[LocalWorkspaceTarget, ...]:
    resolved = policy or load_local_workspace_policy(root_dir=root_dir, error_factory=error_factory)
    return tuple(target for target in resolved.targets if target.cleanup_by_default)


def workspace_target_paths(
    policy: LocalWorkspacePolicy | None = None,
    *,
    root_dir: Path = ROOT_DIR,
    error_factory: type[Exception] = ValueError,
) -> tuple[str, ...]:
    resolved = policy or load_local_workspace_policy(root_dir=root_dir, error_factory=error_factory)
    return tuple(target.path for target in resolved.targets)


def bundle_shared_excludes(
    policy: LocalWorkspacePolicy | None = None,
    *,
    root_dir: Path = ROOT_DIR,
    error_factory: type[Exception] = ValueError,
) -> list[str]:
    resolved = policy or load_local_workspace_policy(root_dir=root_dir, error_factory=error_factory)
    values = [target.bundle_exclude() for target in resolved.targets]
    values.extend(resolved.derived_globs)
    return _dedupe_preserve_order(values)


def gitignore_patterns(
    policy: LocalWorkspacePolicy | None = None,
    *,
    root_dir: Path = ROOT_DIR,
    error_factory: type[Exception] = ValueError,
) -> list[str]:
    resolved = policy or load_local_workspace_policy(root_dir=root_dir, error_factory=error_factory)
    values = [target.gitignore_pattern() for target in resolved.targets]
    values.extend(resolved.derived_globs)
    return _dedupe_preserve_order(values)


def _iter_derived_match_paths(root_dir: Path, pattern: str) -> list[str]:
    normalized = str(pattern or '').strip().replace('\\', '/')
    if not normalized:
        return []
    results: set[str] = set()
    if normalized in {'.coverage', '.coverage.*', 'tmp-*'}:
        for match in root_dir.glob(normalized):
            results.add(match.relative_to(root_dir).as_posix())
        return sorted(results)
    if normalized.endswith('/**'):
        anchor = normalized[:-3]
        if anchor.startswith('**/'):
            leaf = anchor[3:]
            if leaf and not any(ch in leaf for ch in GLOB_CHARS):
                for match in root_dir.rglob(leaf):
                    results.add(match.relative_to(root_dir).as_posix())
                return sorted(results)
        for match in root_dir.glob(anchor):
            results.add(match.relative_to(root_dir).as_posix())
        return sorted(results)
    if normalized in {'*.pyc', '*.pyo', '.DS_Store', 'Thumbs.db'}:
        return sorted({match.relative_to(root_dir).as_posix() for match in root_dir.rglob(normalized)})
    return sorted({match.relative_to(root_dir).as_posix() for match in root_dir.glob(normalized)})


def derived_residue_paths(
    policy: LocalWorkspacePolicy | None = None,
    *,
    root_dir: Path = ROOT_DIR,
    error_factory: type[Exception] = ValueError,
) -> list[str]:
    resolved = policy or load_local_workspace_policy(root_dir=root_dir, error_factory=error_factory)
    return _derived_residue_paths_for_policy(resolved, root_dir=root_dir)


def _derived_residue_paths_for_policy(
    policy: LocalWorkspacePolicy,
    *,
    root_dir: Path,
    prune_policy_targets: bool = False,
) -> list[str]:
    """按策略枚举派生残留；disposable 扫描可提前剪掉保留目标以免走进运行态大目录。"""
    fast = _derived_residue_paths_single_walk(policy, root_dir=root_dir, prune_policy_targets=prune_policy_targets)
    if fast is not None:
        return fast
    results: list[str] = []
    policy_targets = tuple(target.path for target in policy.targets)
    for pattern in policy.derived_globs:
        for rel_path in _iter_derived_match_paths(root_dir, pattern):
            if prune_policy_targets and any(rel_path_is_same_or_child(rel_path, target) for target in policy_targets):
                continue
            results.append(rel_path)
    return _dedupe_preserve_order(sorted(results))


def _derived_residue_paths_single_walk(
    policy: LocalWorkspacePolicy,
    *,
    root_dir: Path,
    prune_policy_targets: bool = False,
) -> list[str] | None:
    """对标准派生残留规则做一次仓库遍历，避免每条 glob 重复扫描整仓。"""
    global_dir_leafs: set[str] = set()
    global_dir_suffixes: set[tuple[str, ...]] = set()
    global_file_patterns: set[str] = set()
    explicit_patterns: list[str] = []
    for raw_pattern in policy.derived_globs:
        pattern = str(raw_pattern or '').strip().replace('\\', '/')
        if pattern.startswith('**/') and pattern.endswith('/**'):
            suffix = tuple(part for part in pattern[3:-3].split('/') if part)
            if not suffix:
                return None
            if len(suffix) == 1:
                global_dir_leafs.add(suffix[0])
            else:
                global_dir_suffixes.add(suffix)
            continue
        if pattern in {'*.pyc', '*.pyo', '.DS_Store', 'Thumbs.db'}:
            global_file_patterns.add(pattern)
            continue
        explicit_patterns.append(pattern)

    results: set[str] = set()
    root = Path(root_dir).resolve()
    policy_targets = tuple(target.path for target in policy.targets)
    ignored_walk_dirs = {'.git'}

    def rel_from(path: Path) -> str:
        return path.resolve().relative_to(root).as_posix()

    def is_policy_target(rel_path: str) -> bool:
        return any(rel_path_is_same_or_child(rel_path, target) for target in policy_targets)

    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        rel_current = '' if current == root else rel_from(current)
        if prune_policy_targets and rel_current and is_policy_target(rel_current):
            dirnames[:] = []
            continue
        kept_dirnames: list[str] = []
        for dirname in sorted(dirnames):
            child = current / dirname
            rel_child = rel_from(child)
            parts = tuple(rel_child.split('/'))
            matched_dir = dirname in global_dir_leafs or any(
                len(parts) >= len(suffix) and parts[-len(suffix):] == suffix
                for suffix in global_dir_suffixes
            )
            if matched_dir:
                results.add(rel_child)
                if dirname == '__pycache__':
                    for child_file in sorted(child.iterdir()):
                        if child_file.is_file() and any(fnmatch.fnmatchcase(child_file.name, pattern) for pattern in global_file_patterns):
                            results.add(rel_from(child_file))
                continue
            if dirname in ignored_walk_dirs or (prune_policy_targets and is_policy_target(rel_child)):
                continue
            kept_dirnames.append(dirname)
        dirnames[:] = kept_dirnames
        for filename in filenames:
            if any(fnmatch.fnmatchcase(filename, pattern) for pattern in global_file_patterns):
                rel_file = rel_from(current / filename)
                results.add(rel_file)

    for pattern in explicit_patterns:
        for rel_path in _iter_derived_match_paths(root, pattern):
            if prune_policy_targets and any(rel_path_is_same_or_child(rel_path, target) for target in policy_targets):
                continue
            results.add(rel_path)
    return _dedupe_preserve_order(sorted(results))


def disposable_workspace_paths(
    policy: LocalWorkspacePolicy | None = None,
    *,
    root_dir: Path = ROOT_DIR,
    error_factory: type[Exception] = ValueError,
) -> list[str]:
    resolved = policy or load_local_workspace_policy(root_dir=root_dir, error_factory=error_factory)
    cleanup_targets = [target.path for target in default_cleanup_targets(resolved, root_dir=root_dir, error_factory=error_factory)]
    policy_targets = workspace_target_paths(resolved, root_dir=root_dir, error_factory=error_factory)
    existing_cleanup_targets = [path for path in cleanup_targets if (root_dir / path).exists()]
    results = list(existing_cleanup_targets)
    for rel_path in _derived_residue_paths_for_policy(resolved, root_dir=root_dir, prune_policy_targets=True):
        if any(rel_path_is_same_or_child(rel_path, target) for target in policy_targets):
            continue
        results.append(rel_path)
    return _dedupe_preserve_order(sorted(results))


def to_shell_rows(
    policy: LocalWorkspacePolicy | None = None,
    *,
    root_dir: Path = ROOT_DIR,
    error_factory: type[Exception] = ValueError,
) -> list[tuple[str, str, str, str, str]]:
    resolved = policy or load_local_workspace_policy(root_dir=root_dir, error_factory=error_factory)
    return [
        (
            target.id,
            target.path,
            target.target_class,
            'yes' if target.cleanup_by_default else 'no',
            target.gitignore_pattern(),
        )
        for target in resolved.targets
    ]


def _usage() -> str:
    return '\n'.join(
        [
            'Usage:',
            '  python -m openclaw.lib.repo.local_workspace_policy <command> [truth-ref]',
            '',
            'Commands:',
            '  host-state-root',
            '  truth-path <truth-ref>',
            '  target-rows',
            '  targets',
            '  default-cleanup-targets',
            '  workspace-target-paths',
            '  gitignore-patterns',
            '  bundle-excludes',
            '  derived-residue-paths',
            '  disposable-paths',
        ]
    )


def _print_lines(values: list[str] | tuple[str, ...]) -> None:
    for value in values:
        if value:
            print(value)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {'-h', '--help'}:
        print(_usage())
        return 0

    command = args[0]
    try:
        if command == 'host-state-root':
            print(host_state_root_path())
            return 0
        if command == 'truth-path':
            if len(args) != 2:
                raise ValueError('truth-path 需要 truthRef 参数')
            print(_resolve_truth_ref(args[1], root_dir=ROOT_DIR, error_factory=ValueError))
            return 0
        if command in {'target-rows', 'targets'}:
            for row in to_shell_rows():
                print('\t'.join(row))
            return 0
        if command == 'default-cleanup-targets':
            _print_lines(tuple(target.path for target in default_cleanup_targets()))
            return 0
        if command == 'workspace-target-paths':
            _print_lines(workspace_target_paths())
            return 0
        if command == 'gitignore-patterns':
            _print_lines(tuple(gitignore_patterns()))
            return 0
        if command == 'bundle-excludes':
            _print_lines(tuple(bundle_shared_excludes()))
            return 0
        if command == 'derived-residue-paths':
            _print_lines(tuple(derived_residue_paths()))
            return 0
        if command == 'disposable-paths':
            _print_lines(tuple(disposable_workspace_paths()))
            return 0
        raise ValueError(f'unknown command: {command}')
    except Exception as exc:
        print(f'[local_workspace_policy][FAIL] {exc}', file=sys.stderr)
        return CLI_EXIT_CODE


if __name__ == '__main__':
    raise SystemExit(main())
