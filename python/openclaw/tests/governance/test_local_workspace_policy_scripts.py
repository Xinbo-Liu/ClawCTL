from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from uuid import uuid4

from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.tests.support.helpers import isolated_test_root
from openclaw.tests.support.lightweight_repo import materialize_local_workspace_shell_repo
from openclaw.tests.support.static_text_assertions import assert_static_text_absent

ROOT_DIR = resolve_repo_root(Path(__file__))
CHECK_HYGIENE_SCRIPT = ROOT_DIR / 'scripts' / 'doctor' / 'check_local_workspace_hygiene.sh'
CLEANUP_SCRIPT = ROOT_DIR / 'scripts' / 'setup' / 'cleanup_local_workspace.sh'
EXPORT_SCRIPT = ROOT_DIR / 'scripts' / 'setup' / 'export_clean_delivery_bundle.sh'
BASH_CANDIDATES = (
    Path(os.environ.get('CODEX_GIT_BASH', '')).resolve() if os.environ.get('CODEX_GIT_BASH') else None,
    Path('/usr/bin/bash'),
    Path('/bin/bash'),
    Path(r'C:\Download\Git\bin\bash.exe'),
    Path(r'C:\Program Files\Git\bin\bash.exe'),
    Path(r'C:\Program Files\Git\usr\bin\bash.exe'),
)

def script_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop('HOST_STATE_DIR', None)
    env.pop('OPENCLAW_STATE_DIR', None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PYTHON_BIN'] = sys.executable
    env['PYTHONIOENCODING'] = 'utf-8'
    env['LANG'] = 'C.UTF-8'
    env['LC_ALL'] = 'C.UTF-8'
    return env


def first_existing_path(candidates: tuple[Path | None, ...]) -> Path:
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    raise FileNotFoundError(candidates)

def run_script(repo_root: Path, rel_path: str, *args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    bash_exe = first_existing_path(BASH_CANDIDATES)
    script_path = (repo_root / rel_path.removeprefix('./')).resolve()
    env = script_env()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(bash_exe), str(script_path), *args],
        cwd=repo_root.resolve(),
        text=True,
        encoding='utf-8',
        errors='replace',
        capture_output=True,
        env=env,
        check=False,
    )


def run_bash_command(repo_root: Path, command: str) -> subprocess.CompletedProcess[str]:
    bash_exe = first_existing_path(BASH_CANDIDATES)
    return subprocess.run(
        [str(bash_exe), '-c', command],
        cwd=repo_root.resolve(),
        text=True,
        encoding='utf-8',
        errors='replace',
        capture_output=True,
        env=script_env(),
        check=False,
    )


class LocalWorkspacePolicyScriptsTest(unittest.TestCase):
    _shared_root_cm: object | None = None
    _shared_root: Path | None = None
    _repo_template_plain: Path | None = None
    _repo_template_seeded: Path | None = None

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._shared_root_cm = isolated_test_root(f'local-workspace-policy-scripts-shared-{uuid4().hex}')
        cls._shared_root = cls._shared_root_cm.__enter__()
        cls._repo_template_plain = cls._shared_root / 'repo_template_plain'
        materialize_local_workspace_shell_repo(cls._repo_template_plain)
        cls._repo_template_seeded = None

    @classmethod
    def tearDownClass(cls) -> None:
        cm = cls._shared_root_cm
        cls._shared_root_cm = None
        cls._shared_root = None
        cls._repo_template_plain = None
        cls._repo_template_seeded = None
        if cm is not None:
            cm.__exit__(None, None, None)
        super().tearDownClass()

    @classmethod
    def _clone_template_repo(cls, prefix: str, *, seeded: bool = False) -> Path:
        shared_root = cls._shared_root
        if shared_root is None:
            raise AssertionError('shared test root is not initialized')
        target = shared_root / f'{prefix}_{uuid4().hex}'
        if not seeded:
            source = cls._repo_template_plain
            if source is None:
                source = shared_root / 'repo_template_plain'
                materialize_local_workspace_shell_repo(source)
                cls._repo_template_plain = source
            shutil.copytree(source, target)
            return target

        source = cls._repo_template_seeded
        if source is None:
            source = shared_root / 'repo_template_seeded'
            materialize_local_workspace_shell_repo(source)
            seeded_result = run_script(source, './scripts/setup/fix_permissions.sh')
            if seeded_result.returncode != 0:
                raise AssertionError(seeded_result.stderr or seeded_result.stdout)
            cls._repo_template_seeded = source
        shutil.copytree(source, target)
        return target

    def _prepare_disposable_export_repo(self, prefix: str) -> tuple[Path, Path, Path, Path]:
        repo_copy = self._clone_template_repo(prefix)
        preserved = repo_copy / 'state' / 'openclaw'
        disposable = repo_copy / 'state' / 'image_pull'
        cached_dir = repo_copy / 'python' / 'openclaw' / '__pycache__'
        preserved.mkdir(parents=True, exist_ok=True)
        disposable.mkdir(parents=True, exist_ok=True)
        cached_dir.mkdir(parents=True, exist_ok=True)
        (cached_dir / 'module.pyc').write_bytes(b'pyc')
        return repo_copy, preserved, disposable, cached_dir

    def test_hygiene_policy_truth_declares_expected_targets_and_ignores(self) -> None:
        policy = json.loads((ROOT_DIR / 'config' / 'governance' / 'support' / 'local_workspace_policy.json').read_text(encoding='utf-8'))
        targets = {row['path']: row for row in policy['targets'] if 'path' in row}

        self.assertEqual(targets['state/image_artifacts']['class'], 'managed_input_cache')
        state_root = next(row for row in policy['targets'] if row.get('truthRef') == 'host_state_root')
        self.assertEqual(state_root['class'], 'managed_runtime_state')
        self.assertIn('**/__pycache__/**', policy['derivedGlobs'])

    def test_cleanup_script_zero_arg_skips_preserved_targets(self) -> None:
        policy = json.loads((ROOT_DIR / 'config' / 'governance' / 'support' / 'local_workspace_policy.json').read_text(encoding='utf-8'))
        cleanup_defaults = {
            str(row.get('path') or row.get('truthRef') or '')
            for row in policy['targets']
            if row.get('cleanupByDefault') is True
        }

        self.assertIn('state/image_pull', cleanup_defaults)
        self.assertIn('state/remote_first_install', cleanup_defaults)
        self.assertIn('release/history', cleanup_defaults)
        self.assertNotIn('host_state_root', cleanup_defaults)

    def test_cleanup_script_rejects_state_target(self) -> None:
        source = CLEANUP_SCRIPT.read_text(encoding='utf-8')
        self.assertIn('粗粒度目标 state 不支持', source)
        self.assertIn('state/openclaw', source)

    def test_scripts_use_policy_targets_and_reject_whole_state_dirty_roots(self) -> None:
        assert_static_text_absent(self, 'HYGIENE_TARGETS=', CHECK_HYGIENE_SCRIPT.read_text(encoding='utf-8'))
        assert_static_text_absent(self, 'WHITELIST_TARGETS=', CLEANUP_SCRIPT.read_text(encoding='utf-8'))
        export_text = EXPORT_SCRIPT.read_text(encoding='utf-8')
        assert_static_text_absent(self, '"$ROOT_DIR/state"', export_text)
        assert_static_text_absent(self, '"$ROOT_DIR/release/evidence"', export_text)
        self.assertIn('openclaw_local_workspace_policy_disposable_paths', export_text)
        policy_shell = (ROOT_DIR / 'scripts' / 'lib' / 'local_workspace_policy.sh').read_text(encoding='utf-8')
        assert_static_text_absent(self, 'openclaw_repo_default_python_bin', policy_shell)
        assert_static_text_absent(self, 'openclaw.lib.repo.local_workspace_policy', policy_shell)

    def test_local_workspace_policy_shell_awk_fallback_is_self_contained(self) -> None:
        source = (ROOT_DIR / 'scripts' / 'lib' / 'local_workspace_policy.sh').read_text(encoding='utf-8')

        self.assertIn('OPENCLAW_LOCAL_WORKSPACE_POLICY_FORCE_AWK', source)
        self.assertIn('openclaw_local_workspace_policy_json_string_awk()', source)
        self.assertIn('openclaw_local_workspace_policy_target_records_awk()', source)
        self.assertIn('openclaw_local_workspace_policy_derived_globs_awk()', source)
        assert_static_text_absent(self, 'openclaw.lib.repo.local_workspace_policy', source)

    def test_workspace_shell_permission_contract_is_source_level(self) -> None:
        fix_permissions_source = (ROOT_DIR / 'scripts' / 'setup' / 'fix_permissions.sh').read_text(encoding='utf-8')
        runtime_permissions_source = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'runtime_permissions.sh').read_text(encoding='utf-8')

        expected_order = [
            'runtime_permissions_prepare_repo_support_dirs "$ROOT_DIR"',
            'runtime_permissions_prepare_openclaw_state_layout "$ROOT_DIR"',
            'runtime_permissions_prepare_image_state_layout "$ROOT_DIR"',
            'runtime_permissions_prepare_release_evidence_layout "$ROOT_DIR"',
            'runtime_permissions_mark_shebang_executable "$ROOT_DIR"',
            'runtime_permissions_harden_certs "$ROOT_DIR"',
            'runtime_permissions_prepare_runtime_bind_mount_selinux_contexts "$ROOT_DIR"',
        ]
        cursor = -1
        for marker in expected_order:
            next_cursor = fix_permissions_source.find(marker)
            self.assertGreater(next_cursor, cursor, msg=marker)
            cursor = next_cursor

        self.assertIn('"$root/agent/extensions"', runtime_permissions_source)
        self.assertIn("-path '*/agent/modules/*/bin/*'", runtime_permissions_source)
        self.assertIn("-path '*/scripts/*.sh'", runtime_permissions_source)
        self.assertIn('chmod 755 "${batch[@]}"', runtime_permissions_source)
        self.assertIn('runtime_permissions_prepare_ingress_nginx_conf_acl "$root"', runtime_permissions_source)
        ingress_log_acl_block = runtime_permissions_source.split(
            'runtime_permissions_prepare_ingress_log_acl() {',
            1,
        )[1].split('runtime_permissions_prepare_ingress_cert_acl() {', 1)[0]
        expected_ingress_log_order = [
            'runtime_permissions_touch_chmod 600 "$nginx_log_dir/access.log"',
            'runtime_permissions_touch_chmod 600 "$nginx_log_dir/error.log"',
            'runtime_permissions_align_openclaw_runtime_owner_only "$root" "$nginx_log_dir" || return $?',
            'runtime_permissions_apply_acl u:0:rwx "$nginx_log_dir" || return $?',
        ]
        cursor = -1
        for marker in expected_ingress_log_order:
            next_cursor = ingress_log_acl_block.find(marker)
            self.assertGreater(next_cursor, cursor, msg=marker)
            cursor = next_cursor
        self.assertIn('runtime_permissions_prepare_runtime_bind_mount_selinux_contexts()', runtime_permissions_source)
        self.assertIn('"$state_root/gateway"', runtime_permissions_source)
        self.assertIn('"$root/python"', runtime_permissions_source)
        self.assertIn('chcon -Rt svirt_sandbox_file_t "$path"', runtime_permissions_source)
        self.assertIn('chcon -t svirt_sandbox_file_t "$path"', runtime_permissions_source)

    def test_export_cleanliness_only_passes_with_preserved_targets_only(self) -> None:
        repo_copy = self._clone_template_repo('export-clean-preserved-only')
        (repo_copy / 'state' / 'openclaw').mkdir(parents=True, exist_ok=True)
        (repo_copy / 'state' / 'image_artifacts').mkdir(parents=True, exist_ok=True)

        result = run_script(repo_copy, './scripts/setup/export_clean_delivery_bundle.sh', '--cleanliness-only')
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_export_cleanliness_only_fails_for_disposable_targets(self) -> None:
        repo_copy, _preserved, _disposable, _cached_dir = self._prepare_disposable_export_repo('export-clean-disposable-fail')
        failed = run_script(repo_copy, './scripts/setup/export_clean_delivery_bundle.sh', '--cleanliness-only')
        self.assertEqual(failed.returncode, 2)
        self.assertIn('state/image_pull', failed.stdout + failed.stderr)

    def test_export_cleanliness_only_clean_preserves_kept_dirs(self) -> None:
        repo_copy, preserved, disposable, cached_dir = self._prepare_disposable_export_repo('export-clean-disposable-clean')
        cleaned = run_script(
            repo_copy,
            './scripts/setup/export_clean_delivery_bundle.sh',
            '--cleanliness-only',
            '--clean',
            '--quiet',
        )
        self.assertEqual(cleaned.returncode, 0, msg=cleaned.stderr)
        self.assertTrue(preserved.exists())
        self.assertFalse(disposable.exists())
        self.assertFalse(cached_dir.exists())

    def test_runtime_permissions_collects_managed_launcher_exec_candidates(self) -> None:
        source = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'runtime_permissions.sh').read_text(encoding='utf-8')
        self.assertIn("$root/agent/extensions", source)
        self.assertIn("*/agent/modules/*/bin/*", source)
        self.assertIn("*/scripts/*.sh", source)

    def test_local_runtime_fs_contract_does_not_seed_image_pull_residue(self) -> None:
        repo_copy = self._clone_template_repo('fs-contract-no-image-pull')
        contracts_path = repo_copy / 'config' / 'governance' / 'support' / 'repo_contracts.json'
        contracts = json.loads(contracts_path.read_text(encoding='utf-8'))
        contracts['contracts'].append(
            {
                'id': 'runtime.paths',
                'relative_path': 'config/runtime/paths.json',
                'format': 'json',
            }
        )
        contracts['contracts'].extend(
            [
                {
                    'id': 'image_pins.openclaw',
                    'relative_path': 'config/image_pins/openclaw.env',
                    'format': 'env',
                },
                {
                    'id': 'image_pins.runtime',
                    'relative_path': 'config/image_pins/runtime.env',
                    'format': 'env',
                },
            ]
        )
        contracts_path.write_text(json.dumps(contracts, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        (repo_copy / 'config' / 'runtime' / 'paths.json').write_text('{"entries": {}}\n', encoding='utf-8')
        for path in (repo_copy / 'scripts').rglob('*.sh'):
            path.chmod(0o755)
        (repo_copy / 'agent' / 'extensions' / 'agent_probe' / 'agent' / 'modules' / 'probe_worker' / 'bin' / 'probe_worker').chmod(0o755)
        for path in (
            repo_copy / 'state' / 'openclaw' / 'tmp',
            repo_copy / 'state' / 'openclaw' / 'setup',
            repo_copy / 'state' / 'openclaw' / 'control_plane' / 'dispatch',
            repo_copy / 'state' / 'openclaw' / 'control_plane' / 'setup' / 'official_cli',
            repo_copy / 'state' / 'openclaw' / 'control_plane' / 'release' / 'evidence',
            repo_copy / 'state' / 'image_artifacts',
            repo_copy / 'release',
            repo_copy / 'deploy' / 'nginx' / 'certs',
        ):
            path.mkdir(parents=True, exist_ok=True)
        image_pull_dir = repo_copy / 'state' / 'image_pull'
        shutil.rmtree(image_pull_dir, ignore_errors=True)

        result = run_script(repo_copy, './scripts/doctor/check_local_runtime_fs_contract.sh')

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertFalse(image_pull_dir.exists())

    def test_image_scripts_create_disposable_state_only_at_runtime(self) -> None:
        pull_source = (ROOT_DIR / 'scripts' / 'images' / 'pull_images.sh').read_text(encoding='utf-8')
        cleanup_source = (ROOT_DIR / 'scripts' / 'images' / 'cleanup_image_aliases.sh').read_text(encoding='utf-8')

        self.assertLess(
            pull_source.index('mkdir -p "$(dirname "$PULL_STATE_FILE")"'),
            pull_source.index('require_file_manageable_or_creatable "$PULL_STATE_FILE"'),
        )
        self.assertLess(
            cleanup_source.index('mkdir -p "$(dirname "$CLEANUP_LOG")"'),
            cleanup_source.index('require_file_manageable_or_creatable "$CLEANUP_LOG"'),
        )

    def test_extension_env_entrypoint_is_ensure_only_and_verifies_lifecycle_lock_first(self) -> None:
        gate_source = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'extension_env_gate.sh').read_text(encoding='utf-8')
        registration_source = (ROOT_DIR / 'python' / 'openclaw' / 'control_plane' / 'cli_support' / 'registration' / 'runtime_execution.py').read_text(encoding='utf-8')

        self.assertIn('extension_env_gate_verify_lifecycle_lock()', gate_source)
        self.assertIn('control-plane extensions doctor', gate_source)
        self.assertIn('control-plane extensions lock', gate_source)
        self.assertIn('extension-env ensure --enabled --offline --json', gate_source)
        self.assertIn('source "$EXTENSION_ENV_GATE_REPO_ROOT/scripts/lib/control_plane_config_paths.sh"', gate_source)
        self.assertIn('openclaw_control_plane_normalize_host_config_path "$config_path"', gate_source)
        self.assertIn('OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH 无法解析为 host 配置路径', gate_source)
        assert_static_text_absent(self, '[[ -n "$config_path" && -f "$config_path" ]] || return 0', gate_source)
        ensure_body = gate_source.split('extension_env_gate_ensure_active_profile() {', 1)[1]
        self.assertLess(
            ensure_body.index('extension_env_gate_verify_lifecycle_lock "$root_dir" "$label"'),
            ensure_body.index('extension-env ensure --enabled --offline --json'),
        )
        self.assertFalse((ROOT_DIR / 'scripts' / 'setup' / 'sync_extension_wheelhouse.sh').exists())
        self.assertFalse((ROOT_DIR / 'scripts' / 'setup' / 'prepare_extension_envs.sh').exists())
        self.assertIn("add_parser('ensure'", registration_source)
        assert_static_text_absent(self, "add_parser('prepare'", registration_source)
        assert_static_text_absent(self, "add_parser('sync-wheelhouse'", registration_source)

    def test_runtime_permissions_collects_extension_script_exec_candidates(self) -> None:
        extension_scripts = sorted((ROOT_DIR / 'agent' / 'extensions').glob('*/scripts/**/*.sh'))
        if not extension_scripts:
            self.skipTest('base release surface has no repo-managed extension scripts')

        result = run_bash_command(
            ROOT_DIR,
            'source scripts/setup/lib/runtime_permissions.sh; '
            'runtime_permissions_collect_repo_exec_candidates "$PWD" | tr "\\0" "\\n"',
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        candidates = {line.strip().replace('\\', '/') for line in result.stdout.splitlines() if line.strip()}
        for script_path in extension_scripts:
            rel_path = script_path.relative_to(ROOT_DIR).as_posix()
            with self.subTest(script=rel_path):
                self.assertTrue(any(item.endswith(rel_path) for item in candidates), msg=sorted(candidates))

    def test_runtime_permissions_resolves_host_runtime_path_placeholders(self) -> None:
        repo_copy = self._clone_template_repo('runtime-path-placeholders')
        contracts_path = repo_copy / 'config' / 'governance' / 'support' / 'repo_contracts.json'
        contracts = json.loads(contracts_path.read_text(encoding='utf-8'))
        contracts['contracts'].append(
            {
                'id': 'runtime.paths',
                'relative_path': 'config/runtime/paths.json',
                'format': 'json',
            }
        )
        contracts_path.write_text(json.dumps(contracts, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        (repo_copy / 'config' / 'runtime' / 'paths.json').write_text(
            json.dumps(
                {
                    'entries': {
                        'state_tmp': {
                            'kind': 'runtime_dir',
                            'create_on_bootstrap': True,
                            'paths': {'host': '{host_state_root}/tmp'},
                        },
                        'gateway_workspace': {
                            'kind': 'runtime_dir',
                            'create_on_bootstrap': True,
                            'paths': {'host': '{host_gateway_root}/workspace-local'},
                        },
                        'gateway_nginx_logs': {
                            'kind': 'runtime_dir',
                            'create_on_bootstrap': True,
                            'paths': {'host': '{host_gateway_logs_root}/nginx-gateway'},
                        },
                        'control_plane_logs': {
                            'kind': 'runtime_dir',
                            'create_on_bootstrap': True,
                            'paths': {'host': '{host_control_plane_root}/logs'},
                        },
                        'control_plane_setup_summary': {
                            'kind': 'runtime_file',
                            'create_parent_on_bootstrap': True,
                            'paths': {'host': '{host_control_plane_setup_root}/summary.json'},
                        },
                        'control_plane_dispatch_out': {
                            'kind': 'runtime_dir',
                            'create_on_bootstrap': True,
                            'paths': {'host': '{host_control_plane_dispatch_root}/out'},
                        },
                    }
                },
                ensure_ascii=False,
                indent=2,
            )
            + '\n',
            encoding='utf-8',
        )

        result = run_bash_command(
            repo_copy,
            'source scripts/setup/lib/runtime_permissions.sh; '
            'runtime_permissions_collect_manifest_host_bootstrap_entries "$PWD"',
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        output = result.stdout.replace('\\', '/')
        self.assertNotIn('{host_', output)
        self.assertIn('/state/openclaw/tmp', output)
        self.assertIn('/state/openclaw/gateway/workspace-local', output)
        self.assertIn('/state/openclaw/gateway/logs/nginx-gateway', output)
        self.assertIn('/state/openclaw/control_plane/logs', output)
        self.assertIn('/state/openclaw/control_plane/setup', output)
        self.assertIn('/state/openclaw/control_plane/dispatch/out', output)

    def test_runtime_permissions_resolves_compose_host_state_template_sources(self) -> None:
        repo_copy = self._clone_template_repo('compose-host-state-template')
        (repo_copy / 'deploy' / 'docker-compose.yml').write_text(
            '\n'.join(
                [
                    'services:',
                    '  openclaw-private-ingress:',
                    '    volumes:',
                    '      - ../${HOST_STATE_ROOT:?HOST_STATE_ROOT_required}/gateway/nginx.gateway.conf:/etc/nginx/nginx.conf:ro,Z',
                    '      - ./nginx/certs:/etc/nginx/certs:ro,Z',
                    '',
                ]
            ),
            encoding='utf-8',
        )

        result = run_bash_command(
            repo_copy,
            'source scripts/setup/lib/runtime_permissions.sh; '
            'runtime_permissions_collect_compose_host_bind_targets "$PWD"',
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        output = result.stdout.replace('\\', '/')
        self.assertNotIn('${HOST_STATE_ROOT', output)
        self.assertIn('/state/openclaw/gateway', output)
        self.assertIn('/deploy/nginx/certs', output)

    def test_runtime_permissions_rejects_nginx_conf_directory(self) -> None:
        repo_copy = self._clone_template_repo('nginx-conf-directory')
        (repo_copy / 'state' / 'openclaw' / 'gateway' / 'nginx.gateway.conf').mkdir(parents=True)

        result = run_bash_command(
            repo_copy,
            'source scripts/setup/lib/runtime_permissions.sh; '
            'runtime_permissions_prepare_ingress_nginx_conf_acl "$PWD"',
        )

        self.assertEqual(result.returncode, 4)
        self.assertIn('Nginx 配置路径应为文件但当前是目录', result.stderr)

    def test_fix_permissions_restores_managed_launcher_exec_bits(self) -> None:
        source = (ROOT_DIR / 'scripts' / 'setup' / 'fix_permissions.sh').read_text(encoding='utf-8')
        self.assertIn('runtime_permissions_mark_shebang_executable "$ROOT_DIR"', source)
        self.assertIn('runtime_permissions_align_repo_local_state_owner_only "$ROOT_DIR" "$ROOT_DIR/state/image_pull"', source)
        self.assertIn('runtime_permissions_align_repo_local_state_owner_only "$ROOT_DIR" "$ROOT_DIR/state/image_artifacts"', source)
        runtime_source = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'runtime_permissions.sh').read_text(encoding='utf-8')
        self.assertIn('runtime_permissions_align_repo_local_state_owner_only()', runtime_source)
        self.assertIn('runtime_permissions_align_repo_local_runtime_bind_owner()', runtime_source)
        self.assertIn('runtime_permissions_find_chmod_existing()', runtime_source)
        self.assertIn('find "$@" ! -perm "$mode" -exec sh -c', runtime_source)
        self.assertIn('[ -e "$path" ] || [ -L "$path" ] || continue', runtime_source)
        self.assertIn('runtime_permissions_restore_extension_env_exec_bits "$dir"', runtime_source)
        self.assertIn('拒绝 chown 非 repo-local image state 目录', runtime_source)
        self.assertIn('repo-local state 路径不是目录', runtime_source)
        self.assertIn('state/image_pull 应为目录但当前不是目录', runtime_source)
        self.assertIn('拒绝 chown 非 repo-local runtime bind 目录', runtime_source)
        self.assertIn('runtime_permissions_align_repo_local_runtime_bind_owner "$root" "$root/deploy/nginx/certs"', runtime_source)

    def test_runtime_permissions_resolves_explicit_runtime_uid_gid(self) -> None:
        result = run_bash_command(
            ROOT_DIR,
            'source scripts/setup/lib/runtime_permissions.sh; '
            'OPENCLAW_RUNTIME_UID=1234 OPENCLAW_RUNTIME_GID=5678 '
            'runtime_permissions_resolve_runtime_uid_gid "$PWD"',
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), '1234:5678')

    def test_runtime_permissions_rejects_partial_runtime_uid_gid(self) -> None:
        result = run_bash_command(
            ROOT_DIR,
            'source scripts/setup/lib/runtime_permissions.sh; '
            'OPENCLAW_RUNTIME_UID=1234 OPENCLAW_RUNTIME_GID= '
            'runtime_permissions_resolve_runtime_uid_gid "$PWD"',
        )

        self.assertEqual(result.returncode, 4)
        self.assertIn('OPENCLAW_RUNTIME_UID/GID 不完整或不是数字', result.stderr)

    def test_runtime_permissions_root_path_is_fail_closed_when_uid_unknown(self) -> None:
        source = (ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'runtime_permissions.sh').read_text(encoding='utf-8')
        fix_permissions_source = (ROOT_DIR / 'scripts' / 'setup' / 'fix_permissions.sh').read_text(encoding='utf-8')
        bootstrap_source = (ROOT_DIR / 'scripts' / 'setup' / 'bootstrap.sh').read_text(encoding='utf-8')

        self.assertIn('runtime_permissions_running_runtime_uid_gid()', source)
        self.assertIn('runtime_permissions_assert_root_runtime_uid_gid_resolvable()', source)
        self.assertIn('openclaw-control-plane-scheduler', source)
        self.assertIn('root 执行时无法确定 OPENCLAW_RUNTIME_UID/GID', source)
        self.assertLess(
            fix_permissions_source.index('runtime_permissions_assert_root_runtime_uid_gid_resolvable "$ROOT_DIR"'),
            fix_permissions_source.index('runtime_permissions_prepare_repo_support_dirs "$ROOT_DIR"'),
        )
        self.assertLess(
            bootstrap_source.index('runtime_permissions_assert_root_runtime_uid_gid_resolvable "$ROOT_DIR"'),
            bootstrap_source.index('runtime_permissions_prepare_repo_support_dirs "$ROOT_DIR"'),
        )

    def test_local_runtime_fs_contract_flags_missing_managed_launcher_exec_bit(self) -> None:
        source = (ROOT_DIR / 'scripts' / 'doctor' / 'check_local_runtime_fs_contract.sh').read_text(encoding='utf-8')
        self.assertIn('runtime_permissions_collect_repo_exec_candidates "$ROOT_DIR"', source)
        self.assertIn('缺少执行位', source)
        self.assertIn('check_line_endings_contract', source)
        self.assertIn('CRLF / 回车字符', source)
        self.assertIn('deploy/targets.d', source)


if __name__ == '__main__':
    unittest.main()
