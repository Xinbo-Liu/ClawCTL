from __future__ import annotations

import json
import unittest
from pathlib import Path
from openclaw.lib.repo.layout import resolve_repo_root
from unittest import mock

from openclaw.control_plane.cli import build_group_parser
from openclaw.control_plane.modules.scaffold import scaffold_agent_module
from openclaw.control_plane.registry import CliError
from openclaw.control_plane.runtime.adapter_registry import RuntimeAdapterSpec
from openclaw.tests.support.helpers import isolated_test_root


ROOT_DIR = resolve_repo_root(Path(__file__))
BASE_SERVICE_PATH = (ROOT_DIR / 'config' / 'control_plane' / 'service.json').resolve()
AGENT_PLATFORM_SERVICE_PATH = (ROOT_DIR / 'config' / 'control_plane' / 'profiles' / 'agent_platform.service.json').resolve()
PYTHON_MODULE_SPEC = RuntimeAdapterSpec(
    adapter_id='python_module',
    title='Python Module',
    description='unit-test stub',
    module='openclaw.runtime.adapters.python_module',
    config_validator='validate_config',
    runner='run',
    supported_entrypoint_kinds=('python_cli',),
    supported_executor_kinds=('python_cli',),
)


class ModuleScaffoldTest(unittest.TestCase):
    def test_scaffold_cli_hides_shell_entrypoint_kind(self) -> None:
        parser = build_group_parser('module')
        subparsers = next(action for action in parser._actions if getattr(action, 'choices', None))
        scaffold_parser = subparsers.choices['scaffold-agent-module']
        entrypoint_action = next(action for action in scaffold_parser._actions if action.dest == 'entrypoint_kind')

        self.assertEqual(tuple(entrypoint_action.choices), ('python_cli', 'openclaw_runtime', 'delivery_adapter'))

    def _write_json(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def _materialize_managed_repo(self) -> tuple[Path, Path, Path]:
        context = isolated_test_root('module-scaffold')
        repo_root = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

        (repo_root / 'python' / 'openclaw').mkdir(parents=True)
        (repo_root / 'python' / 'openclaw' / '__init__.py').write_text('', encoding='utf-8')
        self._write_json(repo_root / 'config' / 'runtime' / 'paths.json', {})
        self._write_json(repo_root / 'config' / 'control_plane' / 'service.json', {})

        package_root = repo_root / 'agent' / 'extensions' / 'agent_probe'
        (package_root / 'agent' / 'modules').mkdir(parents=True)
        (package_root / 'agent' / 'control_plane' / 'groups').mkdir(parents=True)
        (package_root / 'agent' / 'domains' / 'probe' / 'runtime').mkdir(parents=True)
        (package_root / 'python' / 'openclaw_ext_probe' / 'modules').mkdir(parents=True)
        (package_root / 'python' / 'openclaw_ext_probe' / 'domains' / 'probe' / 'shared').mkdir(parents=True)
        (package_root / 'config' / 'control_plane' / 'profiles').mkdir(parents=True)
        (package_root / 'config' / 'control_plane' / 'extensions.d').mkdir(parents=True)
        (package_root / 'python' / 'openclaw_ext_probe' / '__init__.py').write_text('', encoding='utf-8')

        service_path = package_root / 'config' / 'control_plane' / 'profiles' / 'agent_probe.service.json'
        self._write_json(service_path, {})
        self._write_json(
            repo_root / 'agent' / 'extensions' / 'index.json',
            {
                'extensions': [
                    {
                        'id': 'agent_probe',
                        'title': 'Probe Managed Extension',
                        'rootDir': 'agent/extensions/agent_probe',
                        'defaultServiceConfigPath': 'agent/extensions/agent_probe/config/control_plane/profiles/agent_probe.service.json',
                        'manifestDir': 'agent/extensions/agent_probe/config/control_plane/extensions.d',
                        'pythonRoots': ['agent/extensions/agent_probe/python'],
                        'status': 'managed_explicit_extension',
                    }
                ]
            },
        )
        return repo_root, package_root, service_path

    def test_scaffold_rejects_non_managed_configs(self) -> None:
        for config_path in (BASE_SERVICE_PATH, AGENT_PLATFORM_SERVICE_PATH):
            with self.assertRaises(CliError) as ctx:
                scaffold_agent_module(
                    repo_root=ROOT_DIR,
                    config_path=config_path,
                    module_ref='alpha_probe',
                    title='Alpha Probe',
                    owner_domain='probe',
                )

            self.assertIn('只支持受管显式扩展包配置', str(ctx.exception))

    def test_scaffold_writes_managed_extension_paths(self) -> None:
        repo_root, package_root, service_path = self._materialize_managed_repo()
        with mock.patch(
            'openclaw.control_plane.modules.scaffold._runtime_adapter_specs',
            return_value={'python_module': PYTHON_MODULE_SPEC},
        ):
            payload = scaffold_agent_module(
                repo_root=repo_root,
                config_path=service_path,
                module_ref='alpha_probe',
                title='Alpha Probe',
                owner_domain='probe',
                with_agents_doc=True,
            )

        module_dir = package_root / 'agent' / 'modules' / 'alpha_probe'
        module_payload = json.loads((module_dir / 'module.json').read_text(encoding='utf-8'))
        readme_text = (module_dir / 'README.md').read_text(encoding='utf-8')
        agents_text = (module_dir / 'AGENTS.md').read_text(encoding='utf-8')
        launcher_text = (module_dir / 'bin' / 'alpha_probe').read_text(encoding='utf-8')
        smoke_test_text = (package_root / 'tests' / 'modules' / 'alpha_probe' / 'test_smoke.py').read_text(encoding='utf-8')
        main_text = (
            package_root / 'python' / 'openclaw_ext_probe' / 'modules' / 'alpha_probe' / 'main.py'
        ).read_text(encoding='utf-8')
        written_paths = [item.replace('\\', '/') for item in payload['writtenPaths']]

        self.assertEqual(payload['moduleDir'], str(module_dir))
        self.assertFalse((module_dir / '__init__.py').exists())
        self.assertNotIn('agent/extensions/agent_probe/agent/modules/alpha_probe/__init__.py', written_paths)
        self.assertIn('agent/extensions/agent_probe/python/openclaw_ext_probe/modules/alpha_probe/__init__.py', written_paths)
        self.assertEqual(module_payload['activation']['enabledExtensionIds'], ['agent_probe'])
        self.assertEqual(
            module_payload['logic']['sourcePaths'],
            ['@extension/python/openclaw_ext_probe/modules/alpha_probe/main.py'],
        )
        self.assertEqual(
            module_payload['governance']['changeControlDocPaths'],
            [
                '@repo/docs/architecture/agent-governance.md',
                '@repo/docs/architecture/agent-module-governance.md',
            ],
        )
        self.assertIn('python -m openclaw_ext_probe.modules.alpha_probe.main <command>', main_text)
        self.assertNotIn('python -m openclaw.modules.alpha_probe.main <command>', main_text)
        self.assertIn(
            'agent/extensions/agent_probe/agent/modules/alpha_probe/bin/alpha_probe',
            agents_text,
        )
        self.assertIn(
            'agent/extensions/agent_probe/agent/modules/alpha_probe/',
            readme_text,
        )
        self.assertIn(
            'agent/extensions/agent_probe/python/openclaw_ext_probe/modules/alpha_probe/main.py',
            readme_text,
        )
        self.assertIn(
            'agent/extensions/agent_probe/agent/control_plane/groups/*.json',
            readme_text,
        )
        self.assertIn(
            'agent/extensions/agent_probe/python/openclaw_ext_probe/domains/probe/shared/',
            readme_text,
        )
        self.assertIn('run_agent_entrypoint.sh', launcher_text)
        self.assertIn('run_agent_entrypoint.sh" agent_probe:alpha_probe "$@"', launcher_text)
        self.assertNotIn('agent_probe.service.json', launcher_text)
        self.assertNotIn('OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH', launcher_text)
        self.assertNotIn('TEST_DIR = Path(__file__).resolve().parent', smoke_test_text)
        self.assertIn(
            "CONFIG_PATH = (REPO_ROOT / 'agent/extensions/agent_probe/config/control_plane/profiles/agent_probe.service.json').resolve()",
            smoke_test_text,
        )
        self.assertNotIn(
            "CONFIG_PATH = (TEST_DIR /",
            smoke_test_text,
        )
