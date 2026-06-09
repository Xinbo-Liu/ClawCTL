from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from openclaw.control_plane.api.registry_items import _job_rows
from openclaw.control_plane.registry import (
    SCHEDULER_SERVICE_EXEC,
    CliError,
    build_agent_runtime_command,
    build_agent_runtime_command_spec,
    load_registry,
    resolve_dispatch_target_operation_command,
    resolve_dispatch_target_binding_ref,
    resolve_job_command,
    resolve_job_execution_plan,
    resolve_target_binding_ref_for_operation,
)
from openclaw.lib.dispatch import observability_surface as dispatch_observability_surface
from openclaw.lib.dispatch import operations_surface as dispatch_operations_surface
from openclaw.doctor.agent_modules.managed_probe_fixture import PROBE_EXTENSION_ID, PROBE_JOB_REF, PROBE_PRIMARY_MODULE_REF, PROBE_RUNTIME_ENTRY_ID, PROBE_TARGET_REF
from openclaw.lib.runtime.resolver_loader import build_path_resolver
from openclaw.tests.support.managed_probe import managed_probe_repo


PROBE_TARGET_QUALIFIED_REF = f'{PROBE_EXTENSION_ID}:{PROBE_TARGET_REF}'


class DispatchOperationResolutionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._fixture_context = managed_probe_repo('dispatch-resolution-shared')
        cls.fixture = cls._fixture_context.__enter__()
        cls.addClassCleanup(cls._fixture_context.__exit__, None, None, None)
        cls.registry = load_registry(cls.fixture.service_path)

    fixture: Any
    registry: dict[str, Any]

    def _write_target_copy(self, fixture: object, *, target_ref: str) -> None:
        source_path = fixture.targets_dir / f'{PROBE_TARGET_REF}.json'
        target_path = fixture.targets_dir / f'{target_ref}.json'
        payload = json.loads(source_path.read_text(encoding='utf-8'))
        payload['id'] = target_ref
        payload['title'] = f'Probe Dispatch Target {target_ref}'
        target_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def test_extension_profile_exposes_dispatch_runtime_path(self) -> None:
        resolver = build_path_resolver(repo_root=self.fixture.base_repo_root, config_path=self.fixture.service_path)
        self.assertIsNotNone(resolver)
        dispatch_out_dir = resolver.resolve_path(PROBE_RUNTIME_ENTRY_ID, 'host')
        self.assertTrue(dispatch_out_dir.endswith('probe_dispatch_out'))

    def test_explicit_target_binding_ref_is_accepted(self) -> None:
        resolved = resolve_target_binding_ref_for_operation(
            self.registry,
            target_binding_ref=PROBE_TARGET_REF,
            operation='preflight',
            agent_ref=PROBE_PRIMARY_MODULE_REF,
        )
        self.assertEqual(resolved, PROBE_TARGET_QUALIFIED_REF)

    def test_dispatch_target_maps_to_registered_target_binding(self) -> None:
        resolved = resolve_dispatch_target_binding_ref(
            self.registry,
            dispatch_target_id=PROBE_TARGET_REF,
            operation='preflight',
        )
        self.assertEqual(resolved, PROBE_TARGET_QUALIFIED_REF)

    def test_dispatch_target_id_is_accepted_by_unified_resolution(self) -> None:
        resolved = resolve_target_binding_ref_for_operation(
            self.registry,
            dispatch_target_id=PROBE_TARGET_REF,
            operation='preflight',
        )
        self.assertEqual(resolved, PROBE_TARGET_QUALIFIED_REF)

    def test_unique_operation_defaults_to_only_available_target_binding(self) -> None:
        resolved = resolve_target_binding_ref_for_operation(
            self.registry,
            operation='preflight',
            agent_ref=PROBE_PRIMARY_MODULE_REF,
        )
        self.assertEqual(resolved, PROBE_TARGET_QUALIFIED_REF)

    def test_missing_operation_default_resolution_is_rejected(self) -> None:
        with self.assertRaises(CliError):
            resolve_target_binding_ref_for_operation(
                self.registry,
                operation='missing_operation',
                agent_ref=PROBE_PRIMARY_MODULE_REF,
            )

    def test_ambiguous_operation_default_resolution_is_rejected(self) -> None:
        with managed_probe_repo('dispatch-resolution-default-ambiguous') as fixture:
            self._write_target_copy(fixture, target_ref='dispatch_target_secondary')
            registry = load_registry(fixture.service_path)
            with self.assertRaises(CliError):
                resolve_target_binding_ref_for_operation(
                    registry,
                    operation='preflight',
                    agent_ref=PROBE_PRIMARY_MODULE_REF,
                )

    def test_unknown_dispatch_target_is_rejected(self) -> None:
        with self.assertRaises(CliError):
            resolve_dispatch_target_binding_ref(
                self.registry,
                dispatch_target_id='dispatch_missing',
                operation='preflight',
            )

    def test_build_agent_runtime_command_uses_runtime_namespace_and_passthrough_separator(self) -> None:
        command = build_agent_runtime_command(
            agent={'id': 'dispatch_probe'},
            config_path='/tmp/service.json',
            extra_args=['--target', PROBE_TARGET_REF, '--dry-run', 'true'],
        )

        self.assertEqual(
            command[:6],
            [sys.executable, '-m', 'openclaw.cli', 'control-plane', 'runtime', 'run-agent-runtime'],
        )
        self.assertEqual(
            command[-5:],
            ['--', '--target', PROBE_TARGET_REF, '--dry-run', 'true'],
        )

    def test_build_agent_runtime_scheduler_spec_uses_explicit_exec_mode(self) -> None:
        spec = build_agent_runtime_command_spec(
            agent={'id': 'dispatch_probe'},
            config_path='/tmp/service.json',
            extra_args=['--target', PROBE_TARGET_REF],
            exec_mode=SCHEDULER_SERVICE_EXEC,
        )

        self.assertEqual(spec.exec_mode, SCHEDULER_SERVICE_EXEC)
        self.assertEqual(
            list(spec.argv[:4]),
            ['control-plane', 'runtime', 'scheduler-run-agent-runtime', '--agent-ref'],
        )
        self.assertEqual(list(spec.argv[-3:]), ['--', '--target', PROBE_TARGET_REF])

    def test_scheduler_service_exec_materializes_to_host_wrapper_command(self) -> None:
        command = resolve_dispatch_target_operation_command(
            self.registry,
            dispatch_target_id=PROBE_TARGET_REF,
            operation='preflight',
            extra_args=['--dry-run', 'true'],
            exec_mode=SCHEDULER_SERVICE_EXEC,
        )

        self.assertEqual(command[:5], ['bash', './scripts/runtime/run_openclaw_python_tool.sh', 'dispatch', 'ops', 'run-target-operation'])
        self.assertIn('--operation', command)
        self.assertIn('preflight', command)
        self.assertIn('--target', command)
        self.assertIn(PROBE_TARGET_REF, command)
        self.assertEqual(command[-3:], ['--', '--dry-run', 'true'])

    def test_bound_job_exposes_resolved_execution_plan_as_command_truth(self) -> None:
        plan = resolve_job_execution_plan(self.registry, PROBE_JOB_REF)
        command = resolve_job_command(self.registry, PROBE_JOB_REF)

        self.assertEqual(plan['kind'], 'subprocess_exec')
        self.assertEqual(plan['agentRef'], PROBE_PRIMARY_MODULE_REF)
        self.assertEqual(plan['resolvedAgentRef'], f'{PROBE_EXTENSION_ID}:{PROBE_PRIMARY_MODULE_REF}')
        self.assertEqual(plan['targetBindingRef'], PROBE_TARGET_QUALIFIED_REF)
        self.assertEqual(plan['commandSpec']['execMode'], 'direct_control_plane_exec')
        self.assertEqual(plan['commandSpec']['argv'][:6], ['control-plane', 'runtime', 'run-agent-runtime', '--agent-ref', f'{PROBE_EXTENSION_ID}:{PROBE_PRIMARY_MODULE_REF}', '--config-path'])
        self.assertEqual(command, plan['materializedCommand'])
        self.assertEqual(command[-2:], ['--', 'send'])

    def test_job_summary_exposes_execution_plan_without_materialized_command(self) -> None:
        rows = _job_rows(self.registry, {}, [])

        row = next(item for item in rows if item['id'] == PROBE_JOB_REF)
        plan = row['resolvedExecutionPlan']
        self.assertEqual(plan['kind'], 'subprocess_exec')
        self.assertIn('commandSpec', plan)
        self.assertNotIn('materializedCommand', plan)
        self.assertNotIn('resolvedCommand', row)
        self.assertNotIn('resolvedExecutor', row)
        self.assertNotIn('resolvedOperationRef', row)

    @patch('openclaw.control_plane.registry.commands.import_callable')
    def test_resolve_job_command_uses_runner_exec_plan_when_top_level_runner_fields_are_missing(self, import_mock: object) -> None:
        import_mock.return_value = lambda **_kwargs: {'command': ['python', '-m', 'demo']}
        registry = {
            'jobsById': {
                'demo_job': {
                    'id': 'demo_job',
                    'resolvedExecutionPlan': {
                        'schemaVersion': 1,
                        'kind': 'runner_exec',
                        'runnerRef': 'plan_runner',
                    },
                },
            },
            'jobRunnersById': {
                'plan_runner': {'module': 'demo.runner'},
            },
        }

        command = resolve_job_command(registry, 'demo_job')

        self.assertEqual(command, ['python', '-m', 'demo'])
        import_mock.assert_called_once_with('demo.runner', 'build_execution_plan', CliError, 'job demo_job runner plan_runner')

    @patch('openclaw.control_plane.registry.commands.import_callable')
    def test_resolve_job_command_prefers_runner_exec_plan_over_stale_top_level_runner_fields(self, import_mock: object) -> None:
        import_mock.return_value = lambda **_kwargs: {'command': ['python', '-m', 'demo']}
        registry = {
            'jobsById': {
                'demo_job': {
                    'id': 'demo_job',
                    'runnerRef': 'stale_runner',
                    'resolvedRunnerRef': 'stale_runner',
                    'resolvedExecutionPlan': {
                        'schemaVersion': 1,
                        'kind': 'runner_exec',
                        'runnerRef': 'plan_runner',
                    },
                },
            },
            'jobRunnersById': {
                'plan_runner': {'module': 'plan.runner'},
                'stale_runner': {'module': 'stale.runner'},
            },
        }

        command = resolve_job_command(registry, 'demo_job')

        self.assertEqual(command, ['python', '-m', 'demo'])
        import_mock.assert_called_once_with('plan.runner', 'build_execution_plan', CliError, 'job demo_job runner plan_runner')

    @patch('openclaw.lib.dispatch.operations_surface.target_acceptance_payload')
    @patch('openclaw.lib.dispatch.operations_surface.subprocess.run')
    def test_verify_target_uses_dispatch_target_id(self, run_mock: object, acceptance_mock: object) -> None:
        run_mock.return_value = SimpleNamespace(returncode=0)
        acceptance_mock.return_value = {
            'target_id': PROBE_TARGET_REF,
            'status': 'pass',
            'blocking_issues': [],
        }
        payload, exit_code = dispatch_operations_surface._verify_target(
            {
                'target': PROBE_TARGET_REF,
                'config_path': str(self.fixture.service_path),
                'real_send': False,
                'skip_explain': True,
                'skip_acceptance_summary': False,
                'fail_on_fail': False,
                'fail_on_warn': False,
            }
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload.get('status'), 'pass')
        commands = [call.args[0] for call in run_mock.call_args_list]
        self.assertEqual(len(commands), 2)
        for command in commands:
            self.assertEqual(command[0], sys.executable)
            self.assertEqual(command[3:6], ['control-plane', 'runtime', 'run-agent-runtime'])
            self.assertIn('--config-path', command)
            self.assertIn(str(self.fixture.service_path), command)
            self.assertIn('--target', command)
            self.assertIn(PROBE_TARGET_REF, command)
            self.assertIn('--', command)

    def test_verify_target_can_use_scheduler_execution_surface(self) -> None:
        opts = dispatch_operations_surface.parse_args([
            '--target',
            PROBE_TARGET_REF,
            '--config-path',
            str(self.fixture.service_path),
            '--execution-surface',
            'scheduler',
            '--skip-explain',
        ])
        with patch('openclaw.lib.dispatch.operations_surface.subprocess.run') as run_mock:
            with patch('openclaw.lib.dispatch.operations_surface.target_acceptance_payload') as acceptance_mock:
                run_mock.return_value = SimpleNamespace(returncode=0)
                acceptance_mock.return_value = {
                    'target_id': PROBE_TARGET_REF,
                    'status': 'pass',
                    'blocking_issues': [],
                }

                payload, exit_code = dispatch_operations_surface._verify_target(opts)

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload.get('status'), 'pass')
        commands = [call.args[0] for call in run_mock.call_args_list]
        self.assertEqual(len(commands), 2)
        for command in commands:
            self.assertEqual(command[:5], ['bash', './scripts/runtime/run_openclaw_python_tool.sh', 'dispatch', 'ops', 'run-target-operation'])
            self.assertIn('--config-path', command)
            self.assertIn(str(self.fixture.service_path), command)
            self.assertIn('--target', command)
            self.assertIn(PROBE_TARGET_REF, command)

    @patch('openclaw.lib.dispatch.operations_surface.target_acceptance_payload')
    @patch('openclaw.lib.dispatch.operations_surface.subprocess.run')
    def test_verify_target_returns_nonzero_when_operations_fail(self, run_mock: object, acceptance_mock: object) -> None:
        run_mock.return_value = SimpleNamespace(returncode=3)
        acceptance_mock.return_value = {
            'target_id': PROBE_TARGET_REF,
            'status': 'pass',
            'blocking_issues': [],
        }

        payload, exit_code = dispatch_operations_surface._verify_target(
            {
                'target': PROBE_TARGET_REF,
                'config_path': str(self.fixture.service_path),
                'real_send': False,
                'skip_explain': True,
                'skip_acceptance_summary': False,
                'fail_on_fail': False,
                'fail_on_warn': False,
            }
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(payload.get('status'), 'fail')
        self.assertEqual(payload.get('operation_failures'), ['preflight', 'send'])

    def test_dispatch_ops_accepts_control_plane_profile_arg(self) -> None:
        opts = dispatch_operations_surface.parse_args([
            '--control-plane-profile',
            'agent_platform',
        ])

        self.assertEqual(opts.get('control_plane_profile'), 'agent_platform')

    def test_dispatch_ops_config_path_can_resolve_gate_env_file(self) -> None:
        env_file = self.fixture.repo_root / 'deploy' / '.env'
        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.write_text(
            f'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH={self.fixture.service_path}\n',
            encoding='utf-8',
        )
        opts = dispatch_operations_surface.parse_args([
            '--gate-env-file',
            str(env_file),
        ])

        self.assertEqual(dispatch_operations_surface._config_path(opts), self.fixture.service_path)

    def test_dispatch_observability_config_path_can_resolve_gate_env_file(self) -> None:
        env_file = self.fixture.repo_root / 'deploy' / '.env'
        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.write_text(
            f'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH={self.fixture.service_path}\n',
            encoding='utf-8',
        )
        opts = dispatch_observability_surface.parse_args([
            '--gate-env-file',
            str(env_file),
        ])

        self.assertEqual(dispatch_observability_surface._config_path(opts), self.fixture.service_path)

    def test_dispatch_observability_accepts_control_plane_profile_arg(self) -> None:
        opts = dispatch_observability_surface.parse_args([
            '--control-plane-profile',
            'agent_platform',
        ])

        self.assertEqual(opts.get('control_plane_profile'), 'agent_platform')

    def test_dispatch_observability_acceptance_loads_gate_env_values(self) -> None:
        env_key = 'OPENCLAW_TEST_OBSERVABILITY_GATE'
        old_value = os.environ.pop(env_key, None)
        env_file = self.fixture.repo_root / 'deploy' / '.env'
        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.write_text(
            '\n'.join(
                [
                    f'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH={self.fixture.service_path}',
                    f'{env_key}=visible',
                    '',
                ]
            ),
            encoding='utf-8',
        )
        opts = dispatch_observability_surface.parse_args([
            '--target',
            PROBE_TARGET_REF,
            '--gate-env-file',
            str(env_file),
        ])

        def fake_acceptance(target_id: str, *, config_path: object | None = None) -> dict[str, object]:
            return {
                'target_id': target_id,
                'config_path': str(config_path),
                'status': 'pass',
                'seen_env': os.environ.get(env_key),
            }

        try:
            with patch('openclaw.lib.dispatch.observability_surface.target_acceptance_payload', side_effect=fake_acceptance):
                payload, exit_code = dispatch_observability_surface._target_acceptance(opts)
        finally:
            if old_value is None:
                os.environ.pop(env_key, None)
            else:
                os.environ[env_key] = old_value

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload.get('seen_env'), 'visible')
        self.assertEqual(payload.get('config_path'), str(self.fixture.service_path))

    def test_verify_target_requires_target_with_cli_error_exit_code(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as exc:
                dispatch_operations_surface._verify_target(
                    {
                        'target': '',
                        'config_path': '',
                        'real_send': False,
                        'skip_explain': False,
                        'skip_acceptance_summary': False,
                        'fail_on_fail': False,
                        'fail_on_warn': False,
                    }
                )
        self.assertEqual(exc.exception.code, 2)
        self.assertIn('verify-target requires --target', stderr.getvalue())


if __name__ == '__main__':
    unittest.main()
