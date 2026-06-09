from __future__ import annotations

import base64
import hashlib
import hmac
import unittest
from types import SimpleNamespace
from unittest import mock

from openclaw.control_plane.dispatch.provider_adapters.synthetic_webhook import evaluate_delivery_response
from openclaw.lib.channels.sender import ChannelDeliveryRequest, send_channel_message


class ChannelSenderTest(unittest.TestCase):
    def test_synthetic_webhook_secret_adds_fresh_signature_to_payload(self) -> None:
        captured: dict[str, object] = {}

        def fake_post_json(url: str, payload: dict[str, object], timeout_sec: float):
            captured['url'] = url
            captured['payload'] = payload
            captured['timeout_sec'] = timeout_sec
            return SimpleNamespace(ok=True, status_code=200, payload={'code': 0}, error=None)

        with (
            mock.patch('openclaw.lib.channels.sender.http_post_json', side_effect=fake_post_json),
            mock.patch('openclaw.control_plane.dispatch.provider_adapters.synthetic_webhook.time.time', return_value=1700000000),
        ):
            result = send_channel_message(
                ChannelDeliveryRequest(
                    provider='synthetic_webhook',
                    transport='webhook',
                    endpoint_url='https://example.invalid/webhook/demo',
                    title='测试通知',
                    markdown='hello',
                    message_format='text',
                    secret='test-secret',
                )
            )

        payload = captured['payload']
        expected_sign = base64.b64encode(
            hmac.new(b'1700000000\ntest-secret', digestmod=hashlib.sha256).digest()
        ).decode('utf-8')
        self.assertTrue(result.ok)
        self.assertEqual(payload['timestamp'], 1700000000)
        self.assertEqual(payload['sign'], expected_sign)
        self.assertEqual(payload['type'], 'text')

    def test_synthetic_webhook_business_error_preserves_provider_message(self) -> None:
        ok, status, payload, error = evaluate_delivery_response(
            status=200,
            response_payload={'code': 9499, 'msg': '网页链接非法回调'},
            parse_error=None,
        )

        self.assertFalse(ok)
        self.assertEqual(status, 200)
        self.assertEqual(payload, {'code': 9499, 'msg': '网页链接非法回调'})
        self.assertIn('code=9499', str(error))
        self.assertIn('网页链接非法回调', str(error))


if __name__ == '__main__':
    unittest.main()
