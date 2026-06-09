"""HTTP 公共辅助。"""

from .json_client import JsonHttpResult, http_get_text, http_post_json

__all__ = [
    "JsonHttpResult",
    "http_get_text",
    "http_post_json",
]
