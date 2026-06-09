"""Repo-local Python startup hook for disabling bytecode writes."""
from __future__ import annotations

import os
import atexit
import shutil
import sys
from pathlib import Path

os.environ.setdefault('PYTHONDONTWRITEBYTECODE', '1')
sys.dont_write_bytecode = True

_PYCACHE = Path(__file__).resolve().parent / '__pycache__'


def _cleanup_local_bytecode_residue() -> None:
    if _PYCACHE.is_dir():
        shutil.rmtree(_PYCACHE, ignore_errors=True)


_cleanup_local_bytecode_residue()
atexit.register(_cleanup_local_bytecode_residue)
