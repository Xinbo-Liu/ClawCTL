#!/usr/bin/env python3
"""Shell-facing bootstrap helpers backed by the Python truth surface."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .bootstrap import bootstrap_env_defaults, bootstrap_env_defaults_for_repo_root
from .layout import resolve_repo_root


def _repo_root(args: argparse.Namespace) -> Path:
    if args.root_dir:
        return Path(args.root_dir).resolve()
    return resolve_repo_root(Path(__file__))


def _env_defaults(repo_root: Path) -> dict[str, str]:
    if repo_root.exists():
        return bootstrap_env_defaults_for_repo_root(repo_root)
    return bootstrap_env_defaults(repo_root)


def cmd_env_lines(args: argparse.Namespace) -> int:
    repo_root = _repo_root(args)
    for key, value in _env_defaults(repo_root).items():
        print(f'{key}={value}')
    return 0


def cmd_env_args(args: argparse.Namespace) -> int:
    repo_root = _repo_root(args)
    for key, value in _env_defaults(repo_root).items():
        sys.stdout.write('--env\0')
        sys.stdout.write(f'{key}={value}\0')
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='repo-bootstrap')
    parser.add_argument('--root-dir', default=None)
    subparsers = parser.add_subparsers(dest='subcommand', required=True)

    env_lines = subparsers.add_parser('env-lines')
    env_lines.set_defaults(handler=cmd_env_lines)

    env_args = subparsers.add_parser('env-args')
    env_args.set_defaults(handler=cmd_env_args)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args) or 0)


if __name__ == '__main__':
    raise SystemExit(main())
