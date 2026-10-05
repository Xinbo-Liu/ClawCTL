"""验证文档批次遇到单项合同或文件异常时仍产出完整诊断。"""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import unittest
from unittest.mock import Mock, patch

from openclaw.docs.validators import batch


class DocumentationBatchTest(unittest.TestCase):
    def test_validator_exception_is_reported_without_skipping_remaining_checks(self) -> None:
        for error in (ValueError('导航数量无效'), OSError('页面读取失败'), SystemExit('合同无效')):
            with self.subTest(error=type(error).__name__):
                output = io.StringIO()
                following = Mock(return_value=0)
                checks = (('broken', Mock(side_effect=error)), ('following', following))
                with patch.object(batch, 'CHECKS', checks), redirect_stdout(output):
                    self.assertEqual(batch.main([]), 1)
                payload = json.loads(output.getvalue())
                self.assertEqual(payload['status'], 'FAIL')
                self.assertEqual([item['status'] for item in payload['checks']], ['FAIL', 'PASS'])
                self.assertIn(str(error), payload['checks'][0]['detail'])
                following.assert_called_once_with([])

    def test_successful_checks_keep_single_json_report(self) -> None:
        output = io.StringIO()
        with patch.object(batch, 'CHECKS', (('healthy', Mock(return_value=0)),)), redirect_stdout(output):
            self.assertEqual(batch.main([]), 0)
        self.assertEqual(json.loads(output.getvalue())['status'], 'PASS')


if __name__ == '__main__':
    unittest.main()
