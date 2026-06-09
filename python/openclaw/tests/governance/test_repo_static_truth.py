from __future__ import annotations

import contextlib
import io
import tempfile
import json
import unittest
from pathlib import Path

from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo import static_truth
from openclaw.lib.repo.static_truth import (
    REPO_CONTRACTS,
    read_repo_contract_env,
    read_repo_contract_json,
    repo_contract_path,
    repo_contract_relpath,
)
from openclaw.lib.repo.managed_extensions import managed_explicit_extensions
from openclaw.setup.flow import deploy_flow, one_click_deploy, one_click_test
from openclaw.tests.support.static_text_assertions import assert_static_text_absent


ROOT_DIR = resolve_repo_root(Path(__file__))


class RepoStaticTruthTest(unittest.TestCase):
    def _truth_text(self, contracts: list[dict[str, str]], *, comment_prefix: str = '') -> str:
        payload = json.dumps({'schema_version': 1, 'contracts': contracts}, ensure_ascii=False, indent=2)
        return f'{comment_prefix}{payload}\n'

    def _run_static_truth_cli(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            returncode = static_truth.main(argv)
        return returncode, stdout.getvalue(), stderr.getvalue()

    def test_repo_contracts_resolve_existing_files(self) -> None:
        for contract_id, contract in REPO_CONTRACTS.items():
            with self.subTest(contract_id=contract_id):
                path = repo_contract_path(contract_id)
                self.assertEqual(path, ROOT_DIR / contract.relative_path)
                self.assertTrue(path.is_file(), msg=contract_id)

    def test_read_helpers_match_repo_payloads(self) -> None:
        testing_manifest = read_repo_contract_json('runtime.testing_manifest')
        self.assertIsInstance(testing_manifest, dict)
        self.assertIn('execution_order', testing_manifest)

        runtime_pins = read_repo_contract_env('image_pins.runtime')
        self.assertIn('OPENCLAW_RUNTIME_PYTHON_IMAGE', runtime_pins)
        self.assertIn('NGINX_IMAGE', runtime_pins)

    def test_cli_path_and_relpath_match_python_api(self) -> None:
        path_returncode, path_stdout, path_stderr = self._run_static_truth_cli(['path', '--id', 'runtime.testing_manifest'])
        relpath_returncode, relpath_stdout, relpath_stderr = self._run_static_truth_cli(['relpath', '--id', 'governance.setup_entrypoints'])

        self.assertEqual(path_returncode, 0, msg=path_stderr)
        self.assertEqual(relpath_returncode, 0, msg=relpath_stderr)
        self.assertEqual(path_stdout.strip(), str(repo_contract_path('runtime.testing_manifest')))
        self.assertEqual(relpath_stdout.strip(), repo_contract_relpath('governance.setup_entrypoints'))

    def test_cli_rejects_unknown_contract_id(self) -> None:
        returncode, _stdout, stderr = self._run_static_truth_cli(['path', '--id', 'unknown.contract'])

        self.assertEqual(returncode, 2)
        self.assertIn('repo contract id', stderr)

    def test_python_contract_loader_accepts_utf8_bom_truth_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            truth_dir = repo_root / 'config' / 'governance' / 'support'
            truth_dir.mkdir(parents=True, exist_ok=True)
            (truth_dir / 'repo_contracts.json').write_text(
                self._truth_text(
                    [
                        {
                            'id': 'runtime.testing_manifest',
                            'relative_path': 'config/runtime/testing_manifest.json',
                            'format': 'json',
                        }
                    ],
                    comment_prefix='# repo contracts truth\n',
                ),
                encoding='utf-8-sig',
            )

            self.assertEqual(
                repo_contract_relpath('runtime.testing_manifest', root_dir=repo_root),
                'config/runtime/testing_manifest.json',
            )

    def test_entrypoint_gate_truth_references_contract_relpaths(self) -> None:
        self.assertIn(repo_contract_relpath('governance.deploy_stage_flow'), one_click_deploy.REQUIRED_FILES)
        self.assertIn(repo_contract_relpath('governance.setup_entrypoints'), one_click_test.BASIC_REQUIRED_FILES)
        self.assertIn(repo_contract_relpath('runtime.testing_manifest'), one_click_test.FULL_REQUIRED_FILES)
        self.assertNotIn('scripts/lib/flow_preflight_shell.sh', one_click_deploy.REQUIRED_FILES)

    def test_one_click_deploy_bootstrap_json_batches_static_context(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            returncode = one_click_deploy.main(['bootstrap-json'])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(returncode, 0)
        self.assertEqual(payload['schemaVersion'], 1)
        self.assertEqual(payload['entrypoint'], 'one_click_deploy')
        self.assertEqual(payload['preflight']['status'], 'ok')
        self.assertEqual(payload['resume']['status'], 'ok')
        self.assertIn('docker_compose_config', payload['effectiveStages'])
        self.assertEqual(payload['paths']['env-file-path'], 'deploy/.env')
        self.assertEqual(payload['paths']['deploy-stage-runner-script-path'], 'scripts/setup/lib/deploy_stage_runner.sh')
        self.assertIn('runtime-host-env-path', payload['paths'])
        self.assertIn('default-log-dir', payload['paths'])

    def test_deploy_effective_stages_have_execution_mappings(self) -> None:
        manifest = deploy_flow.stage_manifest()
        stages = manifest.get('stages') if isinstance(manifest.get('stages'), dict) else {}
        option_sets = (
            {'mode': 'online', 'releaseCheck': '1', 'browserVerify': '1', 'startServices': '1', 'stage': '', 'imageArchivePath': ''},
            {'mode': 'offline', 'releaseCheck': '0', 'browserVerify': '1', 'startServices': '1', 'stage': '', 'imageArchivePath': ''},
            {'mode': 'online', 'releaseCheck': '0', 'browserVerify': '0', 'startServices': '0', 'stage': '', 'imageArchivePath': ''},
        )
        for options in option_sets:
            for stage in deploy_flow.effective_stages(options):
                with self.subTest(mode=options['mode'], stage=stage):
                    self.assertIn(stage, stages)
                    self.assertIsInstance(stages[stage].get('execution'), dict)

    def test_setup_entrypoint_references_are_unique_and_current(self) -> None:
        payload = read_repo_contract_json('governance.setup_entrypoints')
        blocks = [payload.get('help_surface_contract') or {}, *((payload.get('entrypoints') or {}).values())]
        for block in blocks:
            refs = [str(item) for item in (block.get('references') or [])]
            with self.subTest(title=block.get('title') or 'help_surface_contract'):
                self.assertEqual(refs, list(dict.fromkeys(refs)))
                self.assertFalse(any('deployment.md' in item for item in refs))
        assert_static_text_absent(self, 'deployment.md', json.dumps(payload, ensure_ascii=False))

    def test_control_plane_medium_references_are_unique_and_current(self) -> None:
        payload = read_repo_contract_json('setup.control_plane_medium')
        refs = [str(item) for item in ((payload.get('entrypoint') or {}).get('references') or [])]

        self.assertEqual(refs, list(dict.fromkeys(refs)))
        self.assertFalse(any('deployment.md' in item for item in refs))

    def test_deployment_surfaces_have_no_retired_flow_residue(self) -> None:
        forbidden = (
            'deployment.md',
            '默认四步',
            '四步主链',
            '当前入口直接',
            '本脚本直接复用',
            '直接复用该门禁',
            '只承接 basic gate 已通过',
            'basic gate 已通过后执行正式部署',
            '手工执行 one_click_test_basic',
        )
        scanned_roots = (
            ROOT_DIR / 'docs',
            ROOT_DIR / 'config',
            ROOT_DIR / 'scripts',
            ROOT_DIR / 'python' / 'openclaw' / 'setup',
            ROOT_DIR / 'deploy',
        )
        violations: list[str] = []
        for root in scanned_roots:
            for path in root.rglob('*'):
                if not path.is_file() or path.suffix in {'.png', '.jpg', '.jpeg', '.gif', '.pyc'}:
                    continue
                text = path.read_text(encoding='utf-8', errors='ignore')
                for marker in forbidden:
                    if marker in text:
                        violations.append(f'{path.relative_to(ROOT_DIR)}: {marker}')
        self.assertEqual([], violations)

    def test_platform_docs_do_not_name_managed_business_extensions(self) -> None:
        tokens = tuple(row.id for row in managed_explicit_extensions(ROOT_DIR))
        scanned_roots = (
            ROOT_DIR / 'README.md',
            ROOT_DIR / 'agent' / 'README.md',
            ROOT_DIR / 'agent' / 'governance',
            ROOT_DIR / 'docs',
            ROOT_DIR / 'config' / 'governance' / 'docs',
        )
        violations: list[str] = []
        for root in scanned_roots:
            paths = (root,) if root.is_file() else tuple(root.rglob('*'))
            for path in paths:
                if not path.is_file() or path.suffix in {'.png', '.jpg', '.jpeg', '.gif', '.pyc'}:
                    continue
                rel_path = path.relative_to(ROOT_DIR).as_posix()
                content = path.read_text(encoding='utf-8', errors='ignore')
                for token in tokens:
                    if token in content:
                        violations.append(f'{rel_path}: {token}')
        self.assertEqual([], violations)


if __name__ == '__main__':
    unittest.main()
