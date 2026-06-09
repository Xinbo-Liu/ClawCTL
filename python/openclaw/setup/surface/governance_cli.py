#!/usr/bin/env python3
"""Render merged governance surfaces for shell consumers."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from openclaw.control_plane.governance_surfaces import (
    load_dispatch_operations_surface,
    load_full_test_group_registry,
    load_setup_failures_surface,
)
from openclaw.lib.repo.layout import resolve_default_runtime_control_plane_service_config_path


def _print_json(payload: Any) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_full_test_group_registry(args: argparse.Namespace) -> int:
    return _print_json(load_full_test_group_registry(config_path=Path(args.config_path).resolve()))


def cmd_dispatch_operations_surface(args: argparse.Namespace) -> int:
    return _print_json(load_dispatch_operations_surface(config_path=Path(args.config_path).resolve()))


def cmd_setup_failures_surface(args: argparse.Namespace) -> int:
    return _print_json(load_setup_failures_surface(config_path=Path(args.config_path).resolve()))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='python -m openclaw.setup.surface.governance_cli')
    subparsers = parser.add_subparsers(dest='subcommand', required=True)
    default_config_path = str(resolve_default_runtime_control_plane_service_config_path(Path(__file__)))
    full_test_parser = subparsers.add_parser('full-test-group-registry')
    full_test_parser.add_argument('--config-path', default=default_config_path)
    full_test_parser.set_defaults(func=cmd_full_test_group_registry)
    dispatch_parser = subparsers.add_parser('dispatch-operations-surface')
    dispatch_parser.add_argument('--config-path', default=default_config_path)
    dispatch_parser.set_defaults(func=cmd_dispatch_operations_surface)
    setup_failures_parser = subparsers.add_parser('setup-failures-surface')
    setup_failures_parser.add_argument('--config-path', default=default_config_path)
    setup_failures_parser.set_defaults(func=cmd_setup_failures_surface)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == '__main__':
    raise SystemExit(main())
