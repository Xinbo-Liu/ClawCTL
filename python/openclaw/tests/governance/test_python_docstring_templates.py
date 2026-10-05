from __future__ import annotations

from pathlib import Path
import re
import unittest

from openclaw.lib.repo.layout import resolve_repo_root


ROOT_DIR = resolve_repo_root(Path(__file__))


class PythonDocstringTemplateDebtTest(unittest.TestCase):
    def test_python_sources_do_not_keep_generated_docstring_templates(self) -> None:
        forbidden_patterns = (
            ''.join(('在', 'OpenClaw ', r'[^\r\n。]*', '中')),
            ''.join(('参与', 'OpenClaw ')),
            ''.join(('空值或缺省值', '沿用当前', '实现的分支语义')),
            ''.join(('当前', '实现可能', '读取或写入仓库/运行态文件')),
            ''.join(('当前', '实现会在', '输入不合法、依赖对象缺失或下游调用失败时抛出异常')),
            ''.join(('承载', r'[^\r\n。；]*', '参与', r'[^\r\n。；]*', '流程')),
            ''.join(('在', r'[^\r\n。；]*', '模块中', r'[a-zA-Z0-9_]+')),
            ''.join(('在', r'[^\r\n。；]*', '领域层中', r'[a-zA-Z0-9_]+')),
            ''.join(('返回', r'[^\r\n。]*，表示[^\r\n。]*', '结果')),
            ''.join(('返回当前', '实现计算出的结果', '，表示', r'[^\r\n。]*', '产出')),
            ''.join(('表示', r'OpenClaw\s*', r'[^\r\n。]*', '子系统中的', r'[^\r\n。]*', '对象')),
            ''.join(('表示', 'OpenClaw ', r'[^\r\n。]*', '子系统中的', r'[^\r\n。]*', '结果对象')),
            ''.join(('供', '调度入口使用')),
            ''.join(('供', '调度和业务排查使用')),
            ''.join(('表示', '用于')),
            ''.join(('用于', '使用')),
            ''.join(('供', '使用')),
        )
        forbidden = re.compile('|'.join(forbidden_patterns))
        source_roots = (
            ROOT_DIR / 'python' / 'openclaw',
            ROOT_DIR / 'agent' / 'extensions',
        )

        violations: list[str] = []
        for source_root in source_roots:
            for path in sorted(source_root.rglob('*.py')):
                if any(part in {'__pycache__', '.venv', 'venv'} for part in path.parts):
                    continue
                rel_path = path.relative_to(ROOT_DIR).as_posix()
                for line_number, line in enumerate(path.read_text(encoding='utf-8', errors='ignore').splitlines(), start=1):
                    if forbidden.search(line):
                        violations.append(f'{rel_path}:{line_number}:{line.strip()}')

        self.assertEqual(violations, [])
