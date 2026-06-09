from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from openclaw.doctor.platform import docstring_governance

ROOT_DIR = Path(__file__).resolve().parents[4]


class PlatformDocstringGovernanceTest(unittest.TestCase):
    def test_current_repo_matches_platform_docstring_baseline(self) -> None:
        report = docstring_governance.build_report(ROOT_DIR)
        baseline = docstring_governance.load_baseline(docstring_governance.DEFAULT_BASELINE_PATH)

        self.assertEqual(docstring_governance.compare_with_baseline(report, baseline), [])

    def test_new_public_file_requires_chinese_docstrings(self) -> None:
        with TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            package_dir = repo_root / 'python' / 'openclaw' / 'demo'
            package_dir.mkdir(parents=True)
            (package_dir / 'sample.py').write_text(
                '"""English only."""\n\n'
                'def run(value: str) -> str:\n'
                '    return value\n',
                encoding='utf-8',
            )
            report = docstring_governance.build_report(repo_root)
            baseline = {
                'schemaVersion': 1,
                'fileBaselines': {},
                'summaryMinimums': {},
            }

            issues = docstring_governance.compare_with_baseline(report, baseline)

        self.assertTrue(any('新增平台 Python 文件缺少中文公共接口说明' in item for item in issues))

    def test_new_public_item_cannot_hide_behind_file_level_ratchet(self) -> None:
        with TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            package_dir = repo_root / 'python' / 'openclaw' / 'demo'
            package_dir.mkdir(parents=True)
            sample = package_dir / 'sample.py'
            sample.write_text(
                '"""模块说明。"""\n\n'
                'def old_missing(value: str) -> str:\n'
                '    return value\n',
                encoding='utf-8',
            )
            baseline = docstring_governance.build_baseline_payload(docstring_governance.build_report(repo_root))
            sample.write_text(
                '"""模块说明。"""\n\n'
                'def old_missing(value: str) -> str:\n'
                '    """既有函数现在已补中文说明。"""\n'
                '    return value\n\n'
                'def new_missing(value: str) -> str:\n'
                '    return value\n',
                encoding='utf-8',
            )

            issues = docstring_governance.compare_with_baseline(docstring_governance.build_report(repo_root), baseline)

        self.assertTrue(any('新增公共接口缺少中文 docstring：new_missing' in item for item in issues))

    def test_baseline_payload_is_stable_json_shape(self) -> None:
        report = {
            'scope': {'roots': ['python/openclaw']},
            'summary': {'publicChineseDocstrings': 2, 'moduleChineseDocstrings': 1},
            'files': [
                {
                    'path': 'python/openclaw/demo.py',
                    'publicMissingDocstrings': 0,
                    'publicEnglishOnlyDocstrings': 0,
                    'publicChineseDocstrings': 2,
                    'moduleHasChineseDocstring': True,
                    'publicItemDetails': [
                        {
                            'baselineKey': 'module:<module>',
                            'kind': 'module',
                            'qualname': '<module>',
                            'hasDocstring': True,
                            'hasChineseDocstring': True,
                        },
                        {
                            'baselineKey': 'function:run',
                            'kind': 'function',
                            'qualname': 'run',
                            'hasDocstring': True,
                            'hasChineseDocstring': True,
                        },
                    ],
                }
            ],
        }

        payload = docstring_governance.build_baseline_payload(report)

        self.assertEqual(payload['kind'], 'openclaw_platform_docstring_baseline')
        self.assertEqual(payload['fileBaselines']['python/openclaw/demo.py']['publicChineseDocstrings'], 2)
        self.assertIn('function:run', payload['fileBaselines']['python/openclaw/demo.py']['publicItemBaselines'])
        json.dumps(payload, ensure_ascii=False)

    def test_sharded_baseline_write_and_load_matches_monolithic_shape(self) -> None:
        with TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            package_dir = repo_root / 'python' / 'openclaw' / 'demo'
            package_dir.mkdir(parents=True)
            (package_dir / 'sample.py').write_text(
                '"""模块说明。"""\n\n'
                'def run(value: str) -> str:\n'
                '    """返回原始值。"""\n'
                '    return value\n',
                encoding='utf-8',
            )
            report = docstring_governance.build_report(repo_root)
            baseline_dir = repo_root / 'config' / 'governance' / 'validation' / 'platform_python_docstring_baseline'

            docstring_governance.write_baseline(baseline_dir, report, format_name='sharded')
            loaded = docstring_governance.load_baseline(baseline_dir)

        self.assertIn('python/openclaw/demo/sample.py', loaded['fileBaselines'])
        self.assertEqual(docstring_governance.compare_with_baseline(report, loaded), [])

    def test_sharded_baseline_rejects_index_file_count_drift(self) -> None:
        with TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            package_dir = repo_root / 'python' / 'openclaw' / 'demo'
            package_dir.mkdir(parents=True)
            (package_dir / 'sample.py').write_text('"""模块说明。"""\n', encoding='utf-8')
            baseline_dir = repo_root / 'config' / 'governance' / 'validation' / 'platform_python_docstring_baseline'
            docstring_governance.write_baseline(
                baseline_dir,
                docstring_governance.build_report(repo_root),
                format_name='sharded',
            )
            index_path = baseline_dir / docstring_governance.SHARD_INDEX_NAME
            index_payload = json.loads(index_path.read_text(encoding='utf-8'))
            index_payload['shards'][0]['fileCount'] = 999
            index_path.write_text(json.dumps(index_payload), encoding='utf-8')

            with self.assertRaisesRegex(ValueError, 'fileCount'):
                docstring_governance.load_baseline(baseline_dir)

    def test_sharded_baseline_rejects_duplicate_file_keys(self) -> None:
        with TemporaryDirectory() as tmpdir:
            baseline_dir = Path(tmpdir) / 'baseline'
            baseline_dir.mkdir()
            duplicate_file = {
                'python/openclaw/demo.py': {
                    'publicMissingDocstrings': 0,
                    'publicEnglishOnlyDocstrings': 0,
                    'publicChineseDocstrings': 1,
                    'moduleHasChineseDocstring': True,
                    'publicItemBaselines': {},
                }
            }
            (baseline_dir / docstring_governance.SHARD_INDEX_NAME).write_text(
                json.dumps(
                    {
                        'schemaVersion': 1,
                        'summaryMinimums': {},
                        'shards': [
                            {'key': 'a', 'path': 'a.json', 'fileCount': 1},
                            {'key': 'b', 'path': 'b.json', 'fileCount': 1},
                        ],
                    }
                ),
                encoding='utf-8',
            )
            for shard_key in ('a', 'b'):
                (baseline_dir / f'{shard_key}.json').write_text(
                    json.dumps(
                        {
                            'schemaVersion': 1,
                            'shardKey': shard_key,
                            'fileBaselines': duplicate_file,
                        }
                    ),
                    encoding='utf-8',
                )

            with self.assertRaisesRegex(ValueError, '重复文件基线'):
                docstring_governance.load_baseline(baseline_dir)

    def test_legacy_monolithic_baseline_is_still_readable(self) -> None:
        with TemporaryDirectory() as tmpdir:
            baseline_path = Path(tmpdir) / 'baseline.json'
            payload = {
                'schemaVersion': 1,
                'fileBaselines': {},
                'summaryMinimums': {},
            }
            baseline_path.write_text(json.dumps(payload), encoding='utf-8')

            loaded = docstring_governance.load_baseline(baseline_path)

        self.assertEqual(loaded['fileBaselines'], {})

    def test_issue_groups_expose_new_gaps_and_high_priority_items(self) -> None:
        issues = [
            'python/openclaw/control_plane/demo.py 新增公共接口缺少中文 docstring：run',
            'python/openclaw/runtime/demo.py publicChineseDocstrings 退化：0 < 1',
        ]

        groups = docstring_governance.issue_groups(issues)

        self.assertEqual(groups['newGapIssues']['count'], 1)
        self.assertEqual(groups['highPriorityIssues']['count'], 1)


if __name__ == '__main__':
    unittest.main()
