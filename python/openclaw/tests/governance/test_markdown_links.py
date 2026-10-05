"""用小型隔离文档验证本地链接和 GitHub 标题锚点解析。"""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from openclaw.docs.support.markdown_links import local_link_errors, parse_markdown, resolve_local_link


class MarkdownLinksTest(unittest.TestCase):
    """覆盖实际链接语法、代码排除、锚点与仓库边界。"""

    def test_inline_images_references_and_nested_badge_are_links(self) -> None:
        content = '\n'.join([
            '[页](page.md "标题") ![图](image.png)',
            '[引用][页] [页][] [页]',
            '[![徽章](badge.png)](page.md)',
            '[页]: <page.md#中文标题> "标题"',
        ])
        targets = [link.target for link in parse_markdown(content).links]
        self.assertEqual(targets, ['page.md', 'image.png', 'page.md#中文标题', 'page.md#中文标题', 'page.md#中文标题', 'badge.png', 'page.md'])

    def test_code_examples_and_escaped_brackets_do_not_become_links(self) -> None:
        content = '\n'.join([
            '`[inline](missing.md)`',
            '```markdown',
            '# 示例标题',
            '[fenced](missing.md)',
            '```',
            '',
            '    [indented](missing.md)',
            '',
            '\\[escaped](missing.md)',
            '[real](ok.md)',
        ])
        parsed = parse_markdown(content)
        self.assertEqual([(link.target, link.line) for link in parsed.links], [('ok.md', 10)])
        self.assertNotIn('示例标题', parsed.anchors)

    def test_headings_preserve_chinese_and_unique_suffixes(self) -> None:
        content = '\n'.join([
            '# 中文 **标题** / `API`',
            '## 重复',
            '## 重复',
            '## 重复-1',
            'Setext 标题',
            '======',
            '<a id="显式锚点"></a>',
            '<a name="legacy"></a>',
            '`<a id="code"></a>`',
        ])
        anchors = parse_markdown(content).anchors
        self.assertEqual(anchors, frozenset({'中文-标题--api', '重复', '重复-1', '重复-1-1', 'setext-标题', '显式锚点', 'legacy'}))

    def test_comments_multiline_definitions_and_nested_parentheses(self) -> None:
        parsed = parse_markdown('\n'.join([
            '<!-- [隐藏](missing.md)',
            '<a id="hidden"></a> -->',
            '`<!--` [可见](path(with).md)',
            '[跨行][ref]',
            '[ref]:',
            '  <中文 页面.md#stable> "标题"',
            '[转义](path\\(escaped\\).md)',
        ]))
        self.assertEqual([link.target for link in parsed.links], ['path(with).md', '中文 页面.md#stable', 'path(escaped).md'])
        self.assertNotIn('hidden', parsed.anchors)

    def test_local_files_images_encoded_paths_and_fragments(self) -> None:
        with tempfile.TemporaryDirectory(prefix='openclaw-doc-links-') as directory:
            root = Path(directory)
            page = root / 'README.md'
            (root / '中文 页面.md').write_text('# 中文标题\n<a id="stable"></a>\n', encoding='utf-8')
            (root / 'image.png').write_bytes(b'not-read-as-markdown')
            page.write_text('# 同页\n', encoding='utf-8')
            content = '\n'.join([
                '# 同页',
                '[页](%E4%B8%AD%E6%96%87%20%E9%A1%B5%E9%9D%A2.md#中文标题)',
                '[显式](<中文 页面.md#stable>)',
                '[同页](#同页)',
                '![图](image.png)',
                '[目录](.)',
                '[外部](https://example.test/missing#anything)',
            ])
            self.assertEqual(local_link_errors(page, content, root_dir=root), [])
            errors = local_link_errors(page, '[页](missing.md)\n[错锚点](<中文 页面.md#gone>)', root_dir=root)
            self.assertEqual(len(errors), 2)
            self.assertIn('README.md:1 链接目标不存在', errors[0])
            self.assertIn('README.md:2 链接锚点不存在', errors[1])

    def test_relative_and_encoded_traversal_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix='openclaw-doc-boundary-') as directory:
            root = Path(directory)
            page = root / 'README.md'
            for target in ('../outside.md', '%2e%2e/outside.md'):
                with self.subTest(target=target), self.assertRaises(ValueError):
                    resolve_local_link(page, target, root_dir=root)

    def test_escaped_ticks_do_not_hide_visible_links(self) -> None:
        parsed = parse_markdown('\\`literal [可见](page.md) \\` and ``[示例](missing.md)``')
        self.assertEqual([link.target for link in parsed.links], ['page.md'])

    def test_same_filename_in_two_roots_does_not_share_anchor_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix='openclaw-doc-roots-') as directory:
            base = Path(directory)
            for name, heading in (('first', '甲'), ('second', '乙')):
                root = base / name
                root.mkdir()
                page = root / 'README.md'
                (root / 'target.md').write_text(f'# {heading}\n', encoding='utf-8')
                self.assertEqual(local_link_errors(page, f'[页](target.md#{heading})', root_dir=root), [])

    def test_multiline_links_keep_opening_line_and_check_missing_targets(self) -> None:
        content = '\n'.join([
            '# 跨行链接',
            '[跨行',
            '说明](',
            '  missing.md',
            ')',
            '',
            '[引用',
            '标签][ref]',
            '[ref]: target.md',
        ])
        self.assertEqual([(link.target, link.line) for link in parse_markdown(content).links], [('missing.md', 2), ('target.md', 7)])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page = root / 'README.md'
            page.write_text(content, encoding='utf-8')
            (root / 'target.md').write_text('# 目标', encoding='utf-8')
            errors = local_link_errors(page, content, root_dir=root)
            self.assertEqual(len(errors), 1)
            self.assertIn('README.md:2 链接目标不存在：missing.md', errors[0])

    def test_multiline_inline_code_and_blank_paragraphs_are_not_links(self) -> None:
        content = '`跨行代码\n[示例](missing.md)`\n\n[不闭合\n\n标签](missing.md)\n[真实](target.md)'
        self.assertEqual([(link.target, link.line) for link in parse_markdown(content).links], [('target.md', 7)])

    def test_unmatched_code_delimiters_do_not_mask_another_paragraph(self) -> None:
        content = '`不闭合代码\n\n[真实](target.md)\n\n`'
        self.assertEqual([(link.target, link.line) for link in parse_markdown(content).links], [('target.md', 3)])
        self.assertNotIn('hidden', parse_markdown('`跨行代码\n<a id="hidden"></a>`').anchors)

    def test_destination_ranges_keep_original_offsets_through_masks_and_references(self) -> None:
        content = '\r\n'.join([
            '```markdown', '[代码](../../example)', '```',
            '<!-- [隐藏](../../hidden) -->',
            '[![图](<../../badge.png>)](', '  ../../page.md "标题"', ')',
            '[引用][ref] [ref][]', '[ref]:', '  <../../reference.md> "标题"',
            '普通路径 ../../reference.md',
        ])
        normalized = content.replace('\r\n', '\n')
        links = parse_markdown(content).links
        self.assertEqual([link.target for link in links], ['../../badge.png', '../../page.md', '../../reference.md', '../../reference.md'])
        for link in links:
            self.assertIsNotNone(link.destination_span)
            start, end = link.destination_span
            self.assertEqual(normalized[start:end], link.target)
        self.assertEqual(links[-1].destination_span, links[-2].destination_span)
        self.assertLess(links[-1].destination_span[1], normalized.rfind('../../reference.md'))

    def test_reference_definitions_inside_multiline_inline_code_are_not_registered(self) -> None:
        hidden = '`code\n[ref]: ../../private/input\ncode`\n\n[visible][ref]'
        self.assertEqual(parse_markdown(hidden).links, ())
        content = hidden + '\n\n[ref]: ../../real.md'
        links = parse_markdown(content).links
        self.assertEqual([(link.target, link.line) for link in links], [('../../real.md', 5)])
        self.assertEqual(content[slice(*links[0].destination_span)], '../../real.md')
        self.assertGreater(links[0].destination_span[0], content.index('../../private/input'))

    def test_multiline_definition_destination_and_title_are_not_scanned_as_links(self) -> None:
        content = '\r\n'.join([
            '[ref]:',
            '  <folder/[example](ghost.md).md>',
            '  "说明 [示例](title-ghost.md)"',
            '',
            '[real][ref]',
        ])
        links = parse_markdown(content).links
        self.assertEqual([(link.target, link.line) for link in links], [('folder/[example](ghost.md).md', 5)])
        normalized = content.replace('\r\n', '\n')
        self.assertEqual(normalized[slice(*links[0].destination_span)], links[0].target)

    def test_single_line_definition_can_consume_one_title_continuation(self) -> None:
        content = '[ref]: target.md\n  "说明 [示例](ghost.md)"\n\n[real][ref]'
        self.assertEqual([link.target for link in parse_markdown(content).links], ['target.md'])
        content = '[ref]: target.md "已有标题"\n  "正文 [real](other.md)"\n\n[real][ref]'
        self.assertEqual([link.target for link in parse_markdown(content).links], ['other.md', 'target.md'])

    def test_inline_code_hides_heading_syntax_but_keeps_real_heading_text(self) -> None:
        content = '\n'.join([
            '`code', '# hidden', 'hidden setext', '===', '`', '',
            '# `API` usage', '', '`SDK`指南', '====', '', '`CodeOnly`', '---',
        ])
        self.assertEqual(parse_markdown(content).anchors, frozenset({'api-usage', 'sdk指南', 'codeonly'}))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page = root / 'README.md'
            page.write_text(content, encoding='utf-8')
            errors = local_link_errors(page, content + '\n\n[go](#hidden)', root_dir=root)
            self.assertEqual(len(errors), 1)
            self.assertIn('链接锚点不存在：#hidden', errors[0])


if __name__ == '__main__':
    unittest.main()
