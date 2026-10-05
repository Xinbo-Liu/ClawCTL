from __future__ import annotations

import json
from pathlib import Path
import os
import shutil
import shlex
import subprocess
import sys
import tempfile
import unittest

from openclaw.doctor.agent_modules.support import resolve_bash_executable
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.tests.support.managed_extensions import managed_extensions

ROOT_DIR = resolve_repo_root(Path(__file__))
MANAGED_EXTENSIONS = tuple(sorted(managed_extensions(ROOT_DIR), key=lambda row: row.id))
MANAGED_EXTENSION = MANAGED_EXTENSIONS[0] if MANAGED_EXTENSIONS else None

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
            if os.name == 'nt':
                self.skipTest('Windows 解压目录缺少 Git index 时无法可靠表达 POSIX executable bit')
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
        untracked_result = subprocess.run(
            ['git', 'ls-files', '--others', '--exclude-standard', '--', *sorted(required)],
            cwd=ROOT_DIR,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        untracked = {line.strip() for line in untracked_result.stdout.splitlines() if line.strip()}
        modes = {
            line.split(maxsplit=1)[1].rsplit('\t', 1)[1]: line.split(maxsplit=1)[0]
            for line in result.stdout.splitlines()
            if line.strip()
        }
        self.assertEqual(set(modes), required - untracked)
        self.assertEqual({path: mode for path, mode in modes.items() if mode != '100755'}, {})
        for rel_path in sorted(untracked):
            with self.subTest(untracked_script=rel_path):
                first_line = (ROOT_DIR / rel_path).read_bytes().split(b'\n', 1)[0]
                self.assertEqual(first_line, b'#!/usr/bin/env bash')

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

    def test_repo_path_compare_helper_normalizes_repo_relative_env(self) -> None:
        bash = resolve_bash_executable()
        lib_path = shlex.quote((ROOT_DIR / 'scripts' / 'lib' / 'repo_root.sh').as_posix())
        root_path = shlex.quote(ROOT_DIR.as_posix())
        script = f'''
set -euo pipefail
source {lib_path}
root={root_path}
left="$(openclaw_repo_abs_path_for_compare "$root" deploy/.env)"
right="$(openclaw_repo_abs_path_for_compare "$root" "$root/deploy/.env")"
dot="$(openclaw_repo_abs_path_for_compare "$root" ./deploy/.env)"
parent="$(openclaw_repo_abs_path_for_compare "$root" deploy/../deploy/.env)"
other="$(openclaw_repo_abs_path_for_compare "$root" deploy/site.env)"
[[ "$left" == "$right" ]]
[[ "$left" == "$dot" ]]
[[ "$left" == "$parent" ]]
[[ "$left" != "$other" ]]
[[ "$left" == */deploy/.env ]]
'''
        result = subprocess.run(
            [bash, '-lc', script],
            cwd=ROOT_DIR,
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

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
                '--observed-source-cidr',
                '10.1.2.0/24,fd12:3456:789a::/48',
                '--tls-cn',
                'openclaw.internal.example',
            )
            invalid = self._run_entrypoint(
                'scripts/setup/check_client_access_acceptance.sh',
                '--env-file',
                str(env_path),
                '--observed-source-cidr',
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
openclaw_cidr_validate_list '10.0.0.0/8,fd12:3456::/32,8.8.8.8/32' '--observed-source-cidr'
! openclaw_cidr_validate_list '10.0.0.0/7' '--observed-source-cidr' >/dev/null 2>&1
! openclaw_cidr_validate_list '8.8.8.0/24' '--observed-source-cidr' >/dev/null 2>&1
! openclaw_cidr_validate_list 'fd:::1/8' '--observed-source-cidr' >/dev/null 2>&1
[[ -z "$(openclaw_cidr_first_not_allowed '10.0.0.0/8,fd00::/8' '10.1.2.0/24,fd00:1234::/32')" ]]
[[ "$(openclaw_cidr_first_not_allowed '10.0.0.0/8' '10.1.2.0/24,192.168.50.0/24')" == '192.168.50.0/24' ]]
[[ -z "$(openclaw_cidr_first_not_allowed_ingress_source '10.0.0.0/8,8.8.8.8/32')" ]]
[[ "$(openclaw_cidr_first_not_allowed_ingress_source '8.8.8.0/24')" == '8.8.8.0/24' ]]
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
            '--observed-source-cidr',
            '10.0.0.0/8,192.168.50.0/24',
            '--ssh-port',
            '24110',
        )

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['kind'], 'openclaw_remote_first_install_plan')
        self.assertEqual(payload['observedSourceCidrs'], ['10.0.0.0/8', '192.168.50.0/24'])
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

    def test_remote_first_install_plan_json_includes_configure_inputs_stage(self) -> None:
        result = self._run_entrypoint(
            'scripts/setup/remote_first_install.sh',
            '--plan-json',
            '--host',
            'demo@example',
            '--configure-base',
            '--configure-inputs',
            '--remote-deploy-input-env-file',
            '/opt/openclaw/secrets/deploy-input.env',
            '--deploy',
        )

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload['remoteDeployInputEnvFileProvided'])
        self.assertEqual(payload['selectedStages'], ['configure_base', 'configure_inputs', 'deploy'])
        stage_ids = [stage['id'] for stage in payload['stages']]
        self.assertEqual(stage_ids, ['configure_base', 'configure_inputs', 'deploy'])
        configure_inputs = payload['stages'][1]
        self.assertIn('--remote-deploy-input-env-file', configure_inputs['inputs'])
        self.assertIn('agent/extensions/<id>/deploy/extension.env', configure_inputs['outputs'])

    def test_remote_first_install_plan_json_validates_cidr_before_output(self) -> None:
        state_root = ROOT_DIR / 'state' / 'remote_first_install'
        before = {item.name for item in state_root.iterdir()} if state_root.exists() else set()

        result = self._run_entrypoint(
            'scripts/setup/remote_first_install.sh',
            '--plan-json',
            '--host',
            'demo@example',
            '--deploy',
            '--observed-source-cidr',
            '8.8.8.0/24',
        )

        after = {item.name for item in state_root.iterdir()} if state_root.exists() else set()
        self.assertEqual(result.returncode, 2, msg=result.stdout + result.stderr)
        self.assertIn('只允许私网、loopback 或精确公网主机 CIDR', result.stdout + result.stderr)
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


    def test_openclaw_pin_candidate_next_steps_recheck_digest_first(self) -> None:
        payload = json.loads((ROOT_DIR / 'config' / 'governance' / 'docs' / 'image_governance_surface.json').read_text(encoding='utf-8'))
        next_steps = payload['surfaces']['openclaw_pin_candidate']['next_steps']

        self.assertEqual(next_steps[0], 'bash ./scripts/images/check_openclaw_digest.sh')
        self.assertIn('bash ./scripts/images/pull_images.sh', next_steps)

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
            with self.subTest(script=script_rel, flag=flag):
                result = self._run_entrypoint(script_rel, flag)
                self.assert_readable_cli_error(result, expected)

    def test_image_archive_requires_offline_mode_on_one_click_entries(self) -> None:
        cases = (
            'scripts/setup/one_click_test_basic.sh',
            'scripts/setup/one_click_deploy.sh',
        )
        for script_rel in cases:
            with self.subTest(script=script_rel):
                result = self._run_entrypoint(
                    script_rel,
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
                'OPENCLAW_CONTROL_PLANE_SCHEDULER_RUN_ID="target-operation-proof"',
                'OPENCLAW_CONTROL_PLANE_RUN_ID="target-operation-proof"',
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
        self.assertIn('OPENCLAW_CONTROL_PLANE_SCHEDULER_RUN_ID=target-operation-proof', result.stdout)
        self.assertIn('OPENCLAW_CONTROL_PLANE_RUN_ID=target-operation-proof', result.stdout)

    def test_scheduler_exec_defaults_to_runtime_effective_compose(self) -> None:
        script = '\n'.join(
            [
                'set -euo pipefail',
                'function_file="$(mktemp)"',
                "sed -n '/^openclaw_scheduler_run_agent_runtime()/,/^}/p' scripts/lib/control_plane_scheduler_exec.sh > \"$function_file\"",
                'source "$function_file"',
                'rm -f "$function_file"',
                'OPENCLAW_CONTROL_PLANE_SCHEDULER_EXEC_ROOT="$(pwd -P)"',
                'openclaw_scheduler_exec_fail() { echo "[$1][FAIL] $2" >&2; exit "${3:-2}"; }',
                'openclaw_scheduler_apply_control_plane_selection_from_env_file() { :; }',
                'runtime_compose_default_file() { printf "%s\\n" "$1/state/openclaw/control_plane/setup/docker-compose.effective.yml"; }',
                'runtime_target_service_name_for_target() { printf "%s\\n" openclaw-control-plane-scheduler; }',
                'openclaw_scheduler_resolve_container_control_plane_config_path() { printf "%s\\n" /opt/openclaw-tools/config/control_plane/profile-under-test.service.json; }',
                'openclaw_scheduler_prepare_service_exec() { :; }',
                'runtime_compose_exec_service() { printf "ENV=%s\\nCOMPOSE=%s\\nSERVICE=%s\\n" "$1" "$2" "$3"; }',
                'openclaw_scheduler_run_agent_runtime --root-dir . --env-file deploy/.env --ensure-running strict --control-plane-profile profile_under_test --agent-ref demo',
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

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn('COMPOSE=', result.stdout)
        self.assertIn('docker-compose.effective.yml', result.stdout)


if __name__ == '__main__':
    unittest.main()
