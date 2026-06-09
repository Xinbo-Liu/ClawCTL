from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.path_contracts import extension_anchored_path, resolve_path_contract
from openclaw.tests.support.managed_extensions import managed_extensions, representative_managed_extension


ROOT_DIR = resolve_repo_root(Path(__file__))
MANAGED_EXTENSIONS = tuple(sorted(managed_extensions(ROOT_DIR), key=lambda row: row.id))


class RepoPathContractsTest(unittest.TestCase):
    def test_repo_anchored_path_resolves_from_repo_root(self) -> None:
        resolved = resolve_path_contract(
            '@repo/config/control_plane/service.json',
            base_dir=ROOT_DIR,
            start_path=ROOT_DIR,
        )
        self.assertEqual(
            resolved,
            (ROOT_DIR / 'config' / 'control_plane' / 'service.json').resolve(),
        )

    def test_repo_anchored_path_rejects_repo_escape(self) -> None:
        with self.assertRaisesRegex(ValueError, 'must stay inside the repository'):
            resolve_path_contract(
                '@repo/../../outside.txt',
                base_dir=ROOT_DIR,
                start_path=ROOT_DIR,
            )

    def test_extension_anchored_path_resolves_from_extension_root(self) -> None:
        if not MANAGED_EXTENSIONS:
            self.skipTest('base release surface has no repo-managed extension')
        extension = representative_managed_extension(ROOT_DIR)
        module_dir = extension.root_dir / 'agent'
        target_file = self._representative_extension_python_file(extension.python_roots)
        resolved = resolve_path_contract(
            extension_anchored_path(target_file.relative_to(extension.root_dir).as_posix()),
            base_dir=module_dir,
            start_path=module_dir,
        )

        self.assertEqual(resolved, target_file.resolve())

    def test_extension_anchored_path_rejects_extension_escape(self) -> None:
        if not MANAGED_EXTENSIONS:
            self.skipTest('base release surface has no repo-managed extension')
        extension = representative_managed_extension(ROOT_DIR)
        module_dir = extension.root_dir / 'agent'
        with self.assertRaisesRegex(ValueError, 'must stay inside the extension root'):
            resolve_path_contract(
                '@extension/../outside/README.md',
                base_dir=module_dir,
                start_path=module_dir,
            )

    def test_extension_anchored_path_rejects_non_contract_extension_root(self) -> None:
        with TemporaryDirectory() as tmpdir:
            external_root = Path(tmpdir) / 'external_extension'
            external_root.mkdir(parents=True)

            with self.assertRaisesRegex(ValueError, 'cannot resolve extension root'):
                resolve_path_contract(
                    '@extension/README.md',
                    base_dir=external_root,
                    start_path=external_root,
                )

    def test_control_plane_machine_configs_do_not_use_parent_traversal_paths(self) -> None:
        scan_roots = (
            ROOT_DIR / 'config' / 'control_plane',
            ROOT_DIR / 'agent' / 'extensions',
        )
        violations: list[str] = []
        for scan_root in scan_roots:
            for path in sorted(scan_root.rglob('*.json')):
                text = path.read_text(encoding='utf-8')
                if '../' in text or '..\\' in text:
                    violations.append(path.relative_to(ROOT_DIR).as_posix())

        self.assertEqual(violations, [])

    @staticmethod
    def _representative_extension_python_file(python_roots: tuple[Path, ...]) -> Path:
        for python_root in python_roots:
            for path in sorted(python_root.rglob('*.py')):
                if path.is_file():
                    return path
        raise AssertionError('expected representative managed extension to contain at least one Python file')

    def test_project_surface_does_not_use_deep_parent_traversal_paths(self) -> None:
        scan_roots = (
            ROOT_DIR / 'agent',
            ROOT_DIR / 'config',
            ROOT_DIR / 'docs',
            ROOT_DIR / 'python',
            ROOT_DIR / 'scripts',
        )
        text_suffixes = {'.json', '.md', '.py', '.sh'}
        deep_parent = '../' * 2
        violations: list[str] = []
        for scan_root in scan_roots:
            for path in sorted(scan_root.rglob('*')):
                if not path.is_file() or '__pycache__' in path.parts:
                    continue
                if (ROOT_DIR / 'python' / 'openclaw' / 'tests') in path.parents:
                    continue
                if path.suffix not in text_suffixes and 'bin' not in path.parts:
                    continue
                text = path.read_text(encoding='utf-8', errors='ignore').replace('\\', '/')
                if deep_parent in text:
                    violations.append(path.relative_to(ROOT_DIR).as_posix())

        self.assertEqual(violations, [])


if __name__ == '__main__':
    unittest.main()
