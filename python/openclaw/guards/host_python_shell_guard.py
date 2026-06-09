#!/usr/bin/env python3
"""扫描 shell 文件中的宿主机 Python 直调。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from openclaw.guards.host_python.scanner import collect_shell_files, display_path, scan_shell_source


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog='bash ./scripts/lib/host_python_shell_guard.sh',
        description='扫描 *.sh 中是否出现宿主机 python/python3 直调。',
        add_help=True,
    )
    parser.add_argument('--repo-root', default=None)
    parser.add_argument('paths', nargs='+')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    repo_root = Path(args.repo_root).resolve() if args.repo_root else None
    files = collect_shell_files(args.paths)
    violations: list[str] = []
    for file_path in files:
        source = file_path.read_text(encoding='utf-8')
        violations.extend(scan_shell_source(source, display_path(file_path, repo_root)))
    if violations:
        sys.stdout.write('\n'.join(violations) + '\n')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
