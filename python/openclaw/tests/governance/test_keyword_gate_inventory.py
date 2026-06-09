from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from openclaw.doctor.platform import keyword_gate_inventory
from openclaw.lib.repo.layout import resolve_repo_root


ROOT_DIR = resolve_repo_root(Path(__file__))


def _assert_not_in_call() -> str:
    return 'assert' + 'NotIn('


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _config(*, mode: str = 'enforce') -> dict[str, object]:
    return {
        'schemaVersion': 2,
        'mode': mode,
        'scanRoots': [{'path': 'python', 'includeSuffixes': ['.py']}],
        'excludePaths': [],
        'blockedGatePatterns': [
            {
                'kind': 'python_static_text_assert_not_in',
                'regex': r'assertNotIn\(',
                'scanRoots': ['python'],
                'includeSuffixes': ['.py'],
                'domain': 'python_tests',
                'replacement': 'structured validator',
            }
        ],
        'structuredSentinelPatterns': [
            {
                'kind': 'sentinel_token',
                'regex': r'SENTINEL_TOKEN',
                'scanRoots': ['python'],
                'includeSuffixes': ['.py'],
                'domain': 'fixture',
                'classification': 'sentinel',
                'replacement': 'structured truth',
            }
        ],
        'behaviorAssertionPatterns': [
            {
                'kind': 'behavior_token',
                'regex': r'BEHAVIOR_TOKEN',
                'scanRoots': ['python'],
                'includeSuffixes': ['.py'],
                'domain': 'fixture',
                'classification': 'behavior',
                'replacement': 'behavior assertion',
            }
        ],
    }


class KeywordGateInventoryTest(unittest.TestCase):
    def test_current_repo_inventory_has_no_blocked_gates(self) -> None:
        report = keyword_gate_inventory.build_report(ROOT_DIR, include_informational=False)

        self.assertEqual(report['status'], 'ok')
        self.assertEqual(report['mode'], 'enforce')
        self.assertEqual(report['summary']['blockedEntryCount'], 0)
        self.assertEqual(report['summary']['currentHitCount'], 0)
        self.assertEqual(report['blockedGates'], [])

    def test_blocked_gate_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / 'inventory.json'
            target = root / 'python' / 'demo_test.py'
            target.parent.mkdir(parents=True)
            target.write_text(f"self.{_assert_not_in_call()}'old', source)\n", encoding='utf-8')
            _write_json(config_path, _config())

            report = keyword_gate_inventory.build_report(root, config_path)

        self.assertEqual(report['status'], 'fail')
        self.assertEqual(report['summary']['blockedEntryCount'], 1)
        self.assertEqual(report['blockedGates'][0]['currentCount'], 1)

    def test_sentinel_and_behavior_hits_do_not_count_as_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / 'inventory.json'
            target = root / 'python' / 'demo_test.py'
            target.parent.mkdir(parents=True)
            target.write_text('SENTINEL_TOKEN\nBEHAVIOR_TOKEN\n', encoding='utf-8')
            _write_json(config_path, _config())

            report = keyword_gate_inventory.build_report(root, config_path)

        self.assertEqual(report['status'], 'ok')
        self.assertEqual(report['summary']['currentHitCount'], 0)
        self.assertEqual(report['summary']['sentinelHitCount'], 1)
        self.assertEqual(report['summary']['behaviorHitCount'], 1)
        self.assertEqual(report['blockedGates'], [])

    def test_repo_config_catches_new_static_text_assertion_gate(self) -> None:
        payload = json.loads(keyword_gate_inventory.DEFAULT_CONFIG_PATH.read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / 'inventory.json'
            target = root / 'python' / 'openclaw' / 'tests' / 'governance' / 'demo_test.py'
            target.parent.mkdir(parents=True)
            target.write_text(f"source = 'old gate'\nself.{_assert_not_in_call()}'old', source)\n", encoding='utf-8')
            _write_json(config_path, payload)

            report = keyword_gate_inventory.build_report(root, config_path)

        self.assertEqual(report['status'], 'fail')
        self.assertEqual(report['blockedGates'][0]['kind'], 'python_static_text_assert_not_in')

    def test_repo_config_keeps_behavior_assertion_out_of_blocked(self) -> None:
        payload = json.loads(keyword_gate_inventory.DEFAULT_CONFIG_PATH.read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / 'inventory.json'
            target = root / 'python' / 'openclaw' / 'tests' / 'governance' / 'demo_test.py'
            target.parent.mkdir(parents=True)
            target.write_text(f"self.{_assert_not_in_call()}'Traceback', result.stderr)\n", encoding='utf-8')
            _write_json(config_path, payload)

            report = keyword_gate_inventory.build_report(root, config_path)

        self.assertEqual(report['status'], 'ok')
        self.assertEqual(report['summary']['currentHitCount'], 0)
        self.assertEqual(report['summary']['behaviorHitCount'], 1)


if __name__ == '__main__':
    unittest.main()
