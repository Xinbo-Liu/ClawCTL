"""验证部署镜像治理交付面保留 bundle、managed tag 和权限合同。"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from openclaw.doctor.agent_modules.support import resolve_bash_executable
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.tests.support.static_text_assertions import assert_static_text_absent


ROOT_DIR = resolve_repo_root(Path(__file__))


def _single_quoted_heredoc_body(source: str, marker: str) -> str:
    """从 shell source 中抽取单引号 heredoc 正文，用于检查静态 help 文案。"""
    needle = f"cat <<'{marker}'"
    start = source.index(needle)
    body_start = source.index('\n', start) + 1
    end = source.index(f'\n{marker}', body_start)
    return source[body_start:end]


class DeploymentImageGovernanceSurfaceTest(unittest.TestCase):
    """覆盖部署镜像治理静态合同。"""

    def _env_value(self, path: Path, key: str) -> str:
        """读取简单 KEY=VALUE 文件中的值。"""
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.startswith(f'{key}='):
                return line.split('=', 1)[1].strip()
        raise AssertionError(f'{key} not found in {path}')

    def test_source_selection_records_current_env_rewrite(self) -> None:
        """Gateway source selection 必须记录当前 env 改写，不修改 canonical pin。"""
        source = (ROOT_DIR / 'scripts' / 'images' / 'pull_images.sh').read_text(encoding='utf-8')

        self.assertIn('PULL_GATEWAY_CANDIDATE_MODE', source)
        self.assertIn('auto-switch|fail-fast|off', source)
        self.assertIn('gateway_source_selection.json', source)
        self.assertIn('pin_env_upsert_key "$IMAGE_ENV_DEPLOY_ENV_PATH" OPENCLAW_OFFICIAL_GATEWAY_IMAGE "$candidate_ref"', source)

    def test_bundle_alias_and_status_contracts_are_visible(self) -> None:
        """离线 bundle、managed tag、状态表和 cleanup 保护规则必须同时可见。"""
        deployment_lib = (ROOT_DIR / 'scripts' / 'lib' / 'deployment_images.sh').read_text(encoding='utf-8')
        export_source = (ROOT_DIR / 'scripts' / 'images' / 'export_deployment_images.sh').read_text(encoding='utf-8')
        cleanup_source = (ROOT_DIR / 'scripts' / 'images' / 'cleanup_image_aliases.sh').read_text(encoding='utf-8')
        status_source = (ROOT_DIR / 'scripts' / 'images' / 'show_deployment_image_status.sh').read_text(encoding='utf-8')
        compose_source = (ROOT_DIR / 'scripts' / 'runtime' / 'runtime_compose_lib.sh').read_text(encoding='utf-8')

        self.assertIn('deployment-images.contract.json', deployment_lib)
        self.assertIn('deployment-images.docker.tar', deployment_lib)
        self.assertIn('deployment-images.local-refs.env', deployment_lib)
        self.assertIn('deployment_images_archive_is_bundle()', deployment_lib)
        self.assertIn('deployment_images_contract_json_to_local_refs_env()', deployment_lib)
        self.assertIn('deployment_images_resolve_verified_local_ref', deployment_lib)
        self.assertIn('deployment_images_resolve_verified_local_ref_from_refs_file', deployment_lib)
        self.assertIn('_IMAGE_ID=', deployment_lib)
        self.assertIn('deployment_images_write_contract_json', export_source)
        self.assertIn('deployment_images_managed_tag_for_role', export_source)
        self.assertIn('PROTECTED_IMAGE_REFS', cleanup_source)
        self.assertIn('is_protected_image_ref "$local_ref" && continue', cleanup_source)
        self.assertIn('== deployment image role table ==', status_source)
        self.assertIn('verified-local:', status_source)
        self.assertIn('missing-image-id', status_source)
        self.assertIn('image-id-mismatch', status_source)
        self.assertIn('runtime_compose_append_verified_local_image_refs', compose_source)
        self.assertIn('runtime_compose_runtime_image_vars()', compose_source)
        self.assertIn('recorded_image_id', compose_source)
        self.assertIn('[[ -n "$recorded_image_id" ]] || continue', compose_source)
        self.assertIn('actual_image_id="$(docker image inspect "$local_ref" --format', compose_source)
        self.assertIn('image_env_runtime_service_image_vars', compose_source)

    def test_image_role_lists_are_source_strategy_driven(self) -> None:
        """部署合同角色与 compose 运行角色必须从 source_strategy 派生。"""
        strategy = json.loads((ROOT_DIR / 'config' / 'runtime' / 'source_strategy.json').read_text(encoding='utf-8'))
        images = strategy.get('images') if isinstance(strategy.get('images'), dict) else {}
        self.assertGreaterEqual(len(images), 1)
        for image_id, payload in images.items():
            selected = payload.get('selected_runtime_source') if isinstance(payload, dict) else {}
            contract = payload.get('deployment_contract') if isinstance(payload, dict) else {}
            runtime = payload.get('compose_runtime') if isinstance(payload, dict) else {}
            self.assertTrue(selected.get('ref_env'), msg=image_id)
            self.assertTrue(contract.get('role'), msg=image_id)
            self.assertIn('enabled', contract, msg=image_id)
            self.assertIn('enabled', runtime, msg=image_id)
            if runtime.get('enabled') is True:
                self.assertTrue(runtime.get('target_selector'), msg=image_id)

        image_env_source = (ROOT_DIR / 'scripts' / 'lib' / 'image_env.sh').read_text(encoding='utf-8')
        deployment_lib = (ROOT_DIR / 'scripts' / 'lib' / 'deployment_images.sh').read_text(encoding='utf-8')
        basic_summary_source = (ROOT_DIR / 'python' / 'openclaw' / 'setup' / 'flow' / 'basic_summary.py').read_text(encoding='utf-8')
        source_strategy_helper = (ROOT_DIR / 'python' / 'openclaw' / 'lib' / 'runtime' / 'source_strategy.py').read_text(encoding='utf-8')
        self.assertIn('image_env_strategy_env_keys', image_env_source)
        self.assertIn('deployment_images_role_rows()', deployment_lib)
        self.assertIn('deployment_image_roles', basic_summary_source)
        assert_static_text_absent(self, 'enabled // true', image_env_source)
        assert_static_text_absent(self, 'enabled // true', deployment_lib)
        assert_static_text_absent(self, "contract.get('enabled', True)", source_strategy_helper)

    def test_effective_compose_image_contract_checks_selected_refs(self) -> None:
        """部署镜像合同必须校验最终 effective compose 与 selected refs，而不是只看模板变量。"""
        contract_source = (ROOT_DIR / 'scripts' / 'images' / 'check_deployment_image_contract.sh').read_text(encoding='utf-8')
        deploy_source = (ROOT_DIR / 'scripts' / 'setup' / 'one_click_deploy.sh').read_text(encoding='utf-8')

        self.assertIn('--compose-file', contract_source)
        self.assertIn('--require-local', contract_source)
        self.assertIn('compose_actual_image_refs()', contract_source)
        self.assertIn('runtime_service_images', contract_source)
        self.assertIn('runtime_compose_command "$ENV_FILE" "$COMPOSE_FILE" config --format json', contract_source)
        self.assertIn('verified_local_ref_for_expected()', contract_source)
        self.assertIn('verified_local_image_id_for_expected()', contract_source)
        self.assertIn('image_ref_matches_expected()', contract_source)
        self.assertIn('verified local ref 的 image ID 与合同记录不一致', contract_source)
        self.assertIn('Gateway candidate 已拉取但 compose 仍指 canonical', contract_source)
        self.assertIn('重新加载镜像 env、重渲染 effective compose', contract_source)
        self.assertIn('selected ref 未拉取或本地不可见', contract_source)
        self.assertIn('RepoDigests', contract_source)
        self.assertIn('deploy_refresh_after_pull_images', deploy_source)
        self.assertIn('envRewritten', deploy_source)

    def test_contract_accepts_runtime_verified_local_refs_from_compose_config(self) -> None:
        """合同检查必须按 runtime compose 入口解析，并接受 verified local managed refs。"""
        bash_executable = resolve_bash_executable()
        if not bash_executable:
            self.skipTest('未找到可用 bash；跳过 shell 集成测试')

        gateway_ref = self._env_value(ROOT_DIR / 'config' / 'image_pins' / 'openclaw.env', 'OPENCLAW_OFFICIAL_GATEWAY_IMAGE')
        runtime_ref = self._env_value(ROOT_DIR / 'config' / 'image_pins' / 'runtime.env', 'OPENCLAW_RUNTIME_PYTHON_IMAGE')
        nginx_ref = self._env_value(ROOT_DIR / 'config' / 'image_pins' / 'runtime.env', 'NGINX_IMAGE')
        gateway_local = 'openclaw.local/deployment/official-gateway:2026.6.1-sha256-b12f76a7947e4cdd'
        runtime_local = 'openclaw.local/deployment/runtime-python:3.11.15-slim-bookworm-sha256-9c6f90801e6b68e7'
        nginx_local = 'openclaw.local/deployment/nginx:1.28.3-alpine-slim-sha256-b33eedfdf089be1f'

        temp_parent = ROOT_DIR / 'state'
        temp_parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_parent) as temp_dir:
            temp_root = Path(temp_dir)
            fake_bin = temp_root / 'bin'
            fake_bin.mkdir()
            fake_docker = fake_bin / 'docker'
            fake_docker.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    set -euo pipefail

                    if [[ "${1:-}" == "info" ]]; then
                      exit 0
                    fi

                    if [[ "${1:-}" == "compose" ]]; then
                      printf '%s\n' "${FAKE_DOCKER_COMPOSE_CONFIG_JSON:?}"
                      exit 0
                    fi

                    if [[ "${1:-}" == "image" && "${2:-}" == "inspect" ]]; then
                      ref="${3:-}"
                      format=""
                      shift 3
                      while [[ $# -gt 0 ]]; do
                        case "$1" in
                          --format)
                            format="${2:-}"
                            shift 2
                            ;;
                          *)
                            shift
                            ;;
                        esac
                      done
                      case "$format" in
                        "{{.Id}}")
                          printf 'sha256:fake\n'
                          exit 0
                          ;;
                        "{{range .RepoDigests}}{{println .}}{{end}}")
                          case "$ref" in
                            openclaw.local/*)
                              exit 0
                              ;;
                            *@sha256:*)
                              without_digest="${ref%@*}"
                              digest="${ref#*@}"
                              repo="${without_digest%:*}"
                              printf '%s@%s\n' "$repo" "$digest"
                              exit 0
                              ;;
                          esac
                          exit 0
                          ;;
                        *)
                          if [[ "$ref" == *@sha256:* ]]; then
                            echo "Error: No such object: $ref" >&2
                            exit 1
                          fi
                          printf '[]\n'
                          exit 0
                          ;;
                      esac
                    fi

                    echo "unexpected docker args: $*" >&2
                    exit 98
                    """
                ),
                encoding='utf-8',
            )
            fake_docker.chmod(0o755)

            host_state = temp_root / 'host_state'
            env_file = temp_root / 'deploy.env'
            compose_file = temp_root / 'docker-compose.effective.yml'
            refs_file = temp_root / 'deployment-images.local-refs.env'
            host_state_rel = host_state.relative_to(ROOT_DIR).as_posix()
            env_file_rel = env_file.relative_to(ROOT_DIR).as_posix()
            compose_file_rel = compose_file.relative_to(ROOT_DIR).as_posix()
            refs_file_rel = refs_file.relative_to(ROOT_DIR).as_posix()
            env_file.write_text(
                '\n'.join(
                    [
                        f'HOST_STATE_ROOT={host_state_rel}',
                        f'OPENCLAW_OFFICIAL_GATEWAY_IMAGE={gateway_ref}',
                        f'OPENCLAW_RUNTIME_PYTHON_IMAGE={runtime_ref}',
                        f'NGINX_IMAGE={nginx_ref}',
                        '',
                    ]
                ),
                encoding='utf-8',
            )
            compose_file.write_text(
                textwrap.dedent(
                    """\
                    services:
                      gateway:
                        image: ${OPENCLAW_OFFICIAL_GATEWAY_IMAGE:?OPENCLAW_OFFICIAL_GATEWAY_IMAGE_required}
                      runtime:
                        image: ${OPENCLAW_RUNTIME_PYTHON_IMAGE:?OPENCLAW_RUNTIME_PYTHON_IMAGE_required}
                      ingress:
                        image: ${NGINX_IMAGE:?NGINX_IMAGE_required}
                    """
                ),
                encoding='utf-8',
            )
            refs_file.write_text(
                '\n'.join(
                    [
                        f'OPENCLAW_OFFICIAL_GATEWAY_IMAGE_PIN_REF={gateway_ref}',
                        f'OPENCLAW_OFFICIAL_GATEWAY_IMAGE_LOCAL_REF={gateway_local}',
                        'OPENCLAW_OFFICIAL_GATEWAY_IMAGE_IMAGE_ID=sha256:fake',
                        f'OPENCLAW_RUNTIME_PYTHON_IMAGE_PIN_REF={runtime_ref}',
                        f'OPENCLAW_RUNTIME_PYTHON_IMAGE_LOCAL_REF={runtime_local}',
                        'OPENCLAW_RUNTIME_PYTHON_IMAGE_IMAGE_ID=sha256:fake',
                        f'NGINX_IMAGE_PIN_REF={nginx_ref}',
                        f'NGINX_IMAGE_LOCAL_REF={nginx_local}',
                        'NGINX_IMAGE_IMAGE_ID=sha256:fake',
                        '',
                    ]
                ),
                encoding='utf-8',
            )
            compose_config = {
                'services': {
                    'gateway': {'image': gateway_local},
                    'runtime': {'image': runtime_local},
                    'ingress': {'image': nginx_local},
                }
            }
            env = dict(os.environ)
            env.update(
                {
                    'PATH': os.pathsep.join([str(fake_bin), env.get('PATH', '')]),
                    'DEPLOYMENT_IMAGE_LOCAL_REFS_ENV': refs_file_rel,
                    'IMAGE_ENV_DEPLOY_ENV_PATH': env_file_rel,
                    'FAKE_DOCKER_COMPOSE_CONFIG_JSON': json.dumps(compose_config),
                }
            )

            result = subprocess.run(
                [
                    str(bash_executable),
                    str(ROOT_DIR / 'scripts' / 'images' / 'check_deployment_image_contract.sh'),
                    '--env-file',
                    env_file_rel,
                    '--compose-file',
                    compose_file_rel,
                    '--require-local',
                ],
                cwd=ROOT_DIR,
                text=True,
                encoding='utf-8',
                errors='replace',
                capture_output=True,
                env=env,
                check=False,
            )

            status_result = subprocess.run(
                [
                    str(bash_executable),
                    str(ROOT_DIR / 'scripts' / 'images' / 'show_deployment_image_status.sh'),
                ],
                cwd=ROOT_DIR,
                text=True,
                encoding='utf-8',
                errors='replace',
                capture_output=True,
                env=env,
                check=False,
            )

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn(gateway_local, result.stdout)
        self.assertIn('当前部署镜像合同校验通过', result.stdout)
        self.assertEqual(status_result.returncode, 0, msg=status_result.stdout + status_result.stderr)
        self.assertIn('verified-local', status_result.stdout)
        self.assertIn(gateway_local, status_result.stdout)
        self.assertNotIn('missing-image-id', status_result.stdout)
        self.assertNotIn('image-id-mismatch', status_result.stdout)

    def test_delivery_surfaces_do_not_freeze_image_counts(self) -> None:
        """交付面不得再用固定数量描述部署镜像合同或运行镜像集合。"""
        checked_paths = [
            ROOT_DIR / 'config' / 'governance' / 'docs' / 'image_governance_surface.json',
            ROOT_DIR / 'config' / 'governance' / 'docs' / 'script_catalog_surface.json',
            ROOT_DIR / 'config' / 'governance' / 'flows' / 'deploy_stage_flow.json',
            ROOT_DIR / 'python' / 'openclaw' / 'images' / 'governance_surface.py',
            ROOT_DIR / 'scripts' / 'lib' / 'deployment_images.sh',
            ROOT_DIR / 'scripts' / 'images' / 'check_deployment_image_contract.sh',
            ROOT_DIR / 'scripts' / 'images' / 'show_deployment_image_status.sh',
            ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'setup_cli_common.sh',
        ]
        forbidden = ('四镜像', '三镜像', '四类镜像', '三类镜像')
        for path in checked_paths:
            text = path.read_text(encoding='utf-8')
            for token in forbidden:
                assert_static_text_absent(self, token, text, msg=str(path))

    def test_image_governance_docs_require_verified_local_image_id(self) -> None:
        """离线 managed tag 文档必须说明合同 image id 证明。"""
        checked_paths = [
            ROOT_DIR / 'docs' / 'getting-started' / 'image-preparation.md',
            ROOT_DIR / 'docs' / 'getting-started' / 'environment-setup.md',
            ROOT_DIR / 'config' / 'governance' / 'docs' / 'image_governance_surface.json',
            ROOT_DIR / 'config' / 'governance' / 'docs' / 'getting_started_surface.json',
            ROOT_DIR / 'config' / 'governance' / 'docs' / 'script_catalog_surface.json',
            ROOT_DIR / 'config' / 'governance' / 'flows' / 'deploy_stage_flow.json',
            ROOT_DIR / 'scripts' / 'README.md',
        ]
        for path in checked_paths:
            text = path.read_text(encoding='utf-8')
            self.assertIn('image id', text.lower(), msg=str(path))

    def test_public_image_docs_and_help_do_not_advertise_legacy_paths(self) -> None:
        """公开文档和 help 只描述当前 bundle 与 candidate mode 接口。"""
        public_paths = [
            ROOT_DIR / 'docs' / 'getting-started' / 'image-preparation.md',
            ROOT_DIR / 'docs' / 'getting-started' / 'environment-setup.md',
            ROOT_DIR / 'config' / 'governance' / 'docs' / 'image_governance_surface.json',
            ROOT_DIR / 'config' / 'governance' / 'docs' / 'getting_started_surface.json',
            ROOT_DIR / 'config' / 'governance' / 'docs' / 'script_catalog_surface.json',
            ROOT_DIR / 'config' / 'governance' / 'flows' / 'deploy_stage_flow.json',
            ROOT_DIR / 'scripts' / 'README.md',
        ]
        forbidden = (
            'PULL_CN_GATEWAY_CANDIDATE_FAIL_FAST',
            '旧 raw',
            'raw Docker save',
            'legacy raw',
            '需要保留旧',
            '当前运行镜像 env key 为',
            '三类运行镜像',
            '后续链路',
            'required_images',
            '若 Docker 不能直接',
            '仍能以 managed role tag',
            '运行态会使用合同声明的 managed role tag',
            'bundle image id',
            '新版 deployment image bundle',
        )
        for path in public_paths:
            text = path.read_text(encoding='utf-8')
            for token in forbidden:
                assert_static_text_absent(self, token, text, msg=str(path))

        help_surfaces = {
            'scripts/images/pull_images.sh': _single_quoted_heredoc_body(
                (ROOT_DIR / 'scripts' / 'images' / 'pull_images.sh').read_text(encoding='utf-8'),
                'USAGE',
            ),
            'scripts/images/load_deployment_images.sh': _single_quoted_heredoc_body(
                (ROOT_DIR / 'scripts' / 'images' / 'load_deployment_images.sh').read_text(encoding='utf-8'),
                'USAGE',
            ),
            'scripts/images/check_deployment_image_contract.sh': _single_quoted_heredoc_body(
                (ROOT_DIR / 'scripts' / 'images' / 'check_deployment_image_contract.sh').read_text(encoding='utf-8'),
                'USAGE',
            ),
            'scripts/images/export_deployment_images.sh': _single_quoted_heredoc_body(
                (ROOT_DIR / 'scripts' / 'images' / 'export_deployment_images.sh').read_text(encoding='utf-8'),
                'USAGE',
            ),
            'scripts/setup/lib/setup_cli_common.sh': (
                ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'setup_cli_common.sh'
            ).read_text(encoding='utf-8'),
        }
        for script, help_text in help_surfaces.items():
            for token in forbidden:
                assert_static_text_absent(self, token, help_text, msg=script)
        self.assertIn('image id', help_surfaces['scripts/images/load_deployment_images.sh'].lower())
        self.assertIn('合同 image id', help_surfaces['scripts/images/check_deployment_image_contract.sh'].lower())

    def test_bootstrap_and_gateway_permission_contracts_are_preserved(self) -> None:
        """bootstrap 必须渲染 runtime paths，Gateway 配置权限必须保持 owner-only。"""
        bootstrap_source = (ROOT_DIR / 'scripts' / 'setup' / 'bootstrap.sh').read_text(encoding='utf-8')
        permission_source = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'runtime_permissions.sh').read_text(encoding='utf-8')

        self.assertIn('runtime paths render-generated', bootstrap_source)
        self.assertIn('--config-path "$OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH"', bootstrap_source)
        self.assertIn('runtime_permissions_chmod_if_exists 600 "$gateway_dir/openclaw.json"', permission_source)


if __name__ == '__main__':
    unittest.main()
