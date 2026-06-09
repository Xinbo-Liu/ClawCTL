#!/usr/bin/env python3
"""Reject shell wrappers that hard-code repo-local PYTHONPATH injection."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from openclaw.lib.repo.layout import resolve_repo_root

ROOT_DIR = resolve_repo_root(Path(__file__))
SCRIPTS_ROOT = (ROOT_DIR / 'scripts').resolve()
ALLOWED_PYTHONPATH_ASSIGNMENT_REL_PATHS = {
    'scripts/lib/repo_python_env.sh',
}
FORBIDDEN_PATTERNS = (
    re.compile(r'PYTHONPATH=.*(?:\$\{?[A-Za-z_][A-Za-z0-9_]*\}?)[/\\]python'),
)


def build_report(root_dir: Path = ROOT_DIR) -> dict[str, Any]:
    resolved_root = Path(root_dir).resolve()
    scripts_root = (resolved_root / 'scripts').resolve()
    offenders: list[str] = []
    repo_python_dir = str((resolved_root / 'python').resolve()).replace('\\', '/')
    for path in sorted(scripts_root.rglob('*.sh')):
        if '__pycache__' in path.parts:
            continue
        rel_path = path.resolve().relative_to(resolved_root).as_posix()
        if rel_path in ALLOWED_PYTHONPATH_ASSIGNMENT_REL_PATHS:
            continue
        for line_number, raw_line in enumerate(path.read_text(encoding='utf-8').splitlines(), start=1):
            normalized_line = raw_line.replace('\\', '/')
            if not (
                any(pattern.search(raw_line) for pattern in FORBIDDEN_PATTERNS)
                or ('PYTHONPATH=' in normalized_line and repo_python_dir in normalized_line)
            ):
                continue
            offenders.append(f'{rel_path}:{line_number}:{raw_line.strip()}')
    return {
        'ok': not offenders,
        'offenderCount': len(offenders),
        'offenders': offenders,
    }


def main() -> int:
    payload = build_report()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if bool(payload.get('ok')) else 1


if __name__ == '__main__':
    raise SystemExit(main())
