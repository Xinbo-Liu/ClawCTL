#!/usr/bin/env python3
"""Shared support helpers for control-plane CLI handlers."""
from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from openclaw.control_plane.registry import CliError, load_registry
from openclaw.lib.repo.layout import (
    CONTROL_PLANE_CONFIG_ENV,
    resolve_repo_root,
    resolve_selected_control_plane_config_path,
)


def fail(message: str, exit_code: int = 2) -> int:
    sys.stderr.write(f'[control_plane_cli][FAIL] {message}\n')
    return exit_code


def _print_json(payload: Any) -> int:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    return 0


def _repo_root() -> Path:
    return resolve_repo_root(Path(__file__))


def _config_path_from_args(args: argparse.Namespace) -> str:
    profile = str(getattr(args, 'control_plane_profile', '') or '')
    explicit_config_path = str(getattr(args, 'config_path', '') or '')
    default_config_path = '' if profile else str(getattr(args, '_default_config_path', '') or '')
    return str(
        resolve_selected_control_plane_config_path(
            explicit_config_path or default_config_path or None,
            control_plane_profile=profile,
            start_path=Path(__file__),
            default_to_base=True,
        )
    )


def _required_config_path_from_args(args: argparse.Namespace) -> Path:
    profile = str(getattr(args, 'control_plane_profile', '') or '')
    explicit_config_path = str(getattr(args, 'config_path', '') or '')
    default_config_path = '' if profile else str(getattr(args, '_default_config_path', '') or '')
    try:
        return resolve_selected_control_plane_config_path(
            explicit_config_path or default_config_path or None,
            control_plane_profile=profile,
            start_path=Path(__file__),
            default_to_base=False,
        )
    except ValueError as exc:
        raise CliError(str(exc), 2) from exc


@contextmanager
def _control_plane_config_override(config_path: str | None):
    resolved_text = str(config_path or '').strip()
    if not resolved_text:
        yield
        return
    resolved = str(Path(resolved_text).resolve())
    previous = os.environ.get(CONTROL_PLANE_CONFIG_ENV)
    os.environ[CONTROL_PLANE_CONFIG_ENV] = resolved
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(CONTROL_PLANE_CONFIG_ENV, None)
        else:
            os.environ[CONTROL_PLANE_CONFIG_ENV] = previous


def _load_registry_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return load_registry(Path(_config_path_from_args(args)).resolve())


def _render_config_scoped_payload(args: argparse.Namespace, render: Callable[[], Any]) -> Any:
    with _control_plane_config_override(_config_path_from_args(args)):
        return render()


def _print_config_scoped_json(args: argparse.Namespace, render: Callable[[], Any]) -> int:
    return _print_json(_render_config_scoped_payload(args, render))


def _print_registry_json(args: argparse.Namespace, render: Callable[[dict[str, Any]], Any]) -> int:
    return _print_json(render(_load_registry_from_args(args)))


def _passthrough_args(raw: list[str] | None) -> list[str]:
    args = list(raw or [])
    if args and args[0] == '--':
        return args[1:]
    return args
