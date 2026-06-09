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
SCRIPT_PATH = ROOT_DIR / 'scripts' / 'testing' / 'check_repo_test_readiness.sh'


class RepoTestReadinessScriptTest(unittest.TestCase):
    def _write_fake_docker(self, bin_dir: Path) -> None:
        docker_script = bin_dir / 'docker'
        docker_script.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -euo pipefail

                mode="${OPENCLAW_FAKE_DOCKER_MODE:-ready}"
                command="${1:-}"

                case "$command" in
                  info)
                    case "$mode" in
                      daemon_permission)
                        echo 'Got permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock' >&2
                        exit 1
                        ;;
                      daemon_unreachable)
                        echo 'Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?' >&2
                        exit 1
                        ;;
                      *)
                        exit 0
                        ;;
                    esac
                    ;;
                  image)
                    [[ "${2:-}" == "inspect" ]] || {
                      echo "unexpected docker image subcommand: ${2:-}" >&2
                      exit 98
                    }
                    case "$mode" in
                      ready)
                        exit 0
                        ;;
                      image_missing)
                        echo "Error: No such object: ${3:-}" >&2
                        exit 1
                        ;;
                      *)
                        echo "unexpected inspect mode: $mode" >&2
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

    def _write_fake_jq(self, bin_dir: Path) -> None:
        jq_script = bin_dir / 'jq'
        jq_script.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                exit 0
                """
            ),
            encoding='utf-8',
        )
        jq_script.chmod(0o755)

    def _run_script(
        self,
        *,
        fake_docker_mode: str | None,
        with_archive: bool = False,
        with_fake_jq: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        bash_executable = resolve_bash_executable()
        if not bash_executable:
            self.skipTest('未找到可用 bash；跳过 shell 集成测试')
        bash_path = Path(bash_executable)
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_root = Path(tmpdir)
            fake_bin = temp_root / 'bin'
            fake_bin.mkdir(parents=True)
            minimal_path = os.pathsep.join(
                [
                    str(fake_bin),
                    str(bash_path.parent),
                    str(bash_path.parent.parent / 'usr' / 'bin'),
                ]
            )
            if fake_docker_mode is not None:
                self._write_fake_docker(fake_bin)
            if with_fake_jq:
                self._write_fake_jq(fake_bin)

            archive_root = temp_root / 'image_artifacts'
            if with_archive:
                archive_root.mkdir(parents=True, exist_ok=True)
                (archive_root / 'deployment_images_fixture.tar').write_text('placeholder\n', encoding='utf-8')

            env = dict(os.environ)
            env.update(
                {
                    'PYTHONDONTWRITEBYTECODE': '1',
                    'PYTHONIOENCODING': 'utf-8',
                    'PYTHONUTF8': '1',
                    'OPENCLAW_REPO_CONTRACTS_FORCE_AWK': '1',
                    'IMAGE_ENV_PIN_FILE': str(ROOT_DIR / 'config' / 'image_pins' / 'openclaw.env'),
                    'IMAGE_ENV_RUNTIME_PIN_FILE': str(ROOT_DIR / 'config' / 'image_pins' / 'runtime.env'),
                    'PATH': minimal_path,
                    'DEPLOYMENT_IMAGE_ARTIFACT_DIR': str(archive_root if with_archive else (temp_root / 'missing_image_artifacts')),
                }
            )
            if fake_docker_mode is not None:
                env['OPENCLAW_FAKE_DOCKER_MODE'] = fake_docker_mode

            return subprocess.run(
                [str(bash_executable), str(SCRIPT_PATH)],
                cwd=ROOT_DIR,
                text=True,
                encoding='utf-8',
                errors='replace',
                capture_output=True,
                env=env,
                check=False,
            )

    def test_fails_when_docker_cli_is_missing(self) -> None:
        result = self._run_script(fake_docker_mode=None)

        self.assertEqual(result.returncode, 2)
        self.assertIn('未检测到 docker CLI', result.stderr)
        self.assertIn('重新执行 bash ./scripts/testing/check_repo_test_readiness.sh', result.stderr)

    def test_fails_when_docker_daemon_is_not_accessible(self) -> None:
        result = self._run_script(fake_docker_mode='daemon_permission')

        self.assertEqual(result.returncode, 2)
        self.assertIn('Docker daemon 当前不可用', result.stderr)
        self.assertIn('当前用户无法访问 Docker daemon', result.stderr)

    def test_passes_when_control_plane_image_is_ready(self) -> None:
        result = self._run_script(fake_docker_mode='ready')

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn('控制面执行介质已就绪', result.stdout)
        self.assertIn('bash ./scripts/testing/run_repo_unittest.sh', result.stdout)
        self.assertIn('bash ./scripts/doctor/run_repo_release_gate.sh', result.stdout)
        self.assertIn('--with-docker-sock', result.stdout)
        self.assertIn('jq 已就绪', result.stdout)

    def test_fails_when_jq_is_missing(self) -> None:
        result = self._run_script(fake_docker_mode='ready', with_fake_jq=False)

        self.assertEqual(result.returncode, 2)
        self.assertIn('未检测到 jq', result.stderr)

    def test_prefers_offline_prepare_command_when_archive_is_available(self) -> None:
        result = self._run_script(fake_docker_mode='image_missing', with_archive=True)

        self.assertEqual(result.returncode, 4)
        self.assertIn('prepare_control_plane_medium.sh --offline --image-archive', result.stderr)

    def test_recommends_online_prepare_when_archive_is_missing(self) -> None:
        result = self._run_script(fake_docker_mode='image_missing')

        self.assertEqual(result.returncode, 4)
        self.assertIn('bash ./scripts/setup/prepare_control_plane_medium.sh', result.stderr)
        self.assertNotIn('--offline --image-archive', result.stderr)


if __name__ == '__main__':
    unittest.main()
