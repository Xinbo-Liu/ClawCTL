from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from openclaw.setup.network import gateway_ingress


class GatewayIngressRenderingTest(unittest.TestCase):
    def _write_env(self, tmp_path: Path, *, cidrs: str, tls_cn: str = 'claw.internal', bind_ip: str = '10.0.0.10') -> Path:
        env_file = tmp_path / 'deploy.env'
        env_file.write_text(
            '\n'.join(
                [
                    'OPENCLAW_INGRESS_HSTS_MAX_AGE=300',
                    'OPENCLAW_TLS_MODE=self_signed',
                    f'OPENCLAW_TLS_CN={tls_cn}',
                    f'OPENCLAW_INGRESS_LISTEN_IP={bind_ip}',
                    f'OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS={cidrs}',
                    'OPENCLAW_GATEWAY_TOKEN=gateway_token_test_1234567890',
                    'OPENCLAW_INTERNAL_API_TOKEN=internal_api_test_token_1234567890',
                ]
            )
            + '\n',
            encoding='utf-8',
        )
        return env_file

    def _assert_gateway_failure(self, expected_code: int, callback) -> str:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as ctx:
            callback()
        self.assertEqual(ctx.exception.code, expected_code)
        captured = stderr.getvalue()
        self.assertIn('[gateway_ingress_control_plane][FAIL]', captured)
        return captured

    def test_rendered_nginx_contains_normalized_allowlist_before_default_deny(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = self._write_env(
                Path(tmpdir),
                cidrs='10.0.0.2/32,10.0.0.8/30,127.0.0.1/32,fd00::/8',
            )

            plan = gateway_ingress.build_plan(str(env_file))

        rendered = plan['rendered_nginx']
        self.assertEqual(
            plan['nginx']['sourceCidrs'],
            ['10.0.0.2/32', '10.0.0.8/30', '127.0.0.1/32', 'fd00::/8'],
        )
        self.assertIn('allow 10.0.0.2/32;', rendered)
        self.assertIn('allow fd00::/8;', rendered)
        self.assertIn('geo $openclaw_ingress_source_allowed', rendered)
        self.assertIn('10.0.0.2/32 1;', rendered)
        self.assertIn('if ($openclaw_ingress_source_allowed = 0)', rendered)
        self.assertIn('proxy_pass http://openclaw-internal-api:18081;', rendered)
        self.assertIn('proxy_pass http://openclaw-internal-api:18081/v1/config/summary;', rendered)
        self.assertIn('proxy_set_header Authorization "Bearer internal_api_test_token_1234567890";', rendered)
        self.assertIn('map_hash_bucket_size 256;', rendered)
        self.assertIn('map $http_authorization $openclaw_control_plane_bearer_allowed', rendered)
        self.assertIn('"Bearer gateway_token_test_1234567890" 1;', rendered)
        self.assertIn('map $http_x_openclaw_gateway_token $openclaw_control_plane_header_allowed', rendered)
        self.assertIn('if ($openclaw_control_plane_read_allowed = 0)', rendered)
        self.assertIn('return 401;', rendered)
        self.assertTrue(plan['nginx']['controlPlaneReadGatewayTokenRequired'])
        self.assertEqual(rendered.count('deny all;'), 2)
        self.assertEqual(rendered.count('return 403;'), 2)
        self.assertEqual(rendered.count('return 401;'), 2)
        self.assertLess(rendered.index('allow 10.0.0.2/32;'), rendered.index('deny all;'))
        self.assertLess(rendered.index('if ($openclaw_ingress_source_allowed = 0)'), rendered.index('return 301'))
        self.assertNotIn('__OPENCLAW_INGRESS_SOURCE_MAP__', rendered)
        self.assertNotIn('__OPENCLAW_INGRESS_REWRITE_GUARD__', rendered)
        self.assertNotIn('__OPENCLAW_INGRESS_ACCESS_GUARD__', rendered)
        self.assertNotIn('__OPENCLAW_INTERNAL_API_AUTHORIZATION__', rendered)
        self.assertNotIn('__OPENCLAW_CONTROL_PLANE_READ_AUTH_MAP__', rendered)
        self.assertNotIn('__OPENCLAW_CONTROL_PLANE_READ_AUTH_GUARD__', rendered)

    def test_rendered_nginx_accepts_ipv6_bind_and_source_cidrs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = self._write_env(
                Path(tmpdir),
                cidrs='fd00::2/128,fd00::/8',
                bind_ip='fd00::10',
            )

            plan = gateway_ingress.build_plan(str(env_file))

        self.assertEqual(plan['ingress']['bindIp'], 'fd00::10')
        self.assertEqual(plan['nginx']['sourceCidrs'], ['fd00::2/128', 'fd00::/8'])
        self.assertIn('allow fd00::2/128;', plan['rendered_nginx'])

    def test_rendered_nginx_rejects_tls_cn_directive_injection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = self._write_env(
                Path(tmpdir),
                cidrs='10.0.0.2/32',
                tls_cn='good.internal; return 200 injected;',
            )

            stderr = self._assert_gateway_failure(3, lambda: gateway_ingress.build_plan(str(env_file)))

        self.assertIn('OPENCLAW_TLS_CN', stderr)

    def test_rendered_nginx_rejects_missing_tls_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = self._write_env(Path(tmpdir), cidrs='10.0.0.2/32')
            env_file.write_text(
                '\n'.join(
                    line
                    for line in env_file.read_text(encoding='utf-8').splitlines()
                    if not line.startswith('OPENCLAW_TLS_MODE=')
                )
                + '\n',
                encoding='utf-8',
            )

            stderr = self._assert_gateway_failure(3, lambda: gateway_ingress.build_plan(str(env_file)))

        self.assertIn('OPENCLAW_TLS_MODE', stderr)

    def test_rendered_nginx_rejects_unsafe_internal_api_token(self) -> None:
        for unsafe_token in ('bad;token', 'bad$host'):
            with self.subTest(unsafe_token=unsafe_token):
                with tempfile.TemporaryDirectory() as tmpdir:
                    env_file = self._write_env(Path(tmpdir), cidrs='10.0.0.2/32')
                    env_file.write_text(
                        env_file.read_text(encoding='utf-8').replace(
                            'OPENCLAW_INTERNAL_API_TOKEN=internal_api_test_token_1234567890',
                            f'OPENCLAW_INTERNAL_API_TOKEN={unsafe_token}',
                        ),
                        encoding='utf-8',
                    )

                    stderr = self._assert_gateway_failure(3, lambda: gateway_ingress.build_plan(str(env_file)))

                self.assertIn('OPENCLAW_INTERNAL_API_TOKEN', stderr)

    def test_rendered_nginx_rejects_unsafe_gateway_token(self) -> None:
        for unsafe_token in ('bad;token', 'bad$host'):
            with self.subTest(unsafe_token=unsafe_token):
                with tempfile.TemporaryDirectory() as tmpdir:
                    env_file = self._write_env(Path(tmpdir), cidrs='10.0.0.2/32')
                    env_file.write_text(
                        env_file.read_text(encoding='utf-8').replace(
                            'OPENCLAW_GATEWAY_TOKEN=gateway_token_test_1234567890',
                            f'OPENCLAW_GATEWAY_TOKEN={unsafe_token}',
                        ),
                        encoding='utf-8',
                    )

                    stderr = self._assert_gateway_failure(3, lambda: gateway_ingress.build_plan(str(env_file)))

                self.assertIn('OPENCLAW_GATEWAY_TOKEN', stderr)

    def test_rendered_nginx_rejects_missing_public_and_duplicate_cidrs(self) -> None:
        cases = [
            '',
            '8.8.8.0/24',
            '2001:db8::/32',
            '10.0.0.0/24,10.0.0.0/24',
        ]
        for cidrs in cases:
            with self.subTest(cidrs=cidrs):
                with tempfile.TemporaryDirectory() as tmpdir:
                    env_file = self._write_env(Path(tmpdir), cidrs=cidrs)

                    stderr = self._assert_gateway_failure(3, lambda: gateway_ingress.build_plan(str(env_file)))

                self.assertIn('OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS', stderr)

    def test_check_nginx_fails_when_rendered_file_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            env_file = self._write_env(tmp_path, cidrs='10.0.0.2/32')
            output = tmp_path / 'nginx.conf'
            output.write_text('stale\n', encoding='utf-8')

            stderr = self._assert_gateway_failure(
                4,
                lambda: gateway_ingress.main(['check-nginx', '--env-file', str(env_file), '--output', str(output)]),
            )

        self.assertIn('Nginx 配置与控制面不一致', stderr)

    def test_render_nginx_output_remains_container_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            env_file = self._write_env(tmp_path, cidrs='10.0.0.2/32')
            output = tmp_path / 'nginx.conf'

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = gateway_ingress.main(['render-nginx', '--env-file', str(env_file), '--output', str(output)])

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload['output'], str(output))
            if os.name != 'nt':
                self.assertEqual(output.stat().st_mode & 0o777, 0o644)


if __name__ == '__main__':
    unittest.main()
