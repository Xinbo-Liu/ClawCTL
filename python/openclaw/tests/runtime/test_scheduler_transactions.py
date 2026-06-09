from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from openclaw.control_plane.agent import runtime_runner as agent_runtime_runner
from openclaw.control_plane import evidence_export
from openclaw.control_plane import run_ledger
from openclaw.control_plane.registry.runtime_manifests import read_runtime_manifest_json
from openclaw.doctor.agent_modules.managed_probe_fixture import PROBE_RUNTIME_ENTRY_ID
from openclaw.lib.cli.common import CliError
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.scheduler import engine
from openclaw.scheduler import runtime
from openclaw.scheduler.subprocess_runner import run_subprocess_job_impl
from openclaw.tests.support.managed_probe import managed_probe_repo
ROOT_DIR = resolve_repo_root(Path(__file__))
AGENT_PLATFORM_CONFIG = (ROOT_DIR / 'config' / 'control_plane' / 'profiles' / 'agent_platform.service.json').resolve()


def _history_row(**kwargs: object) -> dict[str, object]:
    payload: dict[str, object] = {
        'status': kwargs['status'],
        'runId': kwargs['due_key'],
        'finished_at': '2026-04-22T00:00:00Z',
    }
    if kwargs.get('reason'):
        payload['reason'] = kwargs['reason']
    payload.update(kwargs.get('extra') or {})
    return payload


class SchedulerTransactionTest(unittest.TestCase):
    def test_agent_runtime_runner_prepares_job_from_resolved_execution_plan_only(self) -> None:
        job = {
            'id': 'demo_job',
            'runnerRef': 'agent_runtime',
            'resolvedExecutionPlan': {
                'schemaVersion': 1,
                'kind': 'subprocess_exec',
                'runnerRef': 'agent_runtime',
                'operationRef': 'run_default',
                'executor': {'kind': 'python_cli', 'argv': ['run']},
                'materializedCommand': ['python', '-m', 'openclaw.cli', 'control-plane', 'runtime', 'run-agent-runtime'],
            },
        }

        plan = agent_runtime_runner.prepare_job(job=job, config={})

        self.assertEqual(plan['operationRef'], 'run_default')
        self.assertEqual(plan['executor'], {'kind': 'python_cli', 'argv': ['run']})
        self.assertEqual(plan['command'], ['python', '-m', 'openclaw.cli', 'control-plane', 'runtime', 'run-agent-runtime'])

    def test_agent_runtime_runner_reports_runner_ref_from_execution_plan(self) -> None:
        job = {
            'id': 'demo_job',
            'runnerRef': 'stale_runner',
            'resolvedExecutionPlan': {
                'schemaVersion': 1,
                'kind': 'subprocess_exec',
                'runnerRef': 'plan_runner',
                'materializedCommand': ['python', '-c', 'print("ok")'],
            },
        }

        plan = agent_runtime_runner.prepare_job(job=job, config={})

        self.assertEqual(plan['runnerRef'], 'plan_runner')

    def test_agent_runtime_runner_rejects_runner_plan_without_command_fallback(self) -> None:
        job = {
            'id': 'demo_job',
            'runnerRef': 'agent_runtime',
            'resolvedExecutionPlan': {
                'schemaVersion': 1,
                'kind': 'runner_exec',
                'runnerRef': 'agent_runtime',
            },
        }

        with self.assertRaises(CliError):
            agent_runtime_runner.prepare_job(job=job, config={'agentsById': {}, 'agentModulesById': {}})

    def test_scheduler_run_job_uses_resolved_execution_plan_without_runner_import(self) -> None:
        job = {
            'id': 'demo_job',
            'title': 'Demo Job',
            'runnerRef': 'agent_runtime',
            'resolvedRunnerRef': 'agent_runtime',
            'resolvedExecutionPlan': {
                'schemaVersion': 1,
                'kind': 'subprocess_exec',
                'runnerRef': 'agent_runtime',
                'materializedCommand': ['python', '-c', 'print("ok")'],
            },
        }
        with mock.patch(
            'openclaw.scheduler.engine.import_extension_callable',
            side_effect=AssertionError('runner import should not be used for subprocess_exec plans'),
        ):
            with mock.patch(
                'openclaw.scheduler.engine.run_subprocess_job',
                return_value={'status': 'succeeded', 'runId': 'demo_job@schedule@2026-04-22T00:00'},
            ) as run_mock:
                result = engine.run_job(
                    job=job,
                    config={'jobRunnersById': {'agent_runtime': {}}},
                    files=SimpleNamespace(),
                    job_state={},
                    due_key='demo_job@schedule@2026-04-22T00:00',
                    current=datetime(2026, 4, 22, tzinfo=timezone.utc),
                    force_all=False,
                )

        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(run_mock.call_args.kwargs['command'], ['python', '-c', 'print("ok")'])

    def test_scheduler_run_job_prefers_command_spec_over_stale_materialized_projection(self) -> None:
        job = {
            'id': 'demo_job',
            'title': 'Demo Job',
            'runnerRef': 'agent_runtime',
            'resolvedRunnerRef': 'agent_runtime',
            'resolvedExecutionPlan': {
                'schemaVersion': 1,
                'kind': 'subprocess_exec',
                'runnerRef': 'agent_runtime',
                'commandSpec': {
                    'execMode': 'direct_control_plane_exec',
                    'argv': ['control-plane', 'runtime', 'run-agent-runtime', '--agent-ref', 'demo_agent'],
                },
                'materializedCommand': ['stale-python', '-m', 'stale.module'],
            },
        }
        with mock.patch(
            'openclaw.scheduler.engine.run_subprocess_job',
            return_value={'status': 'succeeded', 'runId': 'demo_job@schedule@2026-04-22T00:00'},
        ) as run_mock:
            engine.run_job(
                job=job,
                config={'jobRunnersById': {'agent_runtime': {}}},
                files=SimpleNamespace(),
                job_state={},
                due_key='demo_job@schedule@2026-04-22T00:00',
                current=datetime(2026, 4, 22, tzinfo=timezone.utc),
                force_all=False,
            )

        command = run_mock.call_args.kwargs['command']
        self.assertEqual(command[:6], [sys.executable, '-m', 'openclaw.cli', 'control-plane', 'runtime', 'run-agent-runtime'])
        self.assertEqual(command[-2:], ['--agent-ref', 'demo_agent'])

    def test_scheduler_subprocess_env_exposes_config_path_and_scheduler_view(self) -> None:
        context = SimpleNamespace(
            config={'configPath': str(AGENT_PLATFORM_CONFIG)},
            job={'id': 'managed_extension_job'},
            due_key='managed_extension_job@force_all@2026-04-26T13:45',
            trigger='force_all',
        )

        env = run_subprocess_job_impl.__globals__['_scheduler_env'](context)

        self.assertEqual(env['OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH'], context.config['configPath'])
        self.assertEqual(env['OPENCLAW_RUNTIME_PATH_VIEW'], 'scheduler')

    def test_run_ledger_resolves_extension_artifact_root_from_scheduler_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, managed_probe_repo('run-ledger-probe') as fixture:
            state_root = Path(tmpdir).resolve()
            resolved = run_ledger.resolve_artifact_root(
                PROBE_RUNTIME_ENTRY_ID,
                env={
                    'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH': str(fixture.service_path),
                    'OPENCLAW_RUNTIME_PATH_VIEW': 'scheduler',
                    'OPENCLAW_STATE_DIR': str(state_root),
                },
            )

        self.assertEqual(resolved, state_root / 'probe_dispatch_out')

    def test_run_ledger_reads_scheduler_manifest_paths_from_host_control_plane_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            host_control_plane_root = Path(tmpdir).resolve() / 'control_plane'
            scheduler_root = Path('/home/openclaw/.openclaw')
            run_rel = Path('control_plane_scheduler') / 'runs' / 'demo-run'
            run_dir = host_control_plane_root / run_rel
            run_dir.mkdir(parents=True)
            (run_dir / 'run.json').write_text(json.dumps({'runId': 'demo-run'}), encoding='utf-8')
            (run_dir / 'result.json').write_text(
                json.dumps({'runId': 'demo-run', 'status': 'succeeded', 'acceptedByLedger': True}),
                encoding='utf-8',
            )
            (run_dir / 'artifacts.json').write_text(
                json.dumps({'runId': 'demo-run', 'acceptance': {'passed': True, 'reasons': []}}),
                encoding='utf-8',
            )

            class FakeResolver:
                def resolve_path(self, entry_id: str, view: str = 'host', env: dict[str, str] | None = None) -> str:
                    if entry_id == 'state_root' and view == 'scheduler':
                        return str(scheduler_root)
                    if entry_id == 'control_plane_host_state_dir' and view == 'host':
                        return str(host_control_plane_root)
                    raise KeyError(entry_id)

            job_state = {
                'currentStatus': 'succeeded',
                'lastFinishedAt': '2026-04-22T00:00:00Z',
                'lastRunId': 'demo-run',
                'lastRunManifestPath': str(scheduler_root / run_rel / 'run.json'),
                'lastResultManifestPath': str(scheduler_root / run_rel / 'result.json'),
                'lastArtifactsPath': str(scheduler_root / run_rel / 'artifacts.json'),
            }
            with mock.patch.object(run_ledger, '_resolver', return_value=FakeResolver()):
                row = run_ledger.build_job_ledger_row({'id': 'demo_job', 'title': 'Demo Job'}, job_state)

        self.assertIs(row['accepted'], True)
        self.assertEqual(row['issues'], [])
        self.assertEqual(row['latestRun']['runId'], 'demo-run')
        self.assertEqual(row['latestResult']['status'], 'succeeded')

    def test_scheduler_state_uses_runtime_job_key_only(self) -> None:
        state = {'jobs': {'demo_job': {'currentStatus': 'succeeded'}}}

        job_state = engine.ensure_job_state(state, 'agent_demo:demo_job', 'Demo Job')

        self.assertEqual(job_state['jobId'], 'agent_demo:demo_job')
        self.assertEqual(job_state['currentStatus'], engine.STATUS_SCHEDULED)
        self.assertEqual(state['jobs']['demo_job']['currentStatus'], 'succeeded')

    def test_run_ledger_summary_uses_runtime_job_key_only(self) -> None:
        registry = {
            'service': {'name': 'test'},
            'configPath': '',
            'jobs': [
                {
                    'id': 'demo_job',
                    'qualifiedId': 'agent_demo:demo_job',
                    'resolvedRuntimeJobKey': 'agent_demo:demo_job',
                    'title': 'Demo Job',
                    'enabled': True,
                },
            ],
        }
        state = {'jobs': {'demo_job': {'currentStatus': 'succeeded'}}}

        summary = run_ledger.build_run_ledger_summary(registry, state)
        row = summary['items'][0]

        self.assertEqual(row['runtimeJobKey'], 'agent_demo:demo_job')
        self.assertEqual(row['currentStatus'], '')
        self.assertEqual(summary['counts']['missingJobs'], 1)

    def test_run_ledger_ignores_manifest_paths_outside_runtime_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            host_control_plane_root = root / 'control_plane'
            scheduler_root = Path('/home/openclaw/.openclaw')
            outside = root / 'outside.json'
            outside.write_text(json.dumps({'runId': 'leaked'}), encoding='utf-8')

            class FakeResolver:
                def resolve_path(self, entry_id: str, view: str = 'host', env: dict[str, str] | None = None) -> str:
                    if entry_id == 'state_root' and view == 'scheduler':
                        return str(scheduler_root)
                    if entry_id == 'control_plane_host_state_dir' and view == 'host':
                        return str(host_control_plane_root)
                    raise KeyError(entry_id)

            job_state = {
                'currentStatus': 'succeeded',
                'lastRunManifestPath': str(outside),
                'lastResultManifestPath': str(outside),
                'lastArtifactsPath': str(outside),
            }
            with mock.patch.object(run_ledger, '_resolver', return_value=FakeResolver()):
                row = run_ledger.build_job_ledger_row({'id': 'demo_job', 'title': 'Demo Job'}, job_state)

        self.assertIsNone(row['latestRun'])
        self.assertIn('missing_run_manifest', row['issues'])
        self.assertIn('missing_result_manifest', row['issues'])
        self.assertIn('missing_artifacts_manifest', row['issues'])

    def test_runtime_manifest_reader_does_not_allow_whole_state_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir).resolve()
            gateway_payload = state_root / 'gateway' / 'openclaw.json'
            gateway_payload.parent.mkdir(parents=True)
            gateway_payload.write_text(json.dumps({'token': 'leaked'}), encoding='utf-8')
            scheduler_payload = state_root / 'control_plane_scheduler' / 'runs' / 'demo' / 'run.json'
            scheduler_payload.parent.mkdir(parents=True)
            scheduler_payload.write_text(json.dumps({'runId': 'demo'}), encoding='utf-8')

            class MissingResolver:
                def __call__(self, _config_path):
                    raise RuntimeError('resolver unavailable')

            env = {'OPENCLAW_STATE_DIR': str(state_root)}
            leaked = read_runtime_manifest_json(gateway_payload, env=env, resolver_factory=MissingResolver())
            allowed = read_runtime_manifest_json(scheduler_payload, env=env, resolver_factory=MissingResolver())

        self.assertIsNone(leaked)
        self.assertEqual(allowed, {'runId': 'demo'})

    def test_scheduler_run_job_uses_runner_exec_plan_when_top_level_runner_fields_are_missing(self) -> None:
        job = {
            'id': 'demo_job',
            'title': 'Demo Job',
            'resolvedExecutionPlan': {
                'schemaVersion': 1,
                'kind': 'runner_exec',
                'runnerRef': 'plan_runner',
            },
        }
        runner = mock.Mock(return_value={'status': 'succeeded', 'runId': 'demo_job@schedule@2026-04-22T00:00'})
        with mock.patch('openclaw.scheduler.engine.import_extension_callable', return_value=runner) as import_mock:
            result = engine.run_job(
                job=job,
                config={'jobRunnersById': {'plan_runner': {'module': 'demo.runner', 'callable': 'run'}}},
                files=SimpleNamespace(),
                job_state={},
                due_key='demo_job@schedule@2026-04-22T00:00',
                current=datetime(2026, 4, 22, tzinfo=timezone.utc),
                force_all=False,
            )

        self.assertEqual(result['status'], 'succeeded')
        import_mock.assert_called_once_with('demo.runner', 'run')
        runner.assert_called_once()

    def test_scheduler_run_job_prefers_runner_exec_plan_over_stale_top_level_runner_fields(self) -> None:
        job = {
            'id': 'demo_job',
            'title': 'Demo Job',
            'runnerRef': 'stale_runner',
            'resolvedRunnerRef': 'stale_runner',
            'resolvedExecutionPlan': {
                'schemaVersion': 1,
                'kind': 'runner_exec',
                'runnerRef': 'plan_runner',
            },
        }
        runner = mock.Mock(return_value={'status': 'succeeded', 'runId': 'demo_job@schedule@2026-04-22T00:00'})
        config = {
            'jobRunnersById': {
                'plan_runner': {'module': 'plan.runner', 'callable': 'run'},
                'stale_runner': {'module': 'stale.runner', 'callable': 'run'},
            },
        }
        with mock.patch('openclaw.scheduler.engine.import_extension_callable', return_value=runner) as import_mock:
            result = engine.run_job(
                job=job,
                config=config,
                files=SimpleNamespace(),
                job_state={},
                due_key='demo_job@schedule@2026-04-22T00:00',
                current=datetime(2026, 4, 22, tzinfo=timezone.utc),
                force_all=False,
            )

        self.assertEqual(result['status'], 'succeeded')
        import_mock.assert_called_once_with('plan.runner', 'run')
        runner.assert_called_once()

    def test_scheduler_run_job_rejects_malformed_execution_plans(self) -> None:
        malformed_plans = [
            ('unknown_kind', {'schemaVersion': 1, 'kind': 'mystery_exec', 'runnerRef': 'agent_runtime'}),
            ('missing_command', {'schemaVersion': 1, 'kind': 'subprocess_exec', 'runnerRef': 'agent_runtime'}),
            (
                'invalid_exec_mode',
                {
                    'schemaVersion': 1,
                    'kind': 'subprocess_exec',
                    'runnerRef': 'agent_runtime',
                    'commandSpec': {
                        'execMode': 'invalid_exec',
                        'argv': ['control-plane', 'runtime', 'run-agent-runtime'],
                    },
                },
            ),
            ('runner_exec_missing_runner_ref', {'schemaVersion': 1, 'kind': 'runner_exec'}),
        ]
        for label, execution_plan in malformed_plans:
            with self.subTest(label=label):
                with self.assertRaises(CliError):
                    engine.run_job(
                        job={
                            'id': 'demo_job',
                            'title': 'Demo Job',
                            'runnerRef': 'agent_runtime',
                            'resolvedExecutionPlan': execution_plan,
                        },
                        config={'jobRunnersById': {'agent_runtime': {}}},
                        files=SimpleNamespace(),
                        job_state={},
                        due_key='demo_job@schedule@2026-04-22T00:00',
                        current=datetime(2026, 4, 22, tzinfo=timezone.utc),
                        force_all=False,
                    )

    def test_run_subprocess_job_impl_returns_blocked_result_when_lock_is_busy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            files = SimpleNamespace(runs_dir=Path(tmpdir) / 'runs')
            result = run_subprocess_job_impl(
                job={'id': 'demo_job', 'title': 'Demo Job', 'timeoutSeconds': 30},
                config={'configPath': ''},
                files=files,
                job_state={},
                due_key='demo_job@schedule@2026-04-22T00:00',
                current=datetime(2026, 4, 22, tzinfo=timezone.utc),
                force_all=False,
                command=['python', '-c', 'print("ok")'],
                lock_path=Path(tmpdir) / 'demo_job.lock',
                stale_after_seconds=60,
                acquire_lock=lambda *_args, **_kwargs: False,
                release_lock=lambda *_args, **_kwargs: None,
                history_row_builder=_history_row,
                now_utc_iso=lambda: '2026-04-22T00:00:00Z',
            )

        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['run_dir'], None)
        self.assertEqual(result['log_path'], None)

    def test_run_subprocess_job_impl_writes_consistent_run_result_and_artifact_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            files = SimpleNamespace(runs_dir=Path(tmpdir) / 'runs')
            job_state: dict[str, object] = {}
            with mock.patch(
                'openclaw.scheduler.subprocess_runner.subprocess.run',
                return_value=SimpleNamespace(returncode=0),
            ):
                result = run_subprocess_job_impl(
                    job={
                        'id': 'demo_job',
                        'title': 'Demo Job',
                        'timeoutSeconds': 30,
                        'resolvedModelProfileRef': 'ollama_default',
                        'resolvedModelProfileQualifiedRef': 'ext_alpha:ollama_default',
                    },
                    config={'configPath': ''},
                    files=files,
                    job_state=job_state,
                    due_key='demo_job@schedule@2026-04-22T00:00',
                    current=datetime(2026, 4, 22, tzinfo=timezone.utc),
                    force_all=False,
                    command=['python', '-c', 'print("ok")'],
                    lock_path=Path(tmpdir) / 'demo_job.lock',
                    stale_after_seconds=60,
                    acquire_lock=lambda *_args, **_kwargs: True,
                    release_lock=lambda *_args, **_kwargs: None,
                    history_row_builder=_history_row,
                    now_utc_iso=lambda: '2026-04-22T00:00:00Z',
                )

            run_dir = Path(str(result['run_dir']))
            run_payload = json.loads((run_dir / 'run.json').read_text(encoding='utf-8'))
            result_payload = json.loads((run_dir / 'result.json').read_text(encoding='utf-8'))
            artifacts_payload = json.loads((run_dir / 'artifacts.json').read_text(encoding='utf-8'))

        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(run_payload['runId'], result_payload['runId'])
        self.assertEqual(run_payload['runId'], artifacts_payload['runId'])
        self.assertEqual(run_payload['modelProfileRef'], 'ext_alpha:ollama_default')
        self.assertEqual(run_payload['stdoutLogPath'], result_payload['stdoutLogPath'])
        self.assertEqual(result_payload['acceptedByLedger'], result['accepted_by_ledger'])
        self.assertEqual(job_state['activeRun'], None)

    def test_run_subprocess_job_impl_releases_lock_when_env_build_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            files = SimpleNamespace(runs_dir=Path(tmpdir) / 'runs')
            job_state: dict[str, object] = {}
            released: list[Path] = []
            with mock.patch(
                'openclaw.scheduler.subprocess_runner.build_subprocess_env',
                side_effect=RuntimeError('env boom'),
            ):
                result = run_subprocess_job_impl(
                    job={'id': 'demo_job', 'title': 'Demo Job', 'timeoutSeconds': 30},
                    config={'configPath': ''},
                    files=files,
                    job_state=job_state,
                    due_key='demo_job@schedule@2026-04-22T00:00',
                    current=datetime(2026, 4, 22, tzinfo=timezone.utc),
                    force_all=False,
                    command=['python', '-c', 'print("ok")'],
                    lock_path=Path(tmpdir) / 'demo_job.lock',
                    stale_after_seconds=60,
                    acquire_lock=lambda *_args, **_kwargs: True,
                    release_lock=lambda path, *_args, **_kwargs: released.append(path),
                    history_row_builder=_history_row,
                    now_utc_iso=lambda: '2026-04-22T00:00:00Z',
                )

        self.assertEqual(result['status'], 'failed')
        self.assertIn('env boom', str(result.get('reason') or ''))
        self.assertEqual(released, [Path(tmpdir) / 'demo_job.lock'])
        self.assertEqual(job_state.get('activeRun'), None)

    def test_run_subprocess_job_impl_releases_lock_when_mark_running_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            files = SimpleNamespace(runs_dir=Path(tmpdir) / 'runs')
            job_state: dict[str, object] = {}
            released: list[Path] = []
            with mock.patch(
                'openclaw.scheduler.subprocess_runner._mark_job_running',
                side_effect=RuntimeError('write boom'),
            ):
                result = run_subprocess_job_impl(
                    job={'id': 'demo_job', 'title': 'Demo Job', 'timeoutSeconds': 30},
                    config={'configPath': ''},
                    files=files,
                    job_state=job_state,
                    due_key='demo_job@schedule@2026-04-22T00:00',
                    current=datetime(2026, 4, 22, tzinfo=timezone.utc),
                    force_all=False,
                    command=['python', '-c', 'print("ok")'],
                    lock_path=Path(tmpdir) / 'demo_job.lock',
                    stale_after_seconds=60,
                    acquire_lock=lambda *_args, **_kwargs: True,
                    release_lock=lambda path, *_args, **_kwargs: released.append(path),
                    history_row_builder=_history_row,
                    now_utc_iso=lambda: '2026-04-22T00:00:00Z',
                )

        self.assertEqual(result['status'], 'failed')
        self.assertIn('write boom', str(result.get('reason') or ''))
        self.assertEqual(released, [Path(tmpdir) / 'demo_job.lock'])
        self.assertEqual(job_state.get('activeRun'), None)

    def test_agent_group_evidence_failure_is_structured_in_state_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir).resolve()
            with mock.patch(
                'openclaw.scheduler.runtime.export_agent_group_evidence',
                side_effect=RuntimeError('boom'),
            ):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    payload = runtime._maybe_export_agent_group_evidence(
                        config={'service': {'autoExportAgentGroupEvidence': True}},
                        state_root=state_root,
                        execution={'executed_count': 1, 'blocked_count': 0},
                    )

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertIn('agent group evidence export 失败', stderr.getvalue())
        self.assertEqual(payload['status'], 'failed')
        self.assertEqual(payload['errorType'], 'RuntimeError')
        self.assertEqual(payload['executedCount'], 1)
        self.assertEqual(payload['blockedCount'], 0)
        self.assertEqual(payload['stateRoot'], str(state_root))
        self.assertEqual(payload['baseRoot'], str(ROOT_DIR))

    def test_agent_group_evidence_write_uses_exact_object_family_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / 'state' / 'control_plane' / 'release' / 'evidence' / 'run_ledger.json'

            evidence_export._write_json(target, {'ok': True})

            self.assertTrue(target.is_file())
            self.assertIn('"ok": true', target.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
