"""运行态派生产物纯数据整理工具。"""
from __future__ import annotations

from typing import Any, Dict, List

def _json_object(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _json_object_rows(value: Any) -> List[Dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or '').strip()


def _line_text(value: Any) -> str:
    return ' '.join(_text(value).split())


def _string_rows(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [_line_text(item) for item in value if _line_text(item)]


def _markdown_inline_values(values: List[str], *, code: bool = True) -> str:
    if not values:
        return '-'
    if code:
        return ', '.join(f'`{value}`' for value in values)
    return ', '.join(values)


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
