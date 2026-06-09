#!/usr/bin/env python3
"""调度器 cron 解析辅助。"""
from __future__ import annotations

from datetime import datetime, timedelta

from openclaw.lib.cli.common import CliError
from openclaw.lib.runtime.time import TimePolicyError
from openclaw.lib.runtime.time import resolve_timezone as _resolve_timezone


def resolve_timezone(name: str):
    if not str(name or '').strip():
        raise CliError('时区不能为空', 2)
    try:
        return _resolve_timezone(name)
    except TimePolicyError as exc:
        raise CliError(str(exc), 2) from exc


def _parse_int_token(token: str, *, field: str) -> int:
    try:
        return int(token)
    except ValueError as exc:
        raise CliError(f'非法 cron 数值：{field}', 2) from exc


def _ensure_range(value: int, minimum: int, maximum: int, *, field: str) -> int:
    if not minimum <= value <= maximum:
        raise CliError(f'cron 字段超出范围：{field}', 2)
    return value


def parse_value_set(field: str, minimum: int, maximum: int) -> set[int]:
    field = str(field or '').strip()
    if not field or field == '*':
        return set(range(minimum, maximum + 1))
    values: set[int] = set()
    for part in field.split(','):
        token = part.strip()
        if not token:
            continue
        if token == '*':
            values.update(range(minimum, maximum + 1))
            continue
        if token.startswith('*/'):
            step = _parse_int_token(token[2:], field=field)
            if step <= 0:
                raise CliError(f'非法 cron step：{field}', 2)
            values.update(range(minimum, maximum + 1, step))
            continue
        if '-' in token:
            start_text, end_text = token.split('-', 1)
            start = _ensure_range(_parse_int_token(start_text, field=field), minimum, maximum, field=field)
            end = _ensure_range(_parse_int_token(end_text, field=field), minimum, maximum, field=field)
            if start > end:
                raise CliError(f'非法 cron 范围：{field}', 2)
            values.update(range(start, end + 1))
            continue
        values.add(_ensure_range(_parse_int_token(token, field=field), minimum, maximum, field=field))
    if not values:
        raise CliError(f'cron 字段超出范围：{field}', 2)
    return values


def validate_cron_expr(expr: str) -> None:
    fields = str(expr or '').split()
    if len(fields) != 5:
        raise CliError(f'仅支持 5 段 cron 表达式：{expr}', 2)
    minute, hour, day, month, weekday = fields
    parse_value_set(minute, 0, 59)
    parse_value_set(hour, 0, 23)
    parse_value_set(day, 1, 31)
    parse_value_set(month, 1, 12)
    parse_value_set(weekday, 0, 7)


def cron_matches(expr: str, current: datetime) -> bool:
    fields = str(expr or '').split()
    if len(fields) != 5:
        raise CliError(f'仅支持 5 段 cron 表达式：{expr}', 2)
    minute, hour, day, month, weekday = fields
    py_weekday = (current.weekday() + 1) % 7  # sunday=0
    weekday_values = parse_value_set(weekday, 0, 7)
    if 7 in weekday_values:
        weekday_values.add(0)
    return (
        current.minute in parse_value_set(minute, 0, 59)
        and current.hour in parse_value_set(hour, 0, 23)
        and current.day in parse_value_set(day, 1, 31)
        and current.month in parse_value_set(month, 1, 12)
        and py_weekday in weekday_values
    )


def next_cron_occurrence(expr: str, current: datetime, limit_minutes: int = 60 * 24 * 32) -> str | None:
    probe = current.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(limit_minutes):
        if cron_matches(expr, probe):
            return probe.isoformat()
        probe += timedelta(minutes=1)
    return None
