#!/usr/bin/env python3
"""控制面模型画像解析。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openclaw.control_plane.config_loader import ControlPlaneConfigError, load_control_plane_service_payload
from openclaw.control_plane.extensions.api import load_enabled_extensions
from openclaw.control_plane.extensions.normalization import ExtensionError
from openclaw.lib.models.cost_policy import ModelCostPolicyError, validate_model_cost_policy
from openclaw.lib.repo.layout import resolve_control_plane_service_config_path


class ModelRegistryError(RuntimeError):
    """模型画像加载失败。"""


@dataclass(frozen=True)
class ModelProfile:
    profile_id: str
    provider: str
    model_ref: str
    model_ref_env: str
    base_url_env: str
    api_key_env: str
    channel: dict[str, Any]
    request_timeout_seconds: int
    job_default_timeout_seconds: int
    capabilities: dict[str, Any]
    rate_limits: dict[str, Any]
    cost_policy: dict[str, Any]
    raw: dict[str, Any]

    @property
    def remote_model_name(self) -> str:
        model_ref = str(self.model_ref or "").strip()
        provider = str(self.provider or "").strip()
        prefix = f"{provider}/"
        if provider and model_ref.startswith(prefix):
            return model_ref[len(prefix):]
        return model_ref

    @property
    def channel_kind(self) -> str:
        return str((self.channel or {}).get("kind") or "http").strip() or "http"

    @property
    def channel_api(self) -> str:
        return str((self.channel or {}).get("api") or "").strip()

    @property
    def auth_required(self) -> bool:
        auth = self.channel.get("auth") if isinstance(self.channel.get("auth"), dict) else {}
        if "required" in auth:
            return bool(auth.get("required"))
        return bool(self.api_key_env and self.channel_kind == "http")

    @property
    def local_process_command(self) -> list[str]:
        local_process = self.channel.get("localProcess") if isinstance(self.channel.get("localProcess"), dict) else {}
        raw = local_process.get("command")
        if isinstance(raw, list):
            return [str(item) for item in raw if str(item).strip()]
        return []

    @property
    def local_process_command_env(self) -> str:
        local_process = self.channel.get("localProcess") if isinstance(self.channel.get("localProcess"), dict) else {}
        return str(local_process.get("commandEnv") or "").strip()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ModelRegistryError(f"模型画像文件不存在：{path}") from exc
    except Exception as exc:
        raise ModelRegistryError(f"模型画像 JSON 无法解析：{path} ({exc})") from exc
    if not isinstance(payload, dict):
        raise ModelRegistryError(f"模型画像顶层必须为对象：{path}")
    return payload


def _model_profiles_dirs(service_config_path: Path | None = None) -> list[Path]:
    try:
        config_path = Path(service_config_path).resolve() if service_config_path is not None else resolve_control_plane_service_config_path()
        resolved_config_path, payload = load_control_plane_service_payload(config_path)
    except (ValueError, ControlPlaneConfigError) as exc:
        raise ModelRegistryError(str(exc)) from exc
    base_dir = resolved_config_path.parent
    registry = payload.get("registry") if isinstance(payload, dict) else None

    directories: list[Path] = []
    models_dir = str((registry or {}).get("modelsDir") or "").strip()
    if models_dir:
        directories.append((base_dir / models_dir).resolve())

    try:
        extensions = load_enabled_extensions(payload, service_base_dir=base_dir)
    except ExtensionError as exc:
        raise ModelRegistryError(str(exc)) from exc
    for extension in extensions:
        extension_registry = extension.get("registry") if isinstance(extension.get("registry"), dict) else {}
        for candidate in extension_registry.get("modelsDirs") or []:
            if not isinstance(candidate, Path):
                continue
            resolved = candidate.resolve()
            if resolved not in directories:
                directories.append(resolved)

    if not directories:
        raise ModelRegistryError("control plane service.registry.modelsDir 不能为空")
    deduped: list[Path] = []
    for directory in directories:
        if directory not in deduped:
            deduped.append(directory)
    return deduped


def model_profiles_dir(service_config_path: Path | None = None) -> Path:
    directories = _model_profiles_dirs(service_config_path)
    for directory in directories:
        if any(directory.glob("*.json")):
            return directory
    return directories[0]


def _model_profile_path_owner_id(path: Path) -> str:
    parts = path.resolve().parts
    for idx, part in enumerate(parts[:-2]):
        if part == "extensions" and idx > 0 and parts[idx - 1] == "agent":
            return parts[idx + 1]
    return ""


def load_model_profile(profile_ref: str, *, service_config_path: Path | None = None) -> ModelProfile:
    normalized_ref = str(profile_ref or "").strip()
    if not normalized_ref:
        raise ModelRegistryError("model profile ref 不能为空")
    owner_ref = ""
    local_ref = normalized_ref
    if ":" in normalized_ref:
        owner_ref, local_ref = [part.strip() for part in normalized_ref.split(":", 1)]
        if not owner_ref or not local_ref:
            raise ModelRegistryError(f"modelProfileRef 非法：{normalized_ref}")

    matched: ModelProfile | None = None
    matched_path: Path | None = None
    for directory in _model_profiles_dirs(service_config_path):
        for path in sorted(directory.glob("*.json")):
            payload = _read_json(path)
            if str(payload.get("id") or "").strip() != local_ref:
                continue
            if owner_ref and _model_profile_path_owner_id(path) != owner_ref:
                continue
            if matched is not None and matched_path is not None:
                raise ModelRegistryError(f"modelProfileRef {normalized_ref} 在多个目录重复注册：{matched_path}, {path}")
            channel = payload.get("channel") if isinstance(payload.get("channel"), dict) else {}
            timeout_policy = payload.get("timeoutPolicy") if isinstance(payload.get("timeoutPolicy"), dict) else {}
            provider = str(payload.get("provider") or "").strip()
            model_ref_env = str(payload.get("modelRefEnv") or "").strip()
            model_ref = str(payload.get("modelRef") or "").strip()
            if model_ref_env:
                env_model_ref = str(os.environ.get(model_ref_env) or "").strip()
                if env_model_ref:
                    if provider and "/" not in env_model_ref:
                        model_ref = f"{provider}/{env_model_ref}"
                    else:
                        model_ref = env_model_ref
            matched = ModelProfile(
                profile_id=normalized_ref,
                provider=provider,
                model_ref=model_ref,
                model_ref_env=model_ref_env,
                base_url_env=str(channel.get("baseUrlEnv") or "").strip(),
                api_key_env=str(channel.get("apiKeyEnv") or "").strip(),
                channel=dict(channel),
                request_timeout_seconds=max(1, int(timeout_policy.get("requestTimeoutSeconds") or 120)),
                job_default_timeout_seconds=max(1, int(timeout_policy.get("jobDefaultTimeoutSeconds") or 1200)),
                capabilities=dict(payload.get("capabilities") or {}),
                rate_limits=dict(payload.get("rateLimits") or {}),
                cost_policy=dict(payload.get("costPolicy") or {}),
                raw=payload,
            )
            try:
                validate_model_cost_policy(matched)
            except ModelCostPolicyError as exc:
                raise ModelRegistryError(str(exc)) from exc
            matched_path = path

    if matched is not None:
        return matched
    raise ModelRegistryError(f"未注册的 modelProfileRef：{normalized_ref}")
