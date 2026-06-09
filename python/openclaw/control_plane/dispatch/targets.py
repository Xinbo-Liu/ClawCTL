#!/usr/bin/env python3
"""dispatch target 运行态解析、策略评估与摘要构造。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openclaw.lib.channels.provider_registry import endpoint_validator, resolve_channel_provider_adapter


ALLOWED_RELEASE_LEVELS = {"official", "review", "degraded"}
REQUIRED_BOUNDARY_FIELDS = ("dispatchLane", "payloadScope", "publishLatestDefault")


@dataclass(frozen=True)
class DispatchDefaults:
    dedupe_window_hours: int
    max_attempts: int
    backoff_seconds: list[int]
    target_min_interval_ms: int
    target_max_per_second: int
    target_max_per_minute: int
    target_rate_limit_state_ttl_seconds: int


@dataclass(frozen=True)
class ResolvedTarget:
    """控制面注册表与部署环境合并后的单个分发目标。"""

    target_id: str
    transport: str
    provider: str
    target_group: str
    delivery_tier: str
    message_profile: str
    enabled: bool
    enabled_default: bool
    configured: bool
    endpoint_url: str
    endpoint_present: bool
    secret: str
    secret_required: bool
    secret_present: bool
    title: str
    msg_format: str
    at_all: bool
    silence_enabled: bool
    silence_min_delta: float
    allowed_release_levels: list[str]
    max_attempts: int
    dedupe_window_hours: int
    backoff_seconds: list[int]
    env: dict[str, str]
    source_registry_path: str = ""
    extension_id: str = ""
    display_name: str = ""
    role_description: str = ""
    audience_description: str = ""
    dispatch_lane: str = ""
    payload_scope: str = ""
    publish_latest: bool = False
    boundary_description: str = ""


class TargetConfigError(RuntimeError):
    """dispatch target 配置无法解析或不满足运行要求。"""


def parse_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def parse_float(value: object, default: float, minimum: float = 0.0) -> float:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)


def parse_int(value: object, default: int, minimum: int = 0) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)


def parse_int_list(value: object, default: list[int]) -> list[int]:
    text = str(value or "").strip()
    if not text:
        return list(default)
    result: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            result.append(max(0, int(part)))
        except ValueError:
            continue
    return result or list(default)


def parse_release_levels(value: object, default: list[str]) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return list(default)
    levels: list[str] = []
    for part in text.split(","):
        normalized = part.strip().lower()
        if normalized in ALLOWED_RELEASE_LEVELS and normalized not in levels:
            levels.append(normalized)
    return levels or list(default)


def load_dispatch_defaults(config_path: Path) -> DispatchDefaults:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TargetConfigError(f"dispatch target defaults source is unavailable: {config_path}") from exc
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise TargetConfigError(f"dispatch target defaults JSON cannot be parsed: {config_path} ({exc})") from exc
    defaults = payload.get("defaults") if isinstance(payload, dict) else None
    targets = payload.get("targets") if isinstance(payload, dict) else None
    if not isinstance(defaults, dict) or not isinstance(targets, list):
        raise TargetConfigError(f"dispatch target defaults are missing defaults/targets: {config_path}")
    return _parse_dispatch_defaults(defaults)


def _parse_dispatch_defaults(defaults_block: dict[str, Any]) -> DispatchDefaults:
    return DispatchDefaults(
        dedupe_window_hours=parse_int(defaults_block.get("dedupeWindowHours"), 36, 1),
        max_attempts=parse_int(defaults_block.get("maxAttempts"), 5, 1),
        backoff_seconds=parse_int_list(defaults_block.get("backoffSeconds"), [60, 300, 900, 1800, 3600]),
        target_min_interval_ms=parse_int(defaults_block.get("targetMinIntervalMs"), 250, 0),
        target_max_per_second=parse_int(defaults_block.get("targetMaxPerSecond"), 4, 1),
        target_max_per_minute=parse_int(defaults_block.get("targetMaxPerMinute"), 80, 1),
        target_rate_limit_state_ttl_seconds=parse_int(defaults_block.get("targetRateLimitStateTtlSeconds"), 600, 60),
    )


def _payload_registry_version(payload: dict[str, Any]) -> int:
    return parse_int(payload.get("registry_version", payload.get("version")), 0, 0)


def _target_id_for_error(row: dict[str, Any]) -> str:
    return str(row.get("id") or "<unknown>").strip() or "<unknown>"


def _load_target_boundary(
    row: dict[str, Any],
    *,
    registry_version: int,
    source_label: str,
) -> dict[str, Any]:
    target_id = _target_id_for_error(row)
    raw_boundary = row.get("boundary")
    if not isinstance(raw_boundary, dict):
        raise TargetConfigError(
            f"dispatch target {target_id} is missing boundary in registry v{registry_version}: {source_label}"
        )
    missing_fields = [field for field in REQUIRED_BOUNDARY_FIELDS if field not in raw_boundary]
    if missing_fields:
        raise TargetConfigError(
            f"dispatch target {target_id} boundary missing required fields {', '.join(missing_fields)} "
            f"in registry v{registry_version}: {source_label}"
        )
    dispatch_lane = str(raw_boundary.get("dispatchLane") or "").strip()
    payload_scope = str(raw_boundary.get("payloadScope") or "").strip()
    if not dispatch_lane or not payload_scope:
        raise TargetConfigError(
            f"dispatch target {target_id} boundary has empty dispatchLane/payloadScope "
            f"in registry v{registry_version}: {source_label}"
        )
    if not isinstance(raw_boundary.get("publishLatestDefault"), bool):
        raise TargetConfigError(
            f"dispatch target {target_id} boundary publishLatestDefault must be boolean "
            f"in registry v{registry_version}: {source_label}"
        )
    return raw_boundary


def target_publishes_latest(target: object) -> bool:
    """缺少显式 publish_latest 边界时，默认不推进 dispatch latest。"""
    return parse_bool(getattr(target, "publish_latest", None), False)


def load_targets_config(targets_path: Path, env: dict[str, str] | None = None) -> tuple[DispatchDefaults, list[ResolvedTarget]]:
    if not targets_path.exists():
        raise TargetConfigError(f"dispatch target runtime config is missing: {targets_path}")
    try:
        payload = json.loads(targets_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise TargetConfigError(f"dispatch target runtime config cannot be parsed: {targets_path} ({exc})") from exc
    return load_targets_payload(payload, env=env, source_label=str(targets_path))


def load_targets_payload(
    payload: dict[str, Any],
    *,
    env: dict[str, str] | None = None,
    source_label: str = "<payload>",
) -> tuple[DispatchDefaults, list[ResolvedTarget]]:
    env_map = os.environ if env is None else env
    defaults_block = payload.get("defaults") if isinstance(payload, dict) else None
    target_rows = payload.get("targets") if isinstance(payload, dict) else None
    if not isinstance(defaults_block, dict) or not isinstance(target_rows, list):
        raise TargetConfigError(f"dispatch target runtime config is missing defaults/targets: {source_label}")
    defaults = _parse_dispatch_defaults(defaults_block)
    registry_version = _payload_registry_version(payload)
    targets: list[ResolvedTarget] = []
    for row in target_rows:
        if not isinstance(row, dict):
            continue
        target_group = str(row.get("targetGroup") or "").strip()
        boundary = _load_target_boundary(row, registry_version=registry_version, source_label=source_label)
        enabled_env = str(row.get("enabledEnv") or "").strip()
        endpoint_env = str(row.get("endpointEnv") or "").strip()
        secret_env = str(row.get("secretEnv") or "").strip()
        title_env = str(row.get("titleEnv") or "").strip()
        format_env = str(row.get("formatEnv") or "").strip()
        at_all_env = str(row.get("atAllEnv") or "").strip()
        silence_env = str(row.get("silenceEnabledEnv") or "").strip()
        silence_min_env = str(row.get("silenceMinDeltaEnv") or "").strip()
        allowed_levels_env = str(row.get("allowedReleaseLevelsEnv") or "").strip()
        enabled_default = parse_bool(row.get("enabledDefault"), False)
        enabled = parse_bool(env_map.get(enabled_env), enabled_default) if enabled_env else enabled_default
        endpoint_url = str(env_map.get(endpoint_env) or "").strip() if endpoint_env else ""
        secret = str(env_map.get(secret_env) or "").strip() if secret_env else ""
        title = str(env_map.get(title_env) or row.get("titleDefault") or row.get("id") or "").strip()
        msg_format = str(env_map.get(format_env) or row.get("formatDefault") or "card").strip().lower() or "card"
        at_all = parse_bool(env_map.get(at_all_env), parse_bool(row.get("atAllDefault"), False))
        silence_enabled = parse_bool(env_map.get(silence_env), parse_bool(row.get("silenceEnabledDefault"), False))
        silence_min_delta = parse_float(env_map.get(silence_min_env), parse_float(row.get("silenceMinDeltaDefault"), 0.2, 0.0), 0.0)
        allowed_release_levels = parse_release_levels(env_map.get(allowed_levels_env), parse_release_levels(row.get("allowedReleaseLevelsDefault"), ["official"]))
        env_fields = {
            "enabled_env": enabled_env,
            "endpoint_env": endpoint_env,
            "secret_env": secret_env,
            "title_env": title_env,
            "format_env": format_env,
            "at_all_env": at_all_env,
            "silence_enabled_env": silence_env,
            "silence_min_delta_env": silence_min_env,
            "allowed_release_levels_env": allowed_levels_env,
        }
        targets.append(ResolvedTarget(
            target_id=str(row.get("id") or "").strip(),
            transport=str(row.get("transport") or "").strip(),
            provider=str(row.get("provider") or "").strip(),
            target_group=target_group,
            delivery_tier=str(row.get("deliveryTier") or "").strip(),
            message_profile=str(row.get("messageProfile") or "").strip(),
            enabled=enabled,
            enabled_default=enabled_default,
            configured=True,
            endpoint_url=endpoint_url,
            endpoint_present=bool(endpoint_url),
            secret=secret,
            secret_required=parse_bool(row.get("secretRequiredDefault"), False),
            secret_present=bool(secret),
            title=title,
            msg_format=msg_format,
            at_all=at_all,
            silence_enabled=silence_enabled,
            silence_min_delta=silence_min_delta,
            allowed_release_levels=allowed_release_levels,
            max_attempts=defaults.max_attempts,
            dedupe_window_hours=defaults.dedupe_window_hours,
            backoff_seconds=list(defaults.backoff_seconds),
            env=env_fields,
            source_registry_path=str(row.get("sourceRegistryPath") or "").strip(),
            extension_id=str(row.get("extensionId") or "").strip(),
            display_name=str(row.get("displayName") or "").strip(),
            role_description=str(row.get("roleDescription") or "").strip(),
            audience_description=str(row.get("audienceDescription") or "").strip(),
            dispatch_lane=str(boundary.get("dispatchLane") or "").strip(),
            payload_scope=str(boundary.get("payloadScope") or "").strip(),
            publish_latest=parse_bool(boundary.get("publishLatestDefault"), False),
            boundary_description=str(boundary.get("description") or "").strip(),
        ))
    return defaults, targets


def target_provider_supported(target: ResolvedTarget) -> bool:
    return resolve_channel_provider_adapter(target.provider, target.transport) is not None


def validate_target_endpoint(target: ResolvedTarget) -> dict[str, Any]:
    adapter = resolve_channel_provider_adapter(target.provider, target.transport)
    if adapter is None:
        return {"ok": False, "reason": f"unsupported provider adapter: provider={target.provider}, transport={target.transport}"}
    validator = endpoint_validator(adapter)
    return validator(target.endpoint_url)


def evaluate_target_policies(targets: list[ResolvedTarget]) -> dict[str, dict[str, Any]]:
    shared_map: dict[str, list[str]] = {}
    for target in targets:
        if target.enabled and target.endpoint_present:
            shared_map.setdefault(target.endpoint_url, []).append(target.target_id)
    policies: dict[str, dict[str, Any]] = {}
    for target in targets:
        blocking_issues: list[str] = []
        security_warnings: list[str] = []
        if target.enabled and not target_provider_supported(target):
            blocking_issues.append("unsupported_provider_adapter")
        if target.enabled and not target.endpoint_present:
            blocking_issues.append("enabled_but_endpoint_missing")
        if target.enabled and target.secret_required and not target.secret_present:
            blocking_issues.append("enabled_but_secret_missing")
        validation = validate_target_endpoint(target) if target.endpoint_present else {"ok": False, "reason": "endpoint is empty"}
        if target.enabled and target.endpoint_present and not validation["ok"]:
            blocking_issues.append("invalid_target_endpoint")
        if target.enabled and target.endpoint_present:
            shared = [item for item in shared_map.get(target.endpoint_url, []) if item != target.target_id]
            if shared:
                blocking_issues.append(f"shared_target_endpoint:{','.join(sorted(shared))}")
        policies[target.target_id] = {
            "blocking_issues": blocking_issues,
            "security_warnings": security_warnings,
            "endpoint_validation": validation,
        }
    return policies


def build_target_summary(targets: list[ResolvedTarget], policies: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    enabled_count = 0
    blocking_enabled_count = 0
    security_warning_count = 0
    for target in targets:
        policy = policies.get(target.target_id) or {"blocking_issues": [], "security_warnings": []}
        blocking_issues = list(policy.get("blocking_issues") or [])
        security_warnings = list(policy.get("security_warnings") or [])
        if target.enabled:
            enabled_count += 1
            if blocking_issues:
                blocking_enabled_count += 1
            if security_warnings:
                security_warning_count += 1
        rows.append({
            "target_id": target.target_id,
            "transport": target.transport,
            "provider": target.provider,
            "title": str(getattr(target, "title", "") or ""),
            "display_name": str(getattr(target, "display_name", "") or ""),
            "role_description": str(getattr(target, "role_description", "") or ""),
            "audience_description": str(getattr(target, "audience_description", "") or ""),
            "dispatch_lane": str(getattr(target, "dispatch_lane", "") or ""),
            "payload_scope": str(getattr(target, "payload_scope", "") or ""),
            "publish_latest": target_publishes_latest(target),
            "boundary_description": str(getattr(target, "boundary_description", "") or ""),
            "target_group": target.target_group,
            "delivery_tier": target.delivery_tier,
            "message_profile": target.message_profile,
            "configured": target.configured,
            "enabled": target.enabled,
            "enabled_default": target.enabled_default,
            "endpoint_present": target.endpoint_present,
            "secret_required": target.secret_required,
            "secret_present": target.secret_present,
            "allowed_release_levels": list(target.allowed_release_levels),
            "blocking_issues": blocking_issues,
            "security_warnings": security_warnings,
            "format": target.msg_format,
            "silence_enabled": target.silence_enabled,
            "silence_min_delta": target.silence_min_delta,
            "current_release_allowed": None,
        })
    return {
        "enabled_count": enabled_count,
        "blocking_enabled_count": blocking_enabled_count,
        "security_warning_count": security_warning_count,
        "targets": rows,
    }
