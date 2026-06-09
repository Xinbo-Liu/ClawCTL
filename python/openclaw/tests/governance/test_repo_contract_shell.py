from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.static_truth import repo_contract_path, repo_contract_relpath
from openclaw.tests.governance.test_local_workspace_policy_scripts import run_bash_command


ROOT_DIR = resolve_repo_root(Path(__file__))


class RepoContractShellBridgeTest(unittest.TestCase):
    _root_outputs: dict[str, list[str]] = {}
    _malicious_repo_cm: Any = None
    _malicious_repo_root: Path | None = None

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._malicious_repo_cm = tempfile.TemporaryDirectory()
        repo_root = Path(cls._malicious_repo_cm.name).resolve()
        cls._malicious_repo_root = repo_root
        helper_path = repo_root / 'scripts' / 'lib'
        truth_dir = repo_root / 'config' / 'governance' / 'support'
        helper_path.mkdir(parents=True, exist_ok=True)
        truth_dir.mkdir(parents=True, exist_ok=True)
        (helper_path / 'repo_contracts.sh').write_text(
            (ROOT_DIR / 'scripts' / 'lib' / 'repo_contracts.sh').read_text(encoding='utf-8'),
            encoding='utf-8',
        )
        (helper_path / 'repo_root.sh').write_text(
            (ROOT_DIR / 'scripts' / 'lib' / 'repo_root.sh').read_text(encoding='utf-8'),
            encoding='utf-8',
        )
        (truth_dir / 'repo_contracts.json').write_text(
            cls._truth_text(
                [
                    {
                        'id': 'runtime.bad$(touch pwned)',
                        'relative_path': 'config/runtime/testing_manifest.json',
                        'format': 'json',
                    }
                ]
            ),
            encoding='utf-8',
        )
        result = run_bash_command(
            ROOT_DIR,
            (
                'unset PYTHONPATH PYTHONHOME && '
                'emit() { printf "%s\\t%s\\n" "$1" "$2"; }; '
                'source ./scripts/lib/repo_contracts.sh && '
                'emit contract_path "$(repo_contract_path runtime.testing_manifest)" && '
                'emit contract_relpath "$(repo_contract_relpath governance.setup_entrypoints)" && '
                'source ./scripts/lib/image_env.sh && '
                'emit image_pin "$IMAGE_ENV_PIN_FILE" && '
                'emit runtime_image_pin "$IMAGE_ENV_RUNTIME_PIN_FILE" && '
                'source ./scripts/setup/lib/setup_cli_common.sh && '
                'emit setup_entrypoints "$SETUP_CLI_SETUP_ENTRYPOINTS_PATH" && '
                'emit deploy_stage "$SETUP_CLI_DEPLOY_STAGE_PATH" && '
                'emit full_test_manifest "$SETUP_CLI_FULL_TEST_MANIFEST_REL_PATH" && '
                'source ./scripts/runtime/runtime_target_lib.sh && '
                'emit service_registry "$SERVICE_REGISTRY_PATH" && '
                'source ./scripts/setup/lib/deploy_stage_registry.sh && '
                'emit deploy_stage_registry "$DEPLOY_STAGE_REGISTRY_PATH" && '
                'repo_contract_assign_path runtime_manifest runtime.testing_manifest && '
                'repo_contract_assign_relpath setup_entrypoints_rel governance.setup_entrypoints && '
                'emit assigned_runtime_manifest "$runtime_manifest" && '
                'emit assigned_setup_entrypoints_rel "$setup_entrypoints_rel" && '
                'explicit_path=/tmp/custom-pin.env && '
                'explicit_rel=custom/runtime.env && '
                'repo_contract_default_path explicit_path image_pins.runtime && '
                'repo_contract_default_relpath explicit_rel image_pins.runtime && '
                'emit default_explicit_path "$explicit_path" && '
                'emit default_explicit_rel "$explicit_rel" && '
                f'source {json.dumps((repo_root / "scripts" / "lib" / "repo_contracts.sh").resolve().as_posix())} && '
                'repo_contracts_load_cache && '
                f'test ! -e {json.dumps((repo_root / "pwned").resolve().as_posix())} && '
                "emit malicious_relpath \"${OPENCLAW_REPO_CONTRACT_REL_PATHS['runtime.bad$(touch pwned)']}\""
            ),
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr or result.stdout)
        outputs: dict[str, list[str]] = {}
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            key, value = line.split('\t', 1)
            outputs.setdefault(key, []).append(value)
        cls._root_outputs = outputs
        if (repo_root / 'pwned').exists():
            raise AssertionError('repo contract shell helper unexpectedly executed contract id payload')

    @classmethod
    def tearDownClass(cls) -> None:
        cm = cls._malicious_repo_cm
        cls._malicious_repo_cm = None
        cls._malicious_repo_root = None
        if cm is not None:
            cm.cleanup()
        super().tearDownClass()

    def _root_value(self, key: str) -> str:
        return self._root_outputs[key][0]

    def _normalize_path(self, value: str) -> str:
        normalized = str(value).replace('\\', '/')
        if len(normalized) >= 3 and normalized[0] == '/' and normalized[1].isalpha() and normalized[2] == '/':
            normalized = f'{normalized[1]}:/{normalized[3:]}'
        return os.path.normcase(os.path.normpath(str(Path(normalized))))

    @staticmethod
    def _truth_text(contracts: list[dict[str, str]], *, comment_prefix: str = '') -> str:
        payload = json.dumps({'schema_version': 1, 'contracts': contracts}, ensure_ascii=False, indent=2)
        return f'{comment_prefix}{payload}\n'

    def _write_shell_helper_fixture(self, repo_root: Path, truth_text: str) -> Path:
        helper_path = repo_root / 'scripts' / 'lib'
        truth_dir = repo_root / 'config' / 'governance' / 'support'
        helper_path.mkdir(parents=True, exist_ok=True)
        truth_dir.mkdir(parents=True, exist_ok=True)
        (helper_path / 'repo_contracts.sh').write_text(
            (ROOT_DIR / 'scripts' / 'lib' / 'repo_contracts.sh').read_text(encoding='utf-8'),
            encoding='utf-8',
        )
        (helper_path / 'repo_root.sh').write_text(
            (ROOT_DIR / 'scripts' / 'lib' / 'repo_root.sh').read_text(encoding='utf-8'),
            encoding='utf-8',
        )
        (truth_dir / 'repo_contracts.json').write_text(truth_text, encoding='utf-8')
        return helper_path / 'repo_contracts.sh'

    def test_repo_contract_awk_fallback_is_self_contained(self) -> None:
        source = (ROOT_DIR / 'scripts' / 'lib' / 'repo_contracts.sh').read_text(encoding='utf-8')

        self.assertIn('OPENCLAW_REPO_CONTRACTS_FORCE_AWK', source)
        self.assertIn('repo_contracts_truth_records_awk()', source)
        self.assertIn('repo_contracts_truth_records_awk "$truth_path"', source)

    def test_repo_contract_awk_fallback_fails_closed_on_invalid_truth(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir).resolve()
            helper = self._write_shell_helper_fixture(repo_root, '{"schema_version": 1, "contracts": [\n')
            result = run_bash_command(
                ROOT_DIR,
                (
                    'unset PYTHONPATH PYTHONHOME && '
                    'export OPENCLAW_REPO_CONTRACTS_FORCE_AWK=1 && '
                    f'source {json.dumps(helper.as_posix())} && '
                    'repo_contracts_load_cache'
                ),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('repo contracts truth', result.stderr)

    def test_repo_contract_bridge_and_consumers_resolve_expected_paths(self) -> None:
        self.assertEqual(
            self._normalize_path(self._root_value('contract_path')),
            self._normalize_path(str(repo_contract_path('runtime.testing_manifest'))),
        )
        self.assertEqual(self._root_value('contract_relpath'), repo_contract_relpath('governance.setup_entrypoints'))
        self.assertEqual(
            self._normalize_path(self._root_value('image_pin')),
            self._normalize_path(str(repo_contract_path('image_pins.openclaw'))),
        )
        self.assertEqual(
            self._normalize_path(self._root_value('runtime_image_pin')),
            self._normalize_path(str(repo_contract_path('image_pins.runtime'))),
        )
        self.assertEqual(
            self._normalize_path(self._root_value('setup_entrypoints')),
            self._normalize_path(str(repo_contract_path('governance.setup_entrypoints'))),
        )
        self.assertEqual(
            self._normalize_path(self._root_value('deploy_stage')),
            self._normalize_path(str(repo_contract_path('governance.deploy_stage_flow'))),
        )
        self.assertEqual(self._root_value('full_test_manifest'), repo_contract_relpath('runtime.testing_manifest'))
        self.assertEqual(
            self._normalize_path(self._root_value('service_registry')),
            self._normalize_path(str(repo_contract_path('runtime.service_registry'))),
        )
        self.assertEqual(
            self._normalize_path(self._root_value('deploy_stage_registry')),
            self._normalize_path(str(repo_contract_path('governance.deploy_stage_flow'))),
        )

    def test_repo_contract_shell_eval_boundary_does_not_execute_contract_ids(self) -> None:
        self.assertEqual(self._root_value('malicious_relpath'), 'config/runtime/testing_manifest.json')
        repo_root = self._malicious_repo_root
        self.assertIsNotNone(repo_root)
        self.assertFalse((repo_root / 'pwned').exists())

    def test_repo_contract_assignment_helpers_resolve_expected_values(self) -> None:
        self.assertEqual(
            self._normalize_path(self._root_value('assigned_runtime_manifest')),
            self._normalize_path(str(repo_contract_path('runtime.testing_manifest'))),
        )
        self.assertEqual(self._root_value('assigned_setup_entrypoints_rel'), repo_contract_relpath('governance.setup_entrypoints'))

    def test_repo_contract_default_helpers_preserve_explicit_override(self) -> None:
        self.assertEqual([self._root_value('default_explicit_path'), self._root_value('default_explicit_rel')], ['/tmp/custom-pin.env', 'custom/runtime.env'])


if __name__ == '__main__':
    unittest.main()
