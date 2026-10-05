"""验证中文与组合字符的表格源码对齐，以及重复格式化的稳定性。"""
from __future__ import annotations

import unittest

from openclaw.docs.support.markdown_tables import _display_width, format_markdown_tables


class MarkdownTablesTest(unittest.TestCase):
    def test_unicode_width_counts_display_columns(self) -> None:
        for text, width in (('中A', 3), ('Ａ', 2), ('e\u0301', 1), ('✅', 2), ('☑️', 2), ('👩\u200d💻', 2), ('👍🏽', 2), ('🇨🇳', 2)):
            with self.subTest(text=text):
                self.assertEqual(_display_width(text), width)

    def test_chinese_padding_uses_display_width_and_is_idempotent(self) -> None:
        source = '| 项目 | A |\n| --- | --- |\n| 中文 | xy |\n| x | z |\n'
        expected = '| 项目 | A  |\n|------|----|\n| 中文 | xy |\n| x    | z  |\n'
        rendered = format_markdown_tables(source)
        self.assertEqual(rendered, expected)
        self.assertEqual(format_markdown_tables(rendered), rendered)
        self.assertEqual({_display_width(row) for row in rendered.splitlines()}, {13})

    def test_emoji_clusters_align_without_extra_padding_on_repeat(self) -> None:
        source = '| X | Y |\n| --- | --- |\n| 👩\u200d💻 | e\u0301 |\n| 👍🏽 | ☑️ |\n'
        expected = '| X  | Y  |\n|----|----|\n| 👩\u200d💻 | e\u0301  |\n| 👍🏽 | ☑️ |\n'
        rendered = format_markdown_tables(source)
        self.assertEqual(rendered, expected)
        self.assertEqual(format_markdown_tables(rendered), rendered)

    def test_alignment_and_fenced_examples_are_preserved(self) -> None:
        sample = '| 中 | x |\r\n| :--- | ---: |\r\n| a | 全角 |\r\n'
        fenced = '```markdown\r\n| 原样 | 保留 |\r\n| --- | --- |\r\n```\r\n'
        rendered = format_markdown_tables(sample + '\r\n' + fenced)
        self.assertIn('| a  | 全角 |', rendered)
        self.assertTrue(rendered.endswith(fenced))
        self.assertEqual(format_markdown_tables(rendered), rendered)


if __name__ == '__main__':
    unittest.main()
