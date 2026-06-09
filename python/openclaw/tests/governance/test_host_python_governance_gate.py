from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from openclaw.doctor.agent_modules.support import resolve_bash_executable
from openclaw.doctor.platform import host_python_governance
from openclaw.guards.host_python_doc_guard import _has_uncovered_doc_pattern_match
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.tests.support.static_text_assertions import assert_static_text_absent

ROOT_DIR = resolve_repo_root(Path(__file__))
CHECK_SCRIPT = ROOT_DIR / 'scripts' / 'doctor' / 'check_host_python_governance.sh'
HOST_PYTHON_GOVERNANCE_CONFIG = ROOT_DIR / 'config' / 'governance' / 'support' / 'host_python_governance.json'


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


class HostPythonGovernanceGateTest(unittest.TestCase):
    def _posix_ere_search(self, pattern: str, line: str) -> bool:
        python_pattern = pattern.replace('[[:space:]]', r'\s').replace('[[:alnum:]]', 'A-Za-z0-9')
        return re.search(python_pattern, line) is not None

    def test_agent_launcher_contract_function_passes_through_real_source_chain(self) -> None:
        source = (ROOT_DIR / 'scripts' / 'agent_runtime' / 'run_agent_entrypoint.sh').read_text(encoding='utf-8')
        guard_source = (ROOT_DIR / 'scripts' / 'lib' / 'python_runtime_guard.sh').read_text(encoding='utf-8')

        self.assertIn(
            'scripts/runtime/run_openclaw_python_tool.sh" control-plane runtime scheduler-run-agent-runtime --config-path "$RESOLVED_CONFIG_PATH" --agent-ref "$AGENT_REF" -- "$@"',
            source,
        )
        self.assertIn('--config-path "$RESOLVED_CONFIG_PATH"', source)
        self.assertIn('--agent-ref "$AGENT_REF"', source)
        self.assertIn('-- "$@"', source)
        self.assertIn('--config-path "$RESOLVED_CONFIG_PATH"', guard_source)
        assert_static_text_absent(self, 'scripts/control_plane/run_registered_agent_runtime.sh', source)

    def test_python_runtime_verify_checks_scheduler_container_source_consistency(self) -> None:
        source = (ROOT_DIR / 'scripts' / 'runtime' / 'verify_python_runtime_container.sh').read_text(encoding='utf-8')

        self.assertIn('verify_scheduler_source_mount_consistency()', source)
        self.assertIn('/opt/openclaw-tools', source)
        self.assertIn('find scripts python agent config docs', source)
        self.assertIn('scheduler 容器内外源码不一致', source)

    def test_runner_surface_contract_matches_declared_runner_entries(self) -> None:
        payload = json.loads(HOST_PYTHON_GOVERNANCE_CONFIG.read_text(encoding='utf-8'))
        covered_paths: set[str] = set()

        for row in payload['runner_binding_manifest']:
            rel_path = str(row['relPath'])
            root_var = str(row['rootVar'])
            source = (ROOT_DIR / rel_path).read_text(encoding='utf-8')
            pattern = re.compile(rf'^PYTHON_RUNNER="\${re.escape(root_var)}/scripts/runtime/run_python_container\.sh"$', re.MULTILINE)
            self.assertRegex(source, pattern, msg=rel_path)
            covered_paths.add(rel_path)

        for row in payload['runner_surface_literal_manifest']:
            rel_path = str(row['relPath'])
            literal = str(row['literal'])
            self.assertIn(literal, (ROOT_DIR / rel_path).read_text(encoding='utf-8'), msg=rel_path)
            covered_paths.add(rel_path)

        covered_paths.update(str(item).strip() for item in payload['runner_surface_allowlist'] if str(item).strip())
        actual_refs = {
            path.relative_to(ROOT_DIR).as_posix()
            for path in (ROOT_DIR / 'scripts').rglob('*.sh')
            if 'run_python_container.sh' in path.read_text(encoding='utf-8')
        }
        self.assertEqual(actual_refs - covered_paths, set())

    def test_shell_wrapper_keeps_help_surface(self) -> None:
        bash_executable = resolve_bash_executable()
        self.assertIsNotNone(bash_executable)
        result = subprocess.run(
            [str(bash_executable), str(CHECK_SCRIPT), '--help'],
            cwd=ROOT_DIR,
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            env=dict(os.environ),
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn('check_host_python_governance.sh', result.stdout)

    def _config_payload(self, *, mode: str) -> dict[str, object]:
        return {
            'schemaVersion': 1,
            'mode': mode,
            'baselinePath': 'config/governance/validation/host_python_baseline.json',
            'shell_scan_manifest': {'self': [], 'skip': []},
            'doc_scan_roots': ['docs', 'agent/extensions'],
            'doc_category_roots': {
                'generated_doc_example': [],
                'extension_doc_example': ['agent/extensions'],
            },
            'shell_indirect_patterns': [
                {
                    'id': 'python_bin_exec_upper',
                    'pattern': '(^|[[:space:]])(exec[[:space:]]+)?\\\"?\\$\\{?PYTHON_BIN\\}?\\\"?[[:space:]]+-B([[:space:]]|$)',
                }
            ],
            'doc_command_policy': {
                'allowed_families': ['repo_host', 'unittest_openclaw'],
            },
        }

    def test_observe_mode_allows_supported_repo_root_unittest_example_without_baseline_hit(self) -> None:
        line = 'python -m unittest openclaw.tests.governance.test_package_layout -q'

        self.assertFalse(_has_uncovered_doc_pattern_match(line))

    def test_observe_mode_fails_for_new_doc_hit(self) -> None:
        line = 'python -m openclaw.doctor.platform.architecture_import_guards'

        self.assertTrue(_has_uncovered_doc_pattern_match(line))

    def test_observe_mode_fails_for_mixed_supported_and_disallowed_doc_hit_on_same_line(self) -> None:
        line = 'python -m openclaw.cli ; python -m openclaw.doctor.platform.architecture_import_guards'

        self.assertTrue(_has_uncovered_doc_pattern_match(line))

    def test_observe_mode_fails_for_wrong_python_namespace_doc_hit(self) -> None:
        line = 'python -m unittest python.openclaw.tests.governance.test_delivery_cleanliness -q'

        self.assertTrue(_has_uncovered_doc_pattern_match(line))

    def test_shell_indirect_exec_is_reported(self) -> None:
        payload = self._config_payload(mode='observe')
        patterns = payload['shell_indirect_patterns']
        self.assertIsInstance(patterns, list)
        pattern = next(item['pattern'] for item in patterns if item['id'] == 'python_bin_exec_upper')

        self.assertTrue(self._posix_ere_search(str(pattern), 'exec "$PYTHON_BIN" -B -m openclaw.demo'))

    def test_enforce_mode_fails_even_for_baseline_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            target = repo_root / 'docs' / 'guide.md'
            target.parent.mkdir(parents=True)
            line = 'python -m openclaw.doctor.platform.architecture_import_guards'
            target.write_text(line + '\n', encoding='utf-8')
            config_path = repo_root / 'config' / 'governance' / 'support' / 'host_python_governance.json'
            baseline_path = repo_root / 'config' / 'governance' / 'validation' / 'host_python_baseline.json'
            _write_json(config_path, self._config_payload(mode='enforce'))
            _write_json(
                baseline_path,
                {
                    'schemaVersion': 1,
                    'categories': {
                        'shell_exec': [],
                        'shell_indirect_exec': [],
                        'doc_example': [f'docs/guide.md::{line}'],
                        'generated_doc_example': [],
                        'extension_doc_example': [],
                    },
                },
            )

            payload = host_python_governance.build_report(repo_root, config_path=config_path)

        self.assertEqual(payload['status'], 'fail')
        self.assertEqual(payload['summary']['newCount'], 1)

    def test_doc_scan_uses_command_family_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            target = repo_root / 'docs' / 'guide.md'
            target.parent.mkdir(parents=True)
            line = 'python -m unittest openclaw.tests.governance.test_package_layout -q'
            target.write_text(line + '\n', encoding='utf-8')
            config_path = repo_root / 'config' / 'governance' / 'support' / 'host_python_governance.json'
            baseline_path = repo_root / 'config' / 'governance' / 'validation' / 'host_python_baseline.json'
            payload = self._config_payload(mode='enforce')
            payload['doc_command_policy'] = {'allowed_families': ['repo_host']}
            _write_json(config_path, payload)
            _write_json(
                baseline_path,
                {
                    'schemaVersion': 1,
                    'categories': {
                        'shell_exec': [],
                        'shell_indirect_exec': [],
                        'doc_example': [],
                        'generated_doc_example': [],
                        'extension_doc_example': [],
                    },
                },
            )

            report = host_python_governance.build_report(repo_root, config_path=config_path)

        self.assertEqual(report['status'], 'fail')
        self.assertEqual(report['newViolations'][0]['text'], 'python -m unittest openclaw.tests.governance.test_package_layout')


if __name__ == '__main__':
    unittest.main()
