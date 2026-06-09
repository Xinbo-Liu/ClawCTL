from __future__ import annotations

import unittest
from pathlib import Path

from openclaw.lib.repo.layout import resolve_repo_root


ROOT_DIR = resolve_repo_root(Path(__file__))
SCAN_ROOTS = [
    ROOT_DIR / 'README.md',
    ROOT_DIR / 'docs',
    ROOT_DIR / 'config',
    ROOT_DIR / 'agent',
    ROOT_DIR / 'scripts',
    ROOT_DIR / 'python' / 'openclaw',
    ROOT_DIR / 'deploy',
    ROOT_DIR / 'runtime_paths',
]
TEXT_SUFFIXES = {'.env', '.example', '.json', '.md', '.py', '.sh'}
EXCLUDED_PARTS = {'__pycache__', 'tests'}
FORBIDDEN_SNIPPET_PARTS = (
    ('已', '改为'),
    ('已', '不再'),
    ('以后', '凡是'),
    ('恢复', '根级'),
    ('旧', '入口'),
    ('旧 target ', 'env'),
    ('历史', '方案'),
    ('为了', '这', '次'),
    ('为了', '本', '次'),
    ('默认步骤', '已经'),
    ('此前', '已经执行过'),
    ('已', '自动执行'),
    ('已在 ', 'full test'),
    ('已从', '当前'),
    ('第 1 步通常', '已自动回填'),
    ('改成', '真实部署网卡'),
    ('改成', '正式访问主机名'),
    ('手写 ', 'docker logs'),
    ('当前项目', '保留层'),
    ('后续升级', '必须'),
    ('后续会', '自动读取'),
    ('本地开发', '候选'),
    ('本地候选', '可先'),
    ('可先不加', '该开关'),
)
FORBIDDEN_SNIPPETS = tuple(''.join(parts) for parts in FORBIDDEN_SNIPPET_PARTS)
FORBIDDEN_LITERAL_PARTS = (
    ('compose_truth', '_drift'),
    ('compose_truth', '_preflight'),
    ('raw', '/artifact'),
    ('raw', '/effective'),
    ('effective', 'Accepted'),
    ('effective', 'Counts'),
    ('raw', 'Failing'),
    ('runtime mounts sync-compose', ' --check'),
    ('部署链', '只'),
    ('只做', ' verify'),
    ('开发', '痕迹'),
    ('改' + '造', '计划'),
    ('临' + '时', '可用'),
    ('兼容', '旧'),
    ('旧', '口径'),
    ('旧', '字段'),
    ('旧', '实现'),
    ('手工', '同步'),
    ('手动', '同步'),
)
FORBIDDEN_LITERALS = tuple(''.join(parts) for parts in FORBIDDEN_LITERAL_PARTS)


def _iter_text_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        if root.is_file():
            files.append(root)
            continue
        for path in root.rglob('*'):
            if not path.is_file():
                continue
            if EXCLUDED_PARTS.intersection(path.relative_to(ROOT_DIR).parts):
                continue
            if path.suffix in TEXT_SUFFIXES:
                files.append(path)
    return sorted(files)


class DeliveryTraceCleanlinessTest(unittest.TestCase):
    def test_delivery_surfaces_do_not_contain_migration_trace_phrases(self) -> None:
        offenders: list[str] = []
        for path in _iter_text_files():
            text = path.read_text(encoding='utf-8')
            for snippet in FORBIDDEN_SNIPPETS:
                if snippet in text:
                    rel = path.relative_to(ROOT_DIR).as_posix()
                    offenders.append(f'{rel}: {snippet}')
            for snippet in FORBIDDEN_LITERALS:
                if snippet in text:
                    rel = path.relative_to(ROOT_DIR).as_posix()
                    offenders.append(f'{rel}: {snippet}')

        self.assertFalse(offenders, '\n'.join(offenders))


if __name__ == '__main__':
    unittest.main()
