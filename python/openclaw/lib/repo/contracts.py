#!/usr/bin/env python3
"""Shared repo-contract truth loader for Python and shell consumers."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath

from openclaw.lib.repo.layout import resolve_repo_root


CONTRACTS_TRUTH_REL_PATH = Path('config/governance/support/repo_contracts.json')
ALLOWED_CONTRACT_FORMATS = {'json', 'env', 'text'}
REQUIRED_CONTRACT_KEYS = frozenset({'id', 'relative_path', 'format'})
ROOT_DIR = resolve_repo_root(Path(__file__))
UTF8_BOM = '\ufeff'


@dataclass(frozen=True)
class RepoContract:
    id: str
    relative_path: str
    format: str


def _contract(contract_id: str, relative_path: str, format_name: str) -> RepoContract:
    normalized = str(relative_path or '').strip().replace('\\', '/')
    if not normalized:
        raise ValueError(f'repo contract {contract_id} 缺少 relative_path')
    if format_name not in ALLOWED_CONTRACT_FORMATS:
        raise ValueError(f'repo contract {contract_id} 使用了不支持的 format：{format_name}')
    pure_path = PurePosixPath(normalized)
    if pure_path.is_absolute() or '..' in pure_path.parts:
        raise ValueError(f'repo contract {contract_id} relative_path 非法：{relative_path}')
    return RepoContract(id=contract_id, relative_path=normalized, format=format_name)


def _contracts_truth_path(root_dir: Path) -> Path:
    return (Path(root_dir).resolve() / CONTRACTS_TRUTH_REL_PATH).resolve()


def _contracts_truth_text(truth_path: Path) -> str:
    lines: list[str] = []
    for line_number, raw_line in enumerate(truth_path.read_text(encoding='utf-8').splitlines(), start=1):
        line = raw_line.lstrip(UTF8_BOM) if line_number == 1 else raw_line
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        lines.append(line)
    if not lines:
        raise ValueError(f'repo contracts truth is empty: {truth_path}')
    return '\n'.join(lines)


@lru_cache(maxsize=None)
def _load_repo_contracts(root_dir_text: str) -> dict[str, RepoContract]:
    root_dir = Path(root_dir_text).resolve()
    truth_path = _contracts_truth_path(root_dir)
    if not truth_path.is_file():
        raise ValueError(f'repo contracts truth is missing: {truth_path}')

    try:
        payload = json.loads(_contracts_truth_text(truth_path))
    except json.JSONDecodeError as exc:
        raise ValueError(f'repo contracts truth JSON 无法解析: {truth_path} ({exc})') from exc

    if not isinstance(payload, dict):
        raise ValueError(f'repo contracts truth 顶层必须为对象: {truth_path}')
    raw_contracts = payload.get('contracts')
    if not isinstance(raw_contracts, list):
        raise ValueError(f'repo contracts truth 缺少 contracts 数组: {truth_path}')
    if not raw_contracts:
        raise ValueError(f'repo contracts truth is empty: {truth_path}')

    contracts: dict[str, RepoContract] = {}
    for entry_index, entry in enumerate(raw_contracts, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f'repo contracts truth entry {entry_index} 必须为对象')
        entry_keys = set(entry)
        if entry_keys != REQUIRED_CONTRACT_KEYS:
            raise ValueError(f'repo contracts truth entry {entry_index} 字段集合非法: {sorted(entry_keys)}')
        contract = _contract(
            str(entry.get('id') or '').strip(),
            str(entry.get('relative_path') or '').strip(),
            str(entry.get('format') or '').strip(),
        )
        if contract.id in contracts:
            raise ValueError(f'duplicate repo contract id: {contract.id}')
        contracts[contract.id] = contract
    return contracts


def repo_contracts(root_dir: Path = ROOT_DIR) -> dict[str, RepoContract]:
    return dict(_load_repo_contracts(str(Path(root_dir).resolve())))


REPO_CONTRACTS = repo_contracts(ROOT_DIR)


def repo_contract_root(root_dir: Path = ROOT_DIR) -> Path:
    return Path(root_dir).resolve()


def repo_contract(contract_id: str, *, root_dir: Path = ROOT_DIR) -> RepoContract:
    try:
        return _load_repo_contracts(str(Path(root_dir).resolve()))[contract_id]
    except KeyError as exc:
        raise KeyError(f'未知 repo contract id：{contract_id}') from exc


def repo_contract_ids(root_dir: Path = ROOT_DIR) -> tuple[str, ...]:
    return tuple(_load_repo_contracts(str(Path(root_dir).resolve())))


def repo_contract_relpath(contract_id: str, *, root_dir: Path = ROOT_DIR) -> str:
    return repo_contract(contract_id, root_dir=root_dir).relative_path


def repo_contract_path(contract_id: str, *, root_dir: Path = ROOT_DIR) -> Path:
    contract = repo_contract(contract_id, root_dir=root_dir)
    resolved_root = Path(root_dir).resolve()
    resolved_path = (resolved_root / contract.relative_path).resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f'repo contract {contract_id} 解析出了仓库根之外的路径：{resolved_path}') from exc
    return resolved_path
