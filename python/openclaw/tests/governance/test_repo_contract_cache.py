"""验证仓库路径合同由所有者跟随真源版本刷新，不依赖文档批次清理缓存。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from openclaw.lib.repo.contracts import CONTRACTS_TRUTH_REL_PATH, repo_contract, repo_contract_path


def _write_contract(root: Path, relative_path: str) -> Path:
    """在临时根内写入最小合同真源。

    参数：
        root（Path）：测试仓库边界。
        relative_path（str）：测试合同应解析的仓内路径。
    返回：
        Path：写入的合同真源路径。
    副作用：
        创建临时合同目录并写入 JSON，不改动项目的正式真源。
    """
    path = root / CONTRACTS_TRUTH_REL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'contracts': [{'id': 'fixture.page', 'relative_path': relative_path, 'format': 'json'}]}), encoding='utf-8')
    return path


class RepoContractCacheTest(unittest.TestCase):
    """使用公开合同 API 验证真实文件变更与根目录隔离。"""

    def test_same_size_truth_edit_refreshes_public_loader(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            truth = _write_contract(root, 'first.json')
            original = repo_contract('fixture.page', root_dir=root)
            self.assertIs(original, repo_contract('fixture.page', root_dir=root))
            timestamp = truth.stat().st_mtime_ns
            _write_contract(root, 'other.json')
            os.utime(truth, ns=(timestamp + 1_000_000_000, timestamp + 1_000_000_000))
            self.assertEqual(repo_contract_path('fixture.page', root_dir=root), root / 'other.json')

    def test_replacement_with_same_time_and_size_refreshes_contract_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            truth = _write_contract(root, 'first.json')
            repo_contract_path('fixture.page', root_dir=root)
            metadata = truth.stat()
            replacement = truth.with_name('replacement.json')
            replacement.write_text(truth.read_text(encoding='utf-8').replace('first.json', 'other.json'), encoding='utf-8')
            os.utime(replacement, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
            replacement.replace(truth)
            self.assertEqual(repo_contract_path('fixture.page', root_dir=root), root / 'other.json')

    def test_deleted_or_invalid_truth_cannot_reuse_valid_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            truth = _write_contract(root, 'first.json')
            repo_contract_path('fixture.page', root_dir=root)
            truth.write_text('not-json', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'JSON 无法解析'):
                repo_contract_path('fixture.page', root_dir=root)
            truth.unlink()
            with self.assertRaisesRegex(ValueError, 'truth is missing'):
                repo_contract_path('fixture.page', root_dir=root)

    def test_cached_contracts_remain_isolated_between_repository_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / 'first', Path(directory) / 'second'
            _write_contract(first, 'first.json')
            _write_contract(second, 'second.json')
            self.assertEqual(repo_contract_path('fixture.page', root_dir=first), first / 'first.json')
            self.assertEqual(repo_contract_path('fixture.page', root_dir=second), second / 'second.json')


if __name__ == '__main__':
    unittest.main()
