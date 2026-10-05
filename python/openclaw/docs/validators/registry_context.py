#!/usr/bin/env python3
"""提供文档校验所需的配置标签与全仓或显式 profile 注册表上下文。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openclaw.docs.support.docs_registry import ROOT_DIR, load_check_registry
from openclaw.lib.cli.output import stderr_write, stdout_write
from openclaw.lib.repo.layout import resolve_default_runtime_control_plane_service_config_path


@dataclass(frozen=True)
class DocumentationValidatorContext:
    """封装文档 validator 的共享执行上下文。

    参数：
        stdout（bool）：表示当前命令是否需要输出逐项检查详情。
        config_path（Path）：显式选择的或仓库默认的 control-plane service config，用于校验配置和输出标签。
        config_label（str）：表示面向终端输出的 config_path 仓库相对标签；仓库外路径保留绝对路径。
        registry（dict[str, Any]）：按显式 service 或全仓批次范围合并的文档注册表对象。
    """

    stdout: bool
    config_path: Path
    config_label: str
    registry: dict[str, Any]


def config_label(config_path: Path, *, root_dir: Path = ROOT_DIR) -> str:
    """生成文档检查输出使用的配置路径标签。

    参数：
        config_path（Path）：已解析的 control-plane service config 路径。
        root_dir（Path）：仓库根目录，用于把仓内路径压缩为相对路径。

    返回：
        str：返回仓库相对路径；当 config_path 不在 root_dir 下时返回绝对路径字符串。
    """
    try:
        return str(config_path.relative_to(root_dir))
    except ValueError:
        return str(config_path)


def parse_stdout_config_args(
    argv: list[str],
    *,
    usage_text: str,
    error_prefix: str,
    root_dir: Path = ROOT_DIR,
) -> tuple[bool, Path] | int:
    """解析 docs validator 通用 CLI 参数。

    参数：
        argv（list[str]）：不含程序名的命令行参数列表。
        usage_text（str）：当前 validator 的帮助文本。
        error_prefix（str）：当前 validator 的错误前缀，例如 `[check_documentation_navigation][FAIL]`。
        root_dir（Path）：仓库根目录，用于解析默认 runtime control-plane config。

    返回：
        tuple[bool, Path] | int：成功时返回 `(stdout, config_path)`；帮助、缺参或未知参数时输出提示并返回退出码 int。
    """
    stdout = False
    config_path: Path | None = None
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == '--stdout':
            stdout = True
        elif arg == '--config-path':
            index += 1
            if index >= len(argv) or not str(argv[index]).strip():
                stderr_write(f'{error_prefix} --config-path 缺少路径参数\n')
                stderr_write(f'{usage_text}\n')
                return 2
            config_path = Path(argv[index]).resolve()
        elif arg.startswith('--config-path='):
            value = arg.split('=', 1)[1].strip()
            if not value:
                stderr_write(f'{error_prefix} --config-path 缺少路径参数\n')
                stderr_write(f'{usage_text}\n')
                return 2
            config_path = Path(value).resolve()
        elif arg in {'-h', '--help'}:
            stdout_write(f'{usage_text}\n')
            return 0
        else:
            stderr_write(f'{error_prefix} 未知参数：{arg}\n')
            stderr_write(f'{usage_text}\n')
            return 2
        index += 1
    return stdout, (config_path or resolve_default_runtime_control_plane_service_config_path(root_dir))


def load_validator_context(
    argv: list[str],
    *,
    usage_text: str,
    error_prefix: str,
    root_dir: Path = ROOT_DIR,
) -> DocumentationValidatorContext | int:
    """解析通用参数并按批次范围或显式 service 加载文档注册表。

    参数：
        argv（list[str]）：不含程序名的命令行参数列表。
        usage_text（str）：当前 validator 的帮助文本。
        error_prefix（str）：当前 validator 的错误前缀。
        root_dir（Path）：仓库根目录，用于默认 config 解析与输出标签生成。

    返回：
        DocumentationValidatorContext | int：成功时返回上下文对象；参数错误或 registry 加载失败时返回退出码 int。
    """
    parsed = parse_stdout_config_args(argv, usage_text=usage_text, error_prefix=error_prefix, root_dir=root_dir)
    if isinstance(parsed, int):
        return parsed
    stdout, resolved_config = parsed
    try:
        explicit_config = any(arg == '--config-path' or arg.startswith('--config-path=') for arg in argv)
        registry = load_check_registry(resolved_config, root_dir=root_dir, explicit_config=explicit_config)
    except (Exception, SystemExit) as exc:
        stderr_write(f'{error_prefix} {exc}\n')
        return 1
    return DocumentationValidatorContext(
        stdout=stdout,
        config_path=resolved_config,
        config_label=config_label(resolved_config, root_dir=root_dir),
        registry=registry,
    )
