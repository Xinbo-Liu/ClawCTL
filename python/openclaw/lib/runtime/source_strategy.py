#!/usr/bin/env python3
"""读取 runtime source strategy 中声明的镜像角色集合。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openclaw.lib.repo.contracts import repo_contract_path, repo_contracts


@dataclass(frozen=True)
class ImageRole:
    """表示部署或运行镜像角色与其 env key、pin 真源之间的关系。"""

    image_id: str
    env_key: str
    pin_contract: str
    label: str
    role: str
    scope: str
    compose_enabled: bool
    compose_selector: str


def _load_strategy(root_dir: Path) -> dict[str, Any]:
    """从 repo contract 定位并读取 runtime source strategy JSON。"""
    path = repo_contract_path('runtime.source_strategy', root_dir=root_dir)
    payload = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise ValueError('runtime.source_strategy 顶层必须为对象')
    return payload


def _pin_contract_from_file(pin_file: str, root_dir: Path) -> str:
    """把 source strategy 中的 pin_file 映射为 repo contract id。"""
    normalized = str(pin_file or '').strip().replace('\\', '/')
    matches = [
        contract.id
        for contract in repo_contracts(root_dir).values()
        if contract.relative_path == normalized and contract.id.startswith('image_pins.')
    ]
    if len(matches) == 1:
        return matches[0]
    raise ValueError(f'未知镜像 pin_file：{pin_file}')


def _image_role_from_entry(image_id: str, payload: dict[str, Any], root_dir: Path) -> ImageRole:
    """把单个 source strategy image entry 转换为 ImageRole。"""
    selected = payload.get('selected_runtime_source') if isinstance(payload.get('selected_runtime_source'), dict) else {}
    contract = payload.get('deployment_contract') if isinstance(payload.get('deployment_contract'), dict) else {}
    runtime = payload.get('compose_runtime') if isinstance(payload.get('compose_runtime'), dict) else {}
    env_key = str(selected.get('ref_env') or '').strip()
    if not env_key:
        raise ValueError(f'{image_id} 缺少 selected_runtime_source.ref_env')
    pin_contract = _pin_contract_from_file(str(selected.get('pin_file') or '').strip(), root_dir)
    role = str(contract.get('managed_tag_role') or contract.get('role') or image_id).strip()
    label = str(contract.get('label') or payload.get('summary') or image_id).strip()
    scope = str(contract.get('scope') or '').strip()
    return ImageRole(
        image_id=image_id,
        env_key=env_key,
        pin_contract=pin_contract,
        label=label,
        role=role,
        scope=scope,
        compose_enabled=runtime.get('enabled') is True,
        compose_selector=str(runtime.get('target_selector') or '').strip(),
    )


def _iter_strategy_images(root_dir: Path) -> list[tuple[str, dict[str, Any]]]:
    """读取 source_strategy.images 并保留 JSON 中的角色顺序。"""
    strategy = _load_strategy(root_dir)
    images = strategy.get('images')
    if not isinstance(images, dict):
        raise ValueError('runtime.source_strategy.images 必须为对象')
    return [(str(image_id), raw) for image_id, raw in images.items() if isinstance(raw, dict)]


def deployment_image_roles(root_dir: Path) -> list[ImageRole]:
    """按 source_strategy 顺序返回部署镜像合同角色集合。"""
    roles: list[ImageRole] = []
    for image_id, raw in _iter_strategy_images(root_dir):
        contract = raw.get('deployment_contract') if isinstance(raw.get('deployment_contract'), dict) else {}
        if contract.get('enabled') is not True:
            continue
        roles.append(_image_role_from_entry(image_id, raw, root_dir))
    return roles


def runtime_service_image_roles(root_dir: Path) -> list[ImageRole]:
    """按 source_strategy 顺序返回 compose 运行服务镜像角色集合。"""
    roles: list[ImageRole] = []
    for image_id, raw in _iter_strategy_images(root_dir):
        contract = raw.get('deployment_contract') if isinstance(raw.get('deployment_contract'), dict) else {}
        if contract.get('enabled') is not True:
            continue
        runtime = raw.get('compose_runtime') if isinstance(raw.get('compose_runtime'), dict) else {}
        if runtime.get('enabled') is True:
            role = _image_role_from_entry(image_id, raw, root_dir)
            if not role.compose_selector:
                raise ValueError(f'{image_id} 启用 compose_runtime 时必须声明 target_selector')
            roles.append(role)
    return roles
