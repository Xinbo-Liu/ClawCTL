"""文档输出目标真源解析。"""
from __future__ import annotations

from pathlib import Path

from openclaw.docs.support.shared_cache import read_json
from openclaw.lib.repo.layout import resolve_repo_root
from typing import Any

ROOT_DIR = resolve_repo_root(Path(__file__))


def fail(prefix: str, message: str) -> "NoReturn":
    raise SystemExit(f'[{prefix}][FAIL] {message}')


def read_json_object(config_path: Path, *, prefix: str, root_dir: Path = ROOT_DIR) -> dict[str, Any]:
    """读取文档配置对象，并以调用方仓库根定位结构错误。

    参数：
        config_path（Path）：需要读取的 JSON 配置路径。
        prefix（str）：配置错误提示的命令标签。
        root_dir（Path）：配置访问边界及诊断相对路径的基准，默认使用模块所在仓库。
    返回：
        dict[str, Any]：共享只读缓存返回的 JSON 对象。
    异常：
        SystemExit：JSON 顶层不是对象。
        OSError：配置文件不可读取。
        ValueError：配置不属于指定仓库，或 JSON 无法解析。
    副作用：
        只读取配置，并复用共享 JSON 缓存，不修改文件。
    """
    root = Path(root_dir).resolve()
    config_path = Path(config_path).resolve()
    relative = config_path.relative_to(root)
    payload = read_json(config_path)
    if not isinstance(payload, dict):
        fail(prefix, f'{relative} 顶层必须为对象')
    return payload


def require_nested_str(payload: dict[str, Any], key_path: list[str], *, prefix: str, label: str) -> str:
    current: Any = payload
    walked: list[str] = []
    for segment in key_path:
        walked.append(segment)
        if not isinstance(current, dict):
            fail(prefix, f'{label} 缺少字段：{".".join(walked)}')
        current = current.get(segment)
    value = str(current or '').strip()
    if not value:
        fail(prefix, f'{label} 不能为空：{".".join(key_path)}')
    return value


def resolve_target_from_config(config_rel_path: str, key_path: list[str], *, prefix: str, label: str, root_dir: Path = ROOT_DIR) -> tuple[Path, str]:
    """从指定仓库的配置字段解析文档输出目标。

    参数：
        config_rel_path（str）：相对于 root_dir 的配置真源路径。
        key_path（list[str]）：声明生成目标的逐级字段名。
        prefix（str）：配置错误提示的命令标签。
        label（str）：缺失字段诊断中的配置名称。
        root_dir（Path）：配置和输出目标所属的仓库根。
    返回：
        tuple[Path, str]：输出目标绝对路径与合同声明的相对路径。
    异常：
        SystemExit：配置结构无效或目标声明为空。
        OSError：配置不可读取。
        ValueError：配置真源越过仓库边界，或 JSON 无法解析。
    副作用：
        只读取配置并使用共享缓存；输出目标的文件安全检查由发布入口负责。
    """
    _, target, relative = resolve_payload_and_target_from_config(config_rel_path, key_path, prefix=prefix, label=label, root_dir=root_dir)
    return target, relative


def resolve_payload_and_target_from_config(config_rel_path: str, key_path: list[str], *, prefix: str, label: str, root_dir: Path = ROOT_DIR) -> tuple[dict[str, Any], Path, str]:
    """在指定仓库中同时读取文档配置和其输出目标。

    参数：
        config_rel_path（str）：相对于 root_dir 的配置真源路径。
        key_path（list[str]）：声明生成目标的逐级字段名。
        prefix（str）：配置错误提示的命令标签。
        label（str）：缺失字段诊断中的配置名称。
        root_dir（Path）：配置和输出目标所属的仓库根。
    返回：
        tuple[dict[str, Any], Path, str]：配置对象、输出目标绝对路径和声明的相对路径。
    异常：
        SystemExit：配置结构无效或目标声明为空。
        OSError：配置不可读取。
        ValueError：配置真源越过仓库边界，或 JSON 无法解析。
    副作用：
        只读取配置并使用共享缓存，不创建目标目录或写入文档。
    """
    root_dir = Path(root_dir).resolve()
    config_path = root_dir / config_rel_path
    payload = read_json_object(config_path, prefix=prefix, root_dir=root_dir)
    rel_path = require_nested_str(payload, key_path, prefix=prefix, label=label)
    return payload, root_dir / rel_path, rel_path
