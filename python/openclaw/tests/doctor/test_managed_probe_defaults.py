from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openclaw.control_plane.config_loader import load_control_plane_service_payload
from openclaw.doctor.agent_modules import attach_detach, prune_drop
from openclaw.doctor.agent_modules.managed_probe_fixture import PROBE_EXTENSION_ID
from openclaw.lib.repo.layout import CONTROL_PLANE_CONFIG_ENV, CONTROL_PLANE_PROFILE_ENV
from openclaw.tests.support.lightweight_repo import materialize_managed_probe_repo


class ManagedProbeDefaultFlowTest(unittest.TestCase):
    _template_tmp: tempfile.TemporaryDirectory[str] | None = None
    _template_repo_root: Path | None = None

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._template_tmp = tempfile.TemporaryDirectory(prefix='managed-probe-default-template-')
        cls._template_repo_root = materialize_managed_probe_repo(Path(cls._template_tmp.name) / 'repo')

    @classmethod
    def tearDownClass(cls) -> None:
        tmp = cls._template_tmp
        cls._template_tmp = None
        cls._template_repo_root = None
        if tmp is not None:
            tmp.cleanup()
        super().tearDownClass()

    def _copy_repo_without_real_extension(self, original_copy: object, temp_root: Path) -> Path:
        _ = original_copy
        template = self._template_repo_root
        if template is None:
            raise AssertionError('managed probe default template is not initialized')
        target = Path(temp_root) / 'repo'
        shutil.copytree(
            template,
            target,
            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'),
        )
        return target

    def _probe_payload_for(
        self,
        module: object,
        repo_root: Path,
        requested_config_path: Path | None,
        *,
        control_plane_profile: str,
    ) -> dict[str, object]:
        effective_requested_config_path = module._resolve_effective_requested_config_path(
            repo_root,
            requested_config_path,
            control_plane_profile=control_plane_profile,
        )
        if module is attach_detach:
            config_path = module.resolve_config_path(
                repo_root,
                effective_requested_config_path,
                control_plane_profile=control_plane_profile,
            )
        else:
            config_path = module._resolve_config_or_exit(
                repo_root,
                effective_requested_config_path,
                control_plane_profile=control_plane_profile,
            )
        _, service_payload = load_control_plane_service_payload(config_path)
        enabled_ids = [
            str(item).strip()
            for item in ((service_payload.get('extensions') or {}).get('enabledExtensionIds') or [])
            if str(item).strip()
        ]
        return {
            'ok': True,
            'configPath': str(config_path),
            'enabledExtensions': enabled_ids,
            'results': [{'ownerDomain': module._probe_owner_domain(config_path)}],
        }

    def test_attach_detach_zero_arg_mode_uses_isolated_probe_extension(self) -> None:
        original_copy = attach_detach._copy_repo
        with mock.patch.object(
            attach_detach,
            '_copy_repo',
            side_effect=lambda temp_root: self._copy_repo_without_real_extension(original_copy, temp_root),
        ), mock.patch.object(
            attach_detach,
            '_run_probe_in_repo_copy',
            side_effect=lambda repo_root, requested_config_path, *, control_plane_profile: self._probe_payload_for(
                attach_detach,
                repo_root,
                requested_config_path,
                control_plane_profile=control_plane_profile,
            ),
        ), mock.patch.dict(
            'os.environ',
            {CONTROL_PLANE_CONFIG_ENV: '', CONTROL_PLANE_PROFILE_ENV: ''},
            clear=False,
        ):
            payload = attach_detach._run_temp_probe(None, control_plane_profile='')

        self.assertTrue(payload['ok'])
        self.assertTrue(str(payload['configPath']).endswith(f'{PROBE_EXTENSION_ID}.service.json'))
        self.assertEqual(payload['enabledExtensions'], ['agent_platform', PROBE_EXTENSION_ID])
        self.assertEqual(payload['results'][0]['ownerDomain'], 'probe')

    def test_prune_drop_zero_arg_mode_uses_isolated_probe_extension(self) -> None:
        original_copy = prune_drop._copy_repo
        with mock.patch.object(
            prune_drop,
            '_copy_repo',
            side_effect=lambda temp_root: self._copy_repo_without_real_extension(original_copy, temp_root),
        ), mock.patch.object(
            prune_drop,
            '_run_probe_in_repo_copy',
            side_effect=lambda repo_root, requested_config_path, *, control_plane_profile: self._probe_payload_for(
                prune_drop,
                repo_root,
                requested_config_path,
                control_plane_profile=control_plane_profile,
            ),
        ), mock.patch.dict(
            'os.environ',
            {CONTROL_PLANE_CONFIG_ENV: '', CONTROL_PLANE_PROFILE_ENV: ''},
            clear=False,
        ):
            payload = prune_drop._run_temp_probe(None, control_plane_profile='')

        self.assertTrue(payload['ok'])
        self.assertTrue(str(payload['configPath']).endswith(f'{PROBE_EXTENSION_ID}.service.json'))
        self.assertEqual(payload['enabledExtensions'], ['agent_platform', PROBE_EXTENSION_ID])


if __name__ == '__main__':
    unittest.main()
