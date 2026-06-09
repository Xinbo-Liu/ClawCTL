from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.doctor.agent_modules.managed_probe_fixture import ManagedProbeExtensionFixture, materialize_managed_probe_extension

from openclaw.tests.support.helpers import isolated_test_root


ROOT_DIR = resolve_repo_root(Path(__file__))


@contextmanager
def managed_probe_repo(
    prefix: str,
    *,
    base_repo_root: Path | None = None,
) -> Iterator[ManagedProbeExtensionFixture]:
    with isolated_test_root(prefix) as repo_root:
        yield materialize_managed_probe_extension(
            repo_root,
            base_repo_root=ROOT_DIR if base_repo_root is None else Path(base_repo_root).resolve(),
        )
