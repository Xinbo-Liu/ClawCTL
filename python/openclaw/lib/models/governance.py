#!/usr/bin/env python3
"""模型调用运行治理：限流、并发闸门与脱敏审计。"""
from __future__ import annotations

import hashlib
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from openclaw.lib.io.state import append_jsonl, read_json_if_exists, with_lock_dir, write_json_atomic
from openclaw.lib.models.registry import ModelProfile
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.runtime.time import format_datetime_in_app_tz


class ModelGovernanceError(RuntimeError):
    """模型调用治理闸门失败。"""


def _safe_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return token or "unknown"


def model_runtime_state_dir() -> Path:
    raw = str(os.environ.get("OPENCLAW_MODEL_RUNTIME_STATE_DIR") or "").strip()
    if raw:
        return Path(raw).resolve()
    state_root = str(os.environ.get("OPENCLAW_STATE_DIR") or "").strip()
    if state_root:
        return (Path(state_root).resolve() / "model_runtime")
    return resolve_repo_root(Path(__file__)) / "state" / "openclaw" / "model_runtime"


def _rate_state_path(profile: ModelProfile) -> Path:
    return model_runtime_state_dir() / "rate" / f"{_safe_token(profile.profile_id)}.json"


def _concurrency_lock_path(profile: ModelProfile, slot: int) -> Path:
    return model_runtime_state_dir() / "locks" / f"{_safe_token(profile.profile_id)}.{slot}.lock"


def _rate_lock_path(profile: ModelProfile) -> Path:
    return model_runtime_state_dir() / "rate" / f".{_safe_token(profile.profile_id)}.lock"


def _positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _acquire_concurrency_slot(profile: ModelProfile) -> tuple[Path, Iterator[None]]:
    max_concurrent = _positive_int(profile.rate_limits.get("maxConcurrentRequests"), 1)
    deadline = time.time() + float(_positive_int(profile.request_timeout_seconds, 120))
    last_error: Exception | None = None
    while time.time() <= deadline:
        for slot in range(max_concurrent):
            lock_dir = _concurrency_lock_path(profile, slot)
            guard = with_lock_dir(lock_dir)
            try:
                guard.__enter__()
            except Exception as exc:
                last_error = exc
                continue
            return lock_dir, guard
        time.sleep(0.2)
    detail = f"；最后错误：{last_error}" if last_error else ""
    raise ModelGovernanceError(f"模型并发闸门超时：profile={profile.profile_id}{detail}")


def _record_rate_window(profile: ModelProfile) -> None:
    rpm = _positive_int(profile.rate_limits.get("requestsPerMinute"), 0)
    if rpm <= 0:
        return
    now = time.time()
    state_path = _rate_state_path(profile)
    with with_lock_dir(_rate_lock_path(profile)):
        payload = read_json_if_exists(state_path, default={})
        timestamps: list[float] = []
        for item in (payload.get("timestamps") if isinstance(payload, dict) else []) or []:
            try:
                timestamps.append(float(item))
            except (TypeError, ValueError):
                continue
        timestamps = [item for item in timestamps if now - item < 60.0]
        if len(timestamps) >= rpm:
            raise ModelGovernanceError(f"模型 RPM 限流命中：profile={profile.profile_id} rpm={rpm}")
        timestamps.append(now)
        write_json_atomic(state_path, {"profileRef": profile.profile_id, "timestamps": timestamps})


@contextmanager
def model_call_governance(profile: ModelProfile) -> Iterator[None]:
    """围绕单次模型请求执行 provider/profile 级闸门。"""
    lock_path, guard = _acquire_concurrency_slot(profile)
    try:
        _record_rate_window(profile)
        yield
    finally:
        try:
            guard.__exit__(None, None, None)
        except Exception:
            pass


def text_sha1(text: object) -> str:
    return hashlib.sha1(str(text or "").encode("utf-8")).hexdigest()


def audit_log_path() -> Path:
    raw = str(os.environ.get("OPENCLAW_MODEL_AUDIT_LOG") or "").strip()
    if raw:
        return Path(raw).resolve()
    return model_runtime_state_dir() / "audit" / "model_calls.jsonl"


def write_model_call_audit(
    *,
    profile: ModelProfile,
    prompt: str,
    system_prompt: str | None,
    max_tokens: int | None,
    status: str,
    status_code: int | None = None,
    output_text: str = "",
    error: str = "",
    api_kind: str = "",
    cost_estimate: dict[str, Any] | None = None,
    actual_cost: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "schemaVersion": 1,
        "createdAt": format_datetime_in_app_tz(),
        "profileRef": profile.profile_id,
        "provider": profile.provider,
        "modelRef": profile.model_ref,
        "channelKind": profile.channel_kind,
        "api": api_kind or profile.channel_api,
        "status": status,
        "statusCode": status_code,
        "maxTokens": max_tokens,
        "promptChars": len(str(prompt or "")),
        "promptSha1": text_sha1(prompt),
        "systemPromptChars": len(str(system_prompt or "")),
        "systemPromptSha1": text_sha1(system_prompt),
        "outputChars": len(str(output_text or "")),
        "outputSha1": text_sha1(output_text),
    }
    if error:
        payload["errorChars"] = len(str(error or ""))
        payload["errorSha1"] = text_sha1(error)
    if isinstance(cost_estimate, dict) and cost_estimate:
        payload["costEstimate"] = dict(cost_estimate)
    if isinstance(actual_cost, dict) and actual_cost:
        payload["actualCost"] = dict(actual_cost)
    try:
        append_jsonl(audit_log_path(), payload)
    except Exception:
        return
