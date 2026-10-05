"""验证冷启动独立检查与批量导入闭包的故障定位边界。"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from openclaw.doctor.platform import cold_start_imports


def _batch_result(modules: list[str], returncode: int) -> dict[str, object]:
    return {
        'modules': modules,
        'returncode': returncode,
        'detail': 'batch failed' if returncode else '',
        'elapsedSeconds': 0.1,
        'timeoutSeconds': 10.0,
        'timedOut': False,
    }


def _module_result(module: str, returncode: int) -> dict[str, object]:
    return {
        'module': module,
        'returncode': returncode,
        'detail': f'{module} failed' if returncode else '',
        'elapsedSeconds': 0.1,
        'timeoutSeconds': 10.0,
        'timedOut': False,
    }


class ColdStartImportsTest(unittest.TestCase):
    def test_discovery_excludes_unittest_modules_from_runtime_cold_starts(self) -> None:
        with TemporaryDirectory() as tmpdir:
            package_root = Path(tmpdir) / 'openclaw'
            (package_root / 'runtime').mkdir(parents=True)
            (package_root / 'tests').mkdir()
            (package_root / 'tools').mkdir()
            (package_root / '__init__.py').write_text('', encoding='utf-8')
            (package_root / 'runtime' / 'service.py').write_text('', encoding='utf-8')
            (package_root / 'tests' / 'test_service.py').write_text('', encoding='utf-8')
            (package_root / 'tools' / '__init__.py').write_text('', encoding='utf-8')

            modules = cold_start_imports.discover_module_names(package_root)

        self.assertEqual(modules, ['openclaw.runtime.service', 'openclaw.tools'])

    def test_closure_only_isolates_modules_from_failed_batches(self) -> None:
        isolated_modules: list[str] = []

        def import_batch(modules: list[str], **_: object) -> dict[str, object]:
            return _batch_result(modules, 1 if modules == ['alpha', 'beta'] else 0)

        def import_once(module: str, **_: object) -> dict[str, object]:
            isolated_modules.append(module)
            return _module_result(module, 1 if module == 'beta' else 0)

        with (
            mock.patch.object(cold_start_imports, 'discover_module_names', return_value=['alpha', 'beta', 'gamma']),
            mock.patch.object(cold_start_imports, 'build_subprocess_env', return_value={}),
            mock.patch.object(cold_start_imports, '_positive_int_env', side_effect=lambda name, default: 2),
            mock.patch.object(cold_start_imports, '_import_batch', side_effect=import_batch),
            mock.patch.object(cold_start_imports, '_import_once', side_effect=import_once),
        ):
            report = cold_start_imports.build_report(Path('/unused'), mode='closure')

        self.assertEqual(isolated_modules, ['alpha', 'beta'])
        self.assertEqual(report['batchCount'], 2)
        self.assertEqual(report['failureCount'], 1)
        self.assertEqual(report['failures'][0]['module'], 'beta')
        self.assertFalse(report['ok'])

    def test_isolated_mode_checks_every_module_independently(self) -> None:
        checked_modules: list[str] = []

        def import_once(module: str, **_: object) -> dict[str, object]:
            checked_modules.append(module)
            return _module_result(module, 0)

        with (
            mock.patch.object(cold_start_imports, 'discover_module_names', return_value=['alpha', 'beta']),
            mock.patch.object(cold_start_imports, 'build_subprocess_env', return_value={}),
            mock.patch.object(cold_start_imports, '_positive_int_env', return_value=2),
            mock.patch.object(cold_start_imports, '_import_once', side_effect=import_once),
        ):
            report = cold_start_imports.build_report(Path('/unused'), mode='isolated')

        self.assertCountEqual(checked_modules, ['alpha', 'beta'])
        self.assertEqual(report['batchCount'], 0)
        self.assertTrue(report['ok'])


if __name__ == '__main__':
    unittest.main()
