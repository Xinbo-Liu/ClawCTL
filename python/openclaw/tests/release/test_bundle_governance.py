from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from openclaw.control_plane.stack.release import _should_hash_base_file
from openclaw.doctor.agent_modules.support import resolve_bash_executable
from openclaw.release import bundle_governance


class BundleGovernanceSourceClosureTest(unittest.TestCase):
    def test_full_source_preserves_all_hashable_base_material(self) -> None:
        """完整源码包保留来源摘要覆盖的全部基座文件，使无 Git 的解包目录也能校验溯源。"""
        root = bundle_governance.ROOT_DIR
        source_paths = {
            path.relative_to(root).as_posix()
            for path in root.rglob('*')
            if path.is_file() and _should_hash_base_file(path.relative_to(root))
        }
        bundle_paths = set(bundle_governance.resolve_bundle_files('full-source-governance'))
        self.assertTrue(source_paths)
        self.assertFalse(source_paths - bundle_paths, f'完整源码包遗漏基座来源文件：{sorted(source_paths - bundle_paths)}')


class BundleGovernanceZipModeTest(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        # 临时仓库独立声明快照，不继承 artifact smoke 源仓库的输入。
        os.environ.pop(bundle_governance.GIT_INDEX_SNAPSHOT_ENV, None)
        os.environ.pop(bundle_governance.GIT_INDEX_ROOT_ENV, None)

    def test_bound_snapshot_preserves_null_delimited_paths_without_git(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = '目录/含制表\t和换行\n的脚本.sh'
            snapshot = root / 'index.snapshot'
            snapshot.write_bytes(b'100755 ' + b'a' * 40 + b' 0\t' + path.encode('utf-8') + b'\0')
            with (
                patch.object(bundle_governance, 'ROOT_DIR', root),
                patch.dict(os.environ, {
                    bundle_governance.GIT_INDEX_SNAPSHOT_ENV: str(snapshot),
                    bundle_governance.GIT_INDEX_ROOT_ENV: str(root / '.'),
                }),
                patch.object(bundle_governance.subprocess, 'run') as run,
            ):
                self.assertEqual(bundle_governance._git_index_file_modes(), {path: '100755'})
                run.assert_not_called()

    def test_snapshot_arguments_fail_closed_before_git_or_stat_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / 'index.snapshot'
            snapshot.write_bytes(b'')
            cases = (
                {bundle_governance.GIT_INDEX_SNAPSHOT_ENV: str(snapshot)},
                {bundle_governance.GIT_INDEX_ROOT_ENV: str(root)},
                {bundle_governance.GIT_INDEX_SNAPSHOT_ENV: '', bundle_governance.GIT_INDEX_ROOT_ENV: str(root)},
                {bundle_governance.GIT_INDEX_SNAPSHOT_ENV: str(snapshot), bundle_governance.GIT_INDEX_ROOT_ENV: ''},
                {bundle_governance.GIT_INDEX_SNAPSHOT_ENV: str(snapshot), bundle_governance.GIT_INDEX_ROOT_ENV: str(root / 'other')},
                {bundle_governance.GIT_INDEX_SNAPSHOT_ENV: str(root / 'missing'), bundle_governance.GIT_INDEX_ROOT_ENV: str(root)},
                {bundle_governance.GIT_INDEX_SNAPSHOT_ENV: str(root), bundle_governance.GIT_INDEX_ROOT_ENV: str(root)},
            )
            for inputs in cases:
                with (
                    self.subTest(inputs=inputs),
                    patch.object(bundle_governance, 'ROOT_DIR', root),
                    patch.dict(os.environ, inputs),
                    patch.object(bundle_governance.subprocess, 'run') as run,
                ):
                    with self.assertRaises(bundle_governance.BundleGovernanceError):
                        bundle_governance._git_index_file_modes()
                    run.assert_not_called()

    def test_invalid_snapshot_preserves_previous_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / 'index.snapshot'
            snapshot.write_bytes(b'100644 hash 2\tREADME.md\0')
            zip_path = root / 'artifact.zip'
            zip_path.write_bytes(b'previous-archive')
            with (
                patch.object(bundle_governance, 'ROOT_DIR', root),
                patch.dict(os.environ, {
                    bundle_governance.GIT_INDEX_SNAPSHOT_ENV: str(snapshot),
                    bundle_governance.GIT_INDEX_ROOT_ENV: str(root),
                }),
            ):
                with self.assertRaises(bundle_governance.BundleGovernanceError):
                    bundle_governance._write_zip('probe', [], zip_path)
            self.assertEqual(zip_path.read_bytes(), b'previous-archive')

    def test_git_index_modes_read_null_delimited_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.git').mkdir()
            path = '目录/含制表\t和换行\n的脚本.sh'
            output = b'100755 ' + b'a' * 40 + b' 0\t' + path.encode('utf-8') + b'\0'
            with (
                patch.object(bundle_governance, 'ROOT_DIR', root),
                patch.object(bundle_governance.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output)) as run,
            ):
                self.assertEqual(bundle_governance._git_index_file_modes(), {path: '100755'})
                self.assertEqual(run.call_args.args[0], ['git', 'ls-files', '--stage', '-z'])

    def test_git_worktree_requires_readable_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.git').mkdir()
            for failure in (FileNotFoundError('git'), subprocess.CompletedProcess([], 128, b'')):
                with self.subTest(failure=type(failure).__name__):
                    with (
                        patch.object(bundle_governance, 'ROOT_DIR', root),
                        patch.object(bundle_governance.subprocess, 'run') as run,
                    ):
                        if isinstance(failure, Exception):
                            run.side_effect = failure
                        else:
                            run.return_value = failure
                        with self.assertRaises(bundle_governance.BundleGovernanceError):
                            bundle_governance._git_index_file_modes()

    def test_git_index_modes_reject_unmerged_or_malformed_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.git').mkdir()
            for output in (b'100644 hash 2\tREADME.md\0', b'invalid-record\0'):
                with self.subTest(output=output):
                    with (
                        patch.object(bundle_governance, 'ROOT_DIR', root),
                        patch.object(bundle_governance.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output)),
                    ):
                        with self.assertRaises(bundle_governance.BundleGovernanceError):
                            bundle_governance._git_index_file_modes()

    def test_unreadable_index_preserves_previous_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.git').mkdir()
            zip_path = root / 'artifact.zip'
            zip_path.write_bytes(b'previous-archive')
            with (
                patch.object(bundle_governance, 'ROOT_DIR', root),
                patch.object(bundle_governance.subprocess, 'run', side_effect=FileNotFoundError('git')),
            ):
                with self.assertRaises(bundle_governance.BundleGovernanceError):
                    bundle_governance._write_zip('probe', [], zip_path)
            self.assertEqual(zip_path.read_bytes(), b'previous-archive')

    def test_source_package_without_git_uses_file_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = root / 'README.md'
            payload.write_text('content\n', encoding='utf-8')
            with (
                patch.object(bundle_governance, 'ROOT_DIR', root),
                patch.object(bundle_governance.subprocess, 'run') as run,
            ):
                modes = bundle_governance._git_index_file_modes()
                self.assertEqual(modes, {})
                self.assertEqual(bundle_governance._zip_external_mode('README.md', modes), payload.stat().st_mode & 0xFFFF)
                run.assert_not_called()

    def test_write_zip_uses_git_index_executable_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / 'tool.sh'
            script.write_text('#!/usr/bin/env sh\nexit 0\n', encoding='utf-8')
            script.chmod(0o644)
            zip_path = root / 'artifact.zip'

            with (
                patch.object(bundle_governance, 'ROOT_DIR', root),
                patch.object(bundle_governance, '_git_index_file_modes', return_value={'tool.sh': '100755'}),
            ):
                bundle_governance._write_zip('probe', ['tool.sh'], zip_path)

            with zipfile.ZipFile(zip_path) as archive:
                mode = (archive.getinfo('tool.sh').external_attr >> 16) & 0o7777

        self.assertEqual(mode, 0o755)

    def test_compute_bom_uses_git_index_regular_file_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = root / 'README.md'
            payload.write_text('content\n', encoding='utf-8')
            payload.chmod(0o666)

            with (
                patch.object(bundle_governance, 'ROOT_DIR', root),
                patch.object(bundle_governance, '_git_index_file_modes', return_value={'README.md': '100644'}),
            ):
                rows = bundle_governance.compute_bom(['README.md'])

        self.assertEqual(rows[0]['mode'], '0o644')


class BundleExporterSnapshotTest(unittest.TestCase):
    def _prepare_exporter(self, root: Path) -> tuple[Path, Path]:
        """准备真实 exporter 与只记录容器传输参数的 runner，小夹具不执行全仓扫描。"""
        for relative in ('scripts/setup/export_clean_delivery_bundle.sh', 'scripts/lib/repo_root.sh'):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(bundle_governance.ROOT_DIR / relative, target)
        (root / 'python/openclaw').mkdir(parents=True)
        marker = root / 'scripts/runtime/run_openclaw_python_tool.sh'
        marker.parent.mkdir(parents=True)
        marker.write_text('#!/usr/bin/env bash\n', encoding='utf-8')
        runner = root / 'scripts/lib/run_static_python.sh'
        runner.write_text(
            '#!/usr/bin/env bash\nset -euo pipefail\n'
            'if [[ "$*" != *openclaw.release.bundle_governance* ]]; then cat >/dev/null; exit 0; fi\n'
            'printf "%s\\0" "$@" > "$EXPORT_TEST_CAPTURE_ARGS"\n'
            'printf "%s\\0%s\\0" "${OPENCLAW_BUNDLE_GIT_INDEX_SNAPSHOT-}" '
            '"${OPENCLAW_BUNDLE_GIT_INDEX_ROOT-}" > "$EXPORT_TEST_CAPTURE_ENV"\n',
            encoding='utf-8',
        )
        return root / 'capture.args', root / 'capture.env'

    def _run_exporter(self, root: Path, args_capture: Path, env_capture: Path) -> subprocess.CompletedProcess[str]:
        """在独立 shell 中执行 exporter；传入外仓快照以检查继承输入的隔离。"""
        bash = resolve_bash_executable()
        if not bash:
            raise AssertionError('交付入口回归需要 Bash')
        environment = dict(os.environ)
        environment.pop('BASH_ENV', None)
        environment.pop('OPENCLAW_REPO_ROOT_SH_LOADED', None)
        environment.update({
            bundle_governance.GIT_INDEX_SNAPSHOT_ENV: str(root / 'foreign.snapshot'),
            bundle_governance.GIT_INDEX_ROOT_ENV: str(root / 'foreign-repo'),
            'EXPORT_TEST_CAPTURE_ARGS': str(args_capture),
            'EXPORT_TEST_CAPTURE_ENV': str(env_capture),
        })
        return subprocess.run(
            [bash, str(root / 'scripts/setup/export_clean_delivery_bundle.sh'), '--output', str(root / 'artifact.zip'), '--quiet'],
            cwd=root,
            env=environment,
            text=True,
            encoding='utf-8',
            capture_output=True,
            check=False,
            timeout=20,
        )

    def test_exporter_does_not_forward_inherited_snapshot_for_no_git_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args_capture, env_capture = self._prepare_exporter(root)
            result = self._run_exporter(root, args_capture, env_capture)
            self.assertEqual(result.returncode, 0, result.stderr)
            arguments = args_capture.read_bytes().split(b'\0')
            self.assertFalse(any(b'OPENCLAW_BUNDLE_GIT_INDEX_' in argument for argument in arguments))
            self.assertEqual(env_capture.read_bytes(), b'\0\0')

    def test_exporter_git_failure_preserves_previous_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args_capture, env_capture = self._prepare_exporter(root)
            (root / '.git').write_text('invalid Git marker\n', encoding='utf-8')
            zip_path = root / 'artifact.zip'
            zip_path.write_bytes(b'previous-archive')
            result = self._run_exporter(root, args_capture, env_capture)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('宿主 Git 索引', result.stderr)
            self.assertEqual(zip_path.read_bytes(), b'previous-archive')
            self.assertFalse(args_capture.exists())


if __name__ == '__main__':
    unittest.main()
