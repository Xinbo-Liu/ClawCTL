from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openclaw.docs.validators.navigation import check_formal_entry_links, check_page


class DocumentationNavigationTest(unittest.TestCase):
    def test_formal_l1_entry_requires_docs_and_owner_readme_links(self) -> None:
        with tempfile.TemporaryDirectory(prefix='openclaw-doc-nav-') as temp_dir:
            root = Path(temp_dir)
            architecture_dir = root / 'docs' / 'architecture'
            architecture_dir.mkdir(parents=True)
            (root / 'docs' / 'README.md').write_text('# Docs\n', encoding='utf-8')
            (architecture_dir / 'README.md').write_text('# Architecture\n', encoding='utf-8')
            (architecture_dir / 'maintainer-minimal-path.md').write_text('# Minimal\n', encoding='utf-8')
            registry = {
                'pages': [
                    {
                        'path': 'docs/architecture/maintainer-minimal-path.md',
                        'entryLevel': 'L1',
                        'formalEntry': True,
                    }
                ]
            }

            errors = check_formal_entry_links(registry, root_dir=root)

        self.assertEqual(
            errors,
            [
                'docs/architecture/maintainer-minimal-path.md 是 L1 正式入口，但 docs/README.md 未链接该页',
                'docs/architecture/maintainer-minimal-path.md 是 L1 正式入口，但 docs/architecture/README.md 未链接该页',
            ],
        )

    def test_formal_l1_entry_passes_when_both_navigation_pages_link_it(self) -> None:
        with tempfile.TemporaryDirectory(prefix='openclaw-doc-nav-linked-') as temp_dir:
            root = Path(temp_dir)
            architecture_dir = root / 'docs' / 'architecture'
            architecture_dir.mkdir(parents=True)
            (root / 'docs' / 'README.md').write_text(
                '[最小理解路径](architecture/maintainer-minimal-path.md)\n',
                encoding='utf-8',
            )
            (architecture_dir / 'README.md').write_text(
                '[最小理解路径](maintainer-minimal-path.md)\n',
                encoding='utf-8',
            )
            (architecture_dir / 'maintainer-minimal-path.md').write_text('# Minimal\n', encoding='utf-8')
            registry = {
                'pages': [
                    {
                        'path': 'docs/architecture/maintainer-minimal-path.md',
                        'entryLevel': 'L1',
                        'formalEntry': True,
                    }
                ]
            }

            errors = check_formal_entry_links(registry, root_dir=root)

        self.assertEqual(errors, [])

    def test_navigation_page_rejects_missing_local_markdown_target(self) -> None:
        with tempfile.TemporaryDirectory(prefix='openclaw-doc-link-') as temp_dir:
            root = Path(temp_dir)
            docs_dir = root / 'docs'
            docs_dir.mkdir()
            page_path = docs_dir / 'README.md'
            page_path.write_text(
                '\n'.join([
                    '# Docs',
                    '## 任务入口',
                    '[缺失页面](missing.md)',
                    '',
                ]),
                encoding='utf-8',
            )
            _file_path, errors = check_page(
                {
                    'path': 'docs/README.md',
                    'navigationContract': {
                        'requiredTokens': ['## 任务入口'],
                        'minLinks': 1,
                    },
                },
                root_dir=root,
            )

        self.assertEqual(errors, ['docs/README.md:3 链接目标不存在：missing.md'])

    def test_navigation_uses_real_boundaries_instead_of_reserved_target_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page = root / 'README.md'
            (root / '__outside_repository__').write_text('合法仓内目标', encoding='utf-8')
            page.write_text('[合法](__outside_repository__)\n', encoding='utf-8')
            declaration = {'path': 'README.md', 'navigationContract': {'minLinks': 1}}
            self.assertEqual(check_page(declaration, root_dir=root)[1], [])
            page.write_text('[越界](../outside.md)\n', encoding='utf-8')
            self.assertIn('链接越过仓库边界', check_page(declaration, root_dir=root)[1][0])


if __name__ == '__main__':
    unittest.main()
