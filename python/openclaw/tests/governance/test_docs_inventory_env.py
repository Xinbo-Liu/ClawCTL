"""用局部 shell 夹具验证文档清单选择、仓库绑定和容器参数，不扫描真实仓库。"""
from __future__ import annotations

import base64
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from openclaw.lib.repo.layout import resolve_repo_root


ROOT_DIR = resolve_repo_root(Path(__file__))
HELPER = ROOT_DIR / 'scripts/lib/docs_inventory_env.sh'
INVENTORY_ENV_KEYS = (
    'OPENCLAW_DOCS_TRACKED_FILES_B64',
    'OPENCLAW_DOCS_TRACKED_FILES_ROOT',
    'OPENCLAW_DOCS_INVENTORY_BOM_PATH',
    'OPENCLAW_DOCS_INVENTORY_ENV_SH_LOADED',
)


class DocsInventoryEnvironmentTest(unittest.TestCase):
    """覆盖正常参数以及会导致范围错误的失败路径。"""

    def _run(self, root: Path, *, values: dict[str, str] | None = None, cwd: Path | None = None, tools: Path | None = None) -> subprocess.CompletedProcess[bytes]:
        env = {key: value for key, value in os.environ.items() if key not in INVENTORY_ENV_KEYS}
        env.update(values or {})
        if tools is not None:
            env['PATH'] = str(tools) + os.pathsep + env.get('PATH', '')
        script = '\n'.join([
            'set -euo pipefail',
            'source "$1"',
            'openclaw_docs_inventory_prepare_runner_args "$2"',
            'if ((${#OPENCLAW_DOCS_INVENTORY_RUNNER_ARGS[@]})); then',
            "  printf '%s\\0' \"${OPENCLAW_DOCS_INVENTORY_RUNNER_ARGS[@]}\"",
            'fi',
        ])
        return subprocess.run([shutil.which('bash') or 'bash', '-c', script, 'docs-inventory-env', str(HELPER), str(root)], cwd=cwd or root, env=env, capture_output=True, timeout=10)

    def _args(self, result: subprocess.CompletedProcess[bytes]) -> list[str]:
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        return result.stdout.decode('utf-8').rstrip('\0').split('\0') if result.stdout else []

    def _git_tool(self, root: Path, text: str) -> Path:
        tools = root / 'fixture-tools'
        tools.mkdir()
        git = tools / 'git'
        git.write_text('#!/usr/bin/env bash\nset -euo pipefail\n' + text + '\n', encoding='utf-8')
        git.chmod(0o755)
        return tools

    def test_git_snapshot_preserves_paths_and_uses_case_insensitive_source(self) -> None:
        with tempfile.TemporaryDirectory(prefix='docs inventory 中文 ') as directory:
            root = Path(directory)
            (root / '.git').mkdir()
            tools = self._git_tool(root, '\n'.join([
                '[[ "$#" == 9 && "$1" == -C && "$3" == ls-files && "$4" == -z ]]',
                '[[ "$5" == --cached && "$6" == --others && "$7" == --exclude-standard && "$8" == -- && "$9" == ":(icase)*.md" ]]',
                "printf 'README.md\\0中文 页面.MD\\0'",
            ]))
            args = self._args(self._run(root, tools=tools, values={'OPENCLAW_DOCS_TRACKED_FILES_B64': 'stale', 'OPENCLAW_DOCS_TRACKED_FILES_ROOT': '/other/root'}))
            self.assertEqual(args[0], '--env')
            encoded = args[1].split('=', 1)[1]
            self.assertEqual(base64.b64decode(encoded).decode('utf-8'), 'README.md\0中文 页面.MD\0')
            self.assertEqual(args[2:], ['--env', 'OPENCLAW_DOCS_TRACKED_FILES_ROOT=' + str(root.resolve())])

    def test_explicit_relative_bom_wins_and_mount_matches_absolute_env(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.git').mkdir()
            bom = root / '中文 交付.bom.json'
            bom.write_text('{"files": []}', encoding='utf-8')
            tools = self._git_tool(root, 'exit 97')
            args = self._args(self._run(root, tools=tools, values={
                'OPENCLAW_DOCS_INVENTORY_BOM_PATH': bom.name,
                'OPENCLAW_DOCS_TRACKED_FILES_B64': '!stale',
                'OPENCLAW_DOCS_TRACKED_FILES_ROOT': '/other/root',
            }))
            self.assertEqual(args, ['--mount', str(bom.resolve()), '--env', 'OPENCLAW_DOCS_INVENTORY_BOM_PATH=' + str(bom.resolve()), '--env', 'OPENCLAW_DOCS_TRACKED_FILES_B64=', '--env', 'OPENCLAW_DOCS_TRACKED_FILES_ROOT='])

    def test_bound_snapshot_can_be_forwarded_without_git(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            encoded = base64.b64encode(b'README.md\0').decode('ascii')
            args = self._args(self._run(root, values={'OPENCLAW_DOCS_TRACKED_FILES_B64': encoded, 'OPENCLAW_DOCS_TRACKED_FILES_ROOT': str(root)}))
            self.assertEqual(args, ['--env', 'OPENCLAW_DOCS_TRACKED_FILES_B64=' + encoded, '--env', 'OPENCLAW_DOCS_TRACKED_FILES_ROOT=' + str(root.resolve())])

    def test_empty_git_snapshot_keeps_explicit_root_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self._args(self._run(root, values={'OPENCLAW_DOCS_TRACKED_FILES_B64': '', 'OPENCLAW_DOCS_TRACKED_FILES_ROOT': str(root)}))
            self.assertEqual(args, ['--env', 'OPENCLAW_DOCS_TRACKED_FILES_B64=', '--env', 'OPENCLAW_DOCS_TRACKED_FILES_ROOT=' + str(root.resolve())])

    def test_missing_bom_fails_instead_of_falling_back_to_git(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self._run(root, values={'OPENCLAW_DOCS_INVENTORY_BOM_PATH': 'missing.bom.json'})
            self.assertEqual(result.returncode, 2)
            self.assertIn('显式文档 BOM', result.stderr.decode('utf-8'))

    def test_snapshot_without_root_binding_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self._run(root, values={'OPENCLAW_DOCS_TRACKED_FILES_B64': 'UkVBRE1FLm1kAA=='})
            self.assertEqual(result.returncode, 2)
            self.assertIn('仓库 ROOT 绑定', result.stderr.decode('utf-8'))

    def test_other_repository_snapshot_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root, other = parent / 'current', parent / 'other'
            root.mkdir()
            other.mkdir()
            result = self._run(root, values={'OPENCLAW_DOCS_TRACKED_FILES_B64': 'UkVBRE1FLm1kAA==', 'OPENCLAW_DOCS_TRACKED_FILES_ROOT': str(other)})
            self.assertEqual(result.returncode, 2)
            self.assertIn('另一仓库', result.stderr.decode('utf-8'))

    def test_git_failure_does_not_reuse_inherited_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.git').mkdir()
            tools = self._git_tool(root, 'exit 97')
            result = self._run(root, tools=tools, values={'OPENCLAW_DOCS_TRACKED_FILES_B64': 'UkVBRE1FLm1kAA==', 'OPENCLAW_DOCS_TRACKED_FILES_ROOT': str(root)})
            self.assertEqual(result.returncode, 2)
            self.assertIn('Git 文档清单读取失败', result.stderr.decode('utf-8'))

    def test_no_inventory_source_keeps_python_failure_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self._args(self._run(Path(directory))), [])


if __name__ == '__main__':
    unittest.main()
