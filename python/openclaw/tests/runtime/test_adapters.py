from __future__ import annotations

from datetime import datetime, timezone
import os
import sys
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from openclaw.control_plane.cli_support import runtime_support
from openclaw.control_plane.runtime.adapter_registry import RuntimeAdapterRegistryError, load_runtime_adapter_registry
from openclaw.control_plane.schema import SchemaValidationError, validate_payload_against_schema
from openclaw.control_plane.registry import CliError
from openclaw.control_plane.runtime.adapters import resolve_runtime_tokens, run_python_module
from openclaw.lib.repo.extension_envs import PreparedExtensionEnv
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.managed_extensions import ManagedExtensionRow
from openclaw.lib.runtime.execution import build_subprocess_env, import_callable, run_module_main
from openclaw.scheduler.subprocess_runner import run_subprocess_job_impl
from openclaw.testing import repo_host
ROOT_DIR = resolve_repo_root(Path(__file__))
AGENT_PLATFORM_CONFIG = (ROOT_DIR / 'config' / 'control_plane' / 'profiles' / 'agent_platform.service.json').resolve()


class RuntimeAdaptersTest(unittest.TestCase):
    def _write_module(self, directory: Path, module_name: str, body: str) -> str:
        module_path = directory / f'{module_name}.py'
        module_path.write_text(textwrap.dedent(body), encoding='utf-8')
        return module_name

    def test_run_python_module_true_maps_to_success_exit_code_zero(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            module_name = self._write_module(base, 'adapter_bool_true', 'def main(argv):\n    return True\n')
            sys.path.insert(0, str(base))
            try:
                rc = run_python_module(
                    runtime_config={'module': module_name},
                    runtime_args=[],
                    state_root=base,
                    repo_root=base,
                    agent_ref='agent.demo',
                    implementation_ref='impl.demo',
                )
            finally:
                sys.path.remove(str(base))
                sys.modules.pop(module_name, None)
        self.assertEqual(rc, 0)

    def test_run_python_module_false_maps_to_failure_exit_code_one(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            module_name = self._write_module(base, 'adapter_bool_false', 'def main(argv):\n    return False\n')
            sys.path.insert(0, str(base))
            try:
                rc = run_python_module(
                    runtime_config={'module': module_name},
                    runtime_args=[],
                    state_root=base,
                    repo_root=base,
                    agent_ref='agent.demo',
                    implementation_ref='impl.demo',
                )
            finally:
                sys.path.remove(str(base))
                sys.modules.pop(module_name, None)
        self.assertEqual(rc, 1)

    def test_run_python_module_invalid_string_exit_code_raises_cli_error(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            module_name = self._write_module(base, 'adapter_bad_string', "def main(argv):\n    return 'oops'\n")
            sys.path.insert(0, str(base))
            try:
                with self.assertRaises(CliError) as ctx:
                    run_python_module(
                        runtime_config={'module': module_name},
                        runtime_args=[],
                        state_root=base,
                        repo_root=base,
                        agent_ref='agent.demo',
                        implementation_ref='impl.demo',
                    )
            finally:
                sys.path.remove(str(base))
                sys.modules.pop(module_name, None)
        self.assertIn('无法解析为退出码的字符串', str(ctx.exception))

    def test_managed_extension_python_module_runs_with_prepared_venv_subprocess(self) -> None:
        self.skipTest('base release surface has no repo-managed extension env allowlist')
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            extension_root = base / 'agent' / 'extensions' / 'agent_probe'
            extension_python_root = extension_root / 'python'
            extension_python_root.mkdir(parents=True)
            prepared = PreparedExtensionEnv(
                row=ManagedExtensionRow(
                    id='agent_probe',
                    title='Probe',
                    root_dir=extension_root,
                    default_service_config_path=extension_root / 'config' / 'control_plane' / 'profiles' / 'agent_probe.service.json',
                    manifest_dir=extension_root / 'config' / 'control_plane' / 'extensions.d',
                    python_roots=(extension_python_root,),
                    status='managed_explicit_extension',
                ),
                env_path=base / 'env',
                python_executable=base / 'env' / 'bin' / 'python',
                manifest={'schemaVersion': 1},
            )
            with mock.patch.dict(
                os.environ,
                {
                    'LEAK_ME': 'should-not-cross',
                    'HOST_SECRET_TOKEN': 'should-not-cross',
                    'HOST_STATE_DIR': str(base / 'host-state'),
                    'DISPATCH_PRIMARY_BOT_SECRET': 'dispatch-secret',
                    'DISPATCH_PRIMARY_ENABLE': 'true',
                    'DISPATCH_PRIMARY_WEBHOOK_URL': 'https://example.invalid/webhook',
                    'MINIMAX_API_KEY': 'declared-secret',
                    'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH': str(AGENT_PLATFORM_CONFIG),
                    'OPENCLAW_GATEWAY_TOKEN': 'should-not-cross',
                    'OPENCLAW_INTERNAL_API_TOKEN': 'internal-api-token',
                    'OPENCLAW_RUNTIME_PATH_VIEW': 'scheduler',
                    'OPENCLAW_STATE_DIR': str(base / 'scheduler-state'),
                    'OPENCLAW_UNDECLARED_TOKEN': 'should-not-cross',
                    'PYTHONHOME': 'old-python-home',
                    'PYTHONUSERBASE': 'old-python-userbase',
                    'VIRTUAL_ENV': 'old-venv',
                    'PIP_REQUIRE_VIRTUALENV': '1',
                },
                clear=False,
            ), mock.patch(
                'openclaw.control_plane.runtime.adapters.extension_env_for_agent_runtime',
                return_value=prepared,
            ), mock.patch(
                'openclaw.control_plane.runtime.adapters.subprocess.run',
                return_value=SimpleNamespace(returncode=0),
            ) as run_mock:
                rc = run_python_module(
                    runtime_config={'module': 'agent_probe.entry'},
                    runtime_args=['--state', '{state_root}'],
                    state_root=base / 'state',
                    repo_root=ROOT_DIR,
                    agent_ref='agent_probe:demo',
                    implementation_ref='agent_probe:impl',
                )

        self.assertEqual(rc, 0)
        run_mock.assert_called_once()
        command = run_mock.call_args.args[0]
        kwargs = run_mock.call_args.kwargs
        self.assertEqual(command[0], str(prepared.python_executable))
        self.assertEqual(command[-3:-1], ['agent_probe.entry', '--state'])
        self.assertEqual(Path(command[-1]).name, 'state')
        self.assertEqual(kwargs['env']['OPENCLAW_EXTENSION_ID'], 'agent_probe')
        self.assertEqual(kwargs['env']['VIRTUAL_ENV'], str(prepared.env_path))
        self.assertEqual(Path(kwargs['env']['PATH'].split(os.pathsep)[0]), prepared.python_executable.parent)
        self.assertEqual(kwargs['env']['DISPATCH_PRIMARY_BOT_SECRET'], 'dispatch-secret')
        self.assertEqual(kwargs['env']['DISPATCH_PRIMARY_ENABLE'], 'true')
        self.assertEqual(kwargs['env']['DISPATCH_PRIMARY_WEBHOOK_URL'], 'https://example.invalid/webhook')
        self.assertEqual(kwargs['env']['MINIMAX_API_KEY'], 'declared-secret')
        self.assertEqual(kwargs['env']['OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH'], str(AGENT_PLATFORM_CONFIG))
        self.assertEqual(kwargs['env']['OPENCLAW_INTERNAL_API_TOKEN'], 'internal-api-token')
        self.assertEqual(kwargs['env']['OPENCLAW_RUNTIME_PATH_VIEW'], 'scheduler')
        self.assertEqual(kwargs['env']['OPENCLAW_STATE_DIR'], str(base / 'scheduler-state'))
        self.assertEqual(kwargs['env']['HOST_STATE_DIR'], str(base / 'host-state'))
        self.assertNotIn('HOST_SECRET_TOKEN', kwargs['env'])
        self.assertNotIn('LEAK_ME', kwargs['env'])
        self.assertNotIn('OPENCLAW_GATEWAY_TOKEN', kwargs['env'])
        self.assertNotIn('OPENCLAW_UNDECLARED_TOKEN', kwargs['env'])
        self.assertNotIn('PYTHONHOME', kwargs['env'])
        self.assertNotIn('PYTHONUSERBASE', kwargs['env'])
        self.assertNotIn('PIP_REQUIRE_VIRTUALENV', kwargs['env'])
        pythonpath_prefix = [Path(item) for item in kwargs['env']['PYTHONPATH'].split(os.pathsep)[:2]]
        self.assertTrue(pythonpath_prefix[0].as_posix().endswith('/agent/extensions/agent_probe/python'))
        self.assertTrue(pythonpath_prefix[1].samefile((ROOT_DIR / 'python').resolve()))

    def test_base_python_module_stays_in_process_without_venv_subprocess(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            module_name = self._write_module(base, 'adapter_base_in_process', 'def main(argv):\n    return 0\n')
            sys.path.insert(0, str(base))
            try:
                with mock.patch(
                    'openclaw.control_plane.runtime.adapters.extension_env_for_agent_runtime',
                    return_value=None,
                ), mock.patch('openclaw.control_plane.runtime.adapters.subprocess.run') as run_mock:
                    rc = run_python_module(
                        runtime_config={'module': module_name},
                        runtime_args=[],
                        state_root=base,
                        repo_root=base,
                        agent_ref='base:demo',
                        implementation_ref='base:impl',
                    )
            finally:
                sys.path.remove(str(base))
                sys.modules.pop(module_name, None)

        self.assertEqual(rc, 0)
        run_mock.assert_not_called()

    def test_import_callable_missing_member_raises_cli_error(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            module_name = self._write_module(base, 'adapter_missing_main', 'def probe(argv):\n    return 0\n')
            sys.path.insert(0, str(base))
            try:
                with self.assertRaises(CliError) as ctx:
                    import_callable(module_name, 'main', CliError, 'adapter fixture')
            finally:
                sys.path.remove(str(base))
                sys.modules.pop(module_name, None)
        self.assertIn('缺少可调用成员', str(ctx.exception))

    def test_import_callable_preserves_import_failure_cause(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            module_name = self._write_module(base, 'adapter_broken_import', "raise RuntimeError('broken import')\n")
            sys.path.insert(0, str(base))
            try:
                with self.assertRaises(CliError) as ctx:
                    import_callable(module_name, 'main', CliError, 'adapter fixture')
            finally:
                sys.path.remove(str(base))
                sys.modules.pop(module_name, None)
        self.assertIsInstance(ctx.exception.__cause__, RuntimeError)
        self.assertIn('模块导入失败', str(ctx.exception))

    def test_run_module_main_returns_main_exit_code(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            module_name = self._write_module(base, 'adapter_main_result', 'def main(argv):\n    return 7\n')
            sys.path.insert(0, str(base))
            try:
                rc = run_module_main(module_name, ['--flag'], CliError, 'adapter fixture')
            finally:
                sys.path.remove(str(base))
                sys.modules.pop(module_name, None)
        self.assertEqual(rc, 7)

    def test_build_subprocess_env_matches_repo_host_defaults(self) -> None:
        env = build_subprocess_env(Path(repo_host.__file__), base_env={})
        pythonpath_entries = env['PYTHONPATH'].split(os.pathsep)

        self.assertEqual(env['PYTHONDONTWRITEBYTECODE'], '1')
        self.assertEqual(env['PYTHONIOENCODING'], 'UTF-8')
        self.assertEqual(env['PYTHONUTF8'], '1')
        self.assertEqual(pythonpath_entries[0], str(repo_host.PYTHON_DIR))
        self.assertNotIn(str(repo_host.ROOT_DIR.resolve()), pythonpath_entries)

    def test_scheduler_subprocess_runner_uses_shared_env_builder_and_repo_root_cwd(self) -> None:
        history_rows: list[dict[str, object]] = []

        def build_row(**kwargs: object) -> dict[str, object]:
            row = {
                'status': kwargs['status'],
                'runId': kwargs['due_key'],
                'finished_at': '2026-04-21T00:00:00Z',
            }
            row.update(kwargs.get('extra') or {})
            history_rows.append(row)
            return row

        with TemporaryDirectory() as tmpdir:
            runs_dir = Path(tmpdir) / 'runs'
            files = SimpleNamespace(runs_dir=runs_dir)
            job_state: dict[str, object] = {}
            with mock.patch('openclaw.scheduler.subprocess_runner.subprocess.run', return_value=SimpleNamespace(returncode=0)) as run_mock:
                result = run_subprocess_job_impl(
                    job={'id': 'demo_job', 'title': 'Demo Job', 'timeoutSeconds': 30},
                    config={'configPath': str(AGENT_PLATFORM_CONFIG)},
                    files=files,
                    job_state=job_state,
                    due_key='demo_job@schedule@2026-04-21T00:00',
                    current=datetime(2026, 4, 21, tzinfo=timezone.utc),
                    force_all=False,
                    command=['python', '-c', 'print("ok")'],
                    lock_path=Path(tmpdir) / 'demo_job.lock',
                    stale_after_seconds=60,
                    acquire_lock=lambda *_args, **_kwargs: True,
                    release_lock=lambda *_args, **_kwargs: None,
                    history_row_builder=build_row,
                    now_utc_iso=lambda: '2026-04-21T00:00:00Z',
                )

        self.assertEqual(result['status'], 'succeeded')
        run_mock.assert_called_once()
        command = run_mock.call_args.args[0]
        kwargs = run_mock.call_args.kwargs
        self.assertEqual(command, ['python', '-c', 'print("ok")'])
        self.assertEqual(kwargs['cwd'], str(ROOT_DIR))
        self.assertEqual(kwargs['env']['PYTHONDONTWRITEBYTECODE'], '1')
        self.assertEqual(kwargs['env']['PYTHONIOENCODING'], 'UTF-8')
        self.assertEqual(kwargs['env']['PYTHONUTF8'], '1')
        self.assertEqual(kwargs['env']['OPENCLAW_AGENT_CALL_SOURCE'], 'scheduler')
        self.assertEqual(
            kwargs['env']['PYTHONPATH'].split(os.pathsep)[:1],
            [str((ROOT_DIR / 'python').resolve())],
        )
        self.assertNotIn(str(ROOT_DIR.resolve()), kwargs['env']['PYTHONPATH'].split(os.pathsep))

    def test_resolve_runtime_tokens_substitutes_known_placeholders(self) -> None:
        base = Path('/tmp/demo')
        self.assertEqual(
            resolve_runtime_tokens(['--state', '{state_root}', '--repo', '{repo_root}'], state_root=base, repo_root=base.parent),
            ['--state', str(base), '--repo', str(base.parent)],
        )

    def test_cli_runtime_support_reuses_shared_runtime_token_resolver(self) -> None:
        self.assertIs(runtime_support.resolve_runtime_tokens, resolve_runtime_tokens)

    def test_schema_validation_uses_builtin_validator_without_jsonschema(self) -> None:
        with mock.patch('openclaw.control_plane.schema.importlib.import_module', side_effect=ModuleNotFoundError('jsonschema')):
            validate_payload_against_schema({'name': 'demo'}, {'type': 'object', 'required': ['name']}, label='demo', strict_dependency=True)
            with self.assertRaises(SchemaValidationError) as ctx:
                validate_payload_against_schema({}, {'type': 'object', 'required': ['name']}, label='demo', strict_dependency=True)
        self.assertIn('missing required property', str(ctx.exception))

    def test_builtin_schema_validator_enforces_project_keyword_subset(self) -> None:
        schema = {
            'type': 'object',
            'additionalProperties': False,
            'required': ['kind', 'name', 'args', 'settings'],
            'properties': {
                'kind': {'enum': ['shell', 'delivery_adapter']},
                'name': {'type': 'string', 'minLength': 3, 'pattern': '^[a-z_]+$'},
                'args': {'type': 'array', 'minItems': 1, 'uniqueItems': True, 'items': {'type': 'string', 'minLength': 1}},
                'settings': {
                    'type': 'object',
                    'minProperties': 1,
                    'propertyNames': {'pattern': '^[a-z][a-z0-9_]+$'},
                    'additionalProperties': {
                        'oneOf': [
                            {'type': 'string', 'minLength': 1},
                            {'type': 'integer', 'minimum': 1, 'exclusiveMinimum': 0},
                        ],
                    },
                },
                'command': {'type': 'string', 'minLength': 1},
                'operation': {'type': 'string', 'minLength': 1},
            },
            'allOf': [
                {'if': {'properties': {'kind': {'const': 'shell'}}}, 'then': {'required': ['command']}},
                {'if': {'properties': {'kind': {'const': 'delivery_adapter'}}}, 'then': {'required': ['operation']}},
            ],
        }
        payload = {
            'kind': 'shell',
            'name': 'demo_task',
            'args': ['run'],
            'settings': {'timeout_sec': 3},
            'command': 'echo ok',
        }
        with mock.patch('openclaw.control_plane.schema.importlib.import_module', side_effect=ModuleNotFoundError('jsonschema')):
            validate_payload_against_schema(payload, schema, label='demo', strict_dependency=True)
            for mutated in (
                {**payload, 'command': ''},
                {**payload, 'args': ['run', 'run']},
                {**payload, 'settings': {'BadKey': 'value'}},
                {**payload, 'settings': {'timeout_sec': 0}},
                {**payload, 'kind': 'delivery_adapter'},
            ):
                with self.subTest(payload=mutated):
                    with self.assertRaises(SchemaValidationError):
                        validate_payload_against_schema(mutated, schema, label='demo', strict_dependency=True)

    def test_runtime_adapter_registry_validates_callables_during_load(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            module_name = self._write_module(
                base,
                'adapter_runtime_registry',
                '\n'.join([
                    'def validate_config(*_args, **_kwargs):',
                    '    return True',
                    '',
                    'def run_adapter(*_args, **_kwargs):',
                    '    return 0',
                    '',
                ]),
            )
            registry_path = base / 'runtime_adapters.json'
            registry_path.write_text(
                textwrap.dedent(
                    f'''\
                    {{
                      "version": 1,
                      "adapters": [
                        {{
                          "id": "demo",
                          "title": "Demo",
                          "description": "Demo adapter",
                          "module": "{module_name}",
                          "configValidator": "validate_config",
                          "runner": "run_adapter",
                          "supportedEntrypointKinds": ["python_cli"],
                          "supportedExecutorKinds": ["python"]
                        }}
                      ]
                    }}
                    '''
                ),
                encoding='utf-8',
            )
            sys.path.insert(0, str(base))
            try:
                payload = load_runtime_adapter_registry(registry_path)
            finally:
                sys.path.remove(str(base))
                sys.modules.pop(module_name, None)
        self.assertEqual(payload['adapters'][0]['id'], 'demo')

    def test_runtime_adapter_registry_rejects_missing_callable_during_load(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            module_name = self._write_module(base, 'adapter_runtime_missing', 'def run_adapter(*_args, **_kwargs):\n    return 0\n')
            registry_path = base / 'runtime_adapters.json'
            registry_path.write_text(
                textwrap.dedent(
                    f'''\
                    {{
                      "version": 1,
                      "adapters": [
                        {{
                          "id": "demo",
                          "title": "Demo",
                          "description": "Demo adapter",
                          "module": "{module_name}",
                          "configValidator": "validate_config",
                          "runner": "run_adapter",
                          "supportedEntrypointKinds": ["python_cli"],
                          "supportedExecutorKinds": ["python"]
                        }}
                      ]
                    }}
                    '''
                ),
                encoding='utf-8',
            )
            sys.path.insert(0, str(base))
            try:
                with self.assertRaises(RuntimeAdapterRegistryError) as ctx:
                    load_runtime_adapter_registry(registry_path)
            finally:
                sys.path.remove(str(base))
                sys.modules.pop(module_name, None)
        self.assertIn('缺少可调用成员', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
