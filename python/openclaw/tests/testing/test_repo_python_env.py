from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from openclaw.doctor.agent_modules.support import resolve_bash_executable
from openclaw.lib.repo.layout import resolve_repo_root

ROOT_DIR = resolve_repo_root(Path(__file__))
TRUTH_REL = Path('config/governance/support/repo_python_bootstrap.env')


class RepoPythonEnvScriptTest(unittest.TestCase):
    def test_repo_python_env_reads_bootstrap_truth_and_emits_runner_env_args(self) -> None:
        bash_executable = resolve_bash_executable()
        if not bash_executable:
            self.skipTest('未找到可用 bash；跳过 shell 集成测试')

        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = (Path(tmpdir) / 'repo').resolve()
            (repo_root / 'config' / 'governance' / 'support').mkdir(parents=True, exist_ok=True)
            (repo_root / 'python').mkdir(parents=True, exist_ok=True)
            (repo_root / 'alt_python').mkdir(parents=True, exist_ok=True)
            (repo_root / TRUTH_REL).write_text(
                "\n".join(
                    [
                        "OPENCLAW_REPO_BOOTSTRAP_PYTHONPATH_RELS='python|alt_python'",
                        "OPENCLAW_REPO_BOOTSTRAP_PYTHONDONTWRITEBYTECODE='1'",
                        "OPENCLAW_REPO_BOOTSTRAP_PYTHONIOENCODING='UTF-8'",
                        "OPENCLAW_REPO_BOOTSTRAP_PYTHONUTF8='1'",
                        "",
                    ]
                ),
                encoding='utf-8',
            )

            result = subprocess.run(
                [
                    str(bash_executable),
                    '-lc',
                    (
                        'set -euo pipefail; '
                        'source ./scripts/lib/repo_python_env.sh; '
                        'args="$(openclaw_repo_python_env_args '
                        f'"{repo_root.as_posix()}"'
                        " | tr '\\0' '\\n')\"; "
                        'printf "%s" "$args"'
                    ),
                ],
                cwd=ROOT_DIR,
                text=True,
                encoding='utf-8',
                errors='replace',
                capture_output=True,
                env=dict(
                    os.environ,
                    OPENCLAW_REPO_PYTHON_PROXY_PYTHON=sys.executable,
                    PYTHONPATH='/tmp/host-pythonpath-must-not-leak',
                ),
                check=False,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                '--env',
                'PYTHONDONTWRITEBYTECODE=1',
                '--env',
                'PYTHONIOENCODING=UTF-8',
                '--env',
                'PYTHONUTF8=1',
                '--env',
                f'PYTHONPATH={repo_root.as_posix()}/python:{repo_root.as_posix()}/alt_python',
            ],
        )

    def test_repo_python_env_shell_path_does_not_require_python_proxy(self) -> None:
        bash_executable = resolve_bash_executable()
        if not bash_executable:
            self.skipTest('未找到可用 bash；跳过 shell 集成测试')

        result = subprocess.run(
            [
                str(bash_executable),
                '-lc',
                (
                    'set -euo pipefail; '
                    'source ./scripts/lib/repo_python_env.sh; '
                    'openclaw_repo_python_env_args "$PWD" | tr "\\0" "\\n"'
                ),
            ],
            cwd=ROOT_DIR,
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            env={key: value for key, value in os.environ.items() if key != 'OPENCLAW_REPO_PYTHON_PROXY_PYTHON'},
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
        self.assertIn('PYTHONDONTWRITEBYTECODE=1', result.stdout)
        self.assertIn('PYTHONIOENCODING=UTF-8', result.stdout)
        self.assertIn('PYTHONUTF8=1', result.stdout)
        self.assertRegex(result.stdout, r'PYTHONPATH=.*clawctl/python')


if __name__ == '__main__':
    unittest.main()
