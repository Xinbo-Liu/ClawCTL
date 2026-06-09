#!/usr/bin/env python3
"""控制平面文件变更集通用工具。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openclaw.control_plane.registry import CliError, load_registry
from openclaw.lib.repo.layout import resolve_repo_root


def relative_to_repo(repo_root: Path, value: str | Path | None) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    path = Path(text)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except (OSError, RuntimeError, ValueError):
        return str(path.resolve())


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CliError(f'JSON 无法解析：{path}', 2) from exc
    if not isinstance(payload, dict):
        raise CliError(f'JSON 根对象必须为对象：{path}', 2)
    return payload


def render_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + '\n'


def build_write(path: Path, *, action: str, payload: dict[str, Any] | None = None, summary: str) -> dict[str, Any]:
    item: dict[str, Any] = {
        'path': path.resolve(),
        'action': action,
        'summary': summary,
    }
    if payload is not None:
        item['content'] = render_json(payload)
    return item


def apply_staged_writes(*, writes: list[dict[str, Any]], config_path: Path, error_prefix: str = '应用控制平面变更失败') -> None:
    backups: dict[Path, bytes] = {}
    created_paths: list[Path] = []
    try:
        for item in writes:
            path = Path(item['path']).resolve()
            action = str(item.get('action') or '').strip()
            if action not in {'create', 'update', 'delete'}:
                raise CliError(f'未知写入动作：{action}', 2)
            if path.exists() and path not in backups:
                backups[path] = path.read_bytes()
            if action == 'delete':
                if not path.exists():
                    raise CliError(f'待删除文件不存在：{path}', 2)
                path.unlink()
                continue
            if not path.exists():
                created_paths.append(path)
                path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(item.get('content') or ''), encoding='utf-8')
        load_registry(Path(config_path).resolve())
    # noinspection PyBroadException
    except Exception as exc:
        # 任意写入或 registry 校验失败都要回滚 staged writes，避免留下半应用状态。
        for path in reversed(created_paths):
            try:
                if path.exists() and path not in backups:
                    path.unlink()
            except OSError:
                # 回滚阶段尽量恢复可恢复文件；单个新文件删除失败交给最终错误暴露。
                pass
        for path, data in backups.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        if isinstance(exc, CliError):
            raise
        raise CliError(f'{error_prefix}：{exc}', 2) from exc


def summarize_files(repo_root: Path, writes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            'path': relative_to_repo(repo_root, item.get('path')),
            'action': str(item.get('action') or ''),
            'summary': str(item.get('summary') or ''),
        }
        for item in writes
    ]


def repo_root_from_inputs(*, repo_root: Path | None, config_path: Path) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return resolve_repo_root(Path(config_path).resolve())
