from __future__ import annotations

from typing import Any

JsonObject = dict[str, Any]
JsonArray = list[Any]


def json_object(value: Any) -> JsonObject:
    return value if isinstance(value, dict) else {}


def json_object_or_none(value: Any) -> JsonObject | None:
    return value if isinstance(value, dict) else None


def json_array(value: Any) -> JsonArray:
    return value if isinstance(value, list) else []


def json_object_map(value: Any) -> dict[str, JsonObject]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items() if isinstance(item, dict)}


def json_text_list(value: Any) -> list[str]:
    return [str(item) for item in json_array(value)]
