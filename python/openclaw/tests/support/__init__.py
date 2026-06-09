from __future__ import annotations

import atexit
import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True

_CACHE_DIR = Path(__file__).resolve().parent / '__pycache__'


def _cleanup_bytecode_cache() -> None:
    shutil.rmtree(_CACHE_DIR, ignore_errors=True)


_cleanup_bytecode_cache()
atexit.register(_cleanup_bytecode_cache)
