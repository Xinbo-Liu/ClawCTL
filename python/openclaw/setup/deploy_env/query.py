#!/usr/bin/env python3
"""Query helpers for deploy env values."""
from __future__ import annotations

import json
import re
import shlex
from collections import OrderedDict
from pathlib import Path
from typing import Callable, NoReturn

from openclaw.lib.cli import CliError, FlagSpec, parse_typed_flag_args

FailFn = Callable[[str, str, int], NoReturn]


def parse_env_file(path: str | Path | None) -> OrderedDict[str, str]:
    values: OrderedDict[str, str] = OrderedDict()
    if not path:
        return values
    env_path = Path(path)
    if not env_path.exists():
        return values
    for raw_line in env_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        values[key.strip()] = parse_env_value(value)
    return values


def parse_env_value(raw_value: str) -> str:
    value = str(raw_value or '').strip()
    if len(value) >= 2 and value[0] in {'"', "'"}:
        try:
            parsed = shlex.split(value, comments=False, posix=True)
        except ValueError:
            parsed = []
        if len(parsed) == 1:
            return parsed[0]
    if len(value) >= 2 and ((value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'"))):
        return value[1:-1]
    return value


def parse_query_env_options(argv: list[str], *, default_output_path: Path, fail: FailFn) -> tuple[Path, str, bool, list[str]]:
    try:
        values, cleaned = parse_typed_flag_args(
            argv,
            specs={
                'env-file': FlagSpec(kind='path', dest='env_file', default=default_output_path),
                'format': FlagSpec(kind='str', dest='output_format', default='value', choices=('value', 'shell', 'json')),
                'all': FlagSpec(kind='bool', dest='all_keys', default=False),
            },
        )
    except CliError as exc:
        fail('deploy_env_control_plane', str(exc), 2)
    return values['env_file'], values['output_format'], values['all_keys'], cleaned


def render_shell_assignment(key: str, value: str, *, fail: FailFn) -> str:
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
        fail('deploy_env_control_plane', f'非法环境变量键：{key}', 2)
    if '\n' in value or '\r' in value:
        fail('deploy_env_control_plane', f'{key} 包含换行，不能作为 shell 赋值输出', 2)
    return f'{key}={shlex.quote(value)}'


def render_query_env_batch(
    env_map: OrderedDict[str, str],
    keys: list[str],
    env_file: Path,
    output_format: str,
    *,
    fail: FailFn,
) -> int:
    missing = [key for key in keys if key not in env_map]
    if missing:
        fail('deploy_env_control_plane', f'env 中不存在键：{", ".join(missing)}', 2)
    payload = OrderedDict((key, env_map[key]) for key in keys)
    if output_format == 'value':
        for key, value in payload.items():
            print(f'{key}={value}')
        return 0
    if output_format == 'shell':
        for key, value in payload.items():
            print(render_shell_assignment(key, value, fail=fail))
        return 0
    if output_format == 'json':
        print(json.dumps({'env_file': str(env_file), 'values': payload}, ensure_ascii=False, indent=2))
        return 0
    fail('deploy_env_control_plane', f'未知输出格式：{output_format}', 2)


def query_env_value(argv: list[str], *, default_output_path: Path, fail: FailFn) -> int:
    env_file, output_format, _, cleaned = parse_query_env_options(argv, default_output_path=default_output_path, fail=fail)
    if len(cleaned) != 1:
        fail('deploy_env_control_plane', 'query-env 需要且仅接受一个 KEY', 2)
    key = cleaned[0].strip()
    if not key:
        fail('deploy_env_control_plane', 'query-env KEY 不能为空', 2)
    env_map = parse_env_file(env_file)
    if key not in env_map:
        fail('deploy_env_control_plane', f'env 中不存在键：{key}', 2)
    value = env_map[key]
    if output_format == 'value':
        print(value)
        return 0
    if output_format == 'shell':
        print(render_shell_assignment(key, value, fail=fail))
        return 0
    if output_format == 'json':
        print(json.dumps({'key': key, 'value': value, 'env_file': str(env_file)}, ensure_ascii=False, indent=2))
        return 0
    fail('deploy_env_control_plane', f'未知输出格式：{output_format}', 2)


def query_env_batch(argv: list[str], *, default_output_path: Path, fail: FailFn) -> int:
    env_file, output_format, all_keys, cleaned = parse_query_env_options(argv, default_output_path=default_output_path, fail=fail)
    env_map = parse_env_file(env_file)
    keys = list(env_map.keys()) if all_keys else [item.strip() for item in cleaned if item.strip()]
    if not keys:
        fail('deploy_env_control_plane', 'query-env-batch 至少需要一个 KEY，或显式传 --all', 2)
    return render_query_env_batch(env_map, keys, env_file, output_format, fail=fail)


def render_env_lines(values: OrderedDict[str, str], *, redact_secret_keys: set[str] | frozenset[str] | None = None) -> str:
    secret_keys = set(redact_secret_keys or set())
    lines = ['# 由 deploy_env_control_plane.py 生成']
    for key, value in values.items():
        rendered_value = '<redacted>' if key in secret_keys and str(value).strip() else value
        lines.append(f'{key}={rendered_value}')
    return '\n'.join(lines) + '\n'
