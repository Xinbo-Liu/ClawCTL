from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from openclaw.doctor.platform import semantic_docstring_governance as governance


class SemanticDocstringGovernanceTest(unittest.TestCase):
    def _write(self, root: Path, rel_path: str, text: str) -> Path:
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='\n')
        return path

    def _minimal_extension(self, root: Path, extension_id: str = 'agent_demo') -> None:
        extension_root = root / 'agent' / 'extensions' / extension_id
        manifest_dir = extension_root / 'config' / 'control_plane' / 'extensions.d'
        service_dir = extension_root / 'config' / 'control_plane' / 'profiles'
        python_root = extension_root / 'python'
        manifest_dir.mkdir(parents=True)
        service_dir.mkdir(parents=True)
        python_root.mkdir(parents=True)
        self._write(root, f'agent/extensions/{extension_id}/config/control_plane/extensions.d/{extension_id}.json', '{}')
        self._write(root, f'agent/extensions/{extension_id}/config/control_plane/profiles/{extension_id}.service.json', '{}')
        self._write(root, f'agent/extensions/{extension_id}/python/openclaw_ext_demo/__init__.py', '"""扩展包入口。"""\n')
        self._write(
            root,
            'agent/extensions/index.json',
            json.dumps(
                {
                    'extensions': [
                        {
                            'id': extension_id,
                            'title': 'Demo Extension',
                            'rootDir': f'agent/extensions/{extension_id}',
                            'defaultServiceConfigPath': (
                                f'agent/extensions/{extension_id}/config/control_plane/profiles/{extension_id}.service.json'
                            ),
                            'manifestDir': f'agent/extensions/{extension_id}/config/control_plane/extensions.d',
                            'pythonRoots': [f'agent/extensions/{extension_id}/python'],
                            'status': 'managed_explicit_extension',
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        )

    def test_enforce_reports_missing_parameter_and_return_semantics(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root,
                'python/openclaw/sample.py',
                '"""示例模块。"""\n\n'
                'def make_value(name: str) -> str:\n'
                '    """生成值。"""\n'
                '    return name\n',
            )

            report = governance.build_report(root, scope=governance.SCOPE_PLATFORM)
            issues = governance.enforce_issues(report)

        joined = '\n'.join(issues)
        self.assertIn('missing_param_doc:name', joined)
        self.assertIn('missing_param_type:name:str', joined)
        self.assertIn('missing_return_type:str', joined)

    def test_complete_semantic_docstring_passes_enforce(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root,
                'python/openclaw/sample.py',
                '"""示例模块。"""\n\n'
                'def make_value(name: str) -> str:\n'
                '    """生成示例值。\n\n'
                '    参数：\n'
                '        name（str）：原始名称，会作为示例值返回。\n'
                '    返回：\n'
                '        返回 str，表示拼装后的示例值。\n'
                '    """\n'
                '    return name\n',
            )

            report = governance.build_report(root, scope=governance.SCOPE_PLATFORM)

        self.assertEqual(governance.enforce_issues(report), [])

    def test_extension_scope_uses_managed_extension_python_roots(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._minimal_extension(root)
            self._write(
                root,
                'agent/extensions/agent_demo/python/openclaw_ext_demo/workflow.py',
                '"""扩展 workflow 模块。"""\n\n'
                'def run_step(payload: dict[str, object]) -> dict[str, object]:\n'
                '    """执行扩展步骤。\n\n'
                '    参数：\n'
                '        payload（dict[str, object]）：扩展步骤输入状态，函数会原样返回给调用方。\n'
                '    返回：\n'
                '        返回 dict[str, object]，表示扩展步骤输出状态。\n'
                '    """\n'
                '    return payload\n',
            )

            report = governance.build_report(root, scope=governance.SCOPE_EXTENSIONS)

        self.assertEqual(report['summary']['files'], 2)
        self.assertEqual(governance.enforce_issues(report), [])

    def test_template_docstring_is_rejected_even_when_types_are_present(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root,
                'python/openclaw/sample.py',
                '"""示例模块。"""\n\n'
                'def make_value(name: str) -> str:\n'
                '    """生成示例值。\n\n'
                '    参数：\n'
                '        name（str）：'
                + ''.join((
                    '承载示例名称，',
                    '参与示例值拼装流程。',
                ))
                + '\n'
                '    返回：\n'
                '        返回 str，表示拼装后的示例值。\n'
                '    """\n'
                '    return name\n',
            )

            report = governance.build_report(root, scope=governance.SCOPE_PLATFORM)

        joined = '\n'.join(governance.enforce_issues(report))
        self.assertIn('low_information_template', joined)

    def test_ratchet_detects_new_semantic_issue_against_zero_baseline(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(root, 'python/openclaw/sample.py', '"""示例模块。"""\n')
            clean_report = governance.build_report(root, scope=governance.SCOPE_PLATFORM)
            baseline = governance.build_baseline_payload(clean_report)
            self._write(root, 'python/openclaw/bad.py', 'def bad(value: str) -> str:\n    return value\n')
            current_report = governance.build_report(root, scope=governance.SCOPE_PLATFORM)

        issues = governance.compare_with_baseline(current_report, baseline)
        self.assertTrue(any('新增生产 Python 文件存在语义 docstring 缺口' in issue for issue in issues))


if __name__ == '__main__':
    unittest.main()
