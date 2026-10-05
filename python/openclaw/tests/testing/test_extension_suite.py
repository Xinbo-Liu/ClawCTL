"""验证扩展聚合入口只在发现和执行作用域内修改进程状态。"""
from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from openclaw.testing.extension_suite import ExtensionSuiteConfig, load_extension_suite


class ExtensionSuiteIsolationTest(unittest.TestCase):
    def test_loader_and_suite_restore_environment_paths_and_modules(self) -> None:
        original_path = list(sys.path)
        original_mode = os.environ.get('FIXTURE_EXT_MODE')
        os.environ.pop('FIXTURE_EXT_MODE', None)
        os.environ.pop('FIXTURE_EXT_MUTATION', None)
        support_package = types.ModuleType('support')
        support_helper = types.ModuleType('support.helper')
        support_helper.VALUE = 'outside'  # type: ignore[attr-defined]
        stale_namespace_module = types.ModuleType('fixture_ext_prior')
        try:
            with tempfile.TemporaryDirectory() as tmpdir, mock.patch.dict(
                sys.modules,
                {
                    'support': support_package,
                    'support.helper': support_helper,
                    'fixture_ext_prior': stale_namespace_module,
                },
                clear=False,
            ):
                test_root = Path(tmpdir) / 'tests'
                unit_root = test_root / 'unit'
                support_root = test_root / 'support'
                unit_root.mkdir(parents=True)
                support_root.mkdir(parents=True)
                (support_root / 'helper.py').write_text("VALUE = 'inside'\n", encoding='utf-8')
                (unit_root / 'test_state.py').write_text(
                    "import os\n"
                    "import sys\n"
                    "import unittest\n"
                    "from support.helper import VALUE\n\n"
                    "class ScopedStateTest(unittest.TestCase):\n"
                    "    def test_scoped_state(self):\n"
                    "        self.assertEqual(VALUE, 'inside')\n"
                    "        self.assertEqual(os.environ['FIXTURE_EXT_MODE'], 'isolated')\n"
                    "        os.environ['FIXTURE_EXT_MUTATION'] = 'temporary'\n"
                    "        sys.path.append('fixture-extension-transient-path')\n",
                    encoding='utf-8',
                )
                config = ExtensionSuiteConfig(
                    namespace='fixture_ext',
                    test_root=test_root,
                    include_subtrees=('unit',),
                    env_defaults={'FIXTURE_EXT_MODE': 'isolated'},
                    remove_env_prefixes=('FIXTURE_EXT_',),
                )

                suite = load_extension_suite(unittest.defaultTestLoader, config)

                self.assertEqual(sys.path, original_path)
                self.assertNotIn('FIXTURE_EXT_MODE', os.environ)
                self.assertIs(sys.modules['support'], support_package)
                self.assertIs(sys.modules['support.helper'], support_helper)
                self.assertIs(sys.modules['fixture_ext_prior'], stale_namespace_module)
                self.assertFalse(any(name.startswith('fixture_ext_test_') for name in sys.modules))

                result = unittest.TestResult()
                suite.run(result)

                self.assertEqual(result.testsRun, 1)
                self.assertEqual(result.errors, [])
                self.assertEqual(result.failures, [])
                self.assertEqual(sys.path, original_path)
                self.assertNotIn('FIXTURE_EXT_MODE', os.environ)
                self.assertNotIn('FIXTURE_EXT_MUTATION', os.environ)
                self.assertIs(sys.modules['support'], support_package)
                self.assertIs(sys.modules['support.helper'], support_helper)
                self.assertIs(sys.modules['fixture_ext_prior'], stale_namespace_module)
                self.assertFalse(any(name.startswith('fixture_ext_test_') for name in sys.modules))
        finally:
            sys.path[:] = original_path
            os.environ.pop('FIXTURE_EXT_MUTATION', None)
            if original_mode is None:
                os.environ.pop('FIXTURE_EXT_MODE', None)
            else:
                os.environ['FIXTURE_EXT_MODE'] = original_mode


if __name__ == '__main__':
    unittest.main()
