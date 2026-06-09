from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from openclaw.control_plane.module_scheduler.binding import _single_registry_collection_dir
from openclaw.control_plane.registry import CliError


class ModuleSchedulerBindingRegistryPathTest(unittest.TestCase):
    def test_single_registry_collection_dir_accepts_single_directory_entry(self) -> None:
        with TemporaryDirectory() as tmpdir:
            jobs_dir = Path(tmpdir).resolve()
            resolved = _single_registry_collection_dir({'registryPaths': {'jobs': [str(jobs_dir)]}}, key='jobs', label='jobs')
        self.assertEqual(resolved, jobs_dir)

    def test_single_registry_collection_dir_rejects_multiple_directory_entries(self) -> None:
        with TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / 'a').mkdir()
            (base / 'b').mkdir()
            with self.assertRaises(CliError) as ctx:
                _single_registry_collection_dir({'registryPaths': {'jobs': [str((base / 'a').resolve()), str((base / 'b').resolve())]}}, key='jobs', label='jobs')
        self.assertIn('必须唯一', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
