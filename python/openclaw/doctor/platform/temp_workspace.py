#!/usr/bin/env python3
"""Doctor 临时工作区辅助。"""
from __future__ import annotations

import os
import shutil
import stat
import tempfile
import time
from pathlib import Path

FALSEY_ENV_VALUES = {"", "0", "false", "no", "off"}


def _default_global_tmp_root() -> Path:
    base = Path(tempfile.gettempdir()).expanduser().resolve()
    leaf = 'OpenClaw' if os.name == 'nt' else 'openclaw'
    return (base / leaf / '_tmp').resolve()


def global_tmp_root() -> Path:
    override = str(os.environ.get("OPENCLAW_GLOBAL_TMP_ROOT") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return _default_global_tmp_root()


def project_tmp_root(repo_root: Path) -> Path:
    override = str(os.environ.get("OPENCLAW_PROJECT_TMP_ROOT") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (global_tmp_root() / Path(repo_root).resolve().name).resolve()


def _is_within(path: Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def category_tmp_root(repo_root: Path, category: str) -> Path:
    normalized_category = str(category or "").strip().strip("/\\")
    if not normalized_category:
        raise ValueError("category 不能为空")
    resolved_repo_root = Path(repo_root).resolve()
    root = project_tmp_root(resolved_repo_root)
    if _is_within(root, resolved_repo_root):
        raise OSError(f"refusing to place transient workspace inside repository: {root}")
    path = root / normalized_category
    path.mkdir(parents=True, exist_ok=True)
    return path


def make_temp_dir(repo_root: Path, *, category: str, prefix: str) -> Path:
    normalized_prefix = str(prefix or "").strip() or "openclaw_tmp"
    root = category_tmp_root(repo_root, category)
    return Path(tempfile.mkdtemp(prefix=f"{normalized_prefix}_", dir=root)).resolve()


def keep_temp_dirs(flag_name: str) -> bool:
    raw = os.environ.get(flag_name)
    if raw is None:
        return False
    return str(raw).strip().lower() not in FALSEY_ENV_VALUES


def _clear_readonly(func: object, path: str, _exc_info: object) -> None:
    target = Path(path)
    if not target.exists():
        return
    os.chmod(target, stat.S_IWRITE)
    func(path)


def remove_tree(path: Path, *, attempts: int = 6, delay_seconds: float = 0.25) -> None:
    target = Path(path)
    last_error: OSError | None = None
    for attempt in range(max(1, attempts)):
        if not target.exists():
            return
        try:
            shutil.rmtree(target, onerror=_clear_readonly)
            if not target.exists():
                return
        except FileNotFoundError:
            return
        except OSError as exc:
            last_error = exc
        time.sleep(delay_seconds * (attempt + 1))
    if target.exists():
        raise OSError(f"failed to remove transient directory: {target}") from last_error


def prune_empty_parents(path: Path, *, stop_at: Path) -> None:
    current = Path(path).resolve()
    sentinel = Path(stop_at).resolve()
    if current == sentinel or sentinel not in {current, *current.parents}:
        return
    while current != sentinel:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent
