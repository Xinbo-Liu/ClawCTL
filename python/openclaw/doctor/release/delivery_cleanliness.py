#!/usr/bin/env python3
"""检查仓库说明面是否保持有效实现口径与入口闭合。"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from openclaw.lib.repo.local_workspace_policy import disposable_workspace_paths
from openclaw.lib.repo.managed_extensions import (
    ManagedExtensionError,
    ManagedExtensionRow,
    managed_explicit_extensions,
    managed_extensions_index_path,
    validate_managed_explicit_extension_index,
)
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.absent_surfaces import (
    ABSENT_SURFACES_PATH,
    AbsentSurface,
    AbsentSurfaceError,
    load_absent_surfaces,
    validate_absent_surfaces,
)
from openclaw.control_plane.extensions.lifecycle import lifecycle_doctor_issues

ROOT_DIR = resolve_repo_root(Path(__file__))
RULES_PATH = ABSENT_SURFACES_PATH
RuleError = AbsentSurfaceError


@dataclass(frozen=True)
class Violation:
    kind: str
    target: str
    reason: str


def usage() -> str:
    return '\n'.join([
        '用法：',
        '  bash ./scripts/doctor/check_delivery_cleanliness.sh [--json]',
        '',
        '说明：',
        '  检查仓库禁止存在面、受管扩展边界和本地交付残留是否闭合。',
        f'  默认规则真源：{RULES_PATH.relative_to(ROOT_DIR).as_posix()}',
    ])


def load_rules(path: Path = RULES_PATH) -> tuple[AbsentSurface, ...]:
    return load_absent_surfaces(path)


def load_managed_extension_rules(
    rules: tuple[AbsentSurface, ...] | None = None,
) -> tuple[AbsentSurface, ...]:
    _ = rules
    return ()


def _repo_relpath(repo_root: Path, path: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def _python_package_dirs(python_root: Path) -> tuple[Path, ...]:
    if not python_root.is_dir():
        return ()
    return tuple(
        path.resolve()
        for path in sorted(python_root.iterdir())
        if path.is_dir() and path.name != '__pycache__' and (path / '__init__.py').is_file()
    )


def _managed_extension_domain_ids(extension: ManagedExtensionRow) -> tuple[str, ...]:
    domain_ids: list[str] = []
    for python_root in extension.python_roots:
        for package_dir in _python_package_dirs(python_root):
            domains_root = package_dir / 'domains'
            if not domains_root.is_dir():
                continue
            for domain_dir in sorted(domains_root.iterdir()):
                if domain_dir.is_dir() and domain_dir.name not in domain_ids:
                    domain_ids.append(domain_dir.name)
    if not domain_ids and extension.id.startswith('agent_'):
        domain_ids.append(extension.id.removeprefix('agent_'))
    return tuple(domain_ids)


def managed_extension_boundary_absent_paths(repo_root: Path = ROOT_DIR) -> tuple[tuple[str, str], ...]:
    """从 managed extension index 派生受管扩展边界禁止出现的路径。"""
    resolved_root = repo_root.resolve()
    rows: list[tuple[str, str]] = []
    for extension in managed_explicit_extensions(resolved_root):
        rows.append((
            _repo_relpath(resolved_root, resolved_root / 'config' / 'control_plane' / 'profiles' / f'{extension.id}.service.json'),
            '扩展专属 profile 固定在扩展包内部。',
        ))
        rows.append((
            _repo_relpath(resolved_root, resolved_root / 'python' / 'openclaw' / 'extensions' / extension.id),
            '扩展 Python 包固定在扩展包内部。',
        ))
        for domain_id in _managed_extension_domain_ids(extension):
            rows.append((
                _repo_relpath(resolved_root, resolved_root / 'python' / 'openclaw' / 'domains' / domain_id),
                '扩展领域代码固定在扩展包内部。',
            ))
        for python_root in extension.python_roots:
            for package_dir in _python_package_dirs(python_root):
                for target_path in sorted((package_dir / 'domains').glob('*/dispatch/targets.py')):
                    rows.append((
                        _repo_relpath(resolved_root, target_path),
                        '扩展分发目标解析只使用 openclaw.control_plane.dispatch.targets 单一真源。',
                    ))
    return tuple(dict.fromkeys(rows))


def scan_managed_extensions(
    repo_root: Path = ROOT_DIR,
    rules: tuple[AbsentSurface, ...] | None = None,
) -> list[Violation]:
    _ = rules
    if not managed_extensions_index_path(repo_root).is_file():
        return []
    violations = [
        Violation(kind='managed_extension_boundary', target=issue, reason='managed explicit extension index/layout must stay closed')
        for issue in validate_managed_explicit_extension_index(repo_root)
    ]
    violations.extend(
        Violation(kind='extension_lifecycle_doctor', target=issue, reason='managed extension lifecycle metadata/lock must stay consistent')
        for issue in lifecycle_doctor_issues(repo_root)
    )
    for rel_path, reason in managed_extension_boundary_absent_paths(repo_root):
        if (repo_root.resolve() / rel_path).exists():
            violations.append(Violation(kind='managed_extension_boundary', target=rel_path, reason=reason))
    return violations



def scan_repo(
    repo_root: Path = ROOT_DIR,
    rules: tuple[AbsentSurface, ...] | None = None,
) -> list[Violation]:
    shared_rules = load_rules() if rules is None else rules
    violations = [
        Violation(
            kind=item.kind,
            target=item.target,
            reason=item.reason,
        )
        for item in validate_absent_surfaces(repo_root.resolve(), shared_rules)
    ]
    violations.extend(scan_managed_extensions(repo_root.resolve(), shared_rules))
    try:
        residue_paths = disposable_workspace_paths(root_dir=repo_root.resolve())
    except (FileNotFoundError, ValueError):
        residue_paths = []
    for rel_path in residue_paths:
        violations.append(
            Violation(
                kind='workspace_residue',
                target=rel_path,
                reason='local workspace residue must be cleaned or removed before clean delivery',
            )
        )
    return violations


def _render_json(violations: Sequence[Violation]) -> str:
    payload = {
        'suite': 'delivery_cleanliness',
        'status': 'ok' if not violations else 'fail',
        'violations': [
            {
                'kind': item.kind,
                'target': item.target,
                'reason': item.reason,
            }
            for item in violations
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    json_output = False
    for arg in args:
        if arg == '--json':
            json_output = True
        elif arg in {'-h', '--help'}:
            print(usage())
            return 0
        else:
            print(f'[check_delivery_cleanliness][FAIL] 未知参数：{arg}', file=sys.stderr)
            print(usage(), file=sys.stderr)
            return 2
    try:
        rules = load_rules()
        violations = scan_repo(ROOT_DIR, rules)
    except (ManagedExtensionError, RuleError) as exc:
        if json_output:
            print(json.dumps({'suite': 'delivery_cleanliness', 'status': 'error', 'detail': str(exc)}, ensure_ascii=False))
        else:
            print(f'[check_delivery_cleanliness][FAIL] {exc}', file=sys.stderr)
        return 2
    if json_output:
        print(_render_json(violations))
        return 0 if not violations else 1
    if violations:
        print('[check_delivery_cleanliness][FAIL] 发现未同步的说明或入口：', file=sys.stderr)
        for item in violations:
            print(f'- [{item.kind}] {item.target} :: {item.reason}', file=sys.stderr)
        return 1
    print('[check_delivery_cleanliness] 已通过；说明面与入口命名保持有效实现口径。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
