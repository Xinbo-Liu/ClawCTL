from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from openclaw.doctor.platform.temp_workspace import global_tmp_root, make_temp_dir, prune_empty_parents, remove_tree
from openclaw.lib.repo.layout import resolve_repo_root

ROOT_DIR = resolve_repo_root(Path(__file__))


@contextmanager
def isolated_test_root(prefix: str) -> Iterator[Path]:
    root = make_temp_dir(ROOT_DIR, category='tests', prefix=prefix)
    try:
        yield root
    finally:
        try:
            remove_tree(root)
        except OSError:
            pass
        prune_empty_parents(root.parent, stop_at=global_tmp_root())
