"""runtime paths render-generated/check-generated CLI。"""
from __future__ import annotations

import argparse
from pathlib import Path

from openclaw.lib.repo.layout import resolve_default_runtime_control_plane_service_config_path
from openclaw.lib.runtime.path_resolver import PathResolver

from .constants import ROOT_DIR
from .rendering import check_generated_outputs, render_generated_outputs

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='生成统一路径治理运行态文件')
    parser.add_argument('--repo-root', default=None, help='仓库根目录；默认按脚本位置反推')
    parser.add_argument('--config-path', default=None, help='control-plane config/profile 路径；默认读取 OPENCLAW_CONTROL_PLANE_PROFILE / OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH 或正式默认运行 profile')
    parser.add_argument('--check', action='store_true', help='仅校验 manifest / 仓库真源 与运行态产物是否一致，不写文件')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(args.repo_root).resolve() if args.repo_root else ROOT_DIR
    config_path = Path(args.config_path).resolve() if args.config_path else resolve_default_runtime_control_plane_service_config_path(repo_root)
    resolver = PathResolver.from_repo_root(repo_root, config_path=config_path)
    if args.check:
        return check_generated_outputs(repo_root, resolver, config_path)
    render_generated_outputs(repo_root, resolver, config_path)
    print(f"[render_paths] generated path artifacts under {resolver.absolute_host_path('gateway_host_state_dir')} and {resolver.absolute_host_path('control_plane_host_state_dir')}")
    return 0
