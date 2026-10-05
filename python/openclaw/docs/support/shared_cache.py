#!/usr/bin/env python3
"""按文件 identity 为同进程文档读取共享文本和 JSON 缓存。"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


def _file_identity(path: Path) -> tuple[int, int, int, int, int]:
    """取得缓存所依赖的文件版本。

    参数：
        path（Path）：已解析的文档或 JSON 文件路径。
    返回：
        tuple[int, int, int, int, int]：设备、inode、大小、修改与元数据变更的纳秒时间。
    异常：
        OSError：文件已删除或不可访问。
    副作用：
        只读文件元数据，不读正文或写入文件。
    """
    metadata = path.stat()
    return (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)


@lru_cache(maxsize=512)
def _read_text(path: Path, identity: tuple[int, int, int, int, int]) -> str:
    """读取一个文件版本，供文本及 JSON 读取共同复用。

    参数：
        path（Path）：文件的规范绝对路径。
        identity（tuple[int, int, int, int, int]）：缓存版本键；内容只在该键首次出现时读取。
    返回：
        str：UTF-8 文本。
    异常：
        OSError：文件读取失败。
        UnicodeDecodeError：正文不是 UTF-8。
    副作用：
        读取正文并缓存，不改变文件。
    """
    return path.read_text(encoding='utf-8')


@lru_cache(maxsize=512)
def _read_json(path: Path, identity: tuple[int, int, int, int, int]) -> Any:
    """解析指定文件版本的 JSON。

    参数：
        path（Path）：文件的规范绝对路径。
        identity（tuple[int, int, int, int, int]）：与文本缓存共享的文件版本键。
    返回：
        Any：解析后的 JSON 值；调用方不得修改缓存对象。
    异常：
        OSError：文件读取失败。
        UnicodeDecodeError：正文不是 UTF-8。
        json.JSONDecodeError：正文不是有效 JSON。
    副作用：
        复用文本读取并缓存解析结果，不改变文件。
    """
    return json.loads(_read_text(path, identity))


def read_text(path: Path) -> str:
    """读取当前版本的 UTF-8 文档，正文变更或替换后自动刷新。

    参数：
        path（Path）：待读取文件的绝对或相对路径。
    返回：
        str：当前文件的 UTF-8 文本。
    异常：
        OSError：文件不存在或不可读；不返回删除前的缓存正文。
        UnicodeDecodeError：正文不是 UTF-8。
    副作用：
        读取文件元数据，并在版本变化时读取和缓存正文；不写文件。
    """
    resolved = Path(path).resolve()
    return _read_text(resolved, _file_identity(resolved))


def read_json(path: Path) -> Any:
    """读取当前版本的 UTF-8 JSON，正文变更或替换后自动刷新。

    参数：
        path（Path）：待读取 JSON 文件路径。
    返回：
        Any：当前版本的 JSON 值；调用方不得修改缓存对象。
    异常：
        OSError：文件不存在或不可读。
        UnicodeDecodeError：正文不是 UTF-8。
        json.JSONDecodeError：正文不是有效 JSON。
    副作用：
        读取文件元数据并复用当前版本的文本和解析缓存；不写文件。
    """
    resolved = Path(path).resolve()
    return _read_json(resolved, _file_identity(resolved))
