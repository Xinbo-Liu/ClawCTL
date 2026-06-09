from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openclaw.guards.host_python_doc_guard import scan_file


class HostPythonDocGuardTest(unittest.TestCase):
    def test_repo_cli_module_example_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            target = repo_root / 'docs' / 'guide.md'
            target.parent.mkdir(parents=True)
            target.write_text(
                'python -m openclaw.cli dispatch ops commands --control-plane-profile agent_platform\n',
                encoding='utf-8',
            )

            hits = scan_file(repo_root, target)

        self.assertEqual(
            hits,
            ['docs/guide.md:1:python -m openclaw.cli'],
        )

    def test_repo_unittest_module_example_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            target = repo_root / 'docs' / 'guide.md'
            target.parent.mkdir(parents=True)
            target.write_text(
                'python -m unittest openclaw.tests.governance.test_package_layout -q\n',
                encoding='utf-8',
            )

            hits = scan_file(repo_root, target)

        self.assertEqual(hits, [])

    def test_mixed_supported_and_disallowed_repo_root_examples_on_same_line_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            target = repo_root / 'docs' / 'guide.md'
            target.parent.mkdir(parents=True)
            target.write_text(
                'python -m openclaw.testing.repo_host suite repo-check -q ; python -m openclaw.cli\n',
                encoding='utf-8',
            )

            hits = scan_file(repo_root, target)

        self.assertEqual(
            hits,
            ['docs/guide.md:1:python -m openclaw.cli'],
        )

    def test_extension_cli_module_example_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            target = repo_root / 'agent' / 'extensions' / 'demo' / 'README.md'
            target.parent.mkdir(parents=True)
            target.write_text(
                '- CLI workflow: `python -m openclaw_ext_demo.modules.fetch.workflow`\n',
                encoding='utf-8',
            )

            hits = scan_file(repo_root, target)

        self.assertEqual(
            hits,
            ['agent/extensions/demo/README.md:1:python -m openclaw_ext_demo.modules.fetch.workflow'],
        )

    def test_run_openclaw_python_tool_example_does_not_trigger(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            target = repo_root / 'docs' / 'guide.md'
            target.parent.mkdir(parents=True)
            target.write_text(
                'bash ./scripts/runtime/run_openclaw_python_tool.sh dispatch ops commands --control-plane-profile agent_platform\n',
                encoding='utf-8',
            )

            hits = scan_file(repo_root, target)

        self.assertEqual(hits, [])

    def test_repo_host_python_entry_example_does_not_trigger(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            target = repo_root / 'docs' / 'guide.md'
            target.parent.mkdir(parents=True)
            target.write_text(
                'python -m openclaw.testing.repo_host suite repo-check -q\n',
                encoding='utf-8',
            )

            hits = scan_file(repo_root, target)

        self.assertEqual(hits, [])

    def test_wrong_python_namespace_unittest_example_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            target = repo_root / 'docs' / 'guide.md'
            target.parent.mkdir(parents=True)
            target.write_text(
                'python -m unittest python.openclaw.tests.governance.test_delivery_cleanliness -q\n',
                encoding='utf-8',
            )

            hits = scan_file(repo_root, target)

        self.assertEqual(
            hits,
            ['docs/guide.md:1:python -m unittest python.openclaw.tests.governance.test_delivery_cleanliness'],
        )


if __name__ == '__main__':
    unittest.main()
