from __future__ import annotations

import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from openclaw.lib.repo.layout import CONTROL_PLANE_CONTAINER_REPO_ROOT, resolve_repo_root
from tempfile import TemporaryDirectory
from unittest import mock

from openclaw.internal_api.routes.control_plane import (
    render_agent_access_log,
    render_agent_group_access,
    render_jobs,
    render_run_ledger,
    render_summary,
)
from openclaw.internal_api.routes.health import render_ready, reset_ready_cache
from openclaw.scheduler.engine import runtime_job_key
from openclaw.scheduler import runtime as scheduler_runtime
from openclaw.tests.support.managed_extensions import (
    cron_jobs,
    representative_managed_extension_registry,
)

ROOT_DIR = resolve_repo_root(Path(__file__))
BASE_CONFIG = (ROOT_DIR / 'config' / 'control_plane' / 'service.json').resolve()


class ControlPlaneReadonlySmokeTest(unittest.TestCase):
    def tearDown(self) -> None:
        reset_ready_cache()

    def test_base_profile_readonly_surfaces_render_without_builtin_business_assets(self) -> None:
        with TemporaryDirectory() as tmpdir:
            env = {
                'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH': str(BASE_CONFIG),
                'HOST_STATE_DIR': tmpdir,
                'OPENCLAW_READY_CACHE_TTL_SECONDS': '0',
            }
            with mock.patch.dict(os.environ, env, clear=False):
                summary = render_summary()
                jobs = render_jobs()
                run_ledger = render_run_ledger()
                access_log = render_agent_access_log(limit=10)
                group_access = render_agent_group_access(limit=10, timeline_limit=5)
                ready = render_ready()
        self.assertEqual((summary.get('counts') or {}).get('agents'), 0)
        self.assertEqual((summary.get('counts') or {}).get('agentGroups'), 0)
        registry_path_details = summary.get('registryPathDetails') or {}
        for key in ('jobs', 'models', 'targets'):
            self.assertEqual(len(registry_path_details.get(key) or []), 1)
            self.assertTrue((registry_path_details.get(key) or [])[0].get('exists'))
            self.assertFalse((registry_path_details.get(key) or [])[0].get('configuredButMissing'))
        self.assertEqual(len(jobs.get('items') or []), 0)
        self.assertEqual(((run_ledger.get('counts') or {}).get('jobs')), 0)
        self.assertEqual(len(access_log.get('items') or []), 0)
        self.assertEqual(len(group_access.get('items') or []), 0)
        self.assertIn(ready.get('status'), {'ready', 'degraded'})
        self.assertIn('schedulerHeartbeat', ready.get('checks') or {})

    def test_base_profile_scheduler_once_makes_readyz_healthy(self) -> None:
        with TemporaryDirectory() as tmpdir:
            env = {
                'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH': str(BASE_CONFIG),
                'HOST_STATE_DIR': tmpdir,
                'OPENCLAW_READY_CACHE_TTL_SECONDS': '0',
            }
            with mock.patch.dict(os.environ, env, clear=False):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    rc = scheduler_runtime.main(['--once'])
                ready = render_ready()
        self.assertEqual(rc, 0)
        self.assertEqual(stderr.getvalue(), '')
        self.assertEqual(ready.get('status'), 'ready')
        self.assertTrue(((ready.get('checks') or {}).get('schedulerHeartbeat') or {}).get('ok'))

    def test_scheduler_runtime_maps_container_config_path_on_host(self) -> None:
        with TemporaryDirectory() as tmpdir:
            container_config = f'{CONTROL_PLANE_CONTAINER_REPO_ROOT}/config/control_plane/service.json'
            env = {
                'HOST_STATE_DIR': tmpdir,
                'OPENCLAW_READY_CACHE_TTL_SECONDS': '0',
            }
            with mock.patch.dict(os.environ, env, clear=False):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    rc = scheduler_runtime.main(['--once', '--config-path', container_config])

        self.assertEqual(rc, 0, msg=stderr.getvalue())

    def test_scheduler_runtime_resolves_config_from_env_file(self) -> None:
        with TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / 'deploy.env'
            env_file.write_text(
                '\n'.join(
                    [
                        f'HOST_STATE_DIR={tmpdir}',
                        f'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH={CONTROL_PLANE_CONTAINER_REPO_ROOT}/config/control_plane/service.json',
                        'OPENCLAW_READY_CACHE_TTL_SECONDS=0',
                    ]
                ) + '\n',
                encoding='utf-8',
            )
            with mock.patch.dict(os.environ, {'PYTHONIOENCODING': 'UTF-8', 'PYTHONUTF8': '1'}, clear=True):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    rc = scheduler_runtime.main(['--once', '--env-file', str(env_file)])

        self.assertEqual(rc, 0, msg=stderr.getvalue())

    def test_scheduler_runtime_validates_env_file_numeric_defaults(self) -> None:
        with TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / 'deploy.env'
            env_file.write_text(
                '\n'.join(
                    [
                        f'HOST_STATE_DIR={tmpdir}',
                        f'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH={CONTROL_PLANE_CONTAINER_REPO_ROOT}/config/control_plane/service.json',
                        'OPENCLAW_CONTROL_PLANE_TICK_SECONDS=bad',
                    ]
                ) + '\n',
                encoding='utf-8',
            )
            with mock.patch.dict(os.environ, {'PYTHONIOENCODING': 'UTF-8', 'PYTHONUTF8': '1'}, clear=True):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    rc = scheduler_runtime.main(['--once', '--env-file', str(env_file)])

        self.assertEqual(rc, 2)
        self.assertIn('scheduler runtime 数值参数无效', stderr.getvalue())

    def test_scheduler_maintenance_keeps_heartbeat_and_skips_jobs(self) -> None:
        with TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir) / 'control_plane'
            enable_stdout = io.StringIO()
            with contextlib.redirect_stdout(enable_stdout):
                enable_rc = scheduler_runtime.main([
                    'maintenance',
                    'enable',
                    '--state-root',
                    str(state_root),
                    '--reason',
                    'unit_test',
                    '--json',
                ])
            with mock.patch.dict(os.environ, {'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH': str(BASE_CONFIG)}, clear=False):
                with mock.patch.object(scheduler_runtime, 'execute_due_jobs', side_effect=AssertionError('maintenance should skip jobs')):
                    rc = scheduler_runtime.main(['--once', '--state-root', str(state_root), '--config-path', str(BASE_CONFIG)])

            status_path = state_root / 'control_plane_scheduler_status.json'
            status = json.loads(status_path.read_text(encoding='utf-8'))
            self.assertEqual(enable_rc, 0)
            self.assertEqual(rc, 0)
            self.assertTrue(json.loads(enable_stdout.getvalue())['enabled'])
            self.assertEqual(status['execution']['mode'], 'maintenance')
            self.assertTrue(status['execution']['jobsSkipped'])

    def test_scheduler_once_does_not_write_when_cycle_lock_is_busy(self) -> None:
        with TemporaryDirectory() as tmpdir:
            env = {
                'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH': str(BASE_CONFIG),
                'HOST_STATE_DIR': tmpdir,
            }
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch.object(scheduler_runtime.scheduler_locking, 'acquire_lock', return_value=False):
                    with mock.patch.object(scheduler_runtime.scheduler_locking, 'release_lock') as release_mock:
                        stderr = io.StringIO()
                        with contextlib.redirect_stderr(stderr):
                            rc = scheduler_runtime.main(['--once'])

            self.assertEqual(rc, 5)
            self.assertIn('scheduler cycle lock busy', stderr.getvalue())
            self.assertEqual(list(Path(tmpdir).rglob('*')), [])
            release_mock.assert_not_called()

    def test_scheduler_continuous_mode_skips_busy_cycle_lock(self) -> None:
        with TemporaryDirectory() as tmpdir:
            env = {
                'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH': str(BASE_CONFIG),
                'HOST_STATE_DIR': tmpdir,
            }

            def stop_after_sleep(_seconds: float) -> None:
                scheduler_runtime._STOP = True

            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch.object(scheduler_runtime.scheduler_locking, 'acquire_lock', return_value=False):
                    with mock.patch.object(scheduler_runtime, 'execute_due_jobs', side_effect=AssertionError('tick should be skipped')):
                        with mock.patch.object(scheduler_runtime.time, 'sleep', side_effect=stop_after_sleep) as sleep_mock:
                            stderr = io.StringIO()
                            with contextlib.redirect_stderr(stderr):
                                rc = scheduler_runtime.main(['--interval-seconds', '1', '--heartbeat-interval-seconds', '2'])

            self.assertEqual(rc, 0)
            self.assertIn('scheduler cycle lock busy', stderr.getvalue())
            self.assertEqual(sleep_mock.call_count, 1)
            self.assertEqual(list(Path(tmpdir).rglob('*')), [])

    def test_scheduler_syncs_gateway_cron_projection_for_ui(self) -> None:
        try:
            registry = representative_managed_extension_registry(ROOT_DIR)
        except AssertionError:
            self.skipTest('base release surface has no repo-managed extension cron jobs')
        first_job = cron_jobs(registry)[0]
        first_job_id = str(first_job.get('id') or '').strip()
        first_runtime_job_key = runtime_job_key(first_job)
        with TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir) / 'control_plane'
            gateway_state_root = Path(tmpdir) / 'gateway'
            state = {
                'schemaVersion': 1,
                'jobs': {
                    first_runtime_job_key: {
                        'currentStatus': 'succeeded',
                        'lastFinishedAt': '2026-04-27T01:05:00Z',
                        'nextScheduledRunAt': '2026-04-28T08:35:00+08:00',
                    },
                },
            }
            with mock.patch.dict(os.environ, {'OPENCLAW_GATEWAY_STATE_DIR': str(gateway_state_root)}, clear=False):
                previous_fingerprint = scheduler_runtime._sync_gateway_cron_jobs_projection(
                    state_root=state_root,
                    config=registry,
                    state=state,
                    previous_fingerprint=None,
                )
                (gateway_state_root / 'cron' / 'jobs.json').write_text(
                    json.dumps({'version': 1, 'jobs': []}) + '\n',
                    encoding='utf-8',
                )
                scheduler_runtime._sync_gateway_cron_jobs_projection(
                    state_root=state_root,
                    config=registry,
                    state=state,
                    previous_fingerprint=previous_fingerprint,
                )

            payload = json.loads((gateway_state_root / 'cron' / 'jobs.json').read_text(encoding='utf-8'))
        job = next(item for item in payload['jobs'] if item['id'] == first_job_id)
        self.assertTrue(job['enabled'])
        self.assertEqual(job['payload'], {'kind': 'systemEvent', 'text': 'NO_REPLY'})
        self.assertEqual(job['state']['lastStatus'], 'ok')
        self.assertIsInstance(job['state']['nextRunAtMs'], int)



if __name__ == '__main__':
    unittest.main()
