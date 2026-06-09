#!/usr/bin/env python3
"""从模型 profile 派生部署与运行态 env 需求。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openclaw.lib.io.json_access import json_object


@dataclass(frozen=True)
class ModelEnvSpec:
    name: str
    required: bool
    secret: bool
    validator: str
    source_model_ref: str
    purpose: str


def _channel(model: dict[str, Any]) -> dict[str, Any]:
    return json_object(model.get("channel"))


def _channel_env(model: dict[str, Any]) -> dict[str, Any]:
    channel = _channel(model)
    return {
        "baseUrlEnv": channel.get("baseUrlEnv"),
        "apiKeyEnv": channel.get("apiKeyEnv"),
    }


def _model_ref_env(model: dict[str, Any]) -> str:
    return str(model.get("modelRefEnv") or "").strip()


def _auth_required(model: dict[str, Any]) -> bool:
    channel = _channel(model)
    auth = json_object(channel.get("auth"))
    if "required" in auth:
        return bool(auth.get("required"))
    channel_env = _channel_env(model)
    return bool(channel_env.get("apiKeyEnv") and str(channel.get("kind") or "http").strip() == "http")


def _local_command_env(model: dict[str, Any]) -> str:
    local_process = json_object(_channel(model).get("localProcess"))
    return str(local_process.get("commandEnv") or "").strip()


def _local_command_declared(model: dict[str, Any]) -> bool:
    local_process = json_object(_channel(model).get("localProcess"))
    command = local_process.get("command")
    return isinstance(command, list) and any(str(item).strip() for item in command)


def _owned_index(registry: dict[str, Any], collection_key: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    qualified = registry.get(f"{collection_key}ByQualifiedId")
    if isinstance(qualified, dict):
        result.update({str(key): value for key, value in qualified.items() if isinstance(value, dict)})
    by_local_id = registry.get(f"{collection_key}ById")
    if isinstance(by_local_id, dict):
        result.update({str(key): value for key, value in by_local_id.items() if isinstance(value, dict)})
    return result


def model_env_specs_from_registry(registry: dict[str, Any], *, scheduler_scope: bool = True) -> dict[str, ModelEnvSpec]:
    specs: dict[str, ModelEnvSpec] = {}
    models_by_id = _owned_index(registry, "models")
    agents_by_id = _owned_index(registry, "agents")
    used_model_refs: set[str] = set()
    for job in registry.get("jobs") or []:
        if not isinstance(job, dict):
            continue
        model_ref = str(job.get("resolvedModelProfileQualifiedRef") or job.get("resolvedModelProfileRef") or job.get("modelProfileRef") or "").strip()
        if not model_ref:
            agent = agents_by_id.get(str(job.get("resolvedAgentQualifiedRef") or job.get("resolvedAgentRef") or job.get("agentRef") or "").strip())
            if isinstance(agent, dict):
                model_ref = str(agent.get("resolvedDefaultModelProfileRef") or agent.get("defaultModelProfileRef") or "").strip()
        if model_ref:
            used_model_refs.add(model_ref)

    for model_ref in sorted(used_model_refs):
        model = models_by_id.get(model_ref)
        if not isinstance(model, dict):
            continue
        channel = _channel(model)
        channel_kind = str(channel.get("kind") or "http").strip() or "http"
        channel_env = _channel_env(model)

        def add(name: object, *, required: bool, secret: bool, validator: str, purpose: str) -> None:
            env_name = str(name or "").strip()
            if not env_name:
                return
            current = specs.get(env_name)
            incoming = ModelEnvSpec(
                name=env_name,
                required=required,
                secret=secret,
                validator=validator,
                source_model_ref=model_ref,
                purpose=purpose,
            )
            if current is None:
                specs[env_name] = incoming
                return
            specs[env_name] = ModelEnvSpec(
                name=env_name,
                required=current.required or incoming.required,
                secret=current.secret or incoming.secret,
                validator=current.validator or incoming.validator,
                source_model_ref=current.source_model_ref,
                purpose=current.purpose,
            )

        if channel_kind == "http":
            add(_model_ref_env(model), required=True, secret=False, validator="non_empty", purpose="model_ref")
            add(channel_env.get("baseUrlEnv"), required=True, secret=False, validator="http_url", purpose="model_base_url")
            if scheduler_scope:
                add(channel_env.get("apiKeyEnv"), required=_auth_required(model), secret=True, validator="secret_like", purpose="model_api_key")
        elif channel_kind == "local_process":
            add(_model_ref_env(model), required=True, secret=False, validator="non_empty", purpose="model_ref")
            add(
                _local_command_env(model),
                required=not _local_command_declared(model),
                secret=False,
                validator="non_empty",
                purpose="local_model_command",
            )
    return specs
