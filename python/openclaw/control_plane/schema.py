#!/usr/bin/env python3
"""控制平面 schema 加载与 JSON Schema 校验。"""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

class SchemaValidationError(RuntimeError):
    """schema 校验失败。"""


class SchemaDependencyError(SchemaValidationError):
    """schema 校验依赖缺失或不完整。"""


_VALIDATOR_CACHE: dict[tuple[int, str], Any] = {}


class _BuiltinSchemaIssue:
    def __init__(self, path: list[Any], message: str) -> None:
        self.path = path
        self.message = message


def read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError as exc:
        raise SchemaValidationError(f'schema 文件不存在：{path}') from exc
    except Exception as exc:
        # schema 读取失败统一归入校验错误，调用方只需要处理一个治理错误类型。
        raise SchemaValidationError(f'schema 文件无法解析：{path} ({exc})') from exc


@lru_cache(maxsize=128)
def _load_schema_cached(path_text: str, mtime_ns: int, size: int) -> dict[str, Any]:
    _ = (mtime_ns, size)
    payload = read_json_file(Path(path_text))
    if not isinstance(payload, dict):
        raise SchemaValidationError(f'schema 顶层必须为对象：{path_text}')
    return payload


def load_schema(path: Path) -> dict[str, Any]:
    resolved = Path(path).resolve()
    try:
        stat = resolved.stat()
    except FileNotFoundError:
        payload = read_json_file(resolved)
        if not isinstance(payload, dict):
            raise SchemaValidationError(f'schema 顶层必须为对象：{resolved}')
        return payload
    return copy.deepcopy(_load_schema_cached(str(resolved), stat.st_mtime_ns, stat.st_size))


def _schema_fingerprint(schema: dict[str, Any]) -> str:
    try:
        encoded = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    except TypeError:
        encoded = repr(schema).encode('utf-8', errors='replace')
    return hashlib.sha256(encoded).hexdigest()


def _path_to_text(parts: list[Any]) -> str:
    if not parts:
        return '$'
    chunks: list[str] = ['$']
    for part in parts:
        if isinstance(part, int):
            chunks.append(f'[{part}]')
        else:
            if len(chunks) == 1:
                chunks.append(f'.{part}')
            else:
                chunks.append(f'.{part}')
    return ''.join(chunks)


def _json_type_name(value: Any) -> str:
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, str):
        return 'string'
    if isinstance(value, int):
        return 'integer'
    if isinstance(value, float):
        return 'number'
    if isinstance(value, dict):
        return 'object'
    if isinstance(value, list):
        return 'array'
    if value is None:
        return 'null'
    return type(value).__name__


def _matches_json_type(value: Any, expected: str) -> bool:
    if expected == 'object':
        return isinstance(value, dict)
    if expected == 'array':
        return isinstance(value, list)
    if expected == 'string':
        return isinstance(value, str)
    if expected == 'boolean':
        return isinstance(value, bool)
    if expected == 'integer':
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == 'number':
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == 'null':
        return value is None
    return True


def _json_equal(left: Any, right: Any) -> bool:
    return left == right


def _value_fingerprint(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    except TypeError:
        return repr(value)


def _builtin_first_error(value: Any, schema: Any, path: list[Any]) -> _BuiltinSchemaIssue | None:
    if isinstance(schema, bool):
        if schema:
            return None
        return _BuiltinSchemaIssue(path, 'is not allowed by boolean schema')
    if not isinstance(schema, dict):
        return None

    if 'allOf' in schema:
        for item in schema.get('allOf') or []:
            issue = _builtin_first_error(value, item, path)
            if issue is not None:
                return issue

    if 'anyOf' in schema:
        branches = list(schema.get('anyOf') or [])
        if not any(_builtin_first_error(value, item, path) is None for item in branches):
            return _BuiltinSchemaIssue(path, 'must match at least one anyOf schema')

    if 'oneOf' in schema:
        branches = list(schema.get('oneOf') or [])
        match_count = sum(1 for item in branches if _builtin_first_error(value, item, path) is None)
        if match_count != 1:
            return _BuiltinSchemaIssue(path, 'must match exactly one oneOf schema')

    if 'if' in schema and _builtin_first_error(value, schema.get('if'), path) is None:
        issue = _builtin_first_error(value, schema.get('then', {}), path)
        if issue is not None:
            return issue

    if 'const' in schema and not _json_equal(value, schema.get('const')):
        return _BuiltinSchemaIssue(path, f'must be constant {schema.get("const")!r}')

    if 'enum' in schema:
        enum_values = list(schema.get('enum') or [])
        if not any(_json_equal(value, item) for item in enum_values):
            return _BuiltinSchemaIssue(path, f'must be one of {enum_values!r}')

    expected_type = schema.get('type')
    if expected_type is not None:
        expected_types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(isinstance(item, str) and _matches_json_type(value, item) for item in expected_types):
            return _BuiltinSchemaIssue(path, f'{_json_type_name(value)} is not of type {expected_types!r}')

    if isinstance(value, dict):
        min_properties = schema.get('minProperties')
        if isinstance(min_properties, int) and len(value) < min_properties:
            return _BuiltinSchemaIssue(path, f'must contain at least {min_properties} properties')

        required = schema.get('required')
        if isinstance(required, list):
            for key in required:
                if isinstance(key, str) and key not in value:
                    return _BuiltinSchemaIssue(path, f"missing required property '{key}'")

        property_names_schema = schema.get('propertyNames')
        if isinstance(property_names_schema, dict):
            for key in value:
                issue = _builtin_first_error(key, property_names_schema, path + [key])
                if issue is not None:
                    return issue

        properties = schema.get('properties')
        known_properties = set(properties.keys()) if isinstance(properties, dict) else set()
        if isinstance(properties, dict):
            for key, subschema in properties.items():
                if key in value:
                    issue = _builtin_first_error(value[key], subschema, path + [key])
                    if issue is not None:
                        return issue

        additional = schema.get('additionalProperties', True)
        if additional is False:
            for key in value:
                if key not in known_properties:
                    return _BuiltinSchemaIssue(path + [key], 'additional property is not allowed')
        elif isinstance(additional, dict):
            for key in value:
                if key not in known_properties:
                    issue = _builtin_first_error(value[key], additional, path + [key])
                    if issue is not None:
                        return issue

    if isinstance(value, list):
        min_items = schema.get('minItems')
        if isinstance(min_items, int) and len(value) < min_items:
            return _BuiltinSchemaIssue(path, f'must contain at least {min_items} items')
        if schema.get('uniqueItems') is True:
            seen: set[str] = set()
            for index, item in enumerate(value):
                marker = _value_fingerprint(item)
                if marker in seen:
                    return _BuiltinSchemaIssue(path + [index], 'array items must be unique')
                seen.add(marker)
        items_schema = schema.get('items')
        if isinstance(items_schema, dict) or isinstance(items_schema, bool):
            for index, item in enumerate(value):
                issue = _builtin_first_error(item, items_schema, path + [index])
                if issue is not None:
                    return issue
        elif isinstance(items_schema, list):
            for index, item_schema in enumerate(items_schema[: len(value)]):
                issue = _builtin_first_error(value[index], item_schema, path + [index])
                if issue is not None:
                    return issue

    if isinstance(value, str):
        min_length = schema.get('minLength')
        if isinstance(min_length, int) and len(value) < min_length:
            return _BuiltinSchemaIssue(path, f'length must be at least {min_length}')
        max_length = schema.get('maxLength')
        if isinstance(max_length, int) and len(value) > max_length:
            return _BuiltinSchemaIssue(path, f'length must be at most {max_length}')
        pattern = schema.get('pattern')
        if isinstance(pattern, str):
            try:
                matched = re.search(pattern, value) is not None
            except re.error as exc:
                return _BuiltinSchemaIssue(path, f'schema pattern is invalid: {exc}')
            if not matched:
                return _BuiltinSchemaIssue(path, f'does not match pattern {pattern!r}')

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get('minimum')
        if isinstance(minimum, (int, float)) and value < minimum:
            return _BuiltinSchemaIssue(path, f'must be greater than or equal to {minimum}')
        exclusive_minimum = schema.get('exclusiveMinimum')
        if isinstance(exclusive_minimum, (int, float)) and value <= exclusive_minimum:
            return _BuiltinSchemaIssue(path, f'must be greater than {exclusive_minimum}')
        maximum = schema.get('maximum')
        if isinstance(maximum, (int, float)) and value > maximum:
            return _BuiltinSchemaIssue(path, f'must be less than or equal to {maximum}')
        exclusive_maximum = schema.get('exclusiveMaximum')
        if isinstance(exclusive_maximum, (int, float)) and value >= exclusive_maximum:
            return _BuiltinSchemaIssue(path, f'must be less than {exclusive_maximum}')

    return None


def _validate_payload_with_builtin(payload: Any, schema: dict[str, Any], *, label: str) -> None:
    issue = _builtin_first_error(payload, schema, [])
    if issue is None:
        return
    raise SchemaValidationError(f'{label} schema 校验失败：{_path_to_text(issue.path)} {issue.message}')


def validate_payload_against_schema(
    payload: Any,
    schema: dict[str, Any],
    *,
    label: str,
    strict_dependency: bool = False,
) -> None:
    try:
        jsonschema = importlib.import_module('jsonschema')
        validator_cls = getattr(jsonschema, 'Draft202012Validator')
    except ModuleNotFoundError as exc:
        if strict_dependency:
            _ = exc
            _validate_payload_with_builtin(payload, schema, label=label)
            return
        # 轻量环境允许缺少 jsonschema；严格模式由调用方显式开启。
        return
    except AttributeError as exc:
        if strict_dependency:
            raise SchemaDependencyError(f'{label} schema 校验器缺失：Draft202012Validator') from exc
        # 依赖环境缺少目标 validator 时同样按轻量环境降级。
        return
    validator_key = (id(validator_cls), _schema_fingerprint(schema))
    validator = _VALIDATOR_CACHE.get(validator_key)
    if validator is None:
        if len(_VALIDATOR_CACHE) >= 128:
            _VALIDATOR_CACHE.clear()
        validator = validator_cls(schema)
        _VALIDATOR_CACHE[validator_key] = validator
    errors = sorted(validator.iter_errors(payload), key=lambda item: list(item.absolute_path))
    if not errors:
        return
    first = errors[0]
    location = _path_to_text(list(first.absolute_path))
    raise SchemaValidationError(f'{label} schema 校验失败：{location} {first.message}')


__all__ = [
    'SchemaDependencyError',
    'SchemaValidationError',
    'load_schema',
    'read_json_file',
    'validate_payload_against_schema',
]
