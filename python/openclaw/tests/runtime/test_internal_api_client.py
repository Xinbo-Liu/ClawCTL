from __future__ import annotations

import unittest
from unittest import mock

from openclaw.internal_api import client


class _FakeResponse:
    status = 200

    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> '_FakeResponse':
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            return self.body
        return self.body[:size]


class InternalApiClientTest(unittest.TestCase):
    def test_get_json_rejects_response_over_limit(self) -> None:
        with mock.patch(
            'openclaw.lib.http.json_client.urllib.request.urlopen',
            return_value=_FakeResponse(b'{"too":"large"}'),
        ):
            with self.assertRaises(ValueError):
                client.get_json('/v1/config/summary', max_response_bytes=4)

    def test_get_json_parses_object_response(self) -> None:
        with mock.patch(
            'openclaw.lib.http.json_client.urllib.request.urlopen',
            return_value=_FakeResponse(b'{"status":"ok"}'),
        ):
            payload = client.get_json('/healthz', require_auth=False, max_response_bytes=64)

        self.assertEqual(payload, {'status': 'ok'})


if __name__ == '__main__':
    unittest.main()
