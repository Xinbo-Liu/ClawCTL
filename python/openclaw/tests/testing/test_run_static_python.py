"""验证静态 Python 容器入口的 Docker 前提和 verified local ref 回退。"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from openclaw.doctor.agent_modules.support import resolve_bash_executable
from openclaw.lib.repo.layout import resolve_repo_root


ROOT_DIR = resolve_repo_root(Path(__file__))
SCRIPT_PATH = ROOT_DIR / 'scripts' / 'lib' / 'run_static_python.sh'


class RunStaticPythonScriptTest(unittest.TestCase):
    """覆盖 run_static_python.sh 在宿主机与容器视角下的入口行为。"""

    def _write_fake_docker(self, bin_dir: Path) -> None:
        """写出 fake docker，按测试模式模拟 daemon、image inspect 与 run 行为。"""
        docker_script = bin_dir / 'docker'
        docker_script.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -euo pipefail

                mode="${OPENCLAW_FAKE_DOCKER_MODE:-image_missing}"
                command="${1:-}"

                case "$command" in
                  info)
                    case "$mode" in
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
                    [[ "${2:-}" == "inspect" ]] || exit 98
                    case "$mode" in
                      image_missing)
                        echo "Error: No such object: ${3:-}" >&2
                        exit 1
                        ;;
                      local_ref_ready)
                        if [[ "${3:-}" == openclaw.local/deployment/control-plane-python:* ]]; then
                          exit 0
                        fi
                        echo "Error: No such object: ${3:-}" >&2
                        exit 1
                        ;;
                      *)
                        exit 0
                        ;;
                    esac
                    ;;
                  run)
                    python_seen=0
                    env_args=()
                    args=()
                    while [[ $# -gt 0 ]]; do
                      if [[ "$python_seen" == "1" ]]; then
                        args+=("$1")
                        shift
                        continue
                      fi
                      case "$1" in
                        -e)
                          env_args+=("$2")
                          shift 2
                          ;;
                        python3)
                          python_seen=1
                          shift
                          ;;
                        *)
                          shift
                          ;;
                      esac
                    done
                    [[ "$python_seen" == "1" ]] || {
                      echo "docker run did not include python3" >&2
                      exit 97
                    }
                    for assignment in "${env_args[@]}"; do
                      export "$assignment"
                    done
                    joined="${args[*]}"
                    if [[ "$joined" == *print\\(* && "$joined" == *ok* ]]; then
                      printf 'ok\n'
                      exit 0
                    fi
                    exec "${PYTHON_BIN:-python}" "${args[@]}"
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
        """写出 fake jq，覆盖 deployment image role row 查询。"""
        jq_script = bin_dir / 'jq'
        jq_script.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -euo pipefail

                joined="$*"
                if [[ "$joined" == *selected_runtime_source.ref_env* ]]; then
                  printf 'control-plane-python\\tOPENCLAW_CONTROL_PLANE_IMAGE\\tControl plane Python\\n'
                  exit 0
                fi
                echo "unexpected jq args: $*" >&2
                exit 99
                """
            ),
            encoding='utf-8',
        )
        jq_script.chmod(0o755)

    def _run_script(self, *, fake_docker_mode: str | None, args: list[str] | None = None) -> subprocess.CompletedProcess[str]:
        """在受控 PATH 与 env 下运行 shell 入口并返回结果。"""
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
                    str(Path(sys.executable).parent),
                ]
            )
            if fake_docker_mode is not None:
                self._write_fake_docker(fake_bin)
                self._write_fake_jq(fake_bin)

            env = dict(os.environ)
            env.pop('OPENCLAW_STATIC_PYTHON_IN_CONTAINER', None)
            env.pop('OPENCLAW_PYTHON_CONTAINER_IN_CONTAINER', None)
            env.update(
                {
                    'PYTHONDONTWRITEBYTECODE': '1',
                    'PYTHONIOENCODING': 'utf-8',
                    'PYTHONUTF8': '1',
                    'PYTHON_BIN': sys.executable,
                    'PATH': minimal_path,
                    'OPENCLAW_REPO_CONTRACTS_FORCE_AWK': '1',
                    'IMAGE_ENV_PIN_FILE': str(ROOT_DIR / 'config' / 'image_pins' / 'openclaw.env'),
                    'IMAGE_ENV_RUNTIME_PIN_FILE': str(ROOT_DIR / 'config' / 'image_pins' / 'runtime.env'),
                    'OPENCLAW_STATIC_PYTHON_READINESS_LABEL': 'test static python',
                }
            )
            if fake_docker_mode is not None:
                env['OPENCLAW_FAKE_DOCKER_MODE'] = fake_docker_mode
            if fake_docker_mode == 'local_ref_ready':
                refs_file = temp_root / 'deployment-images.local-refs.env'
                control_plane_ref = ''
                for line in (ROOT_DIR / 'config' / 'image_pins' / 'runtime.env').read_text(encoding='utf-8').splitlines():
                    if line.startswith('OPENCLAW_CONTROL_PLANE_IMAGE='):
                        control_plane_ref = line.split('=', 1)[1].strip()
                        break
                refs_file.write_text(
                    '\n'.join(
                        [
                            f'OPENCLAW_CONTROL_PLANE_IMAGE_PIN_REF={control_plane_ref}',
                            'OPENCLAW_CONTROL_PLANE_IMAGE_LOCAL_REF=openclaw.local/deployment/control-plane-python:python-sha256-2222222222222222',
                            '',
                        ]
                    ),
                    encoding='utf-8',
                )
                env['DEPLOYMENT_IMAGE_LOCAL_REFS_ENV'] = str(refs_file)

            return subprocess.run(
                [str(bash_executable), str(SCRIPT_PATH), *(args or ['--workdir', str(ROOT_DIR), '--', '-c', 'print("ok")'])],
                cwd=ROOT_DIR,
                text=True,
                encoding='utf-8',
                errors='replace',
                capture_output=True,
                env=env,
                check=False,
            )

    def test_help_is_available_without_docker(self) -> None:
        """帮助面不得依赖 Docker CLI。"""
        result = self._run_script(fake_docker_mode=None, args=['--help'])

        self.assertEqual(result.returncode, 0)
        self.assertIn('Docker 必需的静态 Python 检查', result.stdout)

    def test_fails_with_readiness_guidance_when_docker_cli_is_missing(self) -> None:
        """缺少 Docker CLI 时输出宿主机 readiness 修复路径。"""
        result = self._run_script(fake_docker_mode=None)

        self.assertEqual(result.returncode, 2)
        self.assertIn('Docker 必需的静态 Python 检查', result.stderr)
        self.assertIn('bash ./scripts/testing/check_repo_test_readiness.sh', result.stderr)

    def test_fails_with_prepare_guidance_when_control_plane_image_is_missing(self) -> None:
        """控制面镜像缺失时提示 prepare_control_plane_medium。"""
        result = self._run_script(fake_docker_mode='image_missing')

        self.assertEqual(result.returncode, 2)
        self.assertIn('OPENCLAW_CONTROL_PLANE_IMAGE', result.stderr)
        self.assertIn('bash ./scripts/setup/prepare_control_plane_medium.sh', result.stderr)

    def test_accepts_verified_local_control_plane_ref(self) -> None:
        """exact pin 不可 inspect 但 managed local ref 已验证时允许运行。"""
        result = self._run_script(fake_docker_mode='local_ref_ready')

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn('ok', result.stdout)

    def test_injects_repo_pythonpath_when_running_container(self) -> None:
        """容器执行面必须注入 repo python bootstrap 路径。"""
        result = self._run_script(
            fake_docker_mode='image_ready',
            args=[
                '--workdir',
                str(ROOT_DIR),
                '--',
                '-c',
                'import os; print(os.environ.get("PYTHONPATH", ""))',
            ],
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        expected_pythonpath = str(ROOT_DIR / 'python').replace('\\', '/')
        self.assertIn(expected_pythonpath, result.stdout.strip().replace('\\', '/'))

    def test_runs_directly_when_already_inside_static_container(self) -> None:
        """容器内部视角应直接执行 Python，不再套 Docker。"""
        env = dict(os.environ)
        env.update(
            {
                'PYTHONDONTWRITEBYTECODE': '1',
                'PYTHONIOENCODING': 'utf-8',
                'PYTHONUTF8': '1',
                'PYTHON_BIN': sys.executable,
                'OPENCLAW_STATIC_PYTHON_IN_CONTAINER': '1',
            }
        )
        bash_executable = resolve_bash_executable()
        if not bash_executable:
            self.skipTest('未找到可用 bash；跳过 shell 集成测试')

        result = subprocess.run(
            [
                str(bash_executable),
                str(SCRIPT_PATH),
                '--workdir',
                str(ROOT_DIR),
                '--env',
                'OPENCLAW_STATIC_DIRECT_TEST=ok',
                '--mount',
                str(ROOT_DIR),
                '--',
                '-c',
                'import os; print(os.environ["OPENCLAW_STATIC_DIRECT_TEST"])',
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


if __name__ == '__main__':
    unittest.main()
