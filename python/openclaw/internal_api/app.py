#!/usr/bin/env python3
"""OpenClaw internal API 运行时。"""
from __future__ import annotations

import argparse
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

from openclaw.internal_api.auth import is_authorized
from openclaw.internal_api.error_support import (
    _internal_error_payload,
    _log_internal_error,
)
from openclaw.internal_api.route_dispatch import (
    _extension_route_effective_auth_required,
    _parse_bounded_non_negative_int,
    dispatch_readonly_request,
    route_requires_auth,
)

class InternalApiHandler(BaseHTTPRequestHandler):
    """内部 API 的 HTTP GET 只读处理器。"""
    server_version = "OpenClawInternalAPI/1.0"

    # noinspection PyShadowingBuiltins
    def log_message(self, format: str, *args: object) -> None:
        """屏蔽 BaseHTTPRequestHandler 默认访问日志。"""
        return

    def _write_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        """输出 JSON 响应。"""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _require_auth(self, path: str) -> bool:
        """执行路由鉴权检查。"""
        if not route_requires_auth(path):
            return True
        header_value = self.headers.get("Authorization")
        if is_authorized(header_value):
            return True
        self._write_json({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
        return False

    def _handle_get(self) -> None:
        """处理 GET 请求并分发到只读路由。"""
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query, keep_blank_values=False)
        if not self._require_auth(path):
            return
        payload, status = dispatch_readonly_request(path, query)
        self._write_json(payload, status)

    # noinspection PyPep8Naming
    def do_GET(self) -> None:  # noqa: N802
        """包装 GET 请求执行并统一处理未捕获异常。"""
        try:
            self._handle_get()
        except BrokenPipeError:
            return
        except Exception as exc:  # pragma: no cover - exercised through server runtime
            parsed = urlparse(getattr(self, 'path', '') or '')
            _log_internal_error(path=parsed.path or '/', exc=exc)
            payload = _internal_error_payload(path=parsed.path or '/', exc=exc)
            try:
                self._write_json(payload, HTTPStatus.INTERNAL_SERVER_ERROR)
            except BrokenPipeError:
                return



def build_parser() -> argparse.ArgumentParser:
    """构建 internal API 命令行参数解析器。"""
    parser = argparse.ArgumentParser(prog="python -m openclaw.cli control-plane api internal-runtime")
    parser.add_argument("--bind", default=os.environ.get("OPENCLAW_INTERNAL_API_BIND", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("OPENCLAW_INTERNAL_API_PORT", "18081")))
    return parser



def main(argv: list[str] | None = None) -> int:
    """internal API 运行时主入口。"""
    args = build_parser().parse_args(argv)
    server = ThreadingHTTPServer((args.bind, args.port), cast(Any, InternalApiHandler))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
