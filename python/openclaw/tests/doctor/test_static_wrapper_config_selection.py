from __future__ import annotations

import os
import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openclaw.doctor.agent_modules.support import resolve_bash_executable
from openclaw.lib.repo.layout import (
    CONTROL_PLANE_CONFIG_ENV,
    CONTROL_PLANE_PROFILE_ENV,
    CONTROL_PLANE_PROFILE_REGISTRY_ENV,
    control_plane_profile_id_for_config_path,
    resolve_control_plane_profile_service_config_path,
    resolve_repo_root,
    resolve_selected_control_plane_config_path,
    resolve_selected_control_plane_container_config_path,
    resolve_selected_control_plane_profile_id,
)
from openclaw.control_plane.registry_loader.config import load_registry_service_context
from openclaw.tests.support.managed_extensions import managed_extensions

ROOT_DIR = resolve_repo_root(Path(__file__))
BASE_CONFIG = (ROOT_DIR / 'config' / 'control_plane' / 'service.json').resolve()
AGENT_PLATFORM_CONFIG = (ROOT_DIR / 'config' / 'control_plane' / 'profiles' / 'agent_platform.service.json').resolve()
MANAGED_EXTENSIONS = tuple(sorted(managed_extensions(ROOT_DIR), key=lambda row: row.id))
MANAGED_EXTENSION = MANAGED_EXTENSIONS[0] if MANAGED_EXTENSIONS else None
MANAGED_EXTENSION_PROFILE_ID = MANAGED_EXTENSION.id if MANAGED_EXTENSION is not None else 'agent_probe'
MANAGED_EXTENSION_CONFIG = (
    MANAGED_EXTENSION.default_service_config_path.resolve()
    if MANAGED_EXTENSION is not None
    else (ROOT_DIR / 'agent/extensions/agent_probe/config/control_plane/profiles/agent_probe.service.json').resolve()
)
MANAGED_EXTENSION_CONFIG_REL = (
    MANAGED_EXTENSION_CONFIG.relative_to(ROOT_DIR).as_posix()
    if MANAGED_EXTENSION is not None
    else 'agent/extensions/agent_probe/config/control_plane/profiles/agent_probe.service.json'
)
MANAGED_EXTENSION_CONTAINER_CONFIG = f'/opt/openclaw-tools/{MANAGED_EXTENSION_CONFIG_REL}'


def _repo_combination_profile() -> tuple[str | None, Path | None]:
    managed_ids = {extension.id for extension in MANAGED_EXTENSIONS}
    for path in sorted((ROOT_DIR / 'config' / 'control_plane' / 'profiles').glob('*.service.json')):
        context = load_registry_service_context(path)
        extension_ids = [item for item in context['enabledExtensionIds'] if item in managed_ids]
        if len(extension_ids) >= 2:
            return path.name.removesuffix('.service.json'), path.resolve()
    return None, None


COMBO_PROFILE_ID, COMBO_CONFIG = _repo_combination_profile()


def _bash_display_path(path: str) -> str:
    normalized = path.replace('\\', '/')
    if os.name == 'nt' and len(normalized) >= 3 and normalized[1:3] == ':/':
        return f'/{normalized[0].lower()}{normalized[2:]}'
    return normalized


class StaticWrapperConfigSelectionTest(unittest.TestCase):
    _shell_outputs: dict[str, str] = {}

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if MANAGED_EXTENSION is None or COMBO_PROFILE_ID is None or COMBO_CONFIG is None:
            raise unittest.SkipTest('base release surface has no repo-managed extension combination profile')
        bash_executable = resolve_bash_executable()
        if not bash_executable:
            raise AssertionError('未找到可用 bash')
        real_runner_tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(real_runner_tmp.cleanup)
        real_runner_repo = Path(real_runner_tmp.name).resolve()
        (real_runner_repo / 'python' / 'openclaw').mkdir(parents=True, exist_ok=True)
        (real_runner_repo / 'scripts' / 'runtime').mkdir(parents=True, exist_ok=True)
        (real_runner_repo / 'scripts' / 'runtime' / 'run_openclaw_python_tool.sh').write_text(
            '#!/usr/bin/env bash\n',
            encoding='utf-8',
        )
        helper_path = real_runner_repo / 'scripts' / 'lib' / 'control_plane_config_paths.sh'
        helper_path.parent.mkdir(parents=True, exist_ok=True)
        helper_path.write_text((ROOT_DIR / 'scripts' / 'lib' / 'control_plane_config_paths.sh').read_text(encoding='utf-8'), encoding='utf-8')
        (helper_path.parent / 'repo_root.sh').write_text(
            (ROOT_DIR / 'scripts' / 'lib' / 'repo_root.sh').read_text(encoding='utf-8'),
            encoding='utf-8',
        )
        fake_runner_path = real_runner_repo / 'scripts' / 'runtime' / 'run_python_container.sh'
        fake_runner_path.parent.mkdir(parents=True, exist_ok=True)
        fake_runner_path.write_text(
            '\n'.join(
                [
                    '#!/usr/bin/env bash',
                    'set -euo pipefail',
                    'for arg in "$@"; do',
                    "  printf 'ARG=%s\\n' \"$arg\"",
                    'done',
                    '',
                ]
            ),
            encoding='utf-8',
        )
        fake_runner_path.chmod(fake_runner_path.stat().st_mode | 0o111)
        script = '\n'.join(
            [
                'set -euo pipefail',
                'source ./scripts/lib/control_plane_config_paths.sh',
                'unset OPENCLAW_CONTROL_PLANE_PROFILE',
                'emit() { printf "%s\\t%s\\n" "$1" "$2"; }',
                "emit windows_path \"$(openclaw_control_plane_resolve_config_path agent_platform 'C:/tmp/demo.service.json')\"",
                (
                    'emit resolve_explicit_profile_over_env "$('
                    f'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH={shlex.quote(MANAGED_EXTENSION_CONFIG.as_posix())} '
                    'openclaw_control_plane_resolve_config_path base "" 1)"'
                ),
                (
                    'emit resolve_default_profile_uses_env "$('
                    f'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH={shlex.quote(MANAGED_EXTENSION_CONFIG.as_posix())} '
                    'openclaw_control_plane_resolve_config_path agent_platform)"'
                ),
                (
                    'emit resolve_ambient_profile "$('
                    f'OPENCLAW_CONTROL_PLANE_PROFILE={shlex.quote(MANAGED_EXTENSION_PROFILE_ID)} '
                    'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH= '
                    'openclaw_control_plane_resolve_config_path agent_platform)"'
                ),
                (
                    'mismatch_status=0; mismatch_output="$('
                    f'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH={shlex.quote(AGENT_PLATFORM_CONFIG.as_posix())} '
                    f'OPENCLAW_CONTROL_PLANE_PROFILE={shlex.quote(MANAGED_EXTENSION_PROFILE_ID)} '
                    'openclaw_control_plane_resolve_config_path agent_platform 2>&1)" || mismatch_status=$?; '
                    'emit mismatch_status "$mismatch_status"; emit mismatch_output "$mismatch_output"'
                ),
                (
                    'emit explicit_container_override "$('
                    'CONTROL_PLANE_CONTAINER_CONFIG_PATH=C:/tmp/custom-container-config.json '
                    'openclaw_control_plane_container_config_path '
                    f'agent_platform {shlex.quote(MANAGED_EXTENSION_CONFIG.as_posix())})"'
                ),
                (
                    'emit internal_container_override "$('
                    'CONTROL_PLANE_CONTAINER_CONFIG_PATH=C:/tmp/custom-container-config.json '
                    'openclaw_control_plane_container_profile_config_path agent_platform)"'
                ),
                (
                    'wrapper_explicit=; '
                    "while IFS= read -r -d '' item; do wrapper_explicit=\"${wrapper_explicit}${item}|\"; done < <("
                    f'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH={shlex.quote(str(AGENT_PLATFORM_CONFIG))} '
                    'openclaw_control_plane_wrapper_args agent_platform --control-plane-profile base); '
                    'emit wrapper_explicit "$wrapper_explicit"'
                ),
                (
                    'wrapper_explicit_config=; '
                    "while IFS= read -r -d '' item; do wrapper_explicit_config=\"${wrapper_explicit_config}${item}|\"; done < <("
                    f'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH={shlex.quote(str(AGENT_PLATFORM_CONFIG))} '
                    f'openclaw_control_plane_wrapper_args agent_platform --config-path {shlex.quote(MANAGED_EXTENSION_CONFIG.as_posix())}); '
                    'emit wrapper_explicit_config "$wrapper_explicit_config"'
                ),
                (
                    'wrapper_default=; '
                    "while IFS= read -r -d '' item; do wrapper_default=\"${wrapper_default}${item}|\"; done < <("
                    'openclaw_control_plane_wrapper_args agent_platform); '
                    'emit wrapper_default "$wrapper_default"'
                ),
                'tmp_env_dir="$(mktemp -d)"',
                'trap \'rm -rf "$tmp_env_dir"\' EXIT',
                (
                    'printf "%s\\n" '
                    f'{shlex.quote(f"OPENCLAW_CONTROL_PLANE_PROFILE={MANAGED_EXTENSION_PROFILE_ID}")} '
                    f'{shlex.quote(f"OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH={MANAGED_EXTENSION_CONTAINER_CONFIG}")} '
                    '> "$tmp_env_dir/deploy.env"'
                ),
                (
                    'default_file_config=; default_file_profile=agent_platform; default_file_explicit=0; '
                    'openclaw_control_plane_apply_default_selection_from_env_files '
                    'default_file_config default_file_profile default_file_explicit '
                    '"$tmp_env_dir/deploy.env|deploy/.env"; '
                    'emit default_file_profile "$default_file_profile"; '
                    'emit default_file_explicit "$default_file_explicit"; '
                    'emit default_file_resolved "$(openclaw_control_plane_resolve_config_path "$default_file_profile" "$default_file_config" "$default_file_explicit")"'
                ),
                (
                    'printf "%s\\n" '
                    f'{shlex.quote(f"OPENCLAW_CONTROL_PLANE_PROFILE={MANAGED_EXTENSION_PROFILE_ID}")} '
                    '> "$tmp_env_dir/site.env"'
                ),
                (
                    'fallback_file_config=; fallback_file_profile=agent_platform; fallback_file_explicit=0; '
                    'openclaw_control_plane_apply_default_selection_from_env_files '
                    'fallback_file_config fallback_file_profile fallback_file_explicit '
                    '"$tmp_env_dir/missing.env|deploy/.env" "$tmp_env_dir/site.env|deploy/site.env"; '
                    'emit fallback_file_profile "$fallback_file_profile"; '
                    'emit fallback_file_resolved "$(openclaw_control_plane_resolve_config_path "$fallback_file_profile" "$fallback_file_config" "$fallback_file_explicit")"'
                ),
                (
                    'public_file_config=; public_file_profile=agent_platform; public_file_explicit=0; '
                    'OPENCLAW_CONTROL_PLANE_PROFILE=base '
                    'openclaw_control_plane_apply_default_selection_from_env_files '
                    'public_file_config public_file_profile public_file_explicit '
                    '"$tmp_env_dir/deploy.env|deploy/.env"; '
                    'emit public_file_profile "$public_file_profile"; emit public_file_explicit "$public_file_explicit"'
                ),
                (
                    f'active_explicit_config={shlex.quote(AGENT_PLATFORM_CONFIG.as_posix())}; '
                    'active_explicit_profile=agent_platform; active_explicit_flag=0; '
                    'openclaw_control_plane_apply_env_file_active_selection '
                    ' "$tmp_env_dir/deploy.env" active_explicit_config active_explicit_profile active_explicit_flag 1 '
                    ' "$tmp_env_dir/deploy.env"; '
                    'emit active_explicit_profile "$active_explicit_profile"; '
                    'emit active_explicit_flag "$active_explicit_flag"; '
                    'emit active_explicit_resolved "$(openclaw_control_plane_resolve_config_path "$active_explicit_profile" "$active_explicit_config" "$active_explicit_flag")"'
                ),
                (
                    'real_runner_idx=0; '
                    'while IFS= read -r item; do '
                    'emit "real_container_runner_${real_runner_idx}" "$item"; '
                    'real_runner_idx=$((real_runner_idx + 1)); '
                    'done < <('
                    f'cd {shlex.quote(real_runner_repo.as_posix())} && '
                    f'export OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH={shlex.quote(MANAGED_EXTENSION_CONFIG.as_posix())} && '
                    'export CONTROL_PLANE_CONTAINER_CONFIG_PATH=C:/tmp/custom-container-config.json && '
                    'unset OPENCLAW_CONTROL_PLANE_CONFIG_PROXY_PYTHON && '
                    'unset OPENCLAW_CONTROL_PLANE_CONFIG_PATHS_SH_LOADED && '
                    'unset OPENCLAW_REPO_ROOT_SH_LOADED && '
                    'source ./scripts/lib/control_plane_config_paths.sh && '
                    'openclaw_control_plane_profile_config_path agent_platform)'
                ),
            ]
        )
        result = subprocess.run(
            [str(bash_executable), '-lc', script],
            cwd=ROOT_DIR,
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            env={
                **os.environ,
                'PYTHONIOENCODING': 'utf-8',
                'PYTHONUTF8': '1',
                'OPENCLAW_CONTROL_PLANE_CONFIG_PROXY_PYTHON': sys.executable,
            },
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr or result.stdout)
        cls._shell_outputs = dict(line.split('\t', 1) for line in result.stdout.splitlines() if '\t' in line)

    def _shell_value(self, key: str) -> str:
        return self._shell_outputs[key]

    def _shell_path(self, key: str) -> Path:
        value = self._shell_value(key)
        if os.name == 'nt' and len(value) >= 3 and value[0] == '/' and value[2] == '/':
            value = f'{value[1].upper()}:{value[2:]}'
        return Path(value).resolve()

    def test_control_plane_helper_defaults_to_agent_platform_profile(self) -> None:
        resolved = resolve_selected_control_plane_config_path(
            control_plane_profile='agent_platform',
            start_path=ROOT_DIR,
        )

        self.assertEqual(resolved, AGENT_PLATFORM_CONFIG)

    def test_control_plane_helper_allows_explicit_profile_override(self) -> None:
        resolved = resolve_selected_control_plane_config_path(
            control_plane_profile='base',
            start_path=ROOT_DIR,
        )

        self.assertEqual(resolved, BASE_CONFIG)

    def test_control_plane_helper_supports_managed_extension_profile(self) -> None:
        resolved = resolve_selected_control_plane_config_path(
            control_plane_profile=MANAGED_EXTENSION_PROFILE_ID,
            start_path=ROOT_DIR,
        )

        self.assertEqual(resolved, MANAGED_EXTENSION_CONFIG)

    def test_control_plane_helper_prefers_explicit_profile_over_ambient_public_config_path(self) -> None:
        with mock.patch.dict(os.environ, {CONTROL_PLANE_CONFIG_ENV: MANAGED_EXTENSION_CONFIG.as_posix(), CONTROL_PLANE_PROFILE_ENV: ''}):
            resolved = resolve_selected_control_plane_config_path(
                control_plane_profile='base',
                start_path=ROOT_DIR,
            )

        self.assertEqual(resolved, BASE_CONFIG)

    def test_control_plane_helper_prefers_ambient_public_config_path_when_requested_path_is_absent(self) -> None:
        with mock.patch.dict(os.environ, {CONTROL_PLANE_CONFIG_ENV: MANAGED_EXTENSION_CONFIG.as_posix(), CONTROL_PLANE_PROFILE_ENV: ''}):
            resolved = resolve_selected_control_plane_config_path(
                control_plane_profile=None,
                start_path=ROOT_DIR,
                default_profile='agent_platform',
            )

        self.assertEqual(resolved, MANAGED_EXTENSION_CONFIG)

    def test_control_plane_helper_ignores_ambient_shell_compat_config_path_when_requested_path_is_absent(self) -> None:
        with mock.patch.dict(os.environ, {'CONTROL_PLANE_CONFIG_PATH': MANAGED_EXTENSION_CONFIG.as_posix()}):
            resolved = resolve_selected_control_plane_config_path(
                control_plane_profile=None,
                start_path=ROOT_DIR,
                default_profile='agent_platform',
            )

        self.assertEqual(resolved, AGENT_PLATFORM_CONFIG)

    def test_control_plane_helper_preserves_windows_absolute_path(self) -> None:
        self.assertEqual(self._shell_value('windows_path'), 'C:/tmp/demo.service.json')

    def test_shell_helper_prefers_explicit_profile_over_ambient_public_config_path(self) -> None:
        self.assertEqual(Path(self._shell_value('resolve_explicit_profile_over_env')).resolve(), BASE_CONFIG)

    def test_shell_helper_uses_ambient_public_config_path_when_profile_is_defaulted(self) -> None:
        self.assertEqual(Path(self._shell_value('resolve_default_profile_uses_env')).resolve(), MANAGED_EXTENSION_CONFIG)

    def test_control_plane_helper_supports_ambient_profile(self) -> None:
        with mock.patch.dict(os.environ, {CONTROL_PLANE_CONFIG_ENV: '', CONTROL_PLANE_PROFILE_ENV: MANAGED_EXTENSION_PROFILE_ID}):
            resolved = resolve_selected_control_plane_config_path(
                control_plane_profile=None,
                start_path=ROOT_DIR,
                default_profile='agent_platform',
            )

        self.assertEqual(resolved, MANAGED_EXTENSION_CONFIG)

    def test_shell_helper_supports_ambient_profile(self) -> None:
        self.assertEqual(Path(self._shell_value('resolve_ambient_profile')).resolve(), MANAGED_EXTENSION_CONFIG)

    def test_control_plane_helper_rejects_ambient_profile_path_mismatch(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                CONTROL_PLANE_CONFIG_ENV: AGENT_PLATFORM_CONFIG.as_posix(),
                CONTROL_PLANE_PROFILE_ENV: MANAGED_EXTENSION_PROFILE_ID,
            },
        ):
            with self.assertRaisesRegex(ValueError, '不一致'):
                resolve_selected_control_plane_config_path(
                    control_plane_profile=None,
                    start_path=ROOT_DIR,
                    default_profile='agent_platform',
                )

    def test_shell_helper_rejects_ambient_profile_path_mismatch(self) -> None:
        self.assertNotEqual(self._shell_value('mismatch_status'), '0')
        self.assertIn('不一致', self._shell_value('mismatch_output'))

    def test_control_plane_helper_reports_managed_extension_profile_id(self) -> None:
        resolved = resolve_selected_control_plane_profile_id(
            config_path=MANAGED_EXTENSION_CONFIG,
            start_path=ROOT_DIR,
            default_profile='agent_platform',
        )

        self.assertEqual(resolved, MANAGED_EXTENSION_PROFILE_ID)

    def test_control_plane_profile_id_rejects_explicit_config_and_profile_pair(self) -> None:
        with self.assertRaisesRegex(ValueError, '不能同时使用'):
            resolve_selected_control_plane_profile_id(
                config_path=MANAGED_EXTENSION_CONFIG,
                control_plane_profile=MANAGED_EXTENSION_PROFILE_ID,
                start_path=ROOT_DIR,
            )

    def test_control_plane_helper_maps_managed_extension_profile_to_container_path(self) -> None:
        self.assertEqual(
            resolve_selected_control_plane_container_config_path(
                config_path=MANAGED_EXTENSION_CONFIG,
                start_path=ROOT_DIR,
            ).as_posix(),
            MANAGED_EXTENSION_CONTAINER_CONFIG,
        )

    def test_control_plane_helper_prefers_explicit_host_path_over_internal_container_override(self) -> None:
        self.assertEqual(
            self._shell_value('explicit_container_override'),
            MANAGED_EXTENSION_CONTAINER_CONFIG,
        )

    def test_control_plane_helper_uses_internal_container_override_when_public_selection_is_absent(self) -> None:
        explicit_override = 'C:/tmp/custom-container-config.json'
        self.assertEqual(self._shell_value('internal_container_override'), explicit_override)

    def test_real_container_runner_branch_receives_public_and_container_config_envs(self) -> None:
        lines = [
            value
            for key, value in sorted(self._shell_outputs.items())
            if key.startswith('real_container_runner_')
        ]
        self.assertIn('ARG=--env', lines)
        self.assertIn(f'ARG=OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH={MANAGED_EXTENSION_CONFIG.as_posix()}', lines)
        self.assertIn('ARG=CONTROL_PLANE_CONTAINER_CONFIG_PATH=C:/tmp/custom-container-config.json', lines)

    def test_wrapper_args_preserve_explicit_profile_even_with_ambient_env(self) -> None:
        self.assertEqual([item for item in self._shell_value('wrapper_explicit').split('|') if item], ['--control-plane-profile', 'base'])

    def test_wrapper_args_preserve_explicit_config_path_even_with_ambient_env(self) -> None:
        self.assertEqual(
            [item for item in self._shell_value('wrapper_explicit_config').split('|') if item],
            ['--config-path', MANAGED_EXTENSION_CONFIG.as_posix()],
        )

    def test_wrapper_args_emit_default_profile_when_selection_is_absent(self) -> None:
        self.assertEqual([item for item in self._shell_value('wrapper_default').split('|') if item], ['--control-plane-profile', 'agent_platform'])

    def test_default_env_file_selection_prefers_deploy_env_when_public_env_is_absent(self) -> None:
        self.assertEqual(self._shell_value('default_file_profile'), MANAGED_EXTENSION_PROFILE_ID)
        self.assertEqual(self._shell_value('default_file_explicit'), '1')
        self.assertEqual(self._shell_path('default_file_resolved'), MANAGED_EXTENSION_CONFIG)

    def test_default_env_file_selection_falls_back_to_site_env(self) -> None:
        self.assertEqual(self._shell_value('fallback_file_profile'), MANAGED_EXTENSION_PROFILE_ID)
        self.assertEqual(self._shell_path('fallback_file_resolved'), MANAGED_EXTENSION_CONFIG)

    def test_default_env_file_selection_respects_public_env(self) -> None:
        self.assertEqual(self._shell_value('public_file_profile'), 'agent_platform')
        self.assertEqual(self._shell_value('public_file_explicit'), '0')

    def test_active_env_file_selection_preserves_explicit_config_path(self) -> None:
        self.assertEqual(self._shell_value('active_explicit_profile'), 'agent_platform')
        self.assertEqual(self._shell_value('active_explicit_flag'), '0')
        self.assertEqual(self._shell_path('active_explicit_resolved'), AGENT_PLATFORM_CONFIG)

    def test_model_profile_connectivity_reads_control_plane_selection_from_env_file(self) -> None:
        bash_executable = resolve_bash_executable()
        self.assertTrue(bash_executable)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            env_file = tmp / 'deploy.env'
            capture_path = tmp / 'runner-capture.txt'
            fake_runner = tmp / 'fake_python_runner.sh'
            env_file.write_text(
                '\n'.join(
                    [
                        f'OPENCLAW_CONTROL_PLANE_PROFILE={MANAGED_EXTENSION_PROFILE_ID}',
                        f'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH={MANAGED_EXTENSION_CONTAINER_CONFIG}',
                    ]
                )
                + '\n',
                encoding='utf-8',
            )
            fake_runner.write_text(
                '\n'.join(
                    [
                        '#!/usr/bin/env bash',
                        'set -euo pipefail',
                        '{',
                        '  printf "ENV=%s\\n" "${OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH:-}"',
                        '  for arg in "$@"; do printf "ARG=%s\\n" "$arg"; done',
                        '} > "$FAKE_RUNNER_CAPTURE"',
                        'printf \'{"envSpecs":[]}\\n\'',
                    ]
                )
                + '\n',
                encoding='utf-8',
            )
            fake_runner.chmod(fake_runner.stat().st_mode | 0o111)

            result = subprocess.run(
                [
                    str(bash_executable),
                    '-lc',
                    (
                        f'export OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH={shlex.quote(AGENT_PLATFORM_CONFIG.as_posix())}; '
                        'export OPENCLAW_CONTROL_PLANE_PROFILE=agent_platform; '
                        'bash ./scripts/doctor/check_model_profile_connectivity.sh '
                        f'--env-file {shlex.quote(env_file.as_posix())}'
                    ),
                ],
                cwd=ROOT_DIR,
                text=True,
                encoding='utf-8',
                errors='replace',
                capture_output=True,
                env={
                    **os.environ,
                    'PYTHONIOENCODING': 'utf-8',
                    'PYTHONUTF8': '1',
                    'OPENCLAW_CONTROL_PLANE_CONFIG_PROXY_PYTHON': sys.executable,
                    'PYTHON_RUNNER': fake_runner.as_posix(),
                    'FAKE_RUNNER_CAPTURE': capture_path.as_posix(),
                },
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            capture = capture_path.read_text(encoding='utf-8')
            expected_path = _bash_display_path(MANAGED_EXTENSION_CONFIG.as_posix())
            self.assertIn(f'ENV={expected_path}', capture)
            self.assertIn(f'ARG=OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH={expected_path}', capture)
            self.assertIn('当前 active profile 未声明需要运行态模型 env 的作业', result.stdout)

    def test_full_test_manifest_merge_reads_profile_only_env_file_selection(self) -> None:
        bash_executable = resolve_bash_executable()
        self.assertTrue(bash_executable)
        testing_manifest = json.loads(
            (MANAGED_EXTENSION.manifest_dir / f'{MANAGED_EXTENSION.id}.testing_manifest.json').read_text(encoding='utf-8')
        )
        expected_groups = set(testing_manifest['valid_groups'])
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / 'deploy.env'
            env_file.write_text(f'OPENCLAW_CONTROL_PLANE_PROFILE={MANAGED_EXTENSION_PROFILE_ID}\n', encoding='utf-8')

            result = subprocess.run(
                [
                    str(bash_executable),
                    '-lc',
                    (
                        f'export OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH={shlex.quote(AGENT_PLATFORM_CONFIG.as_posix())}; '
                        'export OPENCLAW_CONTROL_PLANE_PROFILE=agent_platform; '
                        'source ./scripts/setup/lib/test_gate_common.sh; '
                        'source ./scripts/setup/lib/full_test_env_shell.sh; '
                        'ROOT_DIR="$(pwd -P)"; '
                        f'ENV_FILE={shlex.quote(env_file.as_posix())}; '
                        'full_test_testing_manifest_json | jq -r ".valid_groups[]?"'
                    ),
                ],
                cwd=ROOT_DIR,
                text=True,
                encoding='utf-8',
                errors='replace',
                capture_output=True,
                env={
                    **os.environ,
                    'PYTHONIOENCODING': 'utf-8',
                    'PYTHONUTF8': '1',
                    'PYTHONPATH': str((ROOT_DIR / 'python').resolve()),
                    'OPENCLAW_CONTROL_PLANE_CONFIG_PROXY_PYTHON': sys.executable,
                },
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            actual_groups = {line.strip() for line in result.stdout.splitlines() if line.strip()}
            self.assertFalse(expected_groups - actual_groups)

    def test_deploy_active_config_helper_reads_profile_only_env_file_selection(self) -> None:
        bash_executable = resolve_bash_executable()
        self.assertTrue(bash_executable)
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / 'deploy.env'
            env_file.write_text(f'OPENCLAW_CONTROL_PLANE_PROFILE={MANAGED_EXTENSION_PROFILE_ID}\n', encoding='utf-8')

            result = subprocess.run(
                [
                    str(bash_executable),
                    '-lc',
                    (
                        f'export OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH={shlex.quote(AGENT_PLATFORM_CONFIG.as_posix())}; '
                        'export OPENCLAW_CONTROL_PLANE_PROFILE=agent_platform; '
                        'source ./scripts/setup/lib/deploy_flow_control_plane_shell.sh; '
                        'ROOT_DIR="$(pwd -P)"; '
                        f'deploy_active_control_plane_config_path_for_env {shlex.quote(env_file.as_posix())}'
                    ),
                ],
                cwd=ROOT_DIR,
                text=True,
                encoding='utf-8',
                errors='replace',
                capture_output=True,
                env={
                    **os.environ,
                    'PYTHONIOENCODING': 'utf-8',
                    'PYTHONUTF8': '1',
                    'PYTHONPATH': str((ROOT_DIR / 'python').resolve()),
                    'OPENCLAW_CONTROL_PLANE_CONFIG_PROXY_PYTHON': sys.executable,
                },
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            actual_path = _bash_display_path(result.stdout.strip())
            expected_path = _bash_display_path(MANAGED_EXTENSION_CONFIG.as_posix())
            self.assertEqual(actual_path, expected_path)

    def test_env_file_invalid_config_path_fails_closed_even_with_ambient_selection(self) -> None:
        bash_executable = resolve_bash_executable()
        self.assertTrue(bash_executable)
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / 'deploy.env'
            env_file.write_text(
                'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH=/opt/openclaw-tools/config/control_plane/profiles/missing.service.json\n',
                encoding='utf-8',
            )
            ambient = (
                f'export OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH={shlex.quote(AGENT_PLATFORM_CONFIG.as_posix())}; '
                'export OPENCLAW_CONTROL_PLANE_PROFILE=agent_platform; '
            )
            commands = {
                'model_profile_doctor': (
                    ambient
                    + 'bash ./scripts/doctor/check_model_profile_connectivity.sh '
                    + f'--env-file {shlex.quote(env_file.as_posix())}'
                ),
                'full_test_manifest': (
                    ambient
                    + 'source ./scripts/setup/lib/test_gate_common.sh; '
                    + 'source ./scripts/setup/lib/full_test_env_shell.sh; '
                    + 'ROOT_DIR="$(pwd -P)"; '
                    + f'ENV_FILE={shlex.quote(env_file.as_posix())}; '
                    + 'full_test_testing_manifest_json >/dev/null'
                ),
                'deploy_active_config': (
                    ambient
                    + 'source ./scripts/setup/lib/deploy_flow_control_plane_shell.sh; '
                    + 'ROOT_DIR="$(pwd -P)"; '
                    + f'deploy_active_control_plane_config_path_for_env {shlex.quote(env_file.as_posix())} >/dev/null'
                ),
            }
            for label, script in commands.items():
                with self.subTest(label=label):
                    result = subprocess.run(
                        [str(bash_executable), '-lc', script],
                        cwd=ROOT_DIR,
                        text=True,
                        encoding='utf-8',
                        errors='replace',
                        capture_output=True,
                        env={
                            **os.environ,
                            'PYTHONIOENCODING': 'utf-8',
                            'PYTHONUTF8': '1',
                            'PYTHONPATH': str((ROOT_DIR / 'python').resolve()),
                            'OPENCLAW_CONTROL_PLANE_CONFIG_PROXY_PYTHON': sys.executable,
                        },
                        check=False,
                    )

                    self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)
                    self.assertIn('OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH 指向的配置文件不存在', result.stdout + result.stderr)

    def test_control_plane_profile_helper_reports_unknown_profile_under_set_e(self) -> None:
        with self.assertRaisesRegex(ValueError, 'unknown control-plane profile: missing_profile'):
            resolve_control_plane_profile_service_config_path('missing_profile', start_path=ROOT_DIR)

    def test_control_plane_profile_id_helper_fails_closed_when_registry_is_missing(self) -> None:
        with mock.patch.dict(
            os.environ,
            {CONTROL_PLANE_PROFILE_REGISTRY_ENV: (ROOT_DIR / 'config' / 'control_plane' / 'does-not-exist.tsv').as_posix()},
        ):
            with self.assertRaisesRegex(ValueError, 'missing profile registry'):
                control_plane_profile_id_for_config_path(AGENT_PLATFORM_CONFIG, start_path=ROOT_DIR)

    def test_control_plane_profile_helper_rejects_duplicate_registry_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / 'profile_registry.tsv'
            registry_path.write_text(
                '\n'.join(
                    [
                        'base\tconfig/control_plane/service.json',
                        'agent_platform\tconfig/control_plane/profiles/agent_platform.service.json',
                        f'agent_platform\t{MANAGED_EXTENSION_CONFIG_REL}',
                    ]
                )
                + '\n',
                encoding='utf-8',
            )

            with mock.patch.dict(os.environ, {CONTROL_PLANE_PROFILE_REGISTRY_ENV: registry_path.as_posix()}):
                with self.assertRaisesRegex(ValueError, 'duplicate control-plane profile: agent_platform'):
                    resolve_control_plane_profile_service_config_path('agent_platform', start_path=ROOT_DIR)

    def test_control_plane_profile_id_helper_rejects_duplicate_registry_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / 'profile_registry.tsv'
            registry_path.write_text(
                '\n'.join(
                    [
                        'base\tconfig/control_plane/service.json',
                        'agent_platform\tconfig/control_plane/profiles/agent_platform.service.json',
                        f'agent_platform\t{MANAGED_EXTENSION_CONFIG_REL}',
                    ]
                )
                + '\n',
                encoding='utf-8',
            )

            with mock.patch.dict(os.environ, {CONTROL_PLANE_PROFILE_REGISTRY_ENV: registry_path.as_posix()}):
                with self.assertRaisesRegex(ValueError, 'duplicate control-plane profile: agent_platform'):
                    control_plane_profile_id_for_config_path(AGENT_PLATFORM_CONFIG, start_path=ROOT_DIR)

    def test_control_plane_profile_helper_rejects_invalid_registry_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / 'profile_registry.tsv'
            registry_path.write_text(
                '\n'.join(
                    [
                        'base\tconfig/control_plane/service.json',
                        'agent_platform\tconfig/control_plane/profiles/agent_platform.service.json\textra',
                    ]
                )
                + '\n',
                encoding='utf-8',
            )

            with mock.patch.dict(os.environ, {CONTROL_PLANE_PROFILE_REGISTRY_ENV: registry_path.as_posix()}):
                with self.assertRaisesRegex(ValueError, 'invalid profile registry line'):
                    resolve_control_plane_profile_service_config_path('agent_platform', start_path=ROOT_DIR)

    def test_control_plane_profile_helper_rejects_registry_traversal_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / 'profile_registry.tsv'
            registry_path.write_text(
                '\n'.join(
                    [
                        'base\tconfig/control_plane/service.json',
                        'agent_platform\t../outside.json',
                    ]
                )
                + '\n',
                encoding='utf-8',
            )

            with mock.patch.dict(os.environ, {CONTROL_PLANE_PROFILE_REGISTRY_ENV: registry_path.as_posix()}):
                with self.assertRaisesRegex(ValueError, 'profile registry 路径非法'):
                    resolve_control_plane_profile_service_config_path('agent_platform', start_path=ROOT_DIR)

    def test_control_plane_profile_helper_rejects_missing_registry_target_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / 'profile_registry.tsv'
            registry_path.write_text(
                '\n'.join(
                    [
                        'base\tconfig/control_plane/service.json',
                        'agent_platform\tconfig/control_plane/profiles/does-not-exist.service.json',
                    ]
                )
                + '\n',
                encoding='utf-8',
            )

            with mock.patch.dict(os.environ, {CONTROL_PLANE_PROFILE_REGISTRY_ENV: registry_path.as_posix()}):
                with self.assertRaisesRegex(ValueError, 'profile registry 路径必须使用合同路径'):
                    resolve_control_plane_profile_service_config_path('agent_platform', start_path=ROOT_DIR)

    def test_control_plane_profile_helper_resolves_repo_combination_profile(self) -> None:
        self.assertEqual(
            resolve_control_plane_profile_service_config_path(COMBO_PROFILE_ID, start_path=ROOT_DIR),
            COMBO_CONFIG,
        )
        self.assertEqual(control_plane_profile_id_for_config_path(COMBO_CONFIG, start_path=ROOT_DIR), COMBO_PROFILE_ID)

    def test_control_plane_profile_helper_rejects_profile_alias_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / 'profile_registry.tsv'
            registry_path.write_text(
                '\n'.join(
                    [
                        'base\tconfig/control_plane/service.json',
                        'agent_platform\tconfig/control_plane/profiles/agent_platform.service.json',
                        f'{MANAGED_EXTENSION_PROFILE_ID}\tconfig/control_plane/profiles/agent_platform.service.json',
                    ]
                )
                + '\n',
                encoding='utf-8',
            )

            with mock.patch.dict(os.environ, {CONTROL_PLANE_PROFILE_REGISTRY_ENV: registry_path.as_posix()}):
                with self.assertRaisesRegex(ValueError, 'profile registry 路径必须使用合同路径'):
                    resolve_control_plane_profile_service_config_path(MANAGED_EXTENSION_PROFILE_ID, start_path=ROOT_DIR)


if __name__ == '__main__':
    unittest.main()
