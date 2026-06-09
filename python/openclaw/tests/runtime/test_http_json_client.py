from __future__ import annotations

import unittest
from unittest import mock

from openclaw.lib.http.json_client import http_get_text, http_post_json


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


class JsonHttpClientTest(unittest.TestCase):
    def test_http_post_json_rejects_response_over_limit(self) -> None:
        with mock.patch('openclaw.lib.http.json_client.urllib.request.urlopen', return_value=_FakeResponse(b'{"too":"large"}')):
            result = http_post_json('https://example.test/api', {'ok': True}, max_response_bytes=4)

        self.assertFalse(result.ok)
        self.assertEqual(result.payload, {})
        self.assertIn('response too large', result.error or '')

    def test_http_get_text_rejects_response_over_limit(self) -> None:
        with mock.patch('openclaw.lib.http.json_client.urllib.request.urlopen', return_value=_FakeResponse(b'abcdef')):
            with self.assertRaises(ValueError):
                http_get_text('https://example.test/api', max_response_bytes=4)


if __name__ == '__main__':
    unittest.main()
