from __future__ import annotations

import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from openclaw.release.bundle_runtime_checks import budget_failures, run_artifact_smoke


class BundleRuntimeChecksTest(unittest.TestCase):
    def test_budget_failures_report_zip_headroom_when_below_minimum(self) -> None:
        size_manifest = {
            'size': {'zipBytes': 950},
            'counts': {'files': 3},
        }

        failures = budget_failures(
            size_manifest,
            spec={'budget': {'maxZipBytes': 1000, 'minZipHeadroomBytes': 100}},
        )

        self.assertEqual(failures, ['zipBytes 余量不足：50 < 100'])

    def test_budget_failures_do_not_duplicate_headroom_when_zip_exceeds_max(self) -> None:
        size_manifest = {
            'size': {'zipBytes': 1100},
            'counts': {'files': 3},
        }

        failures = budget_failures(
            size_manifest,
            spec={'budget': {'maxZipBytes': 1000, 'minZipHeadroomBytes': 100}},
        )

        self.assertEqual(failures, ['zipBytes 超预算：1100 > 1000'])

    def test_artifact_smoke_shares_state_without_writing_into_source(self) -> None:
        """多个烟测步骤共享独立状态目录，解包源码保持原样且临时状态随执行结束清理。"""
        probe = (
            "import os, sys\n"
            "from pathlib import Path\n"
            "root = Path.cwd().resolve()\n"
            "state = Path(os.environ['HOST_STATE_DIR']).resolve()\n"
            "assert root != state and root not in state.parents\n"
            "assert sorted(path.relative_to(root).as_posix() for path in root.rglob('*')) == ['probe.py']\n"
            "assert (state / 'control_plane/dispatch').is_dir()\n"
            "marker = state / 'tmp/probe.txt'\n"
            "if sys.argv[1] == 'write':\n"
            "    marker.write_text('shared-state', encoding='utf-8')\n"
            "else:\n"
            "    assert marker.read_text(encoding='utf-8') == 'shared-state'\n"
            "print(state)\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / 'artifact.zip'
            with zipfile.ZipFile(zip_path, 'w') as archive:
                archive.writestr('probe.py', probe)
            results = run_artifact_smoke(
                'test-bundle',
                {
                    'artifactSmoke': [
                        {
                            'id': operation,
                            'env': {'HOST_STATE_DIR': '{artifact_state_root}'},
                            'command': ['{python}', '-B', 'probe.py', operation],
                        }
                        for operation in ('write', 'read')
                    ]
                },
                zip_path,
                artifact_smoke_active_env='OPENCLAW_TEST_ARTIFACT_SMOKE_ACTIVE',
                error_factory=RuntimeError,
            )

        self.assertEqual([row['returncode'] for row in results], [0, 0], msg=results)
        self.assertEqual(results[0]['stdout'], results[1]['stdout'])
        self.assertFalse(Path(results[0]['stdout']).exists())

    @unittest.skipIf(os.name == 'nt', 'POSIX executable mode is not reliable on Windows')
    def test_artifact_smoke_restores_zip_executable_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zip_path = root / 'artifact.zip'
            info = zipfile.ZipInfo('tool.sh')
            info.external_attr = (0o100755 & 0xFFFF) << 16
            with zipfile.ZipFile(zip_path, 'w') as archive:
                archive.writestr(info, '#!/usr/bin/env sh\nexit 0\n')

            results = run_artifact_smoke(
                'test-bundle',
                {
                    'artifactSmoke': [
                        {
                            'id': 'mode_check',
                            'cwd': '.',
                            'command': [
                                '{python}',
                                '-c',
                                "import os, stat, sys; mode=stat.S_IMODE(os.stat('tool.sh').st_mode); sys.exit(0 if mode & 0o111 else 1)",
                            ],
                        }
                    ]
                },
                zip_path,
                artifact_smoke_active_env='OPENCLAW_TEST_ARTIFACT_SMOKE_ACTIVE',
                error_factory=RuntimeError,
            )

        self.assertEqual(results[0]['returncode'], 0, msg=results)


if __name__ == '__main__':
    unittest.main()
