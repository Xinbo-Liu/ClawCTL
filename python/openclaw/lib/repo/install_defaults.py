#!/usr/bin/env python3
"""Install-default truth helpers shared by repo and runtime layers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openclaw.lib.repo.contracts import repo_contract, repo_contract_path, repo_contract_root

ROOT_DIR = repo_contract_root()


def read_repo_contract_json(contract_id: str, *, root_dir: Path = ROOT_DIR) -> dict[str, Any] | list[Any]:
    """读取 repo contract JSON 真源。"""
    contract = repo_contract(contract_id, root_dir=root_dir)
    if contract.format != 'json':
        raise ValueError(f'repo contract {contract_id} 不是 json：{contract.format}')
    return json.loads(repo_contract_path(contract_id, root_dir=root_dir).read_text(encoding='utf-8'))


def host_install_default(name: str, *, fallback: str = '', required: bool = False, root_dir: Path = ROOT_DIR) -> str:
    """读取宿主机安装默认值。"""
    payload = read_repo_contract_json('governance.install_defaults', root_dir=root_dir)
    defaults = payload.get('defaults') if isinstance(payload, dict) else None
    value = defaults.get(name) if isinstance(defaults, dict) else None
    normalized = str(value or '').strip()
    if normalized:
        return normalized
    if required:
        raise ValueError(f'install_defaults 缺少必填真源：{name}')
    return fallback


def host_state_root_default(root_dir: Path = ROOT_DIR) -> str:
    """读取宿主机 state root 默认值。"""
    return host_install_default('host_state_root', required=True, root_dir=root_dir)


def host_state_root_path(root_dir: Path = ROOT_DIR) -> Path:
    """读取宿主机 state root 默认路径。"""
    return Path(host_state_root_default(root_dir))
