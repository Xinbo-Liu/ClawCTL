#!/usr/bin/env python3
"""Bundle artifact smoke helpers."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from openclaw.control_plane.manifest_fields import (
    DISPATCH_PROVIDER_REGISTRY_PATHS_KEY,
    DISPATCH_TARGET_REGISTRY_PATHS_KEY,
)
from openclaw.control_plane.registry import load_registry


def _fail(message: str, exit_code: int = 2) -> int:
    sys.stderr.write(f'[artifact_smoke][FAIL] {message}\n')
    return exit_code


def _require_extension(registry: dict[str, Any], extension_id: str) -> dict[str, Any] | None:
    for row in registry.get('extensions') or []:
        if isinstance(row, dict) and str(row.get('id') or '').strip() == extension_id:
            return row
    return None


def _count_payload(registry: dict[str, Any]) -> dict[str, int]:
    return {
        'agents': len(registry.get('agents') or []),
        'agentGroups': len(registry.get('agentGroups') or []),
        'agentModules': len(registry.get('agentModules') or []),
        'extensions': len(registry.get('extensions') or []),
        'jobRunners': len(registry.get('jobRunners') or []),
        'jobs': len(registry.get('jobs') or []),
        'models': len(registry.get('models') or []),
        'runtimeAdapters': len(registry.get('runtimeAdapters') or []),
        'targets': len(registry.get('targets') or []),
    }


def _extension_registry(extension: dict[str, Any]) -> dict[str, Any]:
    registry = extension.get('registry')
    return registry if isinstance(registry, dict) else {}


def cmd_base_kernel(args: argparse.Namespace) -> int:
    config_path = Path(str(args.config_path or '')).resolve()
    registry = load_registry(config_path)
    counts = _count_payload(registry)
    expected_zero = ('agents', 'agentGroups', 'agentModules', 'extensions', 'jobRunners', 'jobs', 'models', 'runtimeAdapters', 'targets')
    non_zero = {key: counts[key] for key in expected_zero if counts[key] != 0}
    if non_zero:
        return _fail(f'base kernel 应为零业务/零扩展面：{json.dumps(non_zero, ensure_ascii=False, sort_keys=True)}')
    sys.stdout.write(json.dumps({'configPath': str(config_path), 'counts': counts}, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    return 0


def cmd_platform_profile(args: argparse.Namespace) -> int:
    config_path = Path(str(args.config_path or '')).resolve()
    registry = load_registry(config_path)
    counts = _count_payload(registry)
    extension_id = str(args.extension_id or '').strip()
    extension = _require_extension(registry, extension_id)
    if extension is None:
        return _fail(f'未找到启用的 platform extension：{extension_id}')

    missing_non_zero = {}
    for key in ('extensions', 'runtimeAdapters', 'jobRunners'):
        if counts[key] < 1:
            missing_non_zero[key] = counts[key]
    if missing_non_zero:
        return _fail(f'platform 默认面缺少运行时入口：{json.dumps(missing_non_zero, ensure_ascii=False, sort_keys=True)}')

    unexpected_counts = {}
    for key in ('agents', 'agentGroups', 'agentModules', 'jobs', 'models', 'targets'):
        if counts[key] != 0:
            unexpected_counts[key] = counts[key]
    if unexpected_counts:
        return _fail(f'platform 默认面不应携带业务对象：{json.dumps(unexpected_counts, ensure_ascii=False, sort_keys=True)}')

    extension_registry = _extension_registry(extension)
    if list(extension_registry.get(DISPATCH_TARGET_REGISTRY_PATHS_KEY) or []):
        return _fail(f'platform extension 不应携带业务 {DISPATCH_TARGET_REGISTRY_PATHS_KEY}')
    if not list(extension_registry.get(DISPATCH_PROVIDER_REGISTRY_PATHS_KEY) or []):
        return _fail(f'platform extension 缺少 {DISPATCH_PROVIDER_REGISTRY_PATHS_KEY}')

    cli_commands = extension.get('cliCommands') or []
    internal_api_routes = extension.get('internalApiRoutes') or []
    ready_checks = extension.get('readyChecks') or []
    if list(cli_commands):
        return _fail('platform extension 不应携带业务专属 CLI commands')
    if list(internal_api_routes):
        return _fail('platform extension 不应携带业务专属 internal API routes')
    if list(ready_checks):
        return _fail('platform extension 不应携带业务专属 ready checks')

    payload = {
        'configPath': str(config_path),
        'extensionId': extension_id,
        'counts': counts,
        DISPATCH_PROVIDER_REGISTRY_PATHS_KEY: [str(item) for item in list(extension_registry.get(DISPATCH_PROVIDER_REGISTRY_PATHS_KEY) or [])],
        DISPATCH_TARGET_REGISTRY_PATHS_KEY: [str(item) for item in list(extension_registry.get(DISPATCH_TARGET_REGISTRY_PATHS_KEY) or [])],
        'internalApiRoutes': [],
        'jobRunnerIds': [str((row or {}).get('id') or '') for row in (registry.get('jobRunners') or []) if isinstance(row, dict)],
        'runtimeAdapterIds': [str((row or {}).get('id') or '') for row in (registry.get('runtimeAdapters') or []) if isinstance(row, dict)],
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='artifact_smoke', description='bundle artifact smoke helper')
    subparsers = parser.add_subparsers(dest='command', required=True)

    base = subparsers.add_parser('base-kernel', help='校验 base kernel 保持零扩展/零业务对象')
    base.add_argument('--config-path', required=True)
    base.set_defaults(func=cmd_base_kernel)

    platform = subparsers.add_parser('platform-profile', help='校验 agent_platform 默认运行面')
    platform.add_argument('--config-path', required=True)
    platform.add_argument('--extension-id', required=True)
    platform.set_defaults(func=cmd_platform_profile)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == '__main__':
    raise SystemExit(main())
