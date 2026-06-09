"""OpenClaw Python 控制面入口。"""

from __future__ import annotations

import os
import atexit
import shutil
import sys
from pathlib import Path

# 统一阻断仓库运行链在工作树内写入 __pycache__/*.pyc，避免静态回归与干净交付导出前再次污染工作区。
os.environ.setdefault('PYTHONDONTWRITEBYTECODE', '1')
sys.dont_write_bytecode = True


def _cleanup_local_bytecode_residue() -> None:
    package_root = Path(__file__).resolve().parent
    pycache_root = package_root / '__pycache__'
    if pycache_root.is_dir():
        shutil.rmtree(pycache_root, ignore_errors=True)


_cleanup_local_bytecode_residue()
atexit.register(_cleanup_local_bytecode_residue)

if __name__ == 'python.openclaw':
    raise ImportError(
        '禁止通过 `python.openclaw...` 导入 OpenClaw；请改用 `openclaw...`，'
        '例如 `python -m unittest openclaw.tests...` 或 `python -m openclaw.testing.repo_host ...`。'
    )
