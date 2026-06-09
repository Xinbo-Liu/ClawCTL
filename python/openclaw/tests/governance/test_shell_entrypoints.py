from __future__ import annotations

import json
from pathlib import Path
import os
import re
import shutil
import shlex
import subprocess
import sys
import tempfile
import unittest

from openclaw.doctor.agent_modules.support import resolve_bash_executable
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.setup.network.tls_hostname import validate_tls_hostname
from openclaw.tests.support.static_text_assertions import assert_static_text_absent
from openclaw.tests.support.managed_extensions import managed_extensions

ROOT_DIR = resolve_repo_root(Path(__file__))
MANAGED_EXTENSIONS = tuple(sorted(managed_extensions(ROOT_DIR), key=lambda row: row.id))
MANAGED_EXTENSION = MANAGED_EXTENSIONS[0] if MANAGED_EXTENSIONS else None

HELP_SURFACE_CASES = (
    (
        'scripts/doctor/check_docker_host_readiness.sh',
        (
            'bash ./scripts/doctor/check_docker_host_readiness.sh [--offline] [--env-file <path>]',
            '本脚本只检查系统时间、Docker / Compose / DNS / HTTPS 与宿主机基础前提。',
            '--offline',
        ),
    ),
    (
        'scripts/doctor/check_system_time.sh',
        (
            'bash ./scripts/doctor/check_system_time.sh [--offline] [--max-drift-seconds <seconds>] [--min-epoch <epoch>] [--max-epoch <epoch>]',
            'HTTPS HTTP Date',
            '--max-drift-seconds <seconds>',
        ),
    ),
    (
        'scripts/setup/prepare_docker_host.sh',
        (
            'sudo bash ./scripts/setup/prepare_docker_host.sh [选项]',
            '--repair-centos7-vault-repos',
            '--network-profile',
            '--update-system-time',
            '--configure-daemon',
        ),
    ),
    (
        'scripts/setup/update_system_time.sh',
        (
            'sudo bash ./scripts/setup/update_system_time.sh [--offline] [--timezone <zone>] [--max-drift-seconds <seconds>] [--min-epoch <epoch>] [--max-epoch <epoch>]',
            '启用 NTP/chronyd',
            '--timezone <zone>',
        ),
    ),
    (
        'scripts/setup/one_click_config.sh',
        (
            'bash ./scripts/setup/one_click_config.sh [选项]',
            '正式部署路径：',
            '官方 Gateway token auth',
        ),
    ),
    (
        'scripts/setup/one_click_test_basic.sh',
        (
            'bash ./scripts/setup/one_click_test_basic.sh [选项]',
            'one_click_test_basic 是默认 one_click 主链中的唯一部署前门禁',
            '--image-archive <path>',
        ),
    ),
    (
        'scripts/setup/one_click_test_full.sh',
        (
            'bash ./scripts/setup/one_click_test_full.sh [选项]',
            'one_click_test_full 是部署完成后的默认统一验证入口',
            '--strict',
        ),
    ),
    (
        'scripts/setup/recover_runtime_generated_state.sh',
        (
            'bash ./scripts/setup/recover_runtime_generated_state.sh [--force-recover-env] [--restart]',
            '恢复缺失的 deploy/.env 与运行态派生物',
            '--restart',
        ),
    ),
)


class ShellEntrypointHelpSurfaceTest(unittest.TestCase):
    def test_direct_launchers_use_lf_shebangs(self) -> None:
        launcher_paths = []
        if MANAGED_EXTENSION is not None:
            launcher_paths.extend(
                path.relative_to(ROOT_DIR).as_posix()
                for path in sorted((MANAGED_EXTENSION.root_dir / 'agent' / 'modules').glob('*/bin/*'))
            )
        launcher_paths.extend([
            'scripts/runtime/container_openclaw_cli',
            'scripts/runtime/container_python',
        ])
        for rel_path in launcher_paths:
            with self.subTest(script=rel_path):
                payload = (ROOT_DIR / rel_path).read_bytes()
                first_line = payload.split(b'\n', 1)[0]
                self.assertTrue(first_line.startswith(b'#!'))
                self.assertNotIn(b'\r', first_line)

    def test_runtime_permission_exec_candidates_are_executable(self) -> None:
        required = {
            'scripts/runtime/container_openclaw_cli',
            'scripts/runtime/container_python',
        }
        required.update(path.relative_to(ROOT_DIR).as_posix() for path in (ROOT_DIR / 'scripts').rglob('*.sh'))
        required.update(path.relative_to(ROOT_DIR).as_posix() for path in (ROOT_DIR / 'deploy' / 'nginx').glob('*.sh'))
        required.update(
            path.relative_to(ROOT_DIR).as_posix()
            for extension_root in sorted((ROOT_DIR / 'agent' / 'extensions').glob('*'))
            for path in extension_root.glob('agent/modules/*/bin/*')
            if path.is_file()
        )
        required.update(
            path.relative_to(ROOT_DIR).as_posix()
            for extension_root in sorted((ROOT_DIR / 'agent' / 'extensions').glob('*'))
            for path in extension_root.glob('scripts/**/*.sh')
            if path.is_file()
        )
        if shutil.which('git') is None or not (ROOT_DIR / '.git').exists():
            missing_exec = {
                rel_path: oct((ROOT_DIR / rel_path).stat().st_mode & 0o777)
                for rel_path in sorted(required)
                if ((ROOT_DIR / rel_path).stat().st_mode & 0o111) == 0
            }
            self.assertEqual(missing_exec, {})
            return
        result = subprocess.run(
            ['git', 'ls-files', '--stage', '--', *sorted(required)],
            cwd=ROOT_DIR,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        modes = {
            line.split(maxsplit=1)[1].rsplit('\t', 1)[1]: line.split(maxsplit=1)[0]
            for line in result.stdout.splitlines()
            if line.strip()
        }
        self.assertEqual(set(modes), required)
        self.assertEqual({path: mode for path, mode in modes.items() if mode != '100755'}, {})

    def test_shell_scripts_do_not_use_deep_parent_jumps(self) -> None:
        violations: list[str] = []
        for path in sorted((ROOT_DIR / 'scripts').rglob('*.sh')):
            rel_path = path.relative_to(ROOT_DIR).as_posix()
            for line_number, raw_line in enumerate(path.read_text(encoding='utf-8', errors='ignore').splitlines(), start=1):
                if '../..' in raw_line:
                    violations.append(f'{rel_path}:{line_number}: {raw_line.strip()}')

        self.assertEqual(violations, [])

    def test_flow_step_runner_redacts_user_identity_payloads(self) -> None:
        bash = resolve_bash_executable()
        script_path = shlex.quote((ROOT_DIR / 'scripts' / 'lib' / 'flow_step_runner.sh').as_posix())
        result = subprocess.run(
            [bash, '-lc', f'source {script_path}; flow_redact_sensitive_stream'],
            check=True,
            input='\n'.join(
                [
                    'PROBE_ADMIN_USERS_JSON=[{"user_ref":"user_secret"}]',
                    '{"access_token":"tok_secret","user_id":"user_secret","union_id":"union_secret","channel_id":"channel_secret"}',
                    'USER_ID=user_inline',
                    'SYNTHETIC_SIGN_KEY=sign_secret',
                    'Authorization: Bearer bearer_secret',
                    'curl --api-key cli_secret --webhook-url https://hooks.example.invalid/webhook/hook_secret',
                ]
            ),
            text=True,
            stdout=subprocess.PIPE,
        )

        self.assertIn('PROBE_ADMIN_USERS_JSON=<redacted>', result.stdout)
        self.assertIn('"user_id":"<redacted>"', result.stdout)
        self.assertIn('"union_id":"<redacted>"', result.stdout)
        self.assertIn('"channel_id":"<redacted>"', result.stdout)
        self.assertIn('"access_token":"<redacted>"', result.stdout)
        self.assertIn('USER_ID=<redacted>', result.stdout)
        self.assertIn('SYNTHETIC_SIGN_KEY=<redacted>', result.stdout)
        self.assertIn('Authorization: Bearer <redacted>', result.stdout)
        self.assertIn('--api-key <redacted>', result.stdout)
        self.assertIn('--webhook-url <redacted>', result.stdout)
        self.assertNotIn('user_secret', result.stdout)
        self.assertNotIn('union_secret', result.stdout)
        self.assertNotIn('tok_secret', result.stdout)
        self.assertNotIn('channel_secret', result.stdout)
        self.assertNotIn('user_inline', result.stdout)
        self.assertNotIn('sign_secret', result.stdout)
        self.assertNotIn('bearer_secret', result.stdout)
        self.assertNotIn('cli_secret', result.stdout)
        self.assertNotIn('hook_secret', result.stdout)

        line_result = subprocess.run(
            [bash, '-lc', f'source {script_path}; tmp="$(mktemp)"; flow_log_line "$tmp" "SYNTHETIC_APP_SECRET=line_secret"; cat "$tmp"; rm -f "$tmp"'],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        self.assertIn('SYNTHETIC_APP_SECRET=<redacted>', line_result.stdout)
        self.assertNotIn('line_secret', line_result.stdout)

    def test_long_shell_entrypoints_render_help_surface(self) -> None:
        setup_common = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'setup_cli_common.sh').read_text(encoding='utf-8')
        for script_rel, anchors in HELP_SURFACE_CASES:
            with self.subTest(script=script_rel):
                source = '\n'.join(
                    [
                        (ROOT_DIR / script_rel).read_text(encoding='utf-8'),
                        setup_common,
                    ]
                )
                for anchor in anchors:
                    self.assertIn(anchor, source)

    def test_deploy_scripts_do_not_source_user_env_file(self) -> None:
        for script_rel in (
            'scripts/setup/apply_ingress_boundary_rules.sh',
            'scripts/doctor/check_runtime_compose_contract.sh',
            'scripts/doctor/check_ingress_boundary_evidence.sh',
        ):
            with self.subTest(script=script_rel):
                source = (ROOT_DIR / script_rel).read_text(encoding='utf-8')
                assert_static_text_absent(self, 'source "$ENV_FILE"', source)
                assert_static_text_absent(self, 'set -a', source)
                self.assertIn('deploy_env_shell_load_keys', source)

    def test_prebootstrap_compose_contracts_use_transient_env_files(self) -> None:
        for script_rel in (
            'scripts/doctor/check_runtime_compose_contract.sh',
            'scripts/doctor/check_ingress_boundary_evidence.sh',
        ):
            with self.subTest(script=script_rel):
                source = (ROOT_DIR / script_rel).read_text(encoding='utf-8')
                self.assertIn('runtime_compose_prepare_transient_env_files "$ROOT_DIR" "$ENV_FILE"', source)
                self.assertIn('runtime_compose_cleanup_transient_env_files "$ROOT_DIR" "$compose_tmp_dir"', source)
                self.assertIn('COMPOSE_FILE="$(runtime_compose_default_file "$ROOT_DIR" "$ENV_FILE")"', source)
                self.assertIn('runtime_compose_command "$compose_env_file" "$COMPOSE_FILE" config --format json', source)
                if script_rel == 'scripts/doctor/check_runtime_compose_contract.sh':
                    self.assertIn('OPENCLAW_CONTROL_PLANE_PROFILE', source)
                    self.assertIn('OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH', source)
                    self.assertIn('--env "OPENCLAW_CONTROL_PLANE_PROFILE=$OPENCLAW_CONTROL_PLANE_PROFILE"', source)
                    self.assertIn('--env "OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH=$OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH"', source)

    def test_gateway_browser_verification_uses_gateway_node_surface(self) -> None:
        source = (ROOT_DIR / 'scripts' / 'images' / 'verify_gateway_browser.sh').read_text(encoding='utf-8')
        helper = (ROOT_DIR / 'python' / 'openclaw' / 'images' / 'browser_runtime_checks.cjs').read_text(encoding='utf-8')
        self.assertIn('--workdir /app', source)
        self.assertIn('--entrypoint node', source)
        self.assertIn('browser_runtime_checks.cjs', source)
        assert_static_text_absent(self, '--entrypoint python3', source)
        self.assertIn("require('playwright-core')", helper)
        self.assertIn("commandOnPath('openclaw')", helper)
        assert_static_text_absent(self, 'chromium.launch(', helper)

    def test_runtime_up_restarts_ingress_to_apply_rendered_config(self) -> None:
        source = (ROOT_DIR / 'scripts' / 'runtime' / 'run_runtime_service_action.sh').read_text(encoding='utf-8')
        self.assertIn('UP_ARGS+=(--force-recreate)', source)
        self.assertIn('runtime_compose_up_services "$ENV_FILE" "$COMPOSE_FILE" "${UP_ARGS[@]}" "${SERVICES[@]}"', source)
        self.assertIn('runtime_service_name_for_target ingress', source)
        self.assertIn('runtime_compose_command "$ENV_FILE" "$COMPOSE_FILE" restart "$ingress_service"', source)

    def test_scheduler_exec_reads_control_plane_selection_from_env_file(self) -> None:
        source = (ROOT_DIR / 'scripts' / 'lib' / 'control_plane_scheduler_exec.sh').read_text(encoding='utf-8')
        config_paths_source = (ROOT_DIR / 'scripts' / 'lib' / 'control_plane_config_paths.sh').read_text(encoding='utf-8')
        self.assertIn('openclaw_scheduler_apply_control_plane_selection_from_env_file', source)
        self.assertIn('OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH', config_paths_source)
        self.assertIn('OPENCLAW_CONTROL_PLANE_PROFILE', config_paths_source)
        self.assertIn('openclaw_control_plane_apply_selection_from_env_file', source)
        self.assertIn('中 OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH 与 OPENCLAW_CONTROL_PLANE_PROFILE 不一致', config_paths_source)
        self.assertIn('openclaw_scheduler_apply_control_plane_selection_from_env_file \\', source)

    def test_python_tool_wrapper_reads_active_control_plane_selection_from_deploy_env(self) -> None:
        source = (ROOT_DIR / 'scripts' / 'runtime' / 'run_openclaw_python_tool.sh').read_text(encoding='utf-8')

        self.assertIn('openclaw_control_plane_apply_default_selection_from_env_files \\', source)
        self.assertIn('"$ROOT_DIR/deploy/.env|deploy/.env"', source)
        self.assertIn('"$ROOT_DIR/deploy/site.env|deploy/site.env"', source)
        self.assertIn('openclaw_control_plane_resolve_config_path "$RUNNER_CONTROL_PLANE_PROFILE" "$RUNNER_CONTROL_PLANE_CONFIG_PATH" "$RUNNER_CONTROL_PLANE_PROFILE_EXPLICIT"', source)
        self.assertIn('OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH=$RESOLVED_CONFIG_PATH', source)

    def test_runtime_permissions_release_paths_come_from_object_family_truth(self) -> None:
        source = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'runtime_permissions.sh').read_text(encoding='utf-8')
        self.assertIn('runtime_permissions_governed_release_paths()', source)
        self.assertIn('repo_contract_relpath control_plane.object_families', source)
        self.assertIn('.families.runtime_evidence.entries[]?', source)

    def test_runtime_bind_user_contract_strips_yaml_quotes_after_template_resolution(self) -> None:
        source = (ROOT_DIR / 'scripts' / 'doctor' / 'check_runtime_bind_user_contract.sh').read_text(encoding='utf-8')
        self.assertIn('trim_compose_scalar()', source)
        self.assertIn("value=\"${value//$'\\r'/}\"", source)
        self.assertIn('if [[ "${result:0:1}" == \'"\' && "${result: -1}" == \'"\' ]]; then', source)
        self.assertIn('elif [[ "${result:0:1}" == "\'" && "${result: -1}" == "\'" ]]; then', source)
        self.assertIn('source "$ROOT_DIR/scripts/runtime/runtime_compose_lib.sh"', source)
        self.assertIn('COMPOSE_FILE="$(runtime_compose_default_file "$ROOT_DIR" "$ENV_FILE")"', source)

    def test_recover_runtime_generated_state_uses_current_deploy_image_keys(self) -> None:
        source = (ROOT_DIR / 'scripts' / 'setup' / 'recover_runtime_generated_state.sh').read_text(encoding='utf-8')
        self.assertIn('OPENCLAW_OFFICIAL_GATEWAY_IMAGE', source)
        self.assertIn('OPENCLAW_RUNTIME_PYTHON_IMAGE', source)
        self.assertIn('NGINX_IMAGE', source)
        self.assertIn('model_env_lines_from_containers', source)
        self.assertIn('MODEL_REF', source)
        self.assertIn('BASE_URL', source)
        self.assertIn('API_KEY', source)
        self.assertIn("awk '{print $NF}'", source)
        self.assertIn('RECOVERED_ENV[OPENCLAW_CONTROL_PLANE_PROFILE]', source)
        self.assertIn('openclaw_control_plane_container_config_path "${RECOVERED_ENV[OPENCLAW_CONTROL_PLANE_PROFILE]}"', source)
        self.assertIn('openclaw_control_plane_profile_id_for_path "${RECOVERED_ENV[OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH]}"', source)
        self.assertIn('OPENCLAW_CONTROL_PLANE_PROFILE \\', source)
        assert_static_text_absent(self, 'OPENCLAW_GATEWAY_IMAGE', source)
        assert_static_text_absent(self, 'OPENCLAW_INGRESS_IMAGE', source)
        assert_static_text_absent(self, 'OPENCLAW_CONTROL_PLANE_IMAGE', source)
        assert_static_text_absent(self, '"/opt/openclaw-tools/config/control_plane/profiles/agent_platform.service.json"', source)

    def test_gateway_docker_healthcheck_uses_tcp_liveness_not_http_or_rpc(self) -> None:
        source = (ROOT_DIR / 'deploy' / 'docker-compose.yml').read_text(encoding='utf-8')
        gateway_block = source.split('openclaw-official-gateway:', 1)[1].split('openclaw-internal-api:', 1)[0]
        healthcheck_source = (
            ROOT_DIR / 'config' / 'gateway' / 'healthchecks' / 'gateway-tcp-liveness.cjs'
        ).read_text(encoding='utf-8')
        self.assertIn('/home/node/.openclaw/healthchecks/gateway-tcp-liveness.cjs', gateway_block)
        self.assertIn('interval: 15s', gateway_block)
        self.assertIn('timeout: 20s', gateway_block)
        self.assertIn('retries: 8', gateway_block)
        self.assertIn('start_period: 8m', gateway_block)
        assert_static_text_absent(self, 'node -e', gateway_block)
        self.assertIn('net.connect', healthcheck_source)
        self.assertIn('127.0.0.1', healthcheck_source)
        self.assertIn('18789', healthcheck_source)
        assert_static_text_absent(self, '/healthz', gateway_block)
        assert_static_text_absent(self, '/readyz', gateway_block)
        assert_static_text_absent(self, 'openclaw gateway status', gateway_block)
        assert_static_text_absent(self, '--require-rpc', gateway_block)
        assert_static_text_absent(self, '/healthz', healthcheck_source)
        assert_static_text_absent(self, '/readyz', healthcheck_source)
        assert_static_text_absent(self, 'openclaw gateway status', healthcheck_source)
        assert_static_text_absent(self, '--require-rpc', healthcheck_source)

    def test_full_test_model_probe_uses_registry_specs_without_secret_temp_files(self) -> None:
        runner_source = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'full_test_group_runner.sh').read_text(encoding='utf-8')
        doctor_source = (ROOT_DIR / 'scripts' / 'doctor' / 'check_model_profile_connectivity.sh').read_text(encoding='utf-8')
        source = f'{runner_source}\n{doctor_source}'
        self.assertIn('scripts/doctor/check_model_profile_connectivity.sh', runner_source)
        assert_static_text_absent(self, '/tmp/minimax_full_test', source)
        assert_static_text_absent(self, 'header = "Authorization: Bearer', source)
        self.assertIn('model_env_specs_from_registry', source)
        self.assertIn('模型 env 与渠道探测通过', source)

    def test_full_test_records_check_durations_and_bounds_terminal_details(self) -> None:
        gate_common = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'test_gate_common.sh').read_text(encoding='utf-8')
        setup_common = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'setup_cli_common.sh').read_text(encoding='utf-8')
        summary_shell = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'full_test_summary_shell.sh').read_text(encoding='utf-8')
        group_runner = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'full_test_group_runner.sh').read_text(encoding='utf-8')
        group_registry = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'full_test_group_registry.sh').read_text(encoding='utf-8')
        renderer = (ROOT_DIR / 'python' / 'openclaw' / 'lib' / 'testing' / 'full_test' / 'render.py').read_text(encoding='utf-8')

        self.assertIn('SETUP_GATE_LAST_DURATION_SECONDS', gate_common)
        self.assertIn('SETUP_GATE_ENV_CONTEXT_CACHE_KEY', gate_common)
        self.assertIn('setup_gate_load_env_context', gate_common)
        self.assertIn('[setup_gate_duration_seconds=${duration_seconds}]', setup_common)
        self.assertIn('[full_test_duration_seconds=${duration_seconds}]', summary_shell)
        self.assertIn('FULL_TEST_DETAIL_INLINE_LIMIT="${FULL_TEST_DETAIL_INLINE_LIMIT:-8000}"', summary_shell)
        self.assertIn('FULL_TEST_LOG_DIR/check-details/$FULL_TEST_RUN_ID', summary_shell)
        self.assertIn('完整输出见 ${detail_path#"$ROOT_DIR"/}', summary_shell)
        self.assertIn('full_test_mark_check_started "$check_id"', group_runner)
        self.assertIn('full_test_mark_check_started "$check_id"', group_registry)
        self.assertIn('FULL_TEST_GROUP_REGISTRY_JSON_CACHE', group_registry)
        self.assertIn('full_test_group_registry_cached_json', group_registry)
        self.assertIn("mapfile -t script_specs < <(full_test_group_registry_cached_json | jq -c --arg group \"$group\" '.groups[$group].script_checks[]?')", group_registry)
        self.assertIn("mapfile -t entrypoint_specs < <(full_test_group_registry_cached_json | jq -c --arg group \"$group\" '.groups[$group].entrypoint_presence_checks[]?')", group_registry)
        self.assertIn("ingress_boundary_cached_evidence_ok \"$ROOT_DIR\" \"$ENV_FILE\" 1", group_registry)
        self.assertIn('TEXT_DETAIL_LIMIT = 1200', renderer)
        self.assertIn('slow_checks', renderer)

    def test_full_test_failure_followup_scenarios_are_declared(self) -> None:
        full_entry = (ROOT_DIR / 'scripts' / 'setup' / 'one_click_test_full.sh').read_text(encoding='utf-8')
        payload = json.loads((ROOT_DIR / 'config' / 'governance' / 'docs' / 'setup_failures.json').read_text(encoding='utf-8'))
        declared = set(payload['entries']['one_click_test_full']['scenarios'])
        referenced = set(re.findall(r'scenarios\+=\(([^)]+)\)', full_entry))

        self.assertEqual(sorted(referenced - declared), [])

    def test_setup_failure_routes_cover_cn_network_profile_and_offline_image_route(self) -> None:
        payload = json.loads((ROOT_DIR / 'config' / 'governance' / 'docs' / 'setup_failures.json').read_text(encoding='utf-8'))
        rendered = json.dumps(payload, ensure_ascii=False)

        self.assertIn('sudo bash ./scripts/setup/prepare_docker_host.sh --all --network-profile cn', rendered)
        self.assertIn('bash ./scripts/doctor/check_docker_host_readiness.sh', rendered)
        self.assertIn('export_deployment_images.sh', rendered)
        self.assertIn('load_deployment_images.sh', rendered)
        self.assertIn('selected/candidate 都不可达时直接切换离线归档路线', rendered)

    def test_slow_runtime_helpers_use_root_cause_caches_and_fast_paths(self) -> None:
        repo_env = (ROOT_DIR / 'scripts' / 'lib' / 'repo_python_env.sh').read_text(encoding='utf-8')
        control_plane_paths = (ROOT_DIR / 'scripts' / 'lib' / 'control_plane_config_paths.sh').read_text(encoding='utf-8')
        full_acceptance = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'full_test_acceptance_shell.sh').read_text(encoding='utf-8')
        full_env = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'full_test_env_shell.sh').read_text(encoding='utf-8')
        full_group_runner = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'full_test_group_runner.sh').read_text(encoding='utf-8')
        full_entry = (ROOT_DIR / 'scripts' / 'setup' / 'one_click_test_full.sh').read_text(encoding='utf-8')
        runtime_target = (ROOT_DIR / 'scripts' / 'runtime' / 'runtime_target_lib.sh').read_text(encoding='utf-8')
        docs_registry = (ROOT_DIR / 'scripts' / 'docs' / 'check_docs_registry_sync.sh').read_text(encoding='utf-8')
        generated_docs = (ROOT_DIR / 'scripts' / 'docs' / 'check_generated_docs_sync.sh').read_text(encoding='utf-8')
        docker_lib = (ROOT_DIR / 'scripts' / 'runtime' / 'runtime_docker_lib.sh').read_text(encoding='utf-8')
        basic = (ROOT_DIR / 'scripts' / 'setup' / 'one_click_test_basic.sh').read_text(encoding='utf-8')
        official = (ROOT_DIR / 'scripts' / 'doctor' / 'check_openclaw_official_runtime_contract.sh').read_text(encoding='utf-8')
        evidence_prereqs = (ROOT_DIR / 'scripts' / 'runtime' / 'check_runtime_evidence_prereqs.sh').read_text(encoding='utf-8')
        export_evidence = (ROOT_DIR / 'scripts' / 'runtime' / 'export_runtime_acceptance_evidence.sh').read_text(encoding='utf-8')
        ingress_cache = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'ingress_boundary_evidence_cache.sh').read_text(encoding='utf-8')

        self.assertIn('"$OPENCLAW_CONTROL_PLANE_CONFIG_PATHS_ROOT"/*)', control_plane_paths)
        self.assertIn('[[ -f "$selected_path" ]]', control_plane_paths)
        self.assertIn('printf \'%s\\n\' "$normalized_path"', control_plane_paths)
        self.assertIn('full_test_preflight_and_load_control_plane_defaults', full_acceptance)
        self.assertIn('full_test_runtime_path_default()', full_acceptance)
        self.assertIn('full_test_testing_manifest_fast_path_allowed', full_env)
        self.assertIn('full_test_config_declares_testing_manifest_fragments', full_env)
        self.assertIn("jq -r '.execution_order[]? // empty'", full_acceptance)
        self.assertIn("jq -r '.checks[]?.id // empty'", full_group_runner)
        self.assertIn('full_test_group_registry_has_group "$group_name"', full_group_runner)
        self.assertIn('full_test_group_registry_run_declared_group "$group_name"', full_group_runner)
        self.assertIn('full_test_preflight_and_load_control_plane_defaults', full_entry)
        assert_static_text_absent(self, 'flow_preflight_run_and_load load_one_click_test_full_control_plane_defaults', full_entry)
        self.assertIn('openclaw_repo_python_env_defaults_lines()', repo_env)
        self.assertIn('lines="$(openclaw_repo_python_env_lines "$root_dir")"', repo_env)
        assert_static_text_absent(self, 'openclaw_repo_python_env_surface "$root_dir" env-args', repo_env)
        self.assertIn('runtime_target_load_registry_cache_fast()', runtime_target)
        self.assertIn('runtime_target_config_declares_service_registry_fragments', runtime_target)
        self.assertIn('select(. != "agent_platform")', runtime_target)
        self.assertIn('openclaw_control_plane_apply_default_selection_from_env_files', runtime_target)
        self.assertIn('"$ROOT_DIR/deploy/.env|deploy/.env"', runtime_target)
        self.assertIn('"$ROOT_DIR/deploy/site.env|deploy/site.env"', runtime_target)
        self.assertIn('openclaw_control_plane_apply_default_selection_from_env_files', docs_registry)
        self.assertIn('openclaw_control_plane_apply_default_selection_from_env_files', generated_docs)
        self.assertIn('CANONICAL_RESOLVED_CONFIG_PATH="$(openclaw_control_plane_resolve_config_path agent_platform "" 1)"', generated_docs)
        self.assertIn('CANONICAL_CONFIG_ARGS=(--control-plane-profile agent_platform)', generated_docs)
        self.assertIn('run_python_module_for_config "$CANONICAL_RESOLVED_CONFIG_PATH"', generated_docs)
        self.assertIn("run_check 'deployment_inputs_reference' 'openclaw.setup.deploy_env.control_plane' docs render-deployment-inputs --check \"${CONFIG_ARGS[@]}\"", generated_docs)
        self.assertIn("run_canonical_check 'site_env_example_reference' 'openclaw.setup.deploy_env.control_plane' docs render-site-env-example --check", generated_docs)
        self.assertIn("run_canonical_check 'runtime_surface_reference' 'openclaw.docs.renderers.runtime_surface' --check \"${CANONICAL_CONFIG_ARGS[@]}\"", generated_docs)
        self.assertIn("runtime_docker_inspect_format \"$target\" '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'", docker_lib)
        self.assertIn('ingress_boundary_cached_evidence_ok "$ROOT_DIR" "$ENV_FILE" 0 "$(host_install_defaults_state_root_default)"', basic)
        self.assertIn('ingress_boundary_refresh_cached_nginx_policy', ingress_cache)
        self.assertIn('setup ingress check-nginx', ingress_cache)
        self.assertIn('< /dev/null', ingress_cache)
        self.assertIn('.nginx_policy.required == true', ingress_cache)
        self.assertIn('.nginx_policy.checked == true', ingress_cache)
        self.assertIn('.nginx_policy.access_phase_default_deny == true', ingress_cache)
        self.assertIn('((.nginx_policy.source_cidrs // [] | sort) == csv_set($allowed_cidrs))', ingress_cache)
        self.assertIn('official_cli_cache_valid', official)
        self.assertIn('COMMAND_TIMEOUT_SECONDS="${OPENCLAW_OFFICIAL_CLI_COMMAND_TIMEOUT_SECONDS:-300}"', official)
        self.assertIn('run_official_cli_command()', official)
        self.assertIn('summary_json_path="$state_dir/summary.json"', official)
        self.assertIn('RESOLVED_CONFIG_PATH=""', official)
        self.assertIn('deploy_env_shell_load_keys "$ROOT_DIR/deploy/.env" OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH', official)
        self.assertIn('openclaw_control_plane_resolve_config_path agent_platform', official)
        self.assertIn('deploy_env_shell_load_keys "$ENV_FILE" OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH', evidence_prereqs)
        self.assertIn('openclaw_control_plane_resolve_config_path agent_platform', evidence_prereqs)
        self.assertIn('deploy_env_shell_load_keys "$DEPLOY_ENV_FILE" OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH', export_evidence)
        self.assertIn('require_json_flag_true "$RUNTIME_ACCEPTANCE_OUTPUT" ".accepted == true" "runtime acceptance 证据文件"', export_evidence)

    def test_dispatch_runtime_check_uses_repo_state_tmp_visible_to_container_runner(self) -> None:
        source = (ROOT_DIR / 'scripts' / 'doctor' / 'check_dispatch_runtime.sh').read_text(encoding='utf-8')

        self.assertIn('tmp_root="$ROOT_DIR/state/openclaw/control_plane/tmp"', source)
        self.assertIn('mktemp -d "$tmp_root/check-dispatch-runtime.XXXXXX"', source)
        self.assertIn('source "$ROOT_DIR/scripts/setup/lib/deploy_env_shell.sh"', source)
        self.assertIn('deploy_env_shell_load_keys "$ENV_FILE" OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH', source)
        assert_static_text_absent(self, 'preflight_json="$(mktemp)"', source)
        assert_static_text_absent(self, 'status_json="$(mktemp)"', source)

    def test_early_failure_paths_preserve_exit_and_summary_outputs(self) -> None:
        basic_source = (ROOT_DIR / 'scripts' / 'setup' / 'one_click_test_basic.sh').read_text(encoding='utf-8')
        deploy_source = (ROOT_DIR / 'scripts' / 'setup' / 'one_click_deploy.sh').read_text(encoding='utf-8')
        deploy_context_source = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'deploy_runtime_context_shell.sh').read_text(encoding='utf-8')

        self.assertIn(
            'basic_test_cleanup() {\n'
            '  [[ -n "$RESULT_LINES_FILE" && -f "$RESULT_LINES_FILE" ]] && rm -f "$RESULT_LINES_FILE"\n'
            '  return 0\n'
            '}',
            basic_source,
        )
        self.assertIn('reject_root_runtime_user()', basic_source)
        self.assertIn('one_click_test_basic 拒绝以 root 执行部署前门禁', basic_source)
        self.assertIn('apply_ingress_boundary_rules、fix_permissions 等宿主机步骤', basic_source)
        self.assertLess(
            basic_source.find('reject_root_runtime_user\n'),
            basic_source.find("trap 'basic_test_on_error $?"),
        )
        self.assertIn('deploy_prime_fail_control_plane_defaults', deploy_source)
        self.assertIn('deploy_write_summary failed', deploy_source)
        self.assertIn('deploy_emit_terminal_summary failed', deploy_source)
        self.assertIn('if [[ "$DEFAULT_LOG_DIR_REL" = /* ]]; then', deploy_context_source)

    def test_setup_summaries_use_control_plane_summary_surface(self) -> None:
        removed_helper = 'setup_shell_' 'fallback_common.sh'
        self.assertFalse((ROOT_DIR / 'scripts' / 'setup' / 'lib' / removed_helper).exists())
        targets = (
            'scripts/setup/one_click_config.sh',
            'scripts/setup/one_click_test_basic.sh',
            'scripts/setup/one_click_test_full.sh',
            'scripts/setup/one_click_deploy.sh',
            'scripts/setup/lib/deploy_flow_summary_shell.sh',
            'scripts/setup/lib/full_test_summary_shell.sh',
            'scripts/setup/lib/setup_cli_common.sh',
            'config/governance/docs/full_test_surface.json',
            'config/governance/docs/flow_summary_surface.json',
            'config/governance/docs/run_failure_surface.json',
            'config/governance/docs/script_catalog_surface.json',
            'config/governance/support/repo_host_lane.json',
        )
        banned = (
            'setup_shell_' 'fallback_common',
            'emerg' 'ency_' 'fall' 'back',
            'shell_' 'fallback',
            '应急' '摘要',
            'SUMMARY_' 'FALLBACK_REASON',
        )
        for rel_path in targets:
            source = (ROOT_DIR / rel_path).read_text(encoding='utf-8')
            for token in banned:
                with self.subTest(file=rel_path, token=token):
                    assert_static_text_absent(self, token, source)


class ShellEntrypointCliValidationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        bash = resolve_bash_executable()
        if not bash:
            raise unittest.SkipTest('未找到可用 bash；跳过 shell CLI 校验测试')
        cls.bash = Path(bash)

    def _run_entrypoint(self, rel_path: str, *args: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        env['PYTHON_BIN'] = sys.executable
        return subprocess.run(
            [str(self.bash), str(ROOT_DIR / rel_path), *args],
            cwd=ROOT_DIR,
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            env=env,
            check=False,
        )

    def assert_readable_cli_error(self, result: subprocess.CompletedProcess[str], expected: str) -> None:
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 2, msg=output)
        self.assertIn(expected, output)
        self.assertNotIn('unbound variable', output)

    def test_cidr_contract_is_shared_by_remote_install_and_client_acceptance(self) -> None:
        remote_source = (ROOT_DIR / 'scripts' / 'setup' / 'remote_first_install.sh').read_text(encoding='utf-8')
        client_source = (ROOT_DIR / 'scripts' / 'setup' / 'check_client_access_acceptance.sh').read_text(encoding='utf-8')
        contract_source = (ROOT_DIR / 'scripts' / 'lib' / 'cidr_contract.sh').read_text(encoding='utf-8')

        self.assertIn('scripts/lib/cidr_contract.sh', remote_source)
        self.assertIn('scripts/lib/cidr_contract.sh', client_source)
        self.assertIn('openclaw_cidr_validate_list', contract_source)
        self.assertIn('openclaw_cidr_first_not_allowed', contract_source)
        assert_static_text_absent(self, 'validate_client_cidr_list()', remote_source)
        assert_static_text_absent(self, 'cidr_contains_python()', client_source)

    def test_client_access_acceptance_accepts_multi_cidr_and_rejects_invalid_ipv6(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / 'deploy.env'
            env_path.write_text(
                '\n'.join(
                    [
                        'OPENCLAW_TLS_CN=openclaw.internal.example',
                        'OPENCLAW_INGRESS_LISTEN_IP=10.1.2.3',
                        'OPENCLAW_TLS_CERT_DIR=deploy/nginx/certs',
                        'OPENCLAW_TLS_CERT_FILE=openclaw.crt',
                        'OPENCLAW_TLS_MODE=self_signed',
                        'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS=10.0.0.0/8,fd12:3456::/32',
                    ]
                )
                + '\n',
                encoding='utf-8',
            )

            ready = self._run_entrypoint(
                'scripts/setup/check_client_access_acceptance.sh',
                '--env-file',
                str(env_path),
                '--client-cidr',
                '10.1.2.0/24,fd12:3456:789a::/48',
                '--tls-cn',
                'openclaw.internal.example',
            )
            invalid = self._run_entrypoint(
                'scripts/setup/check_client_access_acceptance.sh',
                '--env-file',
                str(env_path),
                '--client-cidr',
                'fd:::1/8',
                '--tls-cn',
                'openclaw.internal.example',
            )

        self.assertEqual(ready.returncode, 0, msg=ready.stdout + ready.stderr)
        self.assertIn('client_access_acceptance=ready', ready.stdout)
        self.assertEqual(invalid.returncode, 2, msg=invalid.stdout + invalid.stderr)
        self.assertIn('IPv6 地址格式无效', invalid.stdout + invalid.stderr)

    def test_cidr_contract_function_cases_cover_validation_and_allowlist(self) -> None:
        contract_path = shlex.quote(str(ROOT_DIR / 'scripts' / 'lib' / 'cidr_contract.sh'))
        script = f'''
set -euo pipefail
source {contract_path}
openclaw_cidr_validate_list '10.0.0.0/8,fd12:3456::/32' '--client-cidr'
! openclaw_cidr_validate_list '10.0.0.0/7' '--client-cidr' >/dev/null 2>&1
! openclaw_cidr_validate_list 'fd:::1/8' '--client-cidr' >/dev/null 2>&1
[[ -z "$(openclaw_cidr_first_not_allowed '10.0.0.0/8,fd00::/8' '10.1.2.0/24,fd00:1234::/32')" ]]
[[ "$(openclaw_cidr_first_not_allowed '10.0.0.0/8' '10.1.2.0/24,192.168.50.0/24')" == '192.168.50.0/24' ]]
'''
        result = subprocess.run(
            [str(self.bash), '-lc', script],
            cwd=ROOT_DIR,
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

    def test_remote_first_install_plan_json_exposes_fixed_deploy_order(self) -> None:
        result = self._run_entrypoint(
            'scripts/setup/remote_first_install.sh',
            '--plan-json',
            '--host',
            'demo@example',
            '--deploy',
            '--client-cidr',
            '10.0.0.0/8,192.168.50.0/24',
            '--ssh-port',
            '24110',
        )

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['kind'], 'openclaw_remote_first_install_plan')
        self.assertEqual(payload['clientCidrs'], ['10.0.0.0/8', '192.168.50.0/24'])
        self.assertEqual(payload['sshPort'], '24110')
        deploy_stage = next(stage for stage in payload['stages'] if stage['id'] == 'deploy')
        step_ids = [step['id'] for step in deploy_stage['steps']]
        self.assertEqual(
            step_ids,
            [
                'prepare_control_plane_medium',
                'one_click_config',
                'apply_ingress_boundary_rules',
                'fix_permissions',
                'one_click_test_basic',
                'one_click_deploy',
                'one_click_test_full',
            ],
        )

    def test_remote_first_install_plan_json_validates_cidr_before_output(self) -> None:
        state_root = ROOT_DIR / 'state' / 'remote_first_install'
        before = {item.name for item in state_root.iterdir()} if state_root.exists() else set()

        result = self._run_entrypoint(
            'scripts/setup/remote_first_install.sh',
            '--plan-json',
            '--host',
            'demo@example',
            '--deploy',
            '--client-cidr',
            '8.8.8.0/24',
        )

        after = {item.name for item in state_root.iterdir()} if state_root.exists() else set()
        self.assertEqual(result.returncode, 2, msg=result.stdout + result.stderr)
        self.assertIn('只允许私网或 loopback CIDR', result.stdout + result.stderr)
        self.assertFalse(result.stdout.strip().startswith('{'))
        self.assertEqual(before, after)

    def test_remote_first_install_rejects_invalid_ssh_port_before_output(self) -> None:
        result = self._run_entrypoint(
            'scripts/setup/remote_first_install.sh',
            '--plan-json',
            '--host',
            'demo@example',
            '--deploy',
            '--ssh-port',
            '70000',
        )

        self.assertEqual(result.returncode, 2, msg=result.stdout + result.stderr)
        self.assertIn('--ssh-port 必须是 1-65535 的整数', result.stdout + result.stderr)
        self.assertFalse(result.stdout.strip().startswith('{'))

    def test_remote_cleanup_entrypoint_is_dry_run_and_does_not_global_prune(self) -> None:
        source = (ROOT_DIR / 'scripts' / 'setup' / 'cleanup_remote_openclaw.sh').read_text(encoding='utf-8')
        prepare_source = (ROOT_DIR / 'scripts' / 'setup' / 'prepare_deploy_user.sh').read_text(encoding='utf-8')
        help_result = self._run_entrypoint('scripts/setup/cleanup_remote_openclaw.sh', '--help')

        self.assertEqual(help_result.returncode, 0, msg=help_result.stdout + help_result.stderr)
        self.assertIn('默认 dry-run', help_result.stdout)
        self.assertIn('--apply', help_result.stdout)
        self.assertIn('--ssh-port', help_result.stdout)
        self.assertIn('validate_ssh_port', source)
        self.assertIn('ssh_remote_cleanup', source)
        self.assertIn('-p "$SSH_PORT"', source)
        self.assertIn('OPENCLAW_INGRESS_BOUNDARY', source)
        self.assertIn('collect_openclaw_containers', source)
        self.assertIn('openclaw_repo_dir_has_evidence', source)
        self.assertIn('deploy_user_has_openclaw_evidence', source)
        self.assertIn('.openclaw', source)
        self.assertIn('deploy-user.marker', source)
        self.assertIn('deploy-user.marker', prepare_source)
        self.assertIn("grep -q '^created_by_openclaw=1$'", prepare_source)
        self.assertIn('created_by_openclaw=$created_flag', prepare_source)
        self.assertIn("^created_by_openclaw=1$", source)
        self.assertIn('mark_deploy_user_evidence', prepare_source)
        user_evidence_body = source.split('deploy_user_has_openclaw_evidence()', 1)[1].split('cleanup_docker_objects()', 1)[0]
        assert_static_text_absent(self, '== *openclaw*', user_evidence_body)
        assert_static_text_absent(self, '[[ "$user" == \'openclaw\' ]] && return 0', source)
        self.assertIn('"\\$user" == \'openclaw\' && "\\$repo_owner" == "\\$user"', source)
        self.assertIn('deploy_group_has_openclaw_evidence', source)
        self.assertIn('部署用户组缺少 OpenClaw 证据', source)
        self.assertIn('拒绝递归删除', source)
        self.assertIn('远程源码临时目录不存在', source)
        self.assertIn('远程源码临时包不存在', source)
        self.assertIn('父目录不存在：/opt/openclaw', source)
        assert_static_text_absent(self, 'docker system prune', source)
        assert_static_text_absent(self, 'docker image prune', source)

    def test_remote_cleanup_rejects_invalid_ssh_port_before_ssh(self) -> None:
        result = self._run_entrypoint(
            'scripts/setup/cleanup_remote_openclaw.sh',
            '--host',
            'demo@example',
            '--ssh-port',
            '0',
        )

        self.assertEqual(result.returncode, 2, msg=result.stdout + result.stderr)
        self.assertIn('--ssh-port 必须是 1-65535 的整数', result.stdout + result.stderr)

    def test_remote_cleanup_rejects_non_openclaw_repo_path_before_ssh(self) -> None:
        result = self._run_entrypoint(
            'scripts/setup/cleanup_remote_openclaw.sh',
            '--host',
            'demo@example',
            '--repo-dir',
            '/tmp/not-project',
            '--apply',
        )

        self.assertEqual(result.returncode, 2, msg=result.stdout + result.stderr)
        self.assertIn('必须包含 openclaw 或 clawctl 路径证据', result.stdout + result.stderr)

    def test_tls_hostname_shell_contract_matches_python_truth(self) -> None:
        cases = (
            'openclaw.internal.example',
            'openclaw-internal.example',
            '',
            ' openclaw.internal.example',
            'openclaw.internal.example ',
            'bad_name.internal',
            '*.internal.example',
            'openclaw.internal.',
            '.openclaw.internal',
            'openclaw..internal',
            '192.168.0.10',
            '999.999.999.999',
            'fd00::10',
            'openclaw;return 200',
            'a' * 64 + '.internal',
            'openclaw.内部',
        )
        lib_path = shlex.quote(str(ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'tls_hostname_contract.sh'))
        for value in cases:
            with self.subTest(value=value):
                script = f'''
set -euo pipefail
source {lib_path}
if openclaw_tls_hostname_is_valid {shlex.quote(value)}; then
  printf VALID
else
  printf INVALID
fi
'''
                result = subprocess.run(
                    [str(self.bash), '-lc', script],
                    cwd=ROOT_DIR,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
                expected = 'VALID' if validate_tls_hostname(value) == '' else 'INVALID'
                self.assertEqual(result.stdout, expected)

    def test_tls_certificate_shell_contract_static_guards(self) -> None:
        gen_source = (ROOT_DIR / 'deploy' / 'nginx' / 'gen-self-signed-cert.sh').read_text(encoding='utf-8')
        install_source = (ROOT_DIR / 'deploy' / 'nginx' / 'install-provided-cert.sh').read_text(encoding='utf-8')
        gen_cert_source = (ROOT_DIR / 'scripts' / 'setup' / 'gen_cert.sh').read_text(encoding='utf-8')
        recover_source = (ROOT_DIR / 'scripts' / 'setup' / 'recover_runtime_generated_state.sh').read_text(encoding='utf-8')
        full_test_source = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'full_test_group_runner.sh').read_text(encoding='utf-8')

        self.assertIn('tls_hostname_contract.sh', gen_source)
        self.assertIn('tls_hostname_contract.sh', install_source)
        self.assertIn('tls_hostname_contract.sh', recover_source)
        assert_static_text_absent(self, 'OPENCLAW_TLS_DAYS', gen_source)
        self.assertIn('DAYS=365', gen_source)
        assert_static_text_absent(self, 'TLS_MODE="${TLS_MODE:-self_signed}"', gen_cert_source)
        self.assertIn('OPENCLAW_TLS_MODE 未配置', gen_cert_source)
        self.assertIn('-checkend 0', install_source)
        self.assertIn('DNS:$host', install_source)
        self.assertIn('未加密 PEM 私钥', install_source)
        assert_static_text_absent(self, 'RECOVERED_ENV[OPENCLAW_TLS_CN]="${listen_ip}"', recover_source)
        assert_static_text_absent(self, 'RECOVERED_ENV[OPENCLAW_TLS_MODE]="self_signed"', recover_source)
        self.assertIn('OPENCLAW_TLS_CERT_SOURCE_PATH', recover_source)
        self.assertIn('full_test_gateway_build_curl_args', full_test_source)
        self.assertIn('provided_files)', full_test_source)
        assert_static_text_absent(self, 'curl_args=( -k', full_test_source)
        assert_static_text_absent(self, 'curl -k', full_test_source)

    def test_install_provided_cert_rejects_non_strict_certificate_inputs(self) -> None:
        openssl_probe = subprocess.run(
            [str(self.bash), '-lc', 'command -v openssl >/dev/null 2>&1'],
            cwd=ROOT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )
        if openssl_probe.returncode != 0:
            raise unittest.SkipTest('缺少 openssl；跳过 provided_files 证书契约测试')

        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            nginx_source = ROOT_DIR / 'deploy' / 'nginx'
            shutil.copytree(nginx_source, temp_root / 'deploy' / 'nginx', ignore=shutil.ignore_patterns('certs'))
            (temp_root / 'scripts' / 'setup' / 'lib').mkdir(parents=True)
            shutil.copy2(
                ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'tls_hostname_contract.sh',
                temp_root / 'scripts' / 'setup' / 'lib' / 'tls_hostname_contract.sh',
            )
            script = r'''
set -euo pipefail
root="$1"
host='openclaw.internal.example'
work="$root/work"
mkdir -p "$work"

make_san_cert() {
  local cert="$1"
  local key="$2"
  local dns="$3"
  cat > "$work/san.cnf" <<EOF
[ req ]
default_bits = 256
prompt = no
default_md = sha256
distinguished_name = dn
x509_extensions = v3_req
[ dn ]
CN = $host
[ v3_req ]
subjectAltName = @alt_names
extendedKeyUsage = serverAuth
[ alt_names ]
DNS.1 = $dns
EOF
  if [[ -f "$work/$key" ]]; then
    openssl req -x509 -nodes -days 30 \
      -key "$work/$key" \
      -out "$work/$cert" \
      -config "$work/san.cnf" \
      -extensions v3_req >/dev/null 2>&1
  else
    openssl ecparam -name prime256v1 -genkey -noout -out "$work/$key" >/dev/null 2>&1
    openssl req -x509 -nodes -days 30 \
      -key "$work/$key" \
      -out "$work/$cert" \
      -config "$work/san.cnf" \
      -extensions v3_req >/dev/null 2>&1
  fi
}

expect_fail() {
  local cert="$1"
  local key="$2"
  local expected="$3"
  local out="$work/out.txt"
  if bash "$root/deploy/nginx/install-provided-cert.sh" "$cert" "$key" "$host" >"$out" 2>&1; then
    cat "$out"
    exit 1
  fi
  grep -F "$expected" "$out" >/dev/null || { cat "$out"; exit 1; }
}

make_san_cert exact.crt exact.key "$host"
if ! bash "$root/deploy/nginx/install-provided-cert.sh" "$work/exact.crt" "$work/exact.key" "$host" >"$work/exact.out" 2>&1; then
  cat "$work/exact.out"
  exit 1
fi

MSYS_NO_PATHCONV=1 openssl req -x509 -nodes -days 30 \
  -subj "/CN=$host" \
  -key "$work/exact.key" \
  -out "$work/cn-only.crt" >/dev/null 2>&1
expect_fail "$work/cn-only.crt" "$work/exact.key" '必须包含精确 dNSName SAN'
'''
            result = subprocess.run(
                [str(self.bash), '-lc', script, 'provided-files-contract', str(temp_root).replace('\\', '/')],
                cwd=ROOT_DIR,
                text=True,
                encoding='utf-8',
                errors='replace',
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

    def test_install_provided_cert_rejects_wildcard_san_input(self) -> None:
        openssl_probe = subprocess.run(
            [str(self.bash), '-lc', 'command -v openssl >/dev/null 2>&1'],
            cwd=ROOT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )
        if openssl_probe.returncode != 0:
            raise unittest.SkipTest('缺少 openssl；跳过 wildcard SAN 证书契约测试')

        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            nginx_source = ROOT_DIR / 'deploy' / 'nginx'
            shutil.copytree(nginx_source, temp_root / 'deploy' / 'nginx', ignore=shutil.ignore_patterns('certs'))
            (temp_root / 'scripts' / 'setup' / 'lib').mkdir(parents=True)
            shutil.copy2(
                ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'tls_hostname_contract.sh',
                temp_root / 'scripts' / 'setup' / 'lib' / 'tls_hostname_contract.sh',
            )
            script = r'''
set -euo pipefail
root="$1"
host='openclaw.internal.example'
work="$root/work"
mkdir -p "$work"
cat > "$work/san.cnf" <<EOF
[ req ]
default_bits = 256
prompt = no
default_md = sha256
distinguished_name = dn
x509_extensions = v3_req
[ dn ]
CN = $host
[ v3_req ]
subjectAltName = @alt_names
extendedKeyUsage = serverAuth
[ alt_names ]
DNS.1 = *.internal.example
EOF

openssl ecparam -name prime256v1 -genkey -noout -out "$work/exact.key" >/dev/null 2>&1
openssl req -x509 -nodes -days 30 \
  -key "$work/exact.key" \
  -out "$work/wildcard.crt" \
  -config "$work/san.cnf" \
  -extensions v3_req >/dev/null 2>&1

out="$work/out.txt"
if bash "$root/deploy/nginx/install-provided-cert.sh" "$work/wildcard.crt" "$work/exact.key" "$host" >"$out" 2>&1; then
  cat "$out"
  exit 1
fi
grep -F '必须包含精确 dNSName SAN' "$out" >/dev/null || { cat "$out"; exit 1; }
'''
            result = subprocess.run(
                [str(self.bash), '-lc', script, 'provided-files-wildcard-contract', str(temp_root).replace('\\', '/')],
                cwd=ROOT_DIR,
                text=True,
                encoding='utf-8',
                errors='replace',
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

    def test_install_provided_cert_rejects_expired_and_encrypted_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            nginx_source = ROOT_DIR / 'deploy' / 'nginx'
            shutil.copytree(nginx_source, temp_root / 'deploy' / 'nginx', ignore=shutil.ignore_patterns('certs'))
            (temp_root / 'scripts' / 'setup' / 'lib').mkdir(parents=True)
            shutil.copy2(
                ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'tls_hostname_contract.sh',
                temp_root / 'scripts' / 'setup' / 'lib' / 'tls_hostname_contract.sh',
            )
            script = r'''
set -euo pipefail
root="$1"
host='openclaw.internal.example'
work="$root/work"
mkdir -p "$work"
cat > "$work/san.cnf" <<EOF
[ req ]
default_bits = 256
prompt = no
default_md = sha256
distinguished_name = dn
x509_extensions = v3_req
[ dn ]
CN = $host
[ v3_req ]
subjectAltName = @alt_names
extendedKeyUsage = serverAuth
[ alt_names ]
DNS.1 = $host
EOF
openssl ecparam -name prime256v1 -genkey -noout -out "$work/exact.key" >/dev/null 2>&1
openssl req -x509 -nodes -days 30 \
  -key "$work/exact.key" \
  -out "$work/exact.crt" \
  -config "$work/san.cnf" \
  -extensions v3_req >/dev/null 2>&1

expect_fail() {
  local cert="$1"
  local key="$2"
  local expected="$3"
  local out="$work/out.txt"
  if bash "$root/deploy/nginx/install-provided-cert.sh" "$cert" "$key" "$host" >"$out" 2>&1; then
    cat "$out"
    exit 1
  fi
  grep -F "$expected" "$out" >/dev/null || { cat "$out"; exit 1; }
}

openssl req -new \
  -key "$work/exact.key" \
  -config "$work/san.cnf" \
  -out "$work/expired.csr" >/dev/null 2>&1
openssl x509 -req \
  -in "$work/expired.csr" \
  -signkey "$work/exact.key" \
  -days 0 \
  -out "$work/expired.crt" >/dev/null 2>&1
expect_fail "$work/expired.crt" "$work/exact.key" '证书已经过期'

openssl pkey -aes256 -in "$work/exact.key" -out "$work/encrypted.key" -passout pass:secret >/dev/null 2>&1
expect_fail "$work/exact.crt" "$work/encrypted.key" '未加密 PEM 私钥'
'''
            result = subprocess.run(
                [str(self.bash), '-lc', script, 'provided-files-expiry-contract', str(temp_root).replace('\\', '/')],
                cwd=ROOT_DIR,
                text=True,
                encoding='utf-8',
                errors='replace',
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

    def test_install_provided_cert_rejects_output_source_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            nginx_source = ROOT_DIR / 'deploy' / 'nginx'
            shutil.copytree(nginx_source, temp_root / 'deploy' / 'nginx', ignore=shutil.ignore_patterns('certs'))
            (temp_root / 'scripts' / 'setup' / 'lib').mkdir(parents=True)
            shutil.copy2(
                ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'tls_hostname_contract.sh',
                temp_root / 'scripts' / 'setup' / 'lib' / 'tls_hostname_contract.sh',
            )
            script = r'''
set -euo pipefail
root="$1"
host='openclaw.internal.example'
cert_dir="$root/deploy/nginx/certs"
work="$root/work"
mkdir -p "$cert_dir" "$work"
printf 'placeholder-cert' > "$cert_dir/openclaw.crt"
printf 'placeholder-key' > "$cert_dir/openclaw.key"

expect_fail() {
  local cert="$1"
  local key="$2"
  local expected="$3"
  local out="$work/out.txt"
  if bash "$root/deploy/nginx/install-provided-cert.sh" "$cert" "$key" "$host" >"$out" 2>&1; then
    cat "$out"
    exit 1
  fi
  grep -F "$expected" "$out" >/dev/null || { cat "$out"; exit 1; }
}

expect_fail "$cert_dir/openclaw.crt" "$cert_dir/openclaw.key" '不得位于输出证书目录'

if ln -s "$cert_dir/openclaw.crt" "$work/link-output.crt" 2>/dev/null \
  && ln -s "$cert_dir/openclaw.key" "$work/link-output.key" 2>/dev/null \
  && [[ -L "$work/link-output.crt" && -L "$work/link-output.key" ]]; then
  expect_fail "$work/link-output.crt" "$work/link-output.key" '不得指向输出证书目录'
fi
'''
            result = subprocess.run(
                [str(self.bash), '-lc', script, 'provided-files-path-contract', str(temp_root).replace('\\', '/')],
                cwd=ROOT_DIR,
                text=True,
                encoding='utf-8',
                errors='replace',
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

    def test_runtime_bind_user_contract_normalizes_crlf_compose_image_refs(self) -> None:
        script = r'''
set -euo pipefail
repo="$(pwd -P)"
tmp="$(mktemp -d)"
cleanup() { rm -rf "$tmp"; }
trap cleanup EXIT
mkdir -p "$tmp/bin" "$tmp/deploy"
cat > "$tmp/bin/docker" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  info)
    exit 0
    ;;
  image)
    [[ "${2:-}" == "inspect" ]] || { printf 'unexpected docker image command: %s\n' "$*" >&2; exit 9; }
    ref="${3:-}"
    if [[ "$ref" == *$'\r'* ]]; then
      printf 'image ref contains CR\n' >&2
      exit 44
    fi
    exit 0
    ;;
  run)
    printf '1000\n1000\n'
    exit 0
    ;;
  *)
    printf 'unexpected docker command: %s\n' "$*" >&2
    exit 9
    ;;
esac
SH
chmod +x "$tmp/bin/docker"
printf '' > "$tmp/deploy/.env"
printf 'services:\r\n  crlf-runtime:\r\n    image: ${OPENCLAW_RUNTIME_PYTHON_IMAGE:?OPENCLAW_RUNTIME_PYTHON_IMAGE_required}\r\n    user: "${OPENCLAW_RUNTIME_UID:-1000}:${OPENCLAW_RUNTIME_GID:-1000}"\r\n' > "$tmp/docker-compose.yml"
PATH="$tmp/bin:$PATH" bash "$repo/scripts/doctor/check_runtime_bind_user_contract.sh" --env-file "$tmp/deploy/.env" --compose-file "$tmp/docker-compose.yml" > "$tmp/out" 2>&1
cat "$tmp/out"
! grep -q '镜像当前不在本机' "$tmp/out"
'''
        result = subprocess.run(
            [str(self.bash), '-lc', script],
            cwd=ROOT_DIR,
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            check=False,
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, msg=output)

    def test_ingress_boundary_cache_refreshes_stale_nginx_policy_from_local_check(self) -> None:
        env = os.environ.copy()
        env['OPENCLAW_TEST_PYTHON_BIN'] = sys.executable
        script = r'''
set -euo pipefail
repo="$(pwd -P)"
tmp="$(mktemp -d)"
cleanup() { rm -rf "$tmp"; }
trap cleanup EXIT
mkdir -p "$tmp/bin" "$tmp/deploy" "$tmp/state/openclaw/control_plane/setup" "$tmp/scripts/runtime"
cat > "$tmp/bin/jq.py" <<'PY'
from __future__ import annotations

import json
import sys


def csv_set(raw: str) -> list[str]:
    return sorted(item.strip() for item in raw.split(',') if item.strip())


def nginx_policy_ok(payload: dict[str, object], allowed: list[str] | None = None) -> bool:
    policy = payload.get('nginx_policy') if isinstance(payload.get('nginx_policy'), dict) else {}
    expected = allowed if allowed is not None else ['10.0.0.0/8', '192.168.50.0/24']
    return (
        policy.get('required') is True
        and policy.get('checked') is True
        and policy.get('ok') is True
        and policy.get('default_deny') is True
        and policy.get('rewrite_phase_default_deny') is True
        and policy.get('access_phase_default_deny') is True
        and sorted(policy.get('source_cidrs') or []) == sorted(expected)
    )


args = sys.argv[1:]
arg_values: dict[str, str] = {}
slurp_nginx = ''
filtered: list[str] = []
i = 0
while i < len(args):
    item = args[i]
    if item in ('-e', '-r'):
        i += 1
        continue
    if item == '--arg':
        arg_values[args[i + 1]] = args[i + 2]
        i += 3
        continue
    if item == '--slurpfile':
        if args[i + 1] == 'nginx':
            slurp_nginx = args[i + 2]
        i += 3
        continue
    filtered.append(item)
    i += 1

if slurp_nginx:
    evidence_path = filtered[-1]
    payload = json.loads(open(evidence_path, encoding='utf-8').read())
    nginx = json.loads(open(slurp_nginx, encoding='utf-8').read())
    merged = {'required': True, 'checked': True, 'ok': True, 'issues': []}
    merged.update(nginx)
    payload['nginx_policy'] = merged
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(0)

filter_text = filtered[0] if len(filtered) >= 2 else ''
payload = json.loads(open(filtered[-1], encoding='utf-8').read())
allowed = csv_set(arg_values.get('allowed_cidrs', '')) if 'allowed_cidrs' in arg_values else None
boundary = payload.get('boundary_evidence') if isinstance(payload.get('boundary_evidence'), dict) else {}
compose = payload.get('compose_contract') if isinstance(payload.get('compose_contract'), dict) else {}
if '.accepted == true' in filter_text:
    ok = (
        payload.get('accepted') is True
        and compose.get('compose_contract_ok') is True
        and boundary.get('accepted') is True
        and boundary.get('method') != 'none'
        and boundary.get('expected_bind_ip') == arg_values.get('listen_ip')
        and boundary.get('expected_tls_cn') == arg_values.get('tls_cn')
        and sorted(boundary.get('allowed_source_cidrs') or []) == sorted(allowed or [])
    )
elif '.nginx_policy.required' in filter_text:
    ok = nginx_policy_ok(payload, allowed)
else:
    ok = False
raise SystemExit(0 if ok else 1)
PY
cat > "$tmp/bin/jq" <<'SH'
#!/usr/bin/env bash
exec "$OPENCLAW_TEST_PYTHON_BIN" "$OPENCLAW_TEST_JQ_PY" "$@"
SH
chmod +x "$tmp/bin/jq"
export OPENCLAW_TEST_JQ_PY="$tmp/bin/jq.py"
export PATH="$tmp/bin:$PATH"
printf '%s\n' \
  'HOST_STATE_ROOT=state/openclaw' \
  'OPENCLAW_INGRESS_LISTEN_IP=10.20.30.40' \
  'OPENCLAW_TLS_CN=openclaw.internal' \
  'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS=192.168.50.0/24,10.0.0.0/8' \
  > "$tmp/deploy/.env"
cat > "$tmp/state/openclaw/control_plane/setup/ingress_boundary_evidence.json" <<'JSON'
{
  "accepted": true,
  "compose_contract": {"compose_contract_ok": true},
  "boundary_evidence": {
    "accepted": true,
    "method": "host_firewall",
    "expected_bind_ip": "10.20.30.40",
    "expected_tls_cn": "openclaw.internal",
    "allowed_source_cidrs": ["10.0.0.0/8", "192.168.50.0/24"]
  },
  "nginx_policy": {
    "required": true,
    "checked": true,
    "ok": true,
    "default_deny": true,
    "rewrite_phase_default_deny": true,
    "access_phase_default_deny": true,
    "source_cidrs": ["172.16.0.0/12"]
  }
}
JSON
cat > "$tmp/scripts/runtime/run_openclaw_python_tool.sh" <<'SH'
#!/usr/bin/env bash
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
printf '%s\n' called > "$repo_root/runner-called"
printf '%s\n' '{"ok":true,"default_deny":true,"rewrite_phase_default_deny":true,"access_phase_default_deny":true,"source_cidrs":["192.168.50.0/24","10.0.0.0/8"]}'
SH
chmod +x "$tmp/scripts/runtime/run_openclaw_python_tool.sh"
source "$repo/scripts/setup/lib/ingress_boundary_evidence_cache.sh"
ingress_boundary_cached_evidence_ok "$tmp" "$tmp/deploy/.env" 1
[[ -f "$tmp/runner-called" ]]
cat "$tmp/state/openclaw/control_plane/setup/ingress_boundary_evidence.json"
'''
        result = subprocess.run(
            [str(self.bash), '-lc', script],
            cwd=ROOT_DIR,
            text=True,
            encoding='utf-8',
            errors='replace',
            env=env,
            capture_output=True,
            check=False,
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, msg=output)
        payload = json.loads(result.stdout)
        self.assertEqual(
            payload['nginx_policy'],
            {
                'required': True,
                'checked': True,
                'ok': True,
                'issues': [],
                'default_deny': True,
                'rewrite_phase_default_deny': True,
                'access_phase_default_deny': True,
                'source_cidrs': ['192.168.50.0/24', '10.0.0.0/8'],
            },
        )

    def test_full_test_external_group_treats_model_ref_as_model_name_not_executable(self) -> None:
        runner_source = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'full_test_group_runner.sh').read_text(encoding='utf-8')
        doctor_source = (ROOT_DIR / 'scripts' / 'doctor' / 'check_model_profile_connectivity.sh').read_text(encoding='utf-8')
        source = f'{runner_source}\n{doctor_source}'
        self.assertIn('scripts/doctor/check_model_profile_connectivity.sh', runner_source)

        self.assertIn('if [[ "$purpose" == \'local_model_command\' ]]; then', source)
        self.assertIn('local_failures+=("$name: 本地模型命令不可执行")', source)
        self.assertIn('checked+=("$name=${purpose:-configured}")', source)

    def test_high_risk_shell_entrypoints_keep_explicit_boundaries(self) -> None:
        mirror_source = (ROOT_DIR / 'scripts' / 'setup' / 'render_local_ro_mirror.sh').read_text(encoding='utf-8')
        release_gate_source = (ROOT_DIR / 'scripts' / 'doctor' / 'run_repo_release_gate.sh').read_text(encoding='utf-8')
        control_plane_runtime_source = (ROOT_DIR / 'scripts' / 'doctor' / 'check_control_plane_runtime.sh').read_text(encoding='utf-8')
        internal_api_runtime_source = (ROOT_DIR / 'scripts' / 'doctor' / 'check_internal_api_runtime.sh').read_text(encoding='utf-8')
        registry_probe_source = (ROOT_DIR / 'scripts' / 'lib' / 'registry_manifest_probe.sh').read_text(encoding='utf-8')
        supply_chain_source = (ROOT_DIR / 'scripts' / 'images' / 'check_openclaw_supply_chain.sh').read_text(encoding='utf-8')

        self.assertIn('ensure_output_dir_in_gateway_state "$OUTPUT_DIR"', mirror_source)
        self.assertIn('runtime_permissions_host_gateway_state_dir "$ROOT_DIR"', mirror_source)
        self.assertIn('setup env render-local-ro-mirror', mirror_source)
        assert_static_text_absent(self, 'rm -rf "$OUTPUT_DIR"', mirror_source)
        assert_static_text_absent(self, 'cp -a "$source_path"', mirror_source)
        self.assertIn('--with-docker-sock', release_gate_source)
        self.assertIn('WITH_DOCKER_SOCK=1', release_gate_source)
        self.assertIn('add_host_tool_overlay jq 1', release_gate_source)
        self.assertIn('add_host_tool_overlay docker 1', release_gate_source)
        self.assertIn('PATH=$TOOL_OVERLAY_DIR/bin:/usr/local/bin:/usr/bin:/bin', release_gate_source)
        self.assertIn('LD_LIBRARY_PATH=$TOOL_OVERLAY_DIR/lib', release_gate_source)
        self.assertIn('ldd_output="$(ldd "$host_tool" 2>/dev/null || true)"', release_gate_source)
        self.assertIn('OPENCLAW_INTERNAL_API_CHECK_MAX_RESPONSE_BYTES', internal_api_runtime_source)
        self.assertIn('OPENCLAW_INTERNAL_API_CHECK_MAX_RESPONSE_BYTES', control_plane_runtime_source)
        self.assertIn('response too large: exceeds {MAX_RESPONSE_BYTES} bytes', internal_api_runtime_source)
        self.assertIn('response too large: exceeds {MAX_RESPONSE_BYTES} bytes', control_plane_runtime_source)
        assert_static_text_absent(
            self,
            'if [[ -S /var/run/docker.sock ]]; then\n  STATIC_RUNNER_ARGS+=(--mount /var/run/docker.sock)',
            release_gate_source,
        )
        assert_static_text_absent(self, 'STATIC_RUNNER_ARGS+=(--mount "$host_tool")', release_gate_source)
        self.assertIn('REGISTRY_MANIFEST_PROBE_MAX_BYTES', registry_probe_source)
        self.assertIn('REGISTRY_MANIFEST_PROBE_TOKEN_MAX_BYTES', registry_probe_source)
        self.assertIn('[[ "$http_code" =~ ^[0-9][0-9][0-9]$ ]] && return 11', registry_probe_source)
        self.assertIn('MAX_GITHUB_RESPONSE_BYTES', supply_chain_source)
        self.assertIn('RESOLVE_DIGEST_CAPTURE_STATUS_CACHE', supply_chain_source)
        self.assertIn('"$OFFICIAL_RELEASE_IMAGE_REPO" == "$RELEASE_CHECK_IMAGE_REPO"', supply_chain_source)
        self.assertIn('OPENCLAW_GIT_LS_REMOTE_TIMEOUT_SECONDS', supply_chain_source)
        self.assertIn('timeout --foreground "$OPENCLAW_GIT_LS_REMOTE_TIMEOUT_SECONDS"', supply_chain_source)
        self.assertIn('supply_chain_tag_is_newer "$git_tag" "$api_tag"', supply_chain_source)
        self.assertIn('resolve_latest_release_info_git_tags_with_release_manifest "$git_remote" "$api_tag"', supply_chain_source)
        self.assertIn('newer-git-tags-without-release-manifest', supply_chain_source)
        self.assertIn('corrected-from-${api_source:-github-api}:$api_tag', supply_chain_source)
        self.assertIn('MAX_RESPONSE_BYTES', control_plane_runtime_source)
        self.assertIn('MAX_RESPONSE_BYTES', internal_api_runtime_source)
        export_bundle_source = (ROOT_DIR / 'scripts' / 'setup' / 'export_clean_delivery_bundle.sh').read_text(encoding='utf-8')
        self.assertIn('add_host_tool_overlay jq 1', export_bundle_source)
        self.assertIn('PATH=$TOOL_OVERLAY_DIR/bin:/usr/local/bin:/usr/bin:/bin', export_bundle_source)
        self.assertIn('ldd_output="$(ldd "$host_tool" 2>/dev/null || true)"', export_bundle_source)

    def test_supply_chain_ignores_newer_git_tag_until_release_manifest_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            digest = 'sha256:142f70fa2751bdedf03648ae427372fff3f92ac0e96ab91abb3824b088c38b7b'
            latest_payload_path = temp_root / 'latest-release.json'
            latest_payload_path.write_text('{"tag_name":"v2026.5.3"}\n', encoding='utf-8')
            deploy_env_path = temp_root / 'deploy.env'
            image_pin_path = temp_root / 'openclaw.env'
            runtime_pin_path = temp_root / 'runtime.env'
            runtime_contract_path = temp_root / 'runtime_contract.json'
            deploy_env_path.write_text('', encoding='utf-8')
            image_pin_path.write_text(
                f'OPENCLAW_OFFICIAL_GATEWAY_IMAGE=ghcr.io/openclaw/openclaw:2026.5.7@{digest}\n',
                encoding='utf-8',
            )
            runtime_pin_path.write_text(
                '\n'.join(
                    [
                        'OPENCLAW_CONTROL_PLANE_IMAGE=docker.invalid/python:3.11@sha256:' + '2' * 64,
                        'OPENCLAW_RUNTIME_PYTHON_IMAGE=docker.invalid/python:3.11@sha256:' + '3' * 64,
                        'NGINX_IMAGE=docker.invalid/nginx:1.28@sha256:' + '4' * 64,
                        '',
                    ]
                ),
                encoding='utf-8',
            )
            runtime_contract_path.write_text(
                json.dumps(
                    {
                        'upstream_release': {
                            'release_discovery': {
                                'github_latest_release_api': 'https://api.github.invalid/repos/openclaw/openclaw/releases/latest',
                                'github_release_url_template': 'https://github.invalid/openclaw/openclaw/releases/tag/v{tag}',
                                'package_url': 'https://github.invalid/openclaw/openclaw/releases',
                            },
                            'image_repositories': {
                                'official_release_image_repo': 'ghcr.io/openclaw/openclaw',
                                'default_official_gateway_image_repo': 'ghcr.io/openclaw/openclaw',
                                'allowed_candidate_image_repos': ['ghcr.io/openclaw/openclaw'],
                            },
                        }
                    },
                    ensure_ascii=False,
                )
                + '\n',
                encoding='utf-8',
            )
            fake_bin = temp_root / 'bin'
            fake_bin.mkdir(parents=True, exist_ok=True)
            fake_jq_py = fake_bin / 'jq.py'
            fake_jq_py.write_text(
                r'''
from __future__ import annotations

import json
import sys
import urllib.parse

args = sys.argv[1:]
arg_values: dict[str, str] = {}
flags: set[str] = set()
filters: list[str] = []
i = 0
while i < len(args):
    item = args[i]
    if item.startswith('-') and item not in ('--arg',):
        flags.add(item)
        i += 1
        continue
    if item == '--arg':
        arg_values[args[i + 1]] = args[i + 2]
        i += 3
        continue
    filters.append(item)
    i += 1

filter_text = filters[0] if filters else ''
if '$value|@uri' in filter_text:
    print(urllib.parse.quote(arg_values.get('value', ''), safe=''))
    raise SystemExit(0)
if filter_text == '.tag_name // empty':
    payload = json.loads(sys.stdin.read() or '{}')
    print(str(payload.get('tag_name') or ''))
    raise SystemExit(0)
if filter_text == '.token // .access_token // empty':
    payload = json.loads(sys.stdin.read() or '{}')
    print(str(payload.get('token') or payload.get('access_token') or ''))
    raise SystemExit(0)
if 'OPENCLAW_RUNTIME_CONTRACT_GITHUB_LATEST_RELEASE_API' in filter_text:
    payload = json.loads(open(filters[-1], encoding='utf-8').read())
    release = payload.get('upstream_release') if isinstance(payload.get('upstream_release'), dict) else {}
    discovery = release.get('release_discovery') if isinstance(release.get('release_discovery'), dict) else {}
    repos = release.get('image_repositories') if isinstance(release.get('image_repositories'), dict) else {}
    model = payload.get('model_runtime') if isinstance(payload.get('model_runtime'), dict) else {}
    defaults = model.get('defaults') if isinstance(model.get('defaults'), dict) else {}
    catalog = model.get('catalog') if isinstance(model.get('catalog'), list) else []
    catalog_ids = ','.join(str(row.get('id') or '') for row in catalog if isinstance(row, dict) and row.get('id'))
    allowed_repos = repos.get('allowed_candidate_image_repos') if isinstance(repos.get('allowed_candidate_image_repos'), list) else []
    lines = {
        'OPENCLAW_RUNTIME_CONTRACT_GITHUB_LATEST_RELEASE_API': discovery.get('github_latest_release_api') or '',
        'OPENCLAW_RUNTIME_CONTRACT_GITHUB_RELEASE_URL_TEMPLATE': discovery.get('github_release_url_template') or '',
        'OPENCLAW_RUNTIME_CONTRACT_PACKAGE_URL': discovery.get('package_url') or '',
        'OPENCLAW_RUNTIME_CONTRACT_OFFICIAL_RELEASE_IMAGE_REPO': repos.get('official_release_image_repo') or '',
        'OPENCLAW_RUNTIME_CONTRACT_DEFAULT_OFFICIAL_GATEWAY_IMAGE_REPO': repos.get('default_official_gateway_image_repo') or '',
        'OPENCLAW_RUNTIME_CONTRACT_ALLOWED_CANDIDATE_IMAGE_REPOS_CSV': ','.join(str(item) for item in allowed_repos),
        'OPENCLAW_RUNTIME_CONTRACT_HAS_MODEL_RUNTIME': '1' if defaults.get('primary') else '0',
        'OPENCLAW_RUNTIME_CONTRACT_MODEL_PRIMARY': defaults.get('primary') or '',
        'OPENCLAW_RUNTIME_CONTRACT_MODEL_CATALOG_IDS_CSV': catalog_ids,
    }
    for key, value in lines.items():
        print(f'{key}={value}')
    raise SystemExit(0)
if '-n' in flags:
    def status_or_none(name: str) -> int | None:
        value = arg_values.get(name, '').strip()
        return int(value) if value else None

    def value_or_none(name: str) -> str | None:
        value = arg_values.get(name, '').strip()
        return value or None

    payload = {
        'schema_version': 1,
        'generated_at': '2026-01-01T00:00:00Z',
        'scope': arg_values.get('scope', ''),
        'current': {
            'ref': arg_values.get('current_ref', ''),
            'repo': arg_values.get('current_repo', ''),
            'tag': arg_values.get('current_tag', ''),
            'release_version': arg_values.get('current_release', ''),
            'pinned_digest': arg_values.get('current_digest', ''),
            'mirror_repo': arg_values.get('current_repo', ''),
            'mirror_digest_status': status_or_none('current_mirror_status'),
            'mirror_digest': value_or_none('current_mirror_digest'),
            'official_repo': arg_values.get('current_official_repo', ''),
            'official_digest_status': status_or_none('current_official_status'),
            'official_digest': value_or_none('current_official_digest'),
        },
        'release_lookup': None,
        'latest': None,
    }
    if arg_values.get('scope') != 'current-tag':
        payload['release_lookup'] = {
            'status': value_or_none('release_lookup_status'),
            'source': value_or_none('release_lookup_source'),
            'detail': value_or_none('release_lookup_detail'),
        }
        payload['latest'] = {
            'tag': arg_values.get('latest_tag', ''),
            'release_version': arg_values.get('latest_release', ''),
            'mirror_repo': arg_values.get('latest_mirror_repo', ''),
            'mirror_digest_status': status_or_none('latest_mirror_status'),
            'mirror_digest': value_or_none('latest_mirror_digest'),
            'official_repo': arg_values.get('latest_official_repo', ''),
            'official_digest_status': status_or_none('latest_official_status'),
            'official_digest': value_or_none('latest_official_digest'),
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(0)
raise SystemExit(1)
'''.lstrip(),
                encoding='utf-8',
            )

            def bash_path(path: Path) -> str:
                if os.name != 'nt':
                    return path.as_posix()
                result = subprocess.run(
                    [str(self.bash), '-lc', 'cygpath -u "$1"', 'openclaw-path', str(path)],
                    cwd=ROOT_DIR,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    capture_output=True,
                    check=False,
                )
                if result.returncode != 0:
                    self.skipTest('无法把 Windows 临时目录转换为 Git Bash 路径')
                return result.stdout.strip()

            cache_dir_for_bash = bash_path(temp_root / 'cache')
            deploy_env_for_bash = bash_path(deploy_env_path)
            image_pin_for_bash = bash_path(image_pin_path)
            runtime_pin_for_bash = bash_path(runtime_pin_path)
            runtime_contract_for_bash = bash_path(runtime_contract_path)
            fake_bin_for_bash = bash_path(fake_bin)
            fake_jq_py_for_bash = bash_path(fake_jq_py)
            python_bin_for_bash = bash_path(Path(sys.executable))
            fake_jq = fake_bin / 'jq'
            fake_jq.write_text(
                '#!/usr/bin/env bash\n'
                f'exec {shlex.quote(python_bin_for_bash)} {shlex.quote(fake_jq_py_for_bash)} "$@"\n',
                encoding='utf-8',
            )
            fake_jq.chmod(fake_jq.stat().st_mode | 0o111)
            latest_payload_url = 'file://' + bash_path(latest_payload_path)
            env = os.environ.copy()
            script = f'''
set -euo pipefail
curl() {{
  local out=''
  local headers=''
  local url=''
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -o) out="$2"; shift 2 ;;
      -D) headers="$2"; shift 2 ;;
      -w) shift 2 ;;
      -H|--connect-timeout|--max-time|--max-filesize) shift 2 ;;
      -sS|-L) shift ;;
      *) url="$1"; shift ;;
    esac
  done
  if [[ "$url" == *'/repos/openclaw/openclaw/releases/latest' || "$url" == {shlex.quote(latest_payload_url)} ]]; then
    printf '%s\\n' '{{"tag_name":"v2026.5.3"}}' > "$out"
    printf '200'
    return 0
  fi
  case "$url" in
    *'/manifests/2026.5.3-1')
      printf 'HTTP/2 200\\r\\nDocker-Content-Digest: {digest}\\r\\n\\r\\n' > "$headers"
      printf '%s\\n' '{{"schemaVersion":2}}' > "$out"
      printf '200'
      ;;
    *'/manifests/2026.5.3'|*'/manifests/2026.5.4')
      printf 'HTTP/2 404\\r\\n\\r\\n' > "$headers"
      printf '%s\\n' '{{"errors":[{{"code":"MANIFEST_UNKNOWN"}}]}}' > "$out"
      printf '404'
      ;;
    *)
      printf 'unexpected url: %s\\n' "$url" >&2
      return 22
      ;;
  esac
}}
git() {{
  printf '%s\\t%s\\n' aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa refs/tags/v2026.5.3
  printf '%s\\t%s\\n' bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb refs/tags/v2026.5.3-1
  printf '%s\\t%s\\n' cccccccccccccccccccccccccccccccccccccccc refs/tags/v2026.5.4
}}
export -f curl git
export PATH={shlex.quote(fake_bin_for_bash)}:$PATH
export OPENCLAW_REPO_CONTRACTS_FORCE_AWK=1
export IMAGE_ENV_DEPLOY_ENV_PATH={shlex.quote(deploy_env_for_bash)}
export IMAGE_ENV_PIN_FILE={shlex.quote(image_pin_for_bash)}
export IMAGE_ENV_RUNTIME_PIN_FILE={shlex.quote(runtime_pin_for_bash)}
export OPENCLAW_RUNTIME_CONTRACT_PATH={shlex.quote(runtime_contract_for_bash)}
export OPENCLAW_SUPPLY_CHAIN_CACHE_DIR_OVERRIDE={shlex.quote(cache_dir_for_bash)}
export OPENCLAW_GITHUB_RELEASES_URL_OVERRIDE={shlex.quote(latest_payload_url)}
export OPENCLAW_GIT_LS_REMOTE_TIMEOUT_SECONDS=0
export OPENCLAW_CURRENT_REMOTE_DIGEST_OVERRIDE={digest}
export OPENCLAW_CURRENT_OFFICIAL_REMOTE_DIGEST_OVERRIDE={digest}
export REGISTRY_MANIFEST_PROBE_PYTHON_BIN=false
./scripts/images/check_openclaw_supply_chain.sh --scope latest-stable
'''

            result = subprocess.run(
                [str(self.bash), '-lc', script],
                cwd=ROOT_DIR,
                text=True,
                encoding='utf-8',
                errors='replace',
                env=env,
                capture_output=True,
                check=False,
            )

            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, msg=output)
            payload = json.loads(result.stdout)
            self.assertEqual(payload['release_lookup']['source'], 'git-tags')
            self.assertEqual(payload['release_lookup']['detail'], 'corrected-from-github-api:2026.5.3')
            self.assertEqual(payload['latest']['tag'], '2026.5.3-1')
            self.assertEqual(payload['latest']['mirror_digest_status'], 0)
            self.assertEqual(payload['latest']['mirror_digest'], digest)

    def test_post_deploy_full_acceptance_resume_does_not_trigger_run_all_once(self) -> None:
        source = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'deploy_flow_control_plane_shell.sh').read_text(encoding='utf-8')
        deploy_source = (ROOT_DIR / 'scripts' / 'setup' / 'one_click_deploy.sh').read_text(encoding='utf-8')
        static_help_source = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'setup_cli_common.sh').read_text(encoding='utf-8')
        resume_guard = source.split('deploy_check_ingress_boundary_resume_guard()', 1)[1].split('deploy_should_run_stage()', 1)[0]

        self.assertIn('${RESUME_FROM:-}" == "post_deploy_full_acceptance"', source)
        self.assertIn('${RESUME_FROM:-}" == "post_deploy_acceptance"', source)
        self.assertIn('执行范围限定为 full test 与 runtime evidence，跳过 control_plane_run_all_once', source)
        self.assertIn('执行 required run ledger jobs、full test 与 runtime evidence', source)
        self.assertIn('发送动作按当前 target 配置执行', source)
        self.assertIn('flow_set_var CURRENT_STAGE_NAME control_plane_run_all_once', source)
        self.assertIn('flow_set_var CURRENT_STAGE_NAME post_deploy_full_acceptance', source)
        self.assertIn('if ! manifest_json="$(deploy_testing_manifest_json_for_env)"; then', source)
        assert_static_text_absent(self, 'deploy_testing_manifest_json_for_env 2>/dev/null)" || return 0', source)
        self.assertIn('deployment_acceptance_export_guard deploy_assert_deployment_acceptance_ready_for_evidence', source)
        self.assertIn('export_runtime_acceptance_evidence bash "$ROOT_DIR/scripts/runtime/export_runtime_acceptance_evidence.sh"', source)
        self.assertIn('deploy_assert_deployment_acceptance_ready_for_evidence', source)
        self.assertIn('deployment_acceptance_export_guard', source)
        self.assertIn('path="${path//\\\\//}"', source)
        self.assertIn('/*|[A-Za-z]:/*) printf', source)
        self.assertIn('*) printf \'%s/%s\\n\' "$ROOT_DIR" "$path"', source)
        self.assertIn('deployment_acceptance_contract" and .status == "PASS"', source)
        self.assertIn('.accepted // false', source)
        self.assertIn('ingress_boundary_cached_evidence_ok "$ROOT_DIR" "$ENV_FILE" 1', resume_guard)
        self.assertIn('check_output="$(bash "$ROOT_DIR/scripts/doctor/check_ingress_boundary_evidence.sh"', resume_guard)
        self.assertLess(
            resume_guard.find('ingress_boundary_cached_evidence_ok "$ROOT_DIR" "$ENV_FILE" 1'),
            resume_guard.find('check_output="$(bash "$ROOT_DIR/scripts/doctor/check_ingress_boundary_evidence.sh"'),
        )
        self.assertIn('post_deploy_acceptance|post_deploy_full_acceptance)', deploy_source)
        self.assertIn('不能与 --prepare-only 或 --skip-acceptance 同用', deploy_source)
        self.assertIn('后置验收 resume 不能与 --prepare-only 或 --skip-acceptance 同用', static_help_source)

    def test_run_all_once_entrypoint_retries_scheduler_cycle_lock_only(self) -> None:
        source = (ROOT_DIR / 'scripts' / 'control_plane' / 'run_control_plane_run_all_once.sh').read_text(encoding='utf-8')

        self.assertIn('OPENCLAW_RUN_ALL_ONCE_MAX_ATTEMPTS', source)
        self.assertIn('OPENCLAW_RUN_ALL_ONCE_RETRY_SLEEP_SECONDS', source)
        self.assertIn('scheduler cycle lock busy', source)
        self.assertIn('[[ "$rc" -eq 5 ]]', source)
        self.assertIn('exit "$rc"', source)
        assert_static_text_absent(self, 'exec bash "$RUNNER"', source)

    def test_dynamic_help_invokes_shell_tools_through_bash(self) -> None:
        source = (ROOT_DIR / 'scripts' / 'lib' / 'flow_entry_surface_shell.sh').read_text(encoding='utf-8')

        self.assertIn('flow_entry_exec_dynamic_command()', source)
        self.assertIn('OPENCLAW_PYTHON_TOOL_NO_PULL=1 bash "$command_path" "$@"', source)
        self.assertIn('if flow_entry_exec_dynamic_command "$@"; then', source)
        self.assertIn('if flow_entry_exec_dynamic_command "$@" >&2; then', source)
        assert_static_text_absent(self, 'env OPENCLAW_PYTHON_TOOL_NO_PULL=1 "$@"', source)

    def test_deploy_entry_repairs_exec_bits_before_control_plane_calls(self) -> None:
        deploy_source = (ROOT_DIR / 'scripts' / 'setup' / 'one_click_deploy.sh').read_text(encoding='utf-8')
        control_plane_source = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'deploy_flow_control_plane_shell.sh').read_text(encoding='utf-8')
        full_test_source = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'full_test_acceptance_shell.sh').read_text(encoding='utf-8')

        self.assertIn('deploy_reject_root_runtime_user()', deploy_source)
        self.assertIn('one_click_deploy 拒绝以 root 执行正式部署主链', deploy_source)
        self.assertIn('apply_ingress_boundary_rules、fix_permissions 等宿主机步骤', deploy_source)
        self.assertIn('deploy_repair_repo_exec_bits()', deploy_source)
        self.assertIn('bash "$ROOT_DIR/scripts/setup/fix_permissions.sh"', deploy_source)
        self.assertLess(
            deploy_source.find('deploy_reject_root_runtime_user\n'),
            deploy_source.find('deploy_repair_repo_exec_bits\n'),
        )
        self.assertLess(
            deploy_source.find('deploy_repair_repo_exec_bits\n'),
            deploy_source.find('deploy_prime_summary_context\n'),
        )
        self.assertIn('bash "$OPENCLAW_PYTHON_TOOL" "${args[@]}"', control_plane_source)
        self.assertIn('DEPLOY_SUCCESS_CMD=(bash "$OPENCLAW_PYTHON_TOOL" setup flow deploy-success)', control_plane_source)
        self.assertIn('FLOW_FAILURE_CMD=(bash "$OPENCLAW_PYTHON_TOOL" setup flow deploy-failure)', control_plane_source)
        self.assertIn('FULL_TEST_SURFACE_CMD=(bash "$OPENCLAW_PYTHON_TOOL" setup flow full-test-surface)', full_test_source)
        self.assertIn('--post-acceptance "$RUN_POST_DEPLOY_ACCEPTANCE"', (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'deploy_flow_summary_shell.sh').read_text(encoding='utf-8'))

    def test_deploy_static_control_plane_context_is_loaded_once(self) -> None:
        deploy_source = (ROOT_DIR / 'scripts' / 'setup' / 'one_click_deploy.sh').read_text(encoding='utf-8')
        control_plane_source = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'deploy_flow_control_plane_shell.sh').read_text(encoding='utf-8')
        registry_source = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'deploy_stage_registry.sh').read_text(encoding='utf-8')
        defaults_body = control_plane_source.split('deploy_load_control_plane_defaults()', 1)[1].split('deploy_verify_basic_gate_proof()', 1)[0]

        self.assertIn('DEPLOY_CONTROL_PLANE_BOOTSTRAP_JSON=""', control_plane_source)
        self.assertIn('local -a args=(bootstrap-json "${DEPLOY_FLOW_ARGS[@]}")', control_plane_source)
        self.assertIn('one_click_deploy_cp "${args[@]}"', control_plane_source)
        self.assertIn('deploy_bootstrap_value()', control_plane_source)
        self.assertIn('deploy_run_control_plane_preflight_from_bootstrap()', control_plane_source)
        self.assertIn('jq -r \'.effectiveStages[]? // empty\'', control_plane_source)
        assert_static_text_absent(
            self,
            'flow_sequence_load_lines EFFECTIVE_STAGES bash "$OPENCLAW_PYTHON_TOOL" setup flow deploy effective-stages',
            control_plane_source,
        )
        assert_static_text_absent(self, 'setup flow deploy validate-resume "${DEPLOY_FLOW_ARGS[@]}" --stage "$RESUME_FROM"', control_plane_source)
        self.assertIn('flow_run_logged_step "$LOG_PATH" CURRENT_STAGE_NAME LAST_FAILED_STEP LAST_FAILED_CODE \\\n  control_plane_preflight deploy_run_control_plane_preflight_from_bootstrap', deploy_source)
        assert_static_text_absent(
            self,
            'flow_preflight_run "$OPENCLAW_PYTHON_TOOL" setup flow one-click-deploy preflight',
            deploy_source,
        )
        self.assertNotIn('one_click_deploy_cp ', defaults_body)
        self.assertIn('deploy_bootstrap_value artifact-dir', defaults_body)
        self.assertIn('deploy_bootstrap_value image-archive-pattern', defaults_body)
        self.assertIn('DEPLOY_STAGE_REGISTRY_INITIALIZED=0', registry_source)
        self.assertIn('if [[ "$DEPLOY_STAGE_REGISTRY_INITIALIZED" == "1" ]]; then', registry_source)
        self.assertIn('DEPLOY_STAGE_REGISTRY_INITIALIZED=1', registry_source)

    def test_deploy_and_upgrade_use_extension_env_ensure_and_effective_compose(self) -> None:
        deploy_source = (ROOT_DIR / 'scripts' / 'setup' / 'one_click_deploy.sh').read_text(encoding='utf-8')
        upgrade_source = (ROOT_DIR / 'scripts' / 'setup' / 'one_click_upgrade.sh').read_text(encoding='utf-8')
        config_source = (ROOT_DIR / 'scripts' / 'setup' / 'one_click_config.sh').read_text(encoding='utf-8')
        runtime_compose = (ROOT_DIR / 'scripts' / 'runtime' / 'runtime_compose_lib.sh').read_text(encoding='utf-8')
        show_runtime_compose = (ROOT_DIR / 'scripts' / 'runtime' / 'show_runtime_compose_config.sh').read_text(encoding='utf-8')
        dispatch_runtime = (ROOT_DIR / 'scripts' / 'doctor' / 'check_dispatch_runtime.sh').read_text(encoding='utf-8')
        deploy_stage_runner = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'deploy_stage_runner.sh').read_text(encoding='utf-8')
        deploy_stage_registry = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'deploy_stage_registry.sh').read_text(encoding='utf-8')
        deploy_stage_flow = json.loads((ROOT_DIR / 'config' / 'governance' / 'flows' / 'deploy_stage_flow.json').read_text(encoding='utf-8'))

        self.assertIn('extension_env_gate_ensure_active_profile', deploy_source)
        self.assertIn('setup/extension_env_preflight.json', deploy_source)
        self.assertIn('extension_env_preflight deploy_check_extension_env_prereqs', deploy_source)
        self.assertIn('runtime mounts sync-compose --output "$effective_compose_path"', deploy_source)
        self.assertIn('source "$ROOT_DIR/scripts/lib/flow_step_runner.sh"', deploy_source)
        assert_static_text_absent(self, 'echo "$@" | tee -a "$LOG_PATH"', deploy_source)
        self.assertIn('setup upgrade readiness --json', upgrade_source)
        self.assertIn('source "$ROOT_DIR/scripts/setup/lib/extension_env_gate.sh"', upgrade_source)
        self.assertIn('extension_env_gate_ensure_active_profile \\', upgrade_source)
        self.assertIn('"$UPGRADE_ROOT/extension_env_ensure.json"', upgrade_source)
        self.assertIn('scheduler_maintenance.json', upgrade_source)
        self.assertIn('--refresh-stack-lock', upgrade_source)
        self.assertIn('verify_or_refresh_stack_lock()', upgrade_source)
        self.assertIn('SOURCE_METADATA_PATH="$UPGRADE_ROOT/source_sync_metadata.json"', upgrade_source)
        self.assertIn('write_source_sync_metadata()', upgrade_source)
        self.assertIn('base_release_bundle_hash_for_dir()', upgrade_source)
        self.assertIn('releaseBundleHash: $releaseBundleHash', upgrade_source)
        self.assertIn('assert_current_tree_matches_target_commit()', upgrade_source)
        self.assertIn('refresh_extension_lock_if_requested()', upgrade_source)
        self.assertIn('control-plane extensions lock >"$UPGRADE_ROOT/extensions_lock_refresh.json"', upgrade_source)
        self.assertIn('local -a verify_args=(control-plane stack verify --strict-release --json)', upgrade_source)
        self.assertIn('[[ "$verify_status" == "ok" && "$REFRESH_STACK_LOCK" != "1" ]]', upgrade_source)
        self.assertIn('离线源码包不包含可解析 Git HEAD', upgrade_source)
        self.assertIn('跳过源码同步时声明的目标 commit 与当前 Git HEAD 不一致', upgrade_source)
        self.assertIn('deploy/nginx/certs', upgrade_source)
        self.assertIn('跳过源码同步时 base release 文件与目标 commit 不一致', upgrade_source)
        self.assertIn('跳过源码同步时发现未纳入目标 commit 的 base release 文件', upgrade_source)
        self.assertIn('--source-metadata "$SOURCE_METADATA_PATH"', upgrade_source)
        self.assertIn('--update-source-provenance', upgrade_source)
        assert_static_text_absent(self, '.codex_deploy_marker', upgrade_source)
        self.assertIn('source "$ROOT_DIR/scripts/lib/flow_step_runner.sh"', upgrade_source)
        self.assertIn('upgrade_run_logged_step "deploy services"', upgrade_source)
        self.assertIn('upgrade_run_logged_step "wait services healthy" wait_runtime_services_healthy', upgrade_source)
        self.assertIn('OPENCLAW_UPGRADE_SERVICE_READY_ATTEMPTS', upgrade_source)
        self.assertIn('runtime_services_all_healthy()', upgrade_source)
        self.assertLess(
            upgrade_source.find('refresh_extension_lock_if_requested\n'),
            upgrade_source.find('verify_or_refresh_stack_lock\n'),
        )
        self.assertLess(
            upgrade_source.find('upgrade_run_logged_step "deploy services"'),
            upgrade_source.find('upgrade_run_logged_step "wait services healthy" wait_runtime_services_healthy'),
        )
        self.assertLess(
            upgrade_source.find('upgrade_run_logged_step "wait services healthy" wait_runtime_services_healthy'),
            upgrade_source.find('disable_maintenance "upgrade_acceptance_start"'),
        )
        self.assertIn('upgrade_run_redacted_file_step "full test"', upgrade_source)
        assert_static_text_absent(self, '2>&1 | tee -a "$LOG_PATH"', upgrade_source)
        assert_static_text_absent(self, '2>&1 | tee -a "$LOG_PATH"', deploy_stage_runner)
        self.assertIn('check_openclaw_release.sh" 2>&1 | flow_redact_sensitive_stream | tee -a "$LOG_PATH"', deploy_stage_runner)
        self.assertIn('runtime mounts sync-compose --output "$EFFECTIVE_COMPOSE_PATH"', config_source)
        self.assertIn('runtime_compose_default_file()', runtime_compose)
        self.assertIn('runtime_compose_rebased_state_env_file()', runtime_compose)
        self.assertIn('runtime_compose_state_value_for_compose()', runtime_compose)
        self.assertIn('--project-directory "$ROOT_DIR/deploy" --project-name deploy', runtime_compose)
        self.assertIn('HOST_STATE_ROOT=" host_state_root', runtime_compose)
        self.assertIn('COMPOSE_FILE="$(runtime_compose_default_file "$ROOT_DIR" "$ENV_FILE")"', show_runtime_compose)
        self.assertIn('COMPOSE_FILE="${COMPOSE_FILE:-$(runtime_compose_default_file "$ROOT_DIR" "$ENV_FILE")}"', dispatch_runtime)
        self.assertIn('env-file-compose-file)', deploy_stage_registry)
        self.assertIn('DEPLOY_STAGE_COMMAND+=(--env-file "$ENV_FILE" --compose-file "$COMPOSE_FILE")', deploy_stage_registry)
        self.assertIn('env-file-compose-file-require-local)', deploy_stage_registry)
        self.assertIn('DEPLOY_STAGE_COMMAND+=(--env-file "$ENV_FILE" --compose-file "$COMPOSE_FILE" --require-local)', deploy_stage_registry)
        self.assertIn('DEPLOY_STAGE_COMMAND+=(--env-file "$ENV_FILE" --compose-file "$COMPOSE_FILE" --require-nginx-policy --no-write)', deploy_stage_registry)
        self.assertEqual(
            deploy_stage_flow['stages']['check_runtime_compose_contract']['execution']['arg_mode'],
            'env-file-compose-file',
        )
        self.assertEqual(
            deploy_stage_flow['stages']['check_deployment_image_contract']['execution']['arg_mode'],
            'env-file-compose-file-require-local',
        )
        self.assertIn('deploy_refresh_after_pull_images', deploy_source)
        self.assertIn('gateway_source_selection.json', deploy_source)
        self.assertIn('basic_gate_proof_after_image_source_switch', deploy_source)
        self.assertIn('deploy_reload_image_env_after_source_selection', deploy_source)
        self.assertIn('IMAGE_ENV_LOADED=0', deploy_source)
        refresh_body = deploy_source.split('deploy_refresh_after_pull_images()', 1)[1].split('deploy_validate_cli_options()', 1)[0]
        self.assertLess(
            refresh_body.find('deploy_reload_image_env_after_source_selection'),
            refresh_body.find('deploy_render_effective_compose'),
        )

    def test_openclaw_pin_candidate_repo_derives_current_tag_digest(self) -> None:
        source = (ROOT_DIR / 'scripts' / 'images' / 'update_openclaw_pin.sh').read_text(encoding='utf-8')

        self.assertIn('--candidate-repo', source)
        self.assertIn('IMAGE_REF="$CANDIDATE_REPO:$OPENCLAW_OFFICIAL_GATEWAY_TAG@$OPENCLAW_OFFICIAL_GATEWAY_DIGEST"', source)
        self.assertIn('--candidate-repo 仅支持 --mode candidate', source)

    def test_openclaw_pin_candidate_next_steps_recheck_digest_first(self) -> None:
        payload = json.loads((ROOT_DIR / 'config' / 'governance' / 'docs' / 'image_governance_surface.json').read_text(encoding='utf-8'))
        next_steps = payload['surfaces']['openclaw_pin_candidate']['next_steps']

        self.assertEqual(next_steps[0], 'bash ./scripts/images/check_openclaw_digest.sh')
        self.assertIn('bash ./scripts/images/pull_images.sh', next_steps)

    def test_pull_images_failure_guidance_links_network_profile_and_candidate_repo(self) -> None:
        """镜像拉取失败提示必须保留网络画像、候选源和离线归档修复路径。"""
        source = (ROOT_DIR / 'scripts' / 'images' / 'pull_images.sh').read_text(encoding='utf-8')

        self.assertIn('print_pull_failure_next_steps', source)
        self.assertIn('--network-profile cn', source)
        self.assertTrue(
            '--candidate-repo ghcr.nju.edu.cn/openclaw/openclaw --mode candidate' in source
            or 'PULL_GATEWAY_CANDIDATE_MODE' in source
        )
        self.assertIn('export_deployment_images.sh -> load_deployment_images.sh', source)
        self.assertIn('PULL_GATEWAY_OFFICIAL_TIMEOUT="${PULL_GATEWAY_OFFICIAL_TIMEOUT:-300}"', source)
        self.assertTrue(
            'PULL_CN_GATEWAY_CANDIDATE_FAIL_FAST="${PULL_CN_GATEWAY_CANDIDATE_FAIL_FAST:-auto}"' in source
            or 'auto-switch|fail-fast|off' in source
        )
        self.assertTrue('should_fail_fast_for_gateway_candidate' in source or 'gateway_source_selection.json' in source)
        self.assertIn('docker_daemon_has_cn_registry_mirrors', source)

    def test_image_readiness_failures_link_host_readiness_network_profile_and_offline_archive(self) -> None:
        sources = {
            'deployment_readiness': (ROOT_DIR / 'scripts' / 'doctor' / 'check_deployment_image_readiness.sh').read_text(encoding='utf-8'),
            'control_plane_medium': (ROOT_DIR / 'scripts' / 'images' / 'ensure_control_plane_image.sh').read_text(encoding='utf-8'),
        }

        for name, source in sources.items():
            with self.subTest(source=name):
                self.assertIn('check_docker_host_readiness.sh', source)
                self.assertIn('--network-profile cn', source)
                self.assertIn('deployment_images_*.tar', source)
                self.assertIn('load_deployment_images.sh', source)

    def test_docker_host_readiness_failure_guidance_links_network_profile_candidate_and_offline_archive(self) -> None:
        """宿主机 readiness 的镜像来源失败提示必须指向 source selection 与离线归档。"""
        source = (ROOT_DIR / 'scripts' / 'doctor' / 'check_docker_host_readiness.sh').read_text(encoding='utf-8')

        self.assertIn('--network-profile cn', source)
        self.assertTrue(
            '--candidate-repo ghcr.nju.edu.cn/openclaw/openclaw --mode candidate' in source
            or 'PULL_GATEWAY_CANDIDATE_MODE=auto-switch' in source
        )
        self.assertIn('selected/candidate 都不可达时使用离线镜像归档', source)
        self.assertIn('受限网络执行 load_deployment_images.sh', source)
        self.assertIn('本脚本不准备 host 控制面执行介质', source)
        self.assertIn('prepare_control_plane_medium.sh', source)
        assert_static_text_absent(self, 'verify_python_runtime_container.sh" --static-only', source)

    def test_first_install_recovery_entrypoints_are_visible(self) -> None:
        """远程首装、客户端访问与扩展 env 首装入口必须提供明确 CLI。"""
        remote_source = (ROOT_DIR / 'scripts' / 'setup' / 'remote_first_install.sh').read_text(encoding='utf-8')
        client_source = (ROOT_DIR / 'scripts' / 'setup' / 'check_client_access_acceptance.sh').read_text(encoding='utf-8')
        apply_ext_source = (ROOT_DIR / 'scripts' / 'setup' / 'apply_extension_env_values.sh').read_text(encoding='utf-8')
        check_ext_source = (ROOT_DIR / 'scripts' / 'setup' / 'check_extension_env_values.sh').read_text(encoding='utf-8')
        config_source = (ROOT_DIR / 'scripts' / 'setup' / 'one_click_config.sh').read_text(encoding='utf-8')
        cidr_contract_source = (ROOT_DIR / 'scripts' / 'lib' / 'cidr_contract.sh').read_text(encoding='utf-8')

        self.assertIn('--preflight', remote_source)
        self.assertIn('--stage-bundle', remote_source)
        self.assertIn('--prepare-repo', remote_source)
        self.assertIn('--configure-base', remote_source)
        self.assertIn('--deploy', remote_source)
        self.assertIn('显式 --apply', remote_source)
        self.assertIn('init_state()', remote_source)
        self.assertIn('dry-run 不生成本地 bundle', remote_source)
        self.assertIn('validate_inputs', remote_source)
        self.assertIn('scripts/lib/cidr_contract.sh', remote_source)
        self.assertIn('openclaw_cidr_validate_list', remote_source)
        self.assertIn('权限修复、basic gate', remote_source)
        self.assertIn('--client-cidr <cidr[,cidr]>', remote_source)
        self.assertIn('--ssh-port <port>', remote_source)
        self.assertIn('validate_ssh_port', remote_source)
        self.assertIn('--ssh-port $(shell_quote "$SSH_PORT")', remote_source)
        self.assertIn('-p "$SSH_PORT"', remote_source)
        self.assertIn('-P "$SSH_PORT"', remote_source)
        self.assertIn('10.0.0.0/8,192.168.50.0/24', cidr_contract_source)
        self.assertIn('IPv4 前缀长度不能超过 32', cidr_contract_source)
        self.assertIn('IPv6 前缀长度不能超过 128', cidr_contract_source)
        self.assertIn('_openclaw_cidr_ipv6_address_syntax_is_valid', cidr_contract_source)
        self.assertIn('IPv6 地址格式无效', cidr_contract_source)
        self.assertIn('只允许私网或 loopback CIDR', cidr_contract_source)
        self.assertIn('IPv4 地址越界', cidr_contract_source)
        self.assertIn('resume_command()', remote_source)
        self.assertIn('RESUME_COMMAND', remote_source)
        self.assertIn('80/443 端口已被占用', remote_source)
        self.assertIn('repo_dir 已存在', remote_source)
        self.assertIn('已存在 OpenClaw 容器', remote_source)
        self.assertIn("deploy/.env", remote_source)
        self.assertIn('agent/extensions/*/deploy/extension.env', remote_source)
        deploy_body = remote_source.split('run_deploy()', 1)[1].split('while [[ $# -gt 0 ]]', 1)[0]
        self.assertLess(
            deploy_body.find('sudo bash ./scripts/setup/apply_ingress_boundary_rules.sh --env-file deploy/.env'),
            deploy_body.find('sudo bash ./scripts/setup/fix_permissions.sh'),
        )
        self.assertLess(
            deploy_body.find('sudo bash ./scripts/setup/fix_permissions.sh'),
            deploy_body.find('sudo -u "$DEPLOY_USER" bash ./scripts/setup/one_click_test_basic.sh'),
        )
        self.assertIn('client_access_acceptance', client_source)
        self.assertIn('deployment_acceptance=target_local_acceptance', client_source)
        self.assertIn('不是私网或 loopback', client_source)
        self.assertIn('scripts/lib/cidr_contract.sh', client_source)
        self.assertIn('openclaw_cidr_first_not_allowed', client_source)
        self.assertIn('openclaw_cidr_list_count', client_source)
        self.assertIn('openclaw_cidr_contains', cidr_contract_source)
        self.assertIn('_openclaw_cidr_contains_ipv4', cidr_contract_source)
        self.assertIn('--client-cidr <cidr[,cidr]>', client_source)
        self.assertIn('clientCidrs', client_source)
        self.assertIn('不能包含空项', cidr_contract_source)
        self.assertIn('过宽 CIDR', client_source)
        self.assertIn('--cacert', client_source)
        assert_static_text_absent(self, 'curl -k', client_source)
        assert_static_text_absent(self, 'curl --insecure', client_source)
        platform_docstring_source = (ROOT_DIR / 'scripts' / 'doctor' / 'check_platform_docstring_governance.sh').read_text(encoding='utf-8')
        self.assertIn('scripts/lib/run_static_python.sh', platform_docstring_source)
        self.assertIn('OPENCLAW_STATIC_PYTHON_READINESS_LABEL', platform_docstring_source)
        assert_static_text_absent(self, 'OPENCLAW_PLATFORM_DOCSTRING_GOVERNANCE_PYTHON', platform_docstring_source)
        assert_static_text_absent(self, 'exec "$PYTHON_BIN"', platform_docstring_source)
        self.assertIn('--set-secret-from-env', apply_ext_source)
        self.assertIn('validate_env_value', apply_ext_source)
        self.assertIn('换行、回车或制表符', apply_ext_source)
        self.assertIn('<redacted>', apply_ext_source)
        self.assertIn('chmod 600 "$EXTENSION_FILE"', apply_ext_source)
        self.assertIn('*.deploy_env_schema.json', check_ext_source)
        self.assertIn('fixCommand', check_ext_source)
        self.assertIn('check_extension_env_values.sh --profile', config_source)

    def test_shell_entrypoints_reject_missing_option_values_with_readable_errors(self) -> None:
        cases = (
            ('scripts/setup/one_click_test_full.sh', '--env-file', '缺少路径参数'),
            ('scripts/setup/one_click_test_full.sh', '--group', '缺少检查组名称'),
            ('scripts/setup/one_click_test_full.sh', '--only', '缺少检查项列表'),
            ('scripts/setup/one_click_test_full.sh', '--skip', '缺少检查项列表'),
            ('scripts/runtime/show_runtime_compose_config.sh', '--compose-file', '缺少路径参数'),
            ('scripts/runtime/show_runtime_compose_config.sh', '--env-file', '缺少路径参数'),
            ('scripts/setup/render_local_ro_mirror.sh', '--manifest', '缺少路径参数'),
            ('scripts/setup/render_local_ro_mirror.sh', '--output-dir', '缺少路径参数'),
            ('scripts/setup/render_local_ro_mirror.sh', '--label', '缺少名称参数'),
            ('scripts/setup/render_local_ro_mirror.sh', '--config-path', '缺少路径参数'),
        )
        for script_rel, flag, expected in cases:
            with self.subTest(script=script_rel, flag=flag, surface='source'):
                source = (ROOT_DIR / script_rel).read_text(encoding='utf-8')
                self.assertTrue(
                    any(flag in line and expected in line for line in source.splitlines()),
                    msg=f'{script_rel} does not guard {flag} with {expected}',
                )
        runtime_cases = (
            ('scripts/setup/one_click_test_full.sh', '--env-file', '缺少路径参数'),
        )
        for script_rel, flag, expected in runtime_cases:
            with self.subTest(script=script_rel, flag=flag, surface='runtime'):
                result = self._run_entrypoint(script_rel, flag)
                self.assert_readable_cli_error(result, expected)

    def test_image_archive_requires_offline_mode_on_one_click_entries(self) -> None:
        cases = (
            'scripts/setup/one_click_test_basic.sh',
            'scripts/setup/one_click_deploy.sh',
        )
        for script_rel in cases:
            with self.subTest(script=script_rel, surface='source'):
                source = (ROOT_DIR / script_rel).read_text(encoding='utf-8')
                self.assertIn('prevalidate_image_archive_args "$@"', source)
                self.assertIn('--image-archive 缺少路径参数', source)
                self.assertIn('--image-archive 仅在 --offline 下有效', source)

        result = self._run_entrypoint(
            'scripts/setup/one_click_test_basic.sh',
            '--image-archive',
            'deployment_images_fixture.tar',
        )
        self.assert_readable_cli_error(result, '仅在 --offline 下有效')

    def test_scheduler_dispatch_target_shortcut_accepts_empty_passthrough_args(self) -> None:
        script = '\n'.join(
            [
                'set -euo pipefail',
                'function_file="$(mktemp)"',
                "sed -n '/^openclaw_scheduler_run_target_operation()/,/^}/p' scripts/lib/control_plane_scheduler_exec.sh > \"$function_file\"",
                'source "$function_file"',
                'rm -f "$function_file"',
                'OPENCLAW_CONTROL_PLANE_SCHEDULER_EXEC_ROOT="$(pwd -P)"',
                'openclaw_scheduler_exec_fail() { echo "[$1][FAIL] $2" >&2; exit "${3:-2}"; }',
                'openclaw_scheduler_apply_control_plane_selection_from_env_file() { :; }',
                'runtime_target_service_name_for_target() { printf "%s\\n" openclaw-control-plane-scheduler; }',
                'openclaw_scheduler_resolve_container_control_plane_config_path() { printf "%s\\n" /opt/openclaw-tools/config/control_plane/profile-under-test.service.json; }',
                'openclaw_scheduler_prepare_service_exec() { :; }',
                'runtime_compose_exec_service() { printf "%s\\n" "$@"; }',
                'openclaw_scheduler_run_target_operation --root-dir . --compose-file deploy/docker-compose.yml --env-file deploy/.env --ensure-running strict --control-plane-profile profile_under_test --target target_under_test --operation preflight',
            ]
        )
        env = dict(os.environ)
        env['PYTHONIOENCODING'] = 'utf-8'
        result = subprocess.run(
            [str(self.bash), '-lc', script],
            cwd=ROOT_DIR,
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            env=env,
            check=False,
        )
        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, msg=output)
        self.assertNotIn('unbound variable', output)
        self.assertIn('--dispatch-target-id', result.stdout)
        self.assertIn('--target', result.stdout)
        self.assertIn('target_under_test', result.stdout)


if __name__ == '__main__':
    unittest.main()
