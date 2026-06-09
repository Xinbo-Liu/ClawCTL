"""Repository-root package that forwards imports to ``python/openclaw``."""
from __future__ import annotations

import importlib.util
import atexit
import os
import shutil
import sys
from pathlib import Path

os.environ.setdefault('PYTHONDONTWRITEBYTECODE', '1')
sys.dont_write_bytecode = True

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ROOT_SHIM_DIR = Path(__file__).resolve().parent
_PYTHON_ROOT = (_REPO_ROOT / 'python').resolve()
_CANONICAL_PACKAGE_DIR = (_PYTHON_ROOT / 'openclaw').resolve()
_CANONICAL_INIT = (_CANONICAL_PACKAGE_DIR / '__init__.py').resolve()
_CANONICAL_SPEC = importlib.util.spec_from_file_location(
    __name__,
    _CANONICAL_INIT,
    submodule_search_locations=[str(_CANONICAL_PACKAGE_DIR)],
)

if _CANONICAL_SPEC is None or _CANONICAL_SPEC.loader is None:
    raise ImportError(f'cannot build canonical package spec for {_CANONICAL_INIT}')

if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

def _cleanup_root_pycache() -> None:
    root_pycache = _ROOT_SHIM_DIR / '__pycache__'
    if root_pycache.is_dir():
        shutil.rmtree(root_pycache, ignore_errors=True)


_cleanup_root_pycache()
atexit.register(_cleanup_root_pycache)

__file__ = str(_CANONICAL_INIT)
__path__ = list(_CANONICAL_SPEC.submodule_search_locations or [str(_CANONICAL_PACKAGE_DIR)])
__package__ = __name__
__loader__ = _CANONICAL_SPEC.loader
__spec__ = _CANONICAL_SPEC
if _CANONICAL_SPEC.cached:
    __cached__ = str(_CANONICAL_SPEC.cached)

exec(compile(_CANONICAL_INIT.read_text(encoding='utf-8'), str(_CANONICAL_INIT), 'exec'), globals(), globals())
