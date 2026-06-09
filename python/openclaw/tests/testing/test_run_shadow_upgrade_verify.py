from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from openclaw.doctor.agent_modules.support import resolve_bash_executable
from openclaw.lib.repo.layout import resolve_repo_root


ROOT_DIR = resolve_repo_root(Path(__file__))
SCRIPT_PATH = ROOT_DIR / 'scripts' / 'gateway' / 'run_shadow_upgrade_verify.sh'


class RunShadowUpgradeVerifyScriptTest(unittest.TestCase):
    def _path_without_docker(self, path_value: str) -> str:
        filtered: list[str] = []
        for raw_entry in path_value.split(os.pathsep):
            entry = raw_entry.strip()
            if not entry:
                continue
            entry_path = Path(entry)
            if any((entry_path / candidate).exists() for candidate in ('docker', 'docker.exe', 'docker.cmd', 'docker.bat')):
                continue
            filtered.append(entry)
        return os.pathsep.join(filtered)

    def _write_fake_docker(self, bin_dir: Path) -> None:
        docker_script = bin_dir / 'docker'
        docker_script.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -euo pipefail

                mode="${OPENCLAW_FAKE_DOCKER_MODE:-daemon_unreachable}"
                command="${1:-}"

                case "$command" in
                  info)
                    case "$mode" in
                      ready)
                        exit 0
                        ;;
                      daemon_unreachable)
                        echo 'Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?' >&2
                        exit 1
                        ;;
                      *)
                        echo "unexpected fake docker mode: $mode" >&2
                        exit 98
                        ;;
                    esac
                    ;;
                  *)
                    echo "unexpected docker args: $*" >&2
                    exit 99
                    ;;
                esac
                """
            ),
            encoding='utf-8',
        )
        docker_script.chmod(0o755)

    def _run_script(self, *, fake_docker_mode: str | None, args: list[str] | None = None) -> subprocess.CompletedProcess[str]:
        bash_executable = resolve_bash_executable()
        if not bash_executable:
            self.skipTest('未找到可用 bash；跳过 shell 集成测试')
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_root = Path(tmpdir)
            fake_bin = temp_root / 'bin'
            fake_bin.mkdir(parents=True)
            if fake_docker_mode is not None:
                self._write_fake_docker(fake_bin)

            env = dict(os.environ)
            env.update(
                {
                    'PYTHONDONTWRITEBYTECODE': '1',
                    'PYTHONIOENCODING': 'utf-8',
                    'PYTHONUTF8': '1',
                    'PATH': os.pathsep.join([str(fake_bin), self._path_without_docker(env.get('PATH', ''))]),
                }
            )
            if fake_docker_mode is not None:
                env['OPENCLAW_FAKE_DOCKER_MODE'] = fake_docker_mode

            return subprocess.run(
                [str(bash_executable), str(SCRIPT_PATH), *(args or [])],
                cwd=ROOT_DIR,
                text=True,
                encoding='utf-8',
                errors='replace',
                capture_output=True,
                env=env,
                check=False,
            )

    def test_help_only_describes_control_plane_container_renderer(self) -> None:
        result = self._run_script(fake_docker_mode=None, args=['--help'])

        self.assertEqual(result.returncode, 0)
        self.assertIn('摘要渲染固定通过控制面容器执行', result.stdout)
        self.assertNotIn('本机 Python 解释器', result.stdout)

    def test_fails_when_docker_cli_is_missing(self) -> None:
        result = self._run_script(fake_docker_mode=None)

        self.assertEqual(result.returncode, 2)
        self.assertIn('未检测到 docker CLI', result.stderr)
        self.assertIn('固定要求 Docker daemon 与控制面容器', result.stderr)
        self.assertNotIn('本机 Python 解释器', result.stderr)

    def test_fails_when_docker_daemon_is_unreachable(self) -> None:
        result = self._run_script(fake_docker_mode='daemon_unreachable')

        self.assertEqual(result.returncode, 2)
        self.assertIn('当前无法连接 Docker daemon', result.stderr)
        self.assertIn('固定要求 Docker daemon 与控制面容器', result.stderr)
        self.assertNotIn('本机 Python 解释器', result.stderr)


if __name__ == '__main__':
    unittest.main()
