from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from openclaw.docs.support.markdown_links import local_link_errors, parse_markdown
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.path_contracts import extension_anchored_path, resolve_path_contract
from openclaw.tests.support.managed_extensions import managed_extensions, representative_managed_extension


ROOT_DIR = resolve_repo_root(Path(__file__))
MANAGED_EXTENSIONS = tuple(sorted(managed_extensions(ROOT_DIR), key=lambda row: row.id))
# 只区分实际 Markdown 导航和文件系统路径；不按 Python/JSON 文件所在目录放行。
DEEP_PARENT_PATTERN = re.compile(r'\.\.[/\\]+(?:\.\.[/\\]+)+')


def _string_has_runtime_deep_parent(value: str) -> bool:
    """判断字符串是否含 Markdown 链接目标以外的深层父目录路径。

    参数：
        value（str）：已由 JSON/Python 解析得到的实际字符串值。
    返回：
        bool：普通路径、代码示例或链接外路径仍受运行路径禁令约束。
    """
    value = value.replace('\r\n', '\n').replace('\r', '\n')
    occurrences = tuple(DEEP_PARENT_PATTERN.finditer(value))
    if not occurrences:
        return False
    spans = [link.destination_span for link in parse_markdown(value).links if link.destination_span is not None]
    return any(
        not any(start <= occurrence.start() and occurrence.end() <= end for start, end in spans)
        for occurrence in occurrences
    )


def _json_has_runtime_deep_parent(value: object) -> bool:
    """递归检查 JSON 值，仅允许明确 doc_page 节点的 Markdown path 与正文导航。

    参数：
        value（object）：解析后的 JSON 节点。
    返回：
        bool：任意普通字段仍包含深层父目录路径时返回 True。
    """
    if isinstance(value, dict):
        for key, child in value.items():
            if DEEP_PARENT_PATTERN.search(key):
                return True
            if key == 'path' and value.get('kind') == 'doc_page' and isinstance(child, str):
                if Path(child.partition('#')[0]).suffix.lower() in {'.md', '.markdown', '.mdx'}:
                    continue
            if _json_has_runtime_deep_parent(child):
                return True
    elif isinstance(value, list):
        return any(_json_has_runtime_deep_parent(child) for child in value)
    elif isinstance(value, str):
        return _string_has_runtime_deep_parent(value)
    return False


def _python_has_runtime_deep_parent(text: str) -> bool:
    """解析 Python 实际字符串，保留导航 literal 之外的源码路径禁令。

    参数：
        text（str）：Python 源码，解析过程不导入或执行模块。
    返回：
        bool：实际字符串或其余源码中包含普通深层父目录路径时返回 True。
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return bool(DEEP_PARENT_PATTERN.search(text))
    encoded = text.encode('utf-8')
    line_offsets = [0]
    for line in encoded.splitlines(keepends=True):
        line_offsets.append(line_offsets[-1] + len(line))
    navigation_spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if _string_has_runtime_deep_parent(node.value):
            return True
        if DEEP_PARENT_PATTERN.search(node.value):
            # AST 列号以 UTF-8 字节计；只屏蔽已验证的源码 literal 范围，保留其余普通路径。
            navigation_spans.append((line_offsets[node.lineno - 1] + node.col_offset, line_offsets[node.end_lineno - 1] + node.end_col_offset))
    residual = bytearray(encoded)
    for start, end in navigation_spans:
        residual[start:end] = b' ' * (end - start)
    return bool(DEEP_PARENT_PATTERN.search(residual.decode('utf-8')))


def _deep_parent_path_violations(root_dir: Path) -> list[str]:
    """查找运行与业务源码、脚本和配置中的深层父目录路径声明。

    参数：
        root_dir（Path）：需要检查的仓库根目录或隔离夹具。
    返回：
        list[str]：仍使用连续两层父目录跳转的运行文件相对路径。
    副作用：
        只读活动源码面；Markdown 页面和生成正文中的真实导航另由本地链接门禁验证。
    """
    scan_roots = tuple(root_dir / directory for directory in ('agent', 'config', 'docs', 'python', 'scripts'))
    text_suffixes = {'.json', '.py', '.sh'}
    violations: list[str] = []
    for scan_root in scan_roots:
        for path in sorted(scan_root.rglob('*')):
            if not path.is_file() or '__pycache__' in path.parts:
                continue
            if (root_dir / 'python/openclaw/tests') in path.parents or path.suffix.lower() in {'.md', '.markdown', '.mdx'}:
                continue
            if path.suffix not in text_suffixes and 'bin' not in path.parts:
                continue
            text = path.read_text(encoding='utf-8', errors='ignore')
            if path.suffix == '.py':
                forbidden = _python_has_runtime_deep_parent(text)
            elif path.suffix == '.json':
                try:
                    forbidden = _json_has_runtime_deep_parent(json.loads(text))
                except json.JSONDecodeError:
                    forbidden = bool(DEEP_PARENT_PATTERN.search(text))
            else:
                forbidden = bool(DEEP_PARENT_PATTERN.search(text))
            if forbidden:
                violations.append(path.relative_to(root_dir).as_posix())
    return violations


class RepoPathContractsTest(unittest.TestCase):
    def test_repo_anchored_path_resolves_from_repo_root(self) -> None:
        resolved = resolve_path_contract(
            '@repo/config/control_plane/service.json',
            base_dir=ROOT_DIR,
            start_path=ROOT_DIR,
        )
        self.assertEqual(
            resolved,
            (ROOT_DIR / 'config' / 'control_plane' / 'service.json').resolve(),
        )

    def test_repo_anchored_path_rejects_repo_escape(self) -> None:
        with self.assertRaisesRegex(ValueError, 'must stay inside the repository'):
            resolve_path_contract(
                '@repo/../../outside.txt',
                base_dir=ROOT_DIR,
                start_path=ROOT_DIR,
            )

    def test_extension_anchored_path_resolves_from_extension_root(self) -> None:
        if not MANAGED_EXTENSIONS:
            self.skipTest('base release surface has no repo-managed extension')
        extension = representative_managed_extension(ROOT_DIR)
        module_dir = extension.root_dir / 'agent'
        target_file = self._representative_extension_python_file(extension.python_roots)
        resolved = resolve_path_contract(
            extension_anchored_path(target_file.relative_to(extension.root_dir).as_posix()),
            base_dir=module_dir,
            start_path=module_dir,
        )

        self.assertEqual(resolved, target_file.resolve())

    def test_extension_anchored_path_rejects_extension_escape(self) -> None:
        if not MANAGED_EXTENSIONS:
            self.skipTest('base release surface has no repo-managed extension')
        extension = representative_managed_extension(ROOT_DIR)
        module_dir = extension.root_dir / 'agent'
        with self.assertRaisesRegex(ValueError, 'must stay inside the extension root'):
            resolve_path_contract(
                '@extension/../outside/README.md',
                base_dir=module_dir,
                start_path=module_dir,
            )

    def test_extension_anchored_path_rejects_non_contract_extension_root(self) -> None:
        with TemporaryDirectory() as tmpdir:
            external_root = Path(tmpdir) / 'external_extension'
            external_root.mkdir(parents=True)

            with self.assertRaisesRegex(ValueError, 'cannot resolve extension root'):
                resolve_path_contract(
                    '@extension/README.md',
                    base_dir=external_root,
                    start_path=external_root,
                )

    def test_control_plane_machine_configs_do_not_use_parent_traversal_paths(self) -> None:
        scan_roots = (
            ROOT_DIR / 'config' / 'control_plane',
            ROOT_DIR / 'agent' / 'extensions',
        )
        violations: list[str] = []
        for scan_root in scan_roots:
            for path in sorted(scan_root.rglob('*.json')):
                text = path.read_text(encoding='utf-8')
                if '../' in text or '..\\' in text:
                    violations.append(path.relative_to(ROOT_DIR).as_posix())

        self.assertEqual(violations, [])

    @staticmethod
    def _representative_extension_python_file(python_roots: tuple[Path, ...]) -> Path:
        for python_root in python_roots:
            for path in sorted(python_root.rglob('*.py')):
                if path.is_file():
                    return path
        raise AssertionError('expected representative managed extension to contain at least one Python file')

    def test_runtime_surface_does_not_use_deep_parent_traversal_paths(self) -> None:
        """运行与业务路径继续禁止深层 parent；文档目标由本地链接门禁检查。"""
        self.assertEqual(_deep_parent_path_violations(ROOT_DIR), [])

    def test_documentation_sources_do_not_trigger_runtime_path_ban(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            content = '[首页](../../README.md#首页)'
            sources = {
                'agent/modules/example/README.md': content,
                'docs/architecture/reference.md': content,
                'python/openclaw/docs/renderers/example.py': 'content = ' + repr(content),
                'config/surfaces/example.json': json.dumps({'body': content}),
                'config/docs/example.json': json.dumps({'references': [{'kind': 'doc_page', 'path': '../../README.md'}]}),
            }
            for relative, text in sources.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding='utf-8')
            self.assertEqual(_deep_parent_path_violations(root), [])

    def test_reference_navigation_does_not_hide_an_unrelated_path(self) -> None:
        reference = '[首页][home]\n[重复入口][home]\n\n[home]: ../../README.md'
        self.assertFalse(_string_has_runtime_deep_parent(reference))
        self.assertTrue(_string_has_runtime_deep_parent(reference + '\n普通路径 ../../private/input'))
        for code_example in (
            '`[首页](../../README.md)`',
            '```markdown\n[首页](../../README.md)\n```',
            '`跨行代码\n[home]: ../../private/input\n代码`\n\n[首页][home]',
        ):
            with self.subTest(code_example=code_example):
                self.assertTrue(_string_has_runtime_deep_parent(code_example))

    def test_runtime_configuration_and_neighbor_directories_keep_parent_ban(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = [
                'agent/extensions/example/config/jobs/task.json',
                'agent/extensions/example/python/runner.py',
                'config/control_plane/profiles/example.service.json',
                'config/runtime/paths.json',
                'config/governance/docs/recovery_operations_surface.json',
                'config/governance/docs_extra/template.json',
                'config/governance/docs/docs_registry.json',
                'config/governance/docs/getting_started_surface.json',
                'python/openclaw/runtime/runner.py',
                'python/openclaw/docs_extra/runner.py',
                'python/openclaw/docs/renderers/example.py',
                'docs/snippets/runner.py',
                'scripts/runtime/runner.sh',
            ]
            for index, relative in enumerate(paths):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                # Windows 分隔符也必须受相同的运行路径禁令约束。
                value = '../../private/input' if index % 2 else '..\\..\\private\\input'
                text = json.dumps({'runtimePath': value}) if path.suffix == '.json' else ('path = ' + repr(value) if path.suffix == '.py' else value)
                path.write_text(text, encoding='utf-8')
            self.assertCountEqual(_deep_parent_path_violations(root), paths)

    def test_doc_page_reference_does_not_exempt_sibling_runtime_fields(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = root / 'config/governance/docs/registry.json'
            path.parent.mkdir(parents=True)
            for value in (
                {'kind': 'doc_page', 'path': '../../README.md', 'runtimePath': '../../private/input'},
                {'kind': 'doc_page', 'path': '../../README.md', 'nested': {'output': r'..\..\private\input'}},
                {'kind': 'runtime_path', 'path': '../../README.md'},
                {'kind': 'doc_page', 'path': '../../private/input'},
                {'body': '[首页](../../README.md)', 'runtimePath': '../../README.md'},
            ):
                with self.subTest(value=value):
                    path.write_text(json.dumps(value), encoding='utf-8')
                    self.assertEqual(_deep_parent_path_violations(root), ['config/governance/docs/registry.json'])

    def test_python_path_and_link_sibling_remain_runtime_paths(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = root / 'python/openclaw/docs/renderers/example.py'
            path.parent.mkdir(parents=True)
            for code in (
                "from pathlib import Path\npath = Path('../../private/input')\n",
                "nav = '[首页](../../README.md)'\npath = '../../README.md'\n",
                "nav = '[首页](../../README.md) 与 ../../README.md'\n",
                r"path = '\u002e\u002e/\u002e\u002e/private/input'",
            ):
                with self.subTest(code=code):
                    path.write_text(code, encoding='utf-8')
                    self.assertEqual(_deep_parent_path_violations(root), ['python/openclaw/docs/renderers/example.py'])

    def test_deep_relative_markdown_requires_real_in_repo_target_and_anchor(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / 'README.md'
            target.write_text('# 首页\n', encoding='utf-8')
            page = root / 'docs/architecture/reference.md'
            page.parent.mkdir(parents=True)
            content = '[首页](../../README.md#首页)'
            page.write_text(content, encoding='utf-8')
            self.assertEqual(_deep_parent_path_violations(root), [])
            self.assertEqual(local_link_errors(page, content, root_dir=root), [])
            for invalid, diagnostic in (
                ('../../../outside.md', '链接越过仓库边界'),
                ('../../missing.md', '链接目标不存在'),
                ('../../README.md#不存在', '链接锚点不存在'),
            ):
                with self.subTest(target=invalid):
                    errors = local_link_errors(page, f'[首页]({invalid})', root_dir=root)
                    self.assertEqual(len(errors), 1)
                    self.assertIn(diagnostic, errors[0])


if __name__ == '__main__':
    unittest.main()
