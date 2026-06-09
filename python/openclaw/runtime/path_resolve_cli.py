#!/usr/bin/env python3
"""路径解析内部 CLI。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, NoReturn

from openclaw.lib.repo.layout import resolve_default_runtime_control_plane_service_config_path, resolve_repo_root
from openclaw.lib.runtime.path_resolver import PathResolver

ROOT_DIR = resolve_repo_root(Path(__file__))


def fail(message: str, code: int = 2) -> NoReturn:
    sys.stderr.write(f'[resolve_path][FAIL] {message}\n')
    raise SystemExit(code)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='resolve runtime path entry')
    parser.add_argument('entry_id', nargs='?')
    parser.add_argument('--view', default='host', help='视角：host / gateway / scheduler；额外视角由 active profile + extension 决定')
    parser.add_argument('--repo-root', default=None)
    parser.add_argument('--config-path', default=None, help='显式控制面 config/profile 路径；默认读取 OPENCLAW_CONTROL_PLANE_PROFILE / OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH 或正式默认运行 profile')
    parser.add_argument('--env-file', default=None, help='可选：先读取 runtime env，再叠加当前进程环境')
    parser.add_argument('--abs-host', action='store_true', help='仅 host 视角可用；输出宿主机绝对路径')
    parser.add_argument('--json-entry', action='store_true', help='输出 resolve_entry(entry_id) JSON')
    parser.add_argument('--gateway-exec-approvals-path', choices=['repo', 'host'], help='输出 gateway exec-approvals 路径（repo 或 host）')
    parser.add_argument('--gateway-exec-approvals', action='store_true', help='输出 gateway exec-approvals 内容')
    parser.add_argument('--batch-pairs-json', default=None, help='输入 JSON 数组 [[entry_id, view], ...]，批量输出路径映射 JSON')
    return parser.parse_args(argv)


def load_env_file(env_file: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not env_file.exists():
        fail(f'env 文件不存在：{env_file}')
    for raw in env_file.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        values[key.strip()] = value.strip()
    return values


def build_merged_env(args: argparse.Namespace, resolver: PathResolver, entry_id: str | None = None, view: str = 'host') -> Dict[str, str]:
    file_env: Dict[str, str] = {}
    if args.env_file:
        file_env.update(load_env_file(Path(args.env_file).resolve()))
    merged_env: Dict[str, str] = dict(file_env)
    merged_env.update(os.environ)
    if not entry_id:
        return merged_env
    entry = resolver.resolve_entry(entry_id)
    state_root = resolver.resolve_entry('state_root')
    entry_env_name = entry['env_names'].get(view)
    state_root_env_name = state_root['env_names'].get(view)
    state_override_present = False
    for candidate in [state_root_env_name] + (['OPENCLAW_STATE_DIR'] if view == 'host' else []):
        if candidate and candidate in os.environ:
            state_override_present = True
            break
    if state_override_present:
        if entry_env_name and entry_env_name not in os.environ:
            merged_env.pop(entry_env_name, None)
        if state_root_env_name and state_root_env_name not in os.environ:
            merged_env.pop(state_root_env_name, None)
    return merged_env


def resolve_config_path(args: argparse.Namespace, repo_root: Path) -> Path:
    if args.config_path:
        return Path(args.config_path).resolve()
    return resolve_default_runtime_control_plane_service_config_path(repo_root)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(args.repo_root).resolve() if args.repo_root else ROOT_DIR
    config_path = resolve_config_path(args, repo_root)
    resolver = PathResolver.from_repo_root(repo_root, config_path=config_path)

    try:
        aux_flags = [args.gateway_exec_approvals_path, args.gateway_exec_approvals]
        if sum(bool(item) for item in aux_flags) > 1:
            fail('输出类参数一次只能够使用一个')
        if args.gateway_exec_approvals_path:
            print(resolver.gateway_exec_approvals_paths()[f'{args.gateway_exec_approvals_path}_output'])
            return 0
        if args.gateway_exec_approvals:
            print(resolver.read_gateway_exec_approvals_source(), end='')
            return 0
        if args.batch_pairs_json:
            pairs = json.loads(args.batch_pairs_json)
            result: Dict[str, str] = {}
            for entry_id, view in pairs:
                normalized_view = resolver.normalize_view(view)
                result[f'{entry_id}::{normalized_view}'] = resolver.resolve_path(
                    entry_id,
                    normalized_view,
                    env=build_merged_env(args, resolver, entry_id, normalized_view),
                )
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if not args.entry_id:
            fail('缺少 entry_id')

        if args.json_entry:
            if args.abs_host:
                fail('--json-entry 与 --abs-host 不能同时使用')
            print(json.dumps(resolver.resolve_entry(args.entry_id), ensure_ascii=False))
            return 0

        normalized_view = resolver.normalize_view(args.view)
        merged_env = build_merged_env(args, resolver, args.entry_id, normalized_view)
        resolved = resolver.resolve_path(args.entry_id, normalized_view, env=merged_env)
        if args.abs_host:
            if normalized_view != 'host':
                fail('--abs-host 仅支持 host 视角')
            path_obj = Path(resolved)
            if not path_obj.is_absolute():
                path_obj = (repo_root / path_obj).resolve()
            print(path_obj)
            return 0
        print(resolved)
        return 0
    except KeyError as exc:
        fail(str(exc))
    except FileNotFoundError as exc:
        fail(str(exc))
    except ValueError as exc:
        fail(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
