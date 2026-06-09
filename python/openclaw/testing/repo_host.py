#!/usr/bin/env python3
"""Windows 无容器宿主机上的仓库级 Python 回归入口。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

if __package__ in {None, ''}:
    raise SystemExit(
        '请使用 `python -m openclaw.testing.repo_host ...` 调用本模块；文件路径执行不会初始化包上下文。'
    )

from openclaw.lib.repo.bootstrap import bootstrap_sys_path
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.runtime.execution import build_subprocess_env

sys.dont_write_bytecode = True
ROOT_DIR = resolve_repo_root(Path(__file__))

PYTHON_DIR = (ROOT_DIR / 'python').resolve()
REPO_HOST_LANE_PATH = ROOT_DIR / 'config' / 'governance' / 'support' / 'repo_host_lane.json'


def _normalize_path(value: str | os.PathLike[str]) -> str:
    return os.path.normcase(str(Path(value).resolve()))


def bootstrap_repo_host() -> Path:
    bootstrap_sys_path(Path(__file__))
    os.environ.update(build_subprocess_env(Path(__file__), base_env=os.environ))
    return ROOT_DIR


bootstrap_repo_host()

from openclaw.testing import repo_unittest


def _lane_path(path: Path | None = None) -> Path:
    return Path(path or REPO_HOST_LANE_PATH)


def load_lane_manifest(path: Path | None = None) -> dict[str, Any]:
    payload = json.loads(_lane_path(path).read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise ValueError('repo_host_lane.json 顶层必须为对象')
    suites = payload.get('suites')
    if not isinstance(suites, dict):
        raise ValueError('repo_host_lane.json -> suites 顶层必须为对象')
    return payload


def available_suites(path: Path | None = None) -> dict[str, dict[str, Any]]:
    suites = load_lane_manifest(path).get('suites') or {}
    result: dict[str, dict[str, Any]] = {}
    for name, spec in suites.items():
        if not isinstance(spec, dict):
            raise ValueError(f'repo_host_lane.json -> suites.{name} 顶层必须为对象')
        result[str(name)] = spec
    return result


def load_suite_selectors(name: str, path: Path | None = None) -> tuple[str, ...]:
    spec = available_suites(path).get(str(name))
    if spec is None:
        raise KeyError(str(name))
    selectors = spec.get('selectors')
    if not isinstance(selectors, list):
        raise ValueError(f'repo_host_lane.json -> suites.{name}.selectors 必须为数组')
    return tuple(str(item).strip() for item in selectors if str(item).strip())


def _format_lane_error(exc: Exception, *, path: Path | None = None) -> str:
    lane_path = _lane_path(path)
    if isinstance(exc, json.JSONDecodeError):
        return (
            f'{lane_path.name} 解析失败：第 {exc.lineno} 行第 {exc.colno} 列附近不是有效 JSON。'
        )
    if isinstance(exc, OSError):
        return f'无法读取 {lane_path.as_posix()}：{exc}'
    return str(exc)


def resolve_suite_selectors(name: str, parser: argparse.ArgumentParser, *, path: Path | None = None) -> tuple[str, ...]:
    try:
        suites = available_suites(path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(f'repo_host_lane manifest 无效：{_format_lane_error(exc, path=path)}')
    spec = suites.get(str(name))
    if spec is None:
        available = ', '.join(sorted(suites)) or '<none>'
        parser.error(f'未知 suite：{name}；可用 suite：{available}')
    selectors = spec.get('selectors')
    if not isinstance(selectors, list):
        parser.error(
            'repo_host_lane manifest 无效：'
            + _format_lane_error(ValueError(f'repo_host_lane.json -> suites.{name}.selectors 必须为数组'), path=path)
        )
    return tuple(str(item).strip() for item in selectors if str(item).strip())


def build_repo_unittest_argv(args: argparse.Namespace, *, selectors: Sequence[str] | None = None) -> list[str]:
    argv: list[str] = []
    if bool(args.quiet):
        argv.append('--quiet')
    argv.extend(['--jobs', str(args.jobs), '--start-dir', str(args.start_dir), '--pattern', str(args.pattern)])
    if str(args.import_mode or '').strip():
        argv.extend(['--import-mode', str(args.import_mode)])
    selected = list(selectors if selectors is not None else getattr(args, 'selectors', ()))
    argv.extend(str(item) for item in selected if str(item).strip())
    return argv


def invoke_repo_unittest(argv: Sequence[str]) -> int:
    return repo_unittest.main(list(argv))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='python -m openclaw.testing.repo_host',
        description=(
            'Windows 无容器宿主机上的仓库级 Python 回归入口。'
            '正式 shell 入口以容器执行介质为准。'
        ),
        epilog=(
            '示例：\n'
            '  python -m openclaw.testing.repo_host suite repo-check -q\n'
            '  python -m openclaw.testing.repo_host unittest python/openclaw/tests/testing/test_repo_unittest.py'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    unittest_parser = subparsers.add_parser(
        'unittest',
        help='通过宿主机 Python 回归入口执行 repo unittest selectors',
        description='宿主机 Python repo unittest 入口；正式 shell 入口为 scripts/testing/run_repo_unittest.sh。',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    repo_unittest.add_parser_arguments(unittest_parser)

    suite_parser = subparsers.add_parser(
        'suite',
        help='执行命名仓库回归 suite',
        description='Windows 无容器宿主机上的命名仓库回归 suite 装载器。',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    suite_parser.add_argument('suite_name')
    repo_unittest.add_parser_arguments(suite_parser, include_selectors=False)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv or sys.argv[1:]))
    if args.command == 'unittest':
        return invoke_repo_unittest(build_repo_unittest_argv(args))
    if args.command == 'suite':
        selectors = resolve_suite_selectors(str(args.suite_name), parser)
        return invoke_repo_unittest(build_repo_unittest_argv(args, selectors=selectors))
    parser.error(f'未知命令：{args.command}')
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
