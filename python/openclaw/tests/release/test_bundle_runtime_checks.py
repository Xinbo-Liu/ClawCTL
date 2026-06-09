from __future__ import annotations

import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from openclaw.release.bundle_runtime_checks import run_artifact_smoke


class BundleRuntimeChecksTest(unittest.TestCase):
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
