#!/usr/bin/env python3
"""control-plane runtime extension-env CLI handlers."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from openclaw.control_plane.cli_support import handler_support as cli_support
from openclaw.control_plane.extensions.lifecycle import lifecycle_doctor_issues
from openclaw.control_plane.registry import CliError
from openclaw.lib.repo.extension_envs import (
    ExtensionEnvError,
    ensure_extension_env,
    extension_env_status,
    prune_extension_envs,
    select_extension_rows,
)


def _selected_config_path(args: argparse.Namespace) -> Path:
    return Path(cli_support._config_path_from_args(args)).resolve()


def _selected_rows(args: argparse.Namespace):
    repo_root = cli_support._repo_root()
    try:
        return select_extension_rows(
            repo_root=repo_root,
            extension_id=getattr(args, 'extension', ''),
            include_all=bool(getattr(args, 'all', False)),
            include_enabled=bool(getattr(args, 'enabled', False)),
            config_path=_selected_config_path(args),
        )
    except ExtensionEnvError as exc:
        raise CliError(str(exc), 2) from exc


def _status_payload(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = cli_support._repo_root()
    config_path = _selected_config_path(args)
    items = [
        extension_env_status(row, repo_root=repo_root, config_path=config_path).to_json()
        for row in _selected_rows(args)
    ]
    return {
        'schemaVersion': 1,
        'configPath': str(config_path),
        'items': items,
        'ok': all(bool(item.get('ok')) for item in items),
    }


def cmd_extension_env_ensure(args: argparse.Namespace) -> int:
    """同步 wheelhouse、准备扩展 venv，并输出部署主链使用的结构化报告。"""
    repo_root = cli_support._repo_root()
    config_path = _selected_config_path(args)
    allow_online = bool(getattr(args, 'allow_online', False))
    offline = not allow_online
    clean = not bool(getattr(args, 'no_clean', False))
    if bool(getattr(args, 'offline', False)) and allow_online:
        raise CliError('--offline 与 --allow-online 不能同时使用', 2)
    lifecycle_issues = lifecycle_doctor_issues(repo_root)
    if lifecycle_issues:
        detail = '；'.join(str(item) for item in lifecycle_issues)
        raise CliError(
            f'managed extension lifecycle lock 未同步；请先执行 '
            f'bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane extensions lock 并复核变更：{detail}',
            2,
        )
    rows = _selected_rows(args)
    items: list[dict[str, Any]] = []
    try:
        for row in rows:
            items.append(
                ensure_extension_env(
                    row,
                    repo_root=repo_root,
                    config_path=config_path,
                    offline=offline,
                    allow_online=allow_online,
                    clean_wheelhouse=clean,
                )
            )
    except ExtensionEnvError as exc:
        raise CliError(str(exc), 2) from exc
    return cli_support._print_json({
        'schemaVersion': 1,
        'configPath': str(config_path),
        'offline': offline,
        'cleanWheelhouse': clean,
        'ok': all(bool(item.get('ok')) for item in items),
        'items': items,
    })


def cmd_extension_env_status(args: argparse.Namespace) -> int:
    payload = _status_payload(args)
    if bool(getattr(args, 'json', False)):
        return cli_support._print_json(payload)
    for item in payload['items']:
        status = 'OK' if item.get('ok') else 'FAIL'
        print(f'[{status}] {item.get("extensionId")}: {item.get("envPath") or item.get("expectedEnvPath")}')
        for issue in item.get('issues') or []:
            print(f'  - {issue}')
    return 0


def cmd_extension_env_verify(args: argparse.Namespace) -> int:
    payload = _status_payload(args)
    if bool(getattr(args, 'json', False)):
        cli_support._print_json(payload)
    else:
        for item in payload['items']:
            status = 'OK' if item.get('ok') else 'FAIL'
            print(f'[{status}] {item.get("extensionId")}')
            for issue in item.get('issues') or []:
                print(f'  - {issue}')
    if payload['ok']:
        return 0
    sys.stderr.write('[control_plane_cli][FAIL] extension env verify failed\n')
    return 2


def cmd_extension_env_prune(args: argparse.Namespace) -> int:
    repo_root = cli_support._repo_root()
    config_path = _selected_config_path(args)
    keep = int(getattr(args, 'keep', 2))
    if keep < 0:
        raise CliError('--keep 不能小于 0', 2)
    rows = _selected_rows(args)
    try:
        items = [
            prune_extension_envs(row, repo_root=repo_root, config_path=config_path, keep=keep)
            for row in rows
        ]
    except ExtensionEnvError as exc:
        raise CliError(str(exc), 2) from exc
    return cli_support._print_json({
        'schemaVersion': 1,
        'configPath': str(config_path),
        'keep': keep,
        'items': items,
    })
