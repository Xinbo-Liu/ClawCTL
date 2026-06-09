from __future__ import annotations

import io
import os
import threading
import time
import unittest
from unittest import mock

from openclaw.internal_api.app import (
    _extension_route_effective_auth_required,
    _internal_error_payload,
    _log_internal_error,
    _parse_bounded_non_negative_int,
    build_parser,
)
from openclaw.internal_api.routes import health


class InternalApiHardeningTest(unittest.TestCase):
    def tearDown(self) -> None:
        health.reset_ready_cache()

    def test_parse_bounded_non_negative_int_caps_large_values(self) -> None:
        value, error = _parse_bounded_non_negative_int('9999', default=50, upper_bound=500, error_key='invalid_limit')
        self.assertEqual(value, 500)
        self.assertIsNone(error)

    def test_internal_api_default_bind_is_loopback(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            args = build_parser().parse_args([])
        self.assertEqual(args.bind, '127.0.0.1')

    def test_internal_error_payload_hides_exception_detail_by_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            payload = _internal_error_payload(path='/v1/test', exc=RuntimeError('secret stack trace'))
        self.assertEqual(payload.get('message'), 'internal server error')
        self.assertNotIn('detail', payload)
        self.assertNotIn('secret stack trace', str(payload))

    def test_internal_error_payload_can_opt_in_debug_detail(self) -> None:
        with mock.patch.dict(os.environ, {'OPENCLAW_INTERNAL_API_DEBUG_ERRORS': '1'}, clear=False):
            payload = _internal_error_payload(path='/v1/test', exc=RuntimeError('secret stack trace'))
        self.assertEqual(payload.get('detail'), 'secret stack trace')

    def test_internal_error_logging_hides_exception_detail_by_default(self) -> None:
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=False):
            with mock.patch('sys.stderr', stderr):
                _log_internal_error(path='/v1/test', exc=RuntimeError('secret stack trace'))
        output = stderr.getvalue()
        self.assertIn('exceptionType=RuntimeError', output)
        self.assertNotIn('secret stack trace', output)

    def test_internal_error_logging_can_opt_in_debug_detail(self) -> None:
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {'OPENCLAW_INTERNAL_API_DEBUG_ERRORS': '1'}, clear=False):
            with mock.patch('sys.stderr', stderr):
                _log_internal_error(path='/v1/test', exc=RuntimeError('secret stack trace'))
        output = stderr.getvalue()
        self.assertIn('exceptionType=RuntimeError', output)
        self.assertIn('secret stack trace', output)

    def test_unauthenticated_extension_route_requires_runtime_flag_and_allowlist(self) -> None:
        spec = {'id': 'public_route', 'authRequired': False}
        with mock.patch.dict(os.environ, {
            'OPENCLAW_INTERNAL_API_ENABLE_UNAUTH_EXTENSION_ROUTES': '',
            'OPENCLAW_INTERNAL_API_UNAUTH_EXTENSION_ROUTE_IDS': '',
        }, clear=False):
            self.assertTrue(_extension_route_effective_auth_required(spec))
        with mock.patch.dict(os.environ, {
            'OPENCLAW_INTERNAL_API_ENABLE_UNAUTH_EXTENSION_ROUTES': '1',
            'OPENCLAW_INTERNAL_API_UNAUTH_EXTENSION_ROUTE_IDS': '',
        }, clear=False):
            self.assertTrue(_extension_route_effective_auth_required(spec))
        with mock.patch.dict(os.environ, {
            'OPENCLAW_INTERNAL_API_ENABLE_UNAUTH_EXTENSION_ROUTES': '1',
            'OPENCLAW_INTERNAL_API_UNAUTH_EXTENSION_ROUTE_IDS': 'public_route',
        }, clear=False):
            self.assertFalse(_extension_route_effective_auth_required(spec))

    def test_render_ready_marks_timeout_and_runtime_id_conflict_as_degraded(self) -> None:
        summary = {
            'counts': {'jobs': 0},
            'scheduler': {'healthy': True, 'heartbeatAgeSeconds': 1},
            'extensions': [{'id': 'ext_a'}],
        }

        def resolve(module_name: str, attr_name: str):
            if attr_name == 'slow_check':
                def _slow() -> dict[str, object]:
                    time.sleep(0.2)
                    return {'id': 'slow_manifest', 'ok': True}
                return _slow

            def _conflict() -> dict[str, object]:
                return {'id': 'schedulerHeartbeat', 'ok': True}
            return _conflict

        rows = [
            {'id': 'slow_manifest', 'module': 'pkg.ready', 'callable': 'slow_check', 'blocking': True, 'extensionId': 'ext_a'},
            {'id': 'conflict_manifest', 'module': 'pkg.ready', 'callable': 'conflict_check', 'blocking': True, 'extensionId': 'ext_a'},
        ]
        with mock.patch.dict('os.environ', {
            'OPENCLAW_READY_CACHE_TTL_SECONDS': '0',
            'OPENCLAW_READY_CHECK_TIMEOUT_SECONDS': '0.05',
        }, clear=False):
            with mock.patch('openclaw.internal_api.routes.health.render_control_plane_summary', return_value=summary):
                with mock.patch('openclaw.internal_api.routes.health.extension_ready_checks', return_value=rows):
                    with mock.patch('openclaw.internal_api.routes.health.import_extension_callable', side_effect=resolve):
                        payload = health.render_ready()
        self.assertEqual(payload.get('status'), 'degraded')
        checks = payload.get('checks') or {}
        self.assertEqual((checks.get('slow_manifest') or {}).get('error'), 'timeout')
        self.assertEqual((checks.get('conflict:schedulerHeartbeat') or {}).get('error'), 'ready_check_conflict')

    def test_render_ready_does_not_spawn_duplicate_thread_for_still_running_check(self) -> None:
        summary = {
            'counts': {'jobs': 0},
            'scheduler': {'healthy': True, 'heartbeatAgeSeconds': 1},
            'extensions': [{'id': 'ext_a'}],
        }
        call_count = 0

        def resolve(_module_name: str, _attr_name: str):
            def _slow() -> dict[str, object]:
                nonlocal call_count
                call_count += 1
                time.sleep(0.2)
                return {'id': 'slow_manifest', 'ok': True}
            return _slow

        rows = [
            {'id': 'slow_manifest', 'module': 'pkg.ready', 'callable': 'slow_check', 'blocking': True, 'extensionId': 'ext_a'},
        ]
        with mock.patch.dict('os.environ', {
            'OPENCLAW_READY_CACHE_TTL_SECONDS': '0',
            'OPENCLAW_READY_CHECK_TIMEOUT_SECONDS': '0.05',
        }, clear=False):
            with mock.patch('openclaw.internal_api.routes.health.render_control_plane_summary', return_value=summary):
                with mock.patch('openclaw.internal_api.routes.health.extension_ready_checks', return_value=rows):
                    with mock.patch('openclaw.internal_api.routes.health.import_extension_callable', side_effect=resolve):
                        first = health.render_ready()
                        second = health.render_ready()

        self.assertEqual(((first.get('checks') or {}).get('slow_manifest') or {}).get('error'), 'timeout')
        self.assertEqual(((second.get('checks') or {}).get('slow_manifest') or {}).get('error'), 'previous_check_still_running')
        self.assertEqual(call_count, 1)

    def test_call_with_timeout_treats_registered_unstarted_thread_as_in_progress(self) -> None:
        placeholder = threading.Thread(target=lambda: None)
        with health._READY_CHECK_INFLIGHT_LOCK:
            health._READY_CHECK_INFLIGHT['demo-key'] = placeholder

        status, value = health._call_with_timeout(lambda: {'ok': True}, timeout_seconds=0.01, call_key='demo-key')

        self.assertEqual(status, 'in_progress')
        self.assertIsNone(value)
        self.assertEqual(placeholder.ident, None)

    def test_call_with_timeout_reserves_key_before_constructing_thread(self) -> None:
        nested_results: list[tuple[str, object]] = []
        created_threads = 0

        class FakeThread:
            ident: int | None = None

            def __init__(self, target, daemon: bool) -> None:
                nonlocal created_threads
                created_threads += 1
                self._target = target
                self._alive = False
                self.daemon = daemon
                if created_threads == 1:
                    nested_results.append(
                        health._call_with_timeout(lambda: {'nested': True}, timeout_seconds=0.01, call_key='race-key')
                    )

            def start(self) -> None:
                self.ident = 1
                self._alive = True
                self._target()
                self._alive = False

            def join(self, _timeout: float) -> None:
                return None

            def is_alive(self) -> bool:
                return self._alive

        with mock.patch('openclaw.internal_api.routes.health.threading.Thread', FakeThread):
            status, value = health._call_with_timeout(lambda: {'ok': True}, timeout_seconds=0.01, call_key='race-key')

        self.assertEqual(nested_results, [('in_progress', None)])
        self.assertEqual(created_threads, 1)
        self.assertEqual(status, 'ok')
        self.assertEqual(value, {'ok': True})

    def test_call_with_timeout_clears_inflight_when_thread_start_fails(self) -> None:
        class FailingThread:
            ident: int | None = None

            def __init__(self, target, daemon: bool) -> None:
                self._target = target
                self.daemon = daemon

            def start(self) -> None:
                raise RuntimeError('start failed')

            def is_alive(self) -> bool:
                return False

        with mock.patch('openclaw.internal_api.routes.health.threading.Thread', FailingThread):
            with self.assertRaises(RuntimeError):
                health._call_with_timeout(lambda: {'ok': True}, timeout_seconds=0.01, call_key='start-fail-key')

        with health._READY_CHECK_INFLIGHT_LOCK:
            self.assertFalse('start-fail-key' in health._READY_CHECK_INFLIGHT)


if __name__ == '__main__':
    unittest.main()
