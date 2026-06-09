from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from openclaw.lib.runtime import resolver_loader


class PathResolverCacheTest(unittest.TestCase):
    def tearDown(self) -> None:
        resolver_loader.clear_path_resolver_cache()

    def test_build_path_resolver_reuses_same_repo_config_instance(self) -> None:
        calls: list[tuple[Path, Path | None]] = []

        class FakeResolverFactory:
            @staticmethod
            def from_repo_root(repo_root: Path, *, config_path: Path | None = None):
                calls.append((repo_root, config_path))
                return SimpleNamespace(repo_root=repo_root, config_path=config_path, call_index=len(calls))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / 'service.json'
            config_path.write_text('{}\n', encoding='utf-8')
            with mock.patch.object(resolver_loader, 'load_path_resolver_class', return_value=FakeResolverFactory):
                first = resolver_loader.build_path_resolver(repo_root=root, config_path=config_path)
                second = resolver_loader.build_path_resolver(repo_root=root, config_path=config_path)

        self.assertIs(first, second)
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
