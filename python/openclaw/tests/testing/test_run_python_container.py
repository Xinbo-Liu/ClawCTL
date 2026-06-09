from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from openclaw.doctor.agent_modules.support import resolve_bash_executable
from openclaw.lib.repo.layout import resolve_repo_root


ROOT_DIR = resolve_repo_root(Path(__file__))
SCRIPT_PATH = ROOT_DIR / 'scripts' / 'runtime' / 'run_python_container.sh'


class RunPythonContainerScriptTest(unittest.TestCase):
    def _direct_mode_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            {
                'PYTHONDONTWRITEBYTECODE': '1',
                'PYTHONIOENCODING': 'utf-8',
                'PYTHONUTF8': '1',
                'PYTHON_BIN': sys.executable,
                'OPENCLAW_PYTHON_CONTAINER_IN_CONTAINER': '1',
            }
        )
        return env

    def test_runs_directly_when_already_inside_python_container(self) -> None:
        bash_executable = resolve_bash_executable()
        if not bash_executable:
            self.skipTest('未找到可用 bash；跳过 shell 集成测试')
        env = self._direct_mode_env()

        result = subprocess.run(
            [
                str(bash_executable),
                str(SCRIPT_PATH),
                '--workdir',
                str(ROOT_DIR),
                '--env',
                'OPENCLAW_PYTHON_CONTAINER_DIRECT_TEST=ok',
                '--mount',
                str(ROOT_DIR),
                '--',
                '-c',
                'import os; print(os.environ["OPENCLAW_PYTHON_CONTAINER_DIRECT_TEST"])',
            ],
            cwd=ROOT_DIR,
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            env=env,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), 'ok')

    def test_mount_to_runs_directly_when_container_target_exists(self) -> None:
        bash_executable = resolve_bash_executable()
        if not bash_executable:
            self.skipTest('未找到可用 bash；跳过 shell 集成测试')
        env = self._direct_mode_env()
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir) / 'host-state'
            source_dir.mkdir()

            result = subprocess.run(
                [
                    str(bash_executable),
                    str(SCRIPT_PATH),
                    '--workdir',
                    str(ROOT_DIR),
                    '--mount-to',
                    str(source_dir),
                    '/',
                    '--',
                    '-c',
                    'print(1)',
                ],
                cwd=ROOT_DIR,
                text=True,
                encoding='utf-8',
                errors='replace',
                capture_output=True,
                env=env,
                check=False,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), '1')

    def test_regular_command_does_not_consume_pipeline_stdin_when_direct(self) -> None:
        bash_executable = resolve_bash_executable()
        if not bash_executable:
            self.skipTest('未找到可用 bash；跳过 shell 集成测试')
        env = self._direct_mode_env()
        env.update({'ROOT_DIR': str(ROOT_DIR), 'SCRIPT_PATH': str(SCRIPT_PATH)})

        shell_script = r'''
set -euo pipefail
probe_file="$(mktemp)"
trap 'rm -f "$probe_file"' EXIT
tail_value="$(
  printf 'openclaw-stdin-sentinel\n' | {
    bash "$SCRIPT_PATH" --workdir "$ROOT_DIR" -- -c 'print("runner-ok")' >"$probe_file"
    cat
  }
)"
printf 'probe=%s\n' "$(cat "$probe_file")"
printf 'tail=%s\n' "$tail_value"
'''
        result = subprocess.run(
            [str(bash_executable), '-s'],
            cwd=ROOT_DIR,
            input=shell_script,
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            env=env,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn('probe=runner-ok', result.stdout)
        self.assertIn('tail=openclaw-stdin-sentinel', result.stdout)

    def test_stdin_flag_passes_data_stdin_when_direct(self) -> None:
        bash_executable = resolve_bash_executable()
        if not bash_executable:
            self.skipTest('未找到可用 bash；跳过 shell 集成测试')

        result = subprocess.run(
            [
                str(bash_executable),
                str(SCRIPT_PATH),
                '--workdir',
                str(ROOT_DIR),
                '--stdin',
                '--',
                '-c',
                'import sys; print(sys.stdin.read().strip())',
            ],
            cwd=ROOT_DIR,
            input='openclaw-stdin-data\n',
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            env=self._direct_mode_env(),
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), 'openclaw-stdin-data')

    def test_python_source_stdin_still_attaches_automatically_when_direct(self) -> None:
        bash_executable = resolve_bash_executable()
        if not bash_executable:
            self.skipTest('未找到可用 bash；跳过 shell 集成测试')

        result = subprocess.run(
            [
                str(bash_executable),
                str(SCRIPT_PATH),
                '--workdir',
                str(ROOT_DIR),
                '--',
                '-',
            ],
            cwd=ROOT_DIR,
            input='print("stdin-source-ok")\n',
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            env=self._direct_mode_env(),
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), 'stdin-source-ok')


if __name__ == '__main__':
    unittest.main()
