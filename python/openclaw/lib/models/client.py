#!/usr/bin/env python3
"""统一远程模型 HTTP 客户端。"""
from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from typing import Any

from openclaw.lib.http.json_client import http_post_json
from openclaw.lib.models.cost_policy import (
    ModelCostPolicyError,
    enforce_model_cost_budget,
    estimate_model_response_cost,
    estimate_requested_model_call_cost,
    record_model_call_cost,
)
from openclaw.lib.models.governance import ModelGovernanceError, model_call_governance, write_model_call_audit
from openclaw.lib.models.registry import ModelProfile, ModelRegistryError, load_model_profile
from openclaw.lib.repo.static_truth import runtime_contract_summary


class ModelClientError(RuntimeError):
    """模型调用失败。"""


@dataclass(frozen=True)
class ModelResponse:
    profile_ref: str
    provider: str
    model_ref: str
    text: str
    status_code: int
    raw_response: dict[str, Any]
    cost_estimate: dict[str, Any] | None = None
    actual_cost: dict[str, Any] | None = None


def _provider_api_kind(profile: ModelProfile) -> str:
    if profile.channel_api:
        return profile.channel_api
    provider = str(profile.provider or "").strip()
    if provider == "openai_compatible":
        return "openai-chat-completions"
    if provider == "anthropic_messages":
        return "anthropic-messages"
    contract = runtime_contract_summary()
    contract_provider = contract.get("contract", {}).get("model_runtime", {}).get("provider", {})
    if str(contract_provider.get("id") or "").strip() == provider:
        return str(contract_provider.get("api") or "").strip() or "openai-chat-completions"
    if provider == "minimax":
        return "anthropic-messages"
    if provider == "ollama":
        return "ollama-chat"
    return "openai-chat-completions"


def _base_url(profile: ModelProfile) -> str:
    raw = str(os.environ.get(profile.base_url_env, "")).strip()
    if not raw:
        raise ModelClientError(f"模型画像 {profile.profile_id} 缺少 base URL 环境变量：{profile.base_url_env}")
    return raw.rstrip("/")


def _api_key(profile: ModelProfile) -> str:
    if not profile.api_key_env:
        return ""
    value = str(os.environ.get(profile.api_key_env, "")).strip()
    if profile.auth_required and not value:
        raise ModelClientError(f"模型画像 {profile.profile_id} 缺少 API key 环境变量：{profile.api_key_env}")
    return value


def _anthropic_payload(profile: ModelProfile, prompt: str, *, system_prompt: str | None, max_tokens: int | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": profile.remote_model_name,
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        "max_tokens": max_tokens or int((profile.capabilities or {}).get("maxTokens") or 1024),
    }
    if str(system_prompt or "").strip():
        payload["system"] = str(system_prompt)
    return payload


def _openai_payload(profile: ModelProfile, prompt: str, *, system_prompt: str | None, max_tokens: int | None) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    if str(system_prompt or "").strip():
        messages.append({"role": "system", "content": str(system_prompt)})
    messages.append({"role": "user", "content": prompt})
    payload: dict[str, Any] = {
        "model": profile.remote_model_name,
        "messages": messages,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    return payload


def _ollama_payload(profile: ModelProfile, prompt: str, *, system_prompt: str | None, max_tokens: int | None) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    if str(system_prompt or "").strip():
        messages.append({"role": "system", "content": str(system_prompt)})
    messages.append({"role": "user", "content": prompt})
    payload: dict[str, Any] = {
        "model": profile.remote_model_name,
        "messages": messages,
        "stream": False,
        "think": False,
    }
    if max_tokens is not None:
        payload["options"] = {"num_predict": max_tokens}
    return payload


def _endpoint_and_payload(profile: ModelProfile, prompt: str, *, system_prompt: str | None, max_tokens: int | None) -> tuple[str, dict[str, Any], dict[str, str]]:
    api_kind = _provider_api_kind(profile)
    base_url = _base_url(profile)
    api_key = _api_key(profile)
    if api_kind == "anthropic-messages":
        headers = {"anthropic-version": "2023-06-01"}
        if api_key:
            headers["x-api-key"] = api_key
        return f"{base_url}/v1/messages", _anthropic_payload(profile, prompt, system_prompt=system_prompt, max_tokens=max_tokens), headers
    if api_kind == "ollama-chat":
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return f"{base_url}/api/chat", _ollama_payload(profile, prompt, system_prompt=system_prompt, max_tokens=max_tokens), headers
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return f"{base_url}/v1/chat/completions", _openai_payload(profile, prompt, system_prompt=system_prompt, max_tokens=max_tokens), headers


def _extract_text(profile: ModelProfile, payload: dict[str, Any]) -> str:
    api_kind = _provider_api_kind(profile)
    if api_kind == "anthropic-messages":
        parts: list[str] = []
        for item in payload.get("content") or []:
            if isinstance(item, dict) and str(item.get("type") or "") == "text":
                parts.append(str(item.get("text") or ""))
        return "\n".join(part for part in parts if part).strip()
    if api_kind == "ollama-chat":
        message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        content = str(message.get("content") or payload.get("response") or "").strip()
        return content
    choices = payload.get("choices") or []
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, list):
                parts = [str(item.get("text") or "") for item in content if isinstance(item, dict)]
                return "\n".join(part for part in parts if part).strip()
            return str(content or "").strip()
    return ""


def _run_http_model(
    profile: ModelProfile,
    prompt: str,
    *,
    system_prompt: str | None,
    max_tokens: int | None,
) -> tuple[str, int, dict[str, Any], str]:
    api_kind = _provider_api_kind(profile)
    endpoint, payload, headers = _endpoint_and_payload(profile, prompt, system_prompt=system_prompt, max_tokens=max_tokens)
    result = http_post_json(endpoint, payload, timeout_sec=float(profile.request_timeout_seconds), headers=headers)
    if not result.ok or result.error:
        raise ModelClientError(
            f"模型调用失败：profile={profile.profile_id} status={result.status_code} error={result.error or 'request failed'}"
        )
    text = _extract_text(profile, result.payload)
    return text, result.status_code, result.payload, api_kind


def _local_process_command(profile: ModelProfile) -> list[str]:
    if profile.local_process_command:
        return list(profile.local_process_command)
    env_name = profile.local_process_command_env
    raw = str(os.environ.get(env_name, "")).strip() if env_name else ""
    if not raw:
        raise ModelClientError(f"模型画像 {profile.profile_id} 缺少本地模型命令：channel.localProcess.command 或 {env_name}")
    try:
        return shlex.split(raw)
    except ValueError as exc:
        raise ModelClientError(f"模型画像 {profile.profile_id} 本地模型命令无法解析：{env_name}") from exc


def _extract_local_process_text(stdout: str) -> tuple[str, dict[str, Any]]:
    text = str(stdout or "").strip()
    if not text:
        return "", {}
    import json

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text, {"stdout": text}
    if not isinstance(payload, dict):
        return text, {"stdout": text}
    for key in ("text", "content", "response", "output"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value, payload
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    value = str(message.get("content") or "").strip()
    return value, payload


def _run_local_process_model(
    profile: ModelProfile,
    prompt: str,
    *,
    system_prompt: str | None,
    max_tokens: int | None,
) -> tuple[str, int, dict[str, Any], str]:
    import json

    api_kind = _provider_api_kind(profile) or "local-process-json"
    command = _local_process_command(profile)
    request = {
        "schemaVersion": 1,
        "model": profile.remote_model_name,
        "modelRef": profile.model_ref,
        "prompt": prompt,
        "system": system_prompt or "",
        "maxTokens": max_tokens,
    }
    try:
        completed = subprocess.run(
            command,
            input=json.dumps(request, ensure_ascii=False),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=float(profile.request_timeout_seconds),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ModelClientError(f"本地模型调用超时：profile={profile.profile_id}") from exc
    except OSError as exc:
        raise ModelClientError(f"本地模型命令启动失败：profile={profile.profile_id} error={exc}") from exc
    if completed.returncode != 0:
        detail = str(completed.stderr or completed.stdout or "").strip()[:500]
        raise ModelClientError(f"本地模型调用失败：profile={profile.profile_id} rc={completed.returncode} {detail}")
    text, payload = _extract_local_process_text(completed.stdout)
    if not payload:
        payload = {"stdout": completed.stdout}
    payload.setdefault("returncode", completed.returncode)
    return text, 0, payload, api_kind


def _run_model_call(
    profile: ModelProfile,
    prompt: str,
    *,
    system_prompt: str | None,
    max_tokens: int | None,
) -> tuple[str, int, dict[str, Any], str]:
    channel_kind = profile.channel_kind
    if channel_kind == "local_process":
        return _run_local_process_model(profile, prompt, system_prompt=system_prompt, max_tokens=max_tokens)
    if channel_kind == "http":
        return _run_http_model(profile, prompt, system_prompt=system_prompt, max_tokens=max_tokens)
    raise ModelClientError(f"模型画像 {profile.profile_id} 不支持的 channel.kind：{channel_kind}")


def generate_text(
    *,
    model_profile_ref: str,
    prompt: str,
    system_prompt: str | None = None,
    max_tokens: int | None = None,
) -> ModelResponse:
    try:
        profile = load_model_profile(model_profile_ref)
    except ModelRegistryError as exc:
        # 画像缺失、profile 配置选择失败等治理错误统一向调用方表现为模型不可用。
        raise ModelClientError(str(exc)) from exc
    api_kind = ""
    requested_cost = None
    actual_cost = None
    try:
        requested_cost = estimate_requested_model_call_cost(
            profile,
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
        )
        enforce_model_cost_budget(profile, requested_cost)
        with model_call_governance(profile):
            text, status_code, raw_response, api_kind = _run_model_call(
                profile,
                prompt,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
            )
        if not text:
            raise ModelClientError(f"模型调用失败：profile={profile.profile_id} 响应缺少可读文本")
        actual_cost = estimate_model_response_cost(
            profile,
            prompt=prompt,
            system_prompt=system_prompt,
            output_text=text,
            raw_response=raw_response,
        )
        record_model_call_cost(profile, actual_cost, status="ok")
        write_model_call_audit(
            profile=profile,
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            status="ok",
            status_code=status_code,
            output_text=text,
            api_kind=api_kind,
            cost_estimate=requested_cost.to_audit_payload(),
            actual_cost=actual_cost.to_audit_payload(),
        )
    except (ModelClientError, ModelGovernanceError, ModelCostPolicyError) as exc:
        write_model_call_audit(
            profile=profile,
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            status="error",
            error=str(exc),
            api_kind=api_kind or _provider_api_kind(profile),
            cost_estimate=requested_cost.to_audit_payload() if requested_cost is not None else None,
            actual_cost=actual_cost.to_audit_payload() if actual_cost is not None else None,
        )
        raise ModelClientError(str(exc)) from exc
    return ModelResponse(
        profile_ref=profile.profile_id,
        provider=profile.provider,
        model_ref=profile.model_ref,
        text=text,
        status_code=status_code,
        raw_response=raw_response,
        cost_estimate=requested_cost.to_audit_payload() if requested_cost is not None else None,
        actual_cost=actual_cost.to_audit_payload() if actual_cost is not None else None,
    )
