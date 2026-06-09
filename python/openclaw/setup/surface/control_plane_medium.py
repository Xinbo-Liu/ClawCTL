"""host 控制面执行介质真源读取。"""
from __future__ import annotations

import sys
from typing import Any, NoReturn

from openclaw.lib.repo.static_truth import read_repo_contract_json, repo_contract_relpath


def fail(message: str, code: int = 2) -> NoReturn:
    sys.stderr.write(f'[control_plane_medium_surface][FAIL] {message}\n')
    raise SystemExit(code)


def load_config() -> dict[str, Any]:
    payload = read_repo_contract_json('setup.control_plane_medium')
    if not isinstance(payload, dict):
        fail(f'{repo_contract_relpath("setup.control_plane_medium")} 顶层必须为对象')
    return payload


def entrypoint(config: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = load_config() if config is None else config
    raw = payload.get('entrypoint') or {}
    if not isinstance(raw, dict):
        fail('entrypoint 必须为对象')
    return raw


def command(config: dict[str, Any] | None = None, mode: str = 'default') -> str:
    entry = entrypoint(config)
    key = 'offline_command' if mode == 'offline' else 'command'
    value = str(entry.get(key) or '').strip()
    if not value:
        fail(f'entrypoint.{key} 不能为空')
    return value


def purpose(config: dict[str, Any] | None = None) -> str:
    value = str(entrypoint(config).get('purpose') or '').strip()
    if not value:
        fail('entrypoint.purpose 不能为空')
    return value


def boundaries(config: dict[str, Any] | None = None) -> list[str]:
    return [str(item).strip() for item in list(entrypoint(config).get('boundaries') or []) if str(item).strip()]


def consumers(config: dict[str, Any] | None = None) -> list[str]:
    return [str(item).strip() for item in list(entrypoint(config).get('consumers') or []) if str(item).strip()]


def references(config: dict[str, Any] | None = None) -> list[str]:
    return [str(item).strip() for item in list(entrypoint(config).get('references') or []) if str(item).strip()]


def generated_doc(config: dict[str, Any] | None = None) -> str:
    payload = load_config() if config is None else config
    generated = payload.get('generated_artifacts') or {}
    if not isinstance(generated, dict):
        fail('generated_artifacts 必须为对象')
    value = str(generated.get('control_plane_medium_doc') or '').strip()
    if not value:
        fail('generated_artifacts.control_plane_medium_doc 不能为空')
    return value


def mode_section(config: dict[str, Any] | None = None, mode: str = 'online') -> dict[str, Any]:
    payload = load_config() if config is None else config
    raw = payload.get(mode) or {}
    if not isinstance(raw, dict):
        fail(f'{mode} 必须为对象')
    return raw


def mode_title(config: dict[str, Any] | None = None, mode: str = 'online') -> str:
    value = str(mode_section(config, mode).get('title') or '').strip()
    if not value:
        fail(f'{mode}.title 不能为空')
    return value


def mode_command(config: dict[str, Any] | None = None, mode: str = 'online') -> str:
    value = str(mode_section(config, mode).get('command') or '').strip()
    if not value:
        fail(f'{mode}.command 不能为空')
    return value


def mode_steps(config: dict[str, Any] | None = None, mode: str = 'online') -> list[str]:
    return [str(item).strip() for item in list(mode_section(config, mode).get('steps') or []) if str(item).strip()]




def help_surface(config: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = load_config() if config is None else config
    raw = payload.get('help_surface') or {}
    if not isinstance(raw, dict):
        fail('help_surface 必须为对象')
    return raw


def help_surface_title(config: dict[str, Any] | None = None) -> str:
    value = str(help_surface(config).get('title') or '').strip()
    if not value:
        fail('help_surface.title 不能为空')
    return value


def help_surface_lines(config: dict[str, Any] | None = None) -> list[str]:
    return [str(item).strip() for item in list(help_surface(config).get('lines') or []) if str(item).strip()]

def failure_guidance(config: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = load_config() if config is None else config
    raw = payload.get('failure_guidance') or {}
    if not isinstance(raw, dict):
        fail('failure_guidance 必须为对象')
    return raw


def failure_title(config: dict[str, Any] | None = None) -> str:
    value = str(failure_guidance(config).get('title') or '').strip()
    if not value:
        fail('failure_guidance.title 不能为空')
    return value


def failure_lines(config: dict[str, Any] | None = None) -> list[str]:
    return [str(item).strip() for item in list(failure_guidance(config).get('lines') or []) if str(item).strip()]
