#!/usr/bin/env python3
"""在一个隔离仓库副本中验证受管 agent 模块生命周期矩阵。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from openclaw.doctor.agent_modules import attach_detach, prune_drop
from openclaw.doctor.agent_modules.support import copy_repo_tree
from openclaw.lib.cli import CliError, FlagSpec, parse_typed_flag_args
from openclaw.lib.repo.layout import resolve_repo_root


ROOT_DIR = resolve_repo_root(Path(__file__))


def usage() -> str:
    """返回生命周期矩阵命令的稳定帮助文本。

    返回：
        str：包含参数与隔离执行行为的帮助文本。
    """
    return '\n'.join([
        '用法:',
        '  python -m openclaw.doctor.agent_modules.lifecycle_matrix [--config-path <path>] [--control-plane-profile <profile_id>]',
        '',
        '行为:',
        '  只复制一次仓库，在同一隔离副本中依次验证 scaffold、attach、detach、prune、drop、回滚与 registry 闭合。',
    ])


def parse_args(argv: list[str]) -> tuple[Path | None, str]:
    """解析可选配置路径与控制面 profile。

    参数：
        argv（list[str]）：不含程序名的命令行参数。

    返回：
        tuple[Path | None, str]：显式配置路径和 profile 标识。

    异常：
        SystemExit：帮助请求或参数不合法时按 CLI 约定退出。

    副作用：
        帮助请求或参数错误时向对应输出流写入诊断文本。
    """
    if any(arg in {'-h', '--help'} for arg in argv):
        print(usage())
        raise SystemExit(0)
    try:
        values, positionals = parse_typed_flag_args(
            argv,
            specs={
                'config-path': FlagSpec(kind='path', dest='config_path'),
                'control-plane-profile': FlagSpec(kind='str', dest='control_plane_profile'),
            },
        )
    except CliError as exc:
        print(f'[check_agent_module_lifecycle_matrix][FAIL] {exc}', file=sys.stderr)
        print(usage(), file=sys.stderr)
        raise SystemExit(2) from exc
    if positionals:
        print(
            f'[check_agent_module_lifecycle_matrix][FAIL] 未知参数: {" ".join(positionals)}',
            file=sys.stderr,
        )
        raise SystemExit(2)
    return values['config_path'], values['control_plane_profile'] or ''


def run_matrix(
    repo_root: Path,
    requested_config_path: Path | None,
    *,
    control_plane_profile: str,
) -> dict[str, Any]:
    """在同一仓库副本中顺序执行两个可逆生命周期探针。

    参数：
        repo_root（Path）：已复制完成的隔离仓库根目录。
        requested_config_path（Path | None）：调用方指定的服务配置路径。
        control_plane_profile（str）：调用方指定的控制面 profile。

    返回：
        dict[str, Any]：attach/detach 与 prune/drop 的完整机器结果。
    """
    attach_result = attach_detach._run_probe_in_repo_copy(
        repo_root,
        requested_config_path,
        control_plane_profile=control_plane_profile,
    )
    prune_result = prune_drop._run_probe_in_repo_copy(
        repo_root,
        requested_config_path,
        control_plane_profile=control_plane_profile,
    )
    return {
        'ok': bool(attach_result.get('ok')) and bool(prune_result.get('ok')),
        'checks': {
            'attachDetach': attach_result,
            'pruneDrop': prune_result,
        },
    }


def main(argv: list[str] | None = None) -> int:
    """复制一次仓库、执行生命周期矩阵并输出 JSON。

    参数：
        argv（list[str] | None）：显式参数；为 ``None`` 时读取进程参数。

    返回：
        int：矩阵全部通过时为 0，否则为非零退出码。
    """
    requested_config_path, control_plane_profile = parse_args(list(sys.argv[1:] if argv is None else argv))
    with tempfile.TemporaryDirectory(prefix='openclaw_lifecycle_matrix_') as temp_dir:
        repo_root = copy_repo_tree(ROOT_DIR, Path(temp_dir))
        try:
            payload = run_matrix(
                repo_root,
                requested_config_path,
                control_plane_profile=control_plane_profile,
            )
        except SystemExit as exc:
            return int(exc.code)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if bool(payload.get('ok')) else 1


if __name__ == '__main__':
    raise SystemExit(main())
