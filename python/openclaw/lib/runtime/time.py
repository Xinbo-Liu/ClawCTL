#!/usr/bin/env python3
"""跨模块时间与标识辅助。"""
from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timezone, tzinfo

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]
    ZoneInfoNotFoundError = None  # type: ignore[assignment]


DEFAULT_APP_TZ = "Asia/Shanghai"


class TimePolicyError(ValueError):
    """时间策略配置错误。"""


def app_timezone_name(env: dict[str, str] | None = None) -> str:
    """返回当前应用业务时区名称。"""
    source = env if env is not None else os.environ
    return str(source.get("APP_TZ") or source.get("TZ") or DEFAULT_APP_TZ).strip() or DEFAULT_APP_TZ


def resolve_timezone(name: object | None = None) -> tzinfo:
    """严格解析 IANA 时区名称，拒绝静默退回 UTC。"""
    timezone_name = str(name or "").strip() or DEFAULT_APP_TZ
    if timezone_name.upper() in {"UTC", "Z"}:
        return timezone.utc
    if ZoneInfo is None:
        raise TimePolicyError(f"当前 Python 运行时不支持 zoneinfo，无法解析时区：{timezone_name}")
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise TimePolicyError(f"未知时区：{timezone_name}") from exc


def now_utc() -> datetime:
    """返回 UTC aware datetime。"""
    return datetime.now(timezone.utc)


def utc_iso(value: datetime | None = None) -> str:
    """返回 UTC ISO 字符串，使用 Z 后缀。"""
    current = ensure_aware(value or now_utc()).astimezone(timezone.utc)
    return current.isoformat().replace("+00:00", "Z")


def parse_iso_datetime(value: object, *, assume_tz: tzinfo = timezone.utc) -> datetime | None:
    """解析 ISO 时间字符串；无时区时按 assume_tz 补齐。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ensure_aware(parsed, assume_tz=assume_tz)


def ensure_aware(value: datetime, *, assume_tz: tzinfo = timezone.utc) -> datetime:
    """确保 datetime 带时区；无时区值按 assume_tz 解释。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=assume_tz)
    return value


def align_datetime_for_compare(
    value: datetime | None,
    reference: datetime,
    *,
    assume_tz: tzinfo = timezone.utc,
) -> datetime | None:
    """把 value 对齐到 reference 的比较形态，避免丢失绝对时间语义。"""
    if value is None:
        return None
    reference_has_tz = reference.tzinfo is not None and reference.utcoffset() is not None
    value_has_tz = value.tzinfo is not None and value.utcoffset() is not None
    if value_has_tz and reference_has_tz:
        return value.astimezone(reference.tzinfo)
    if not value_has_tz and not reference_has_tz:
        return value
    if not value_has_tz and reference_has_tz:
        return value.replace(tzinfo=assume_tz).astimezone(reference.tzinfo)
    return value.astimezone(assume_tz).replace(tzinfo=None)


def now_in_app_tz(timezone_name: object | None = None) -> datetime:
    return datetime.now(resolve_timezone(timezone_name or app_timezone_name()))


def format_date_in_app_tz() -> str:
    return now_in_app_tz().strftime("%Y-%m-%d")


def format_datetime_in_app_tz() -> str:
    return now_in_app_tz().isoformat(timespec="seconds")


def date_only_from_value(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    if len(text) >= 8 and text[:8].isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return None


def make_run_id(prefix: str, timestamp: datetime | None = None) -> str:
    now = timestamp or now_in_app_tz()
    entropy = f"{time.time_ns()}:{os.getpid()}:{prefix}:{now.isoformat()}"
    short_id = hashlib.sha1(entropy.encode("utf-8")).hexdigest()[:6]
    return f"{now.strftime('%Y%m%d-%H%M%S')}-{prefix}-{short_id}"


def sha1_text(text: str) -> str:
    return hashlib.sha1(str(text or "").encode("utf-8")).hexdigest()
