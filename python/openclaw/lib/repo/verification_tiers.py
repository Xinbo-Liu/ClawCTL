#!/usr/bin/env python3
"""验证层级真源读取器，统一 release gate、维护图和测试说明的口径。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.lib.repo.contracts import repo_contract_root
from openclaw.lib.repo.install_defaults import read_repo_contract_json

ROOT_DIR = repo_contract_root()


def load_verification_tiers(root_dir: Path = ROOT_DIR) -> dict[str, Any]:
    """读取验证层级真源，并校验正式门禁与诊断补充的基本字段。"""
    payload = read_repo_contract_json('governance.verification_tiers', root_dir=root_dir)
    if not isinstance(payload, dict):
        raise ValueError('governance.verification_tiers 顶层必须为对象')
    if int(payload.get('schemaVersion') or 0) != 1:
        raise ValueError('governance.verification_tiers schemaVersion 必须为 1')
    tiers = payload.get('tiers')
    if not isinstance(tiers, list) or not tiers:
        raise ValueError('governance.verification_tiers 必须包含非空 tiers 数组')
    seen: set[str] = set()
    has_release_required = False
    has_diagnostic_only = False
    for index, tier in enumerate(tiers, start=1):
        if not isinstance(tier, dict):
            raise ValueError(f'verification tier {index} 必须为对象')
        tier_id = str(tier.get('id') or '').strip()
        if not tier_id:
            raise ValueError(f'verification tier {index} 缺少 id')
        if tier_id in seen:
            raise ValueError(f'duplicate verification tier id: {tier_id}')
        seen.add(tier_id)
        title = str(tier.get('title') or '').strip()
        description = str(tier.get('description') or '').strip()
        commands = tier.get('commands')
        if (
            not title
            or not description
            or not isinstance(commands, list)
            or not commands
            or any(not isinstance(command, str) or not command.strip() for command in commands)
        ):
            raise ValueError(f'verification tier {tier_id} 缺少 title、description 或 commands')
        release_required = tier.get('releaseRequired')
        diagnostic_only = tier.get('diagnosticOnly')
        if not isinstance(release_required, bool) or not isinstance(diagnostic_only, bool):
            raise ValueError(f'verification tier {tier_id} 必须显式声明 releaseRequired 与 diagnosticOnly 布尔值')
        if release_required == diagnostic_only:
            raise ValueError(f'verification tier {tier_id} 必须且只能属于正式门禁或诊断补充之一')
        has_release_required = has_release_required or release_required
        has_diagnostic_only = has_diagnostic_only or diagnostic_only
    if not has_release_required or not has_diagnostic_only:
        raise ValueError('governance.verification_tiers 必须同时声明正式门禁与诊断补充层级')
    return payload


def verification_tier_rows(root_dir: Path = ROOT_DIR) -> list[dict[str, Any]]:
    """返回验证层级行，供渲染器直接消费。"""
    payload = load_verification_tiers(root_dir)
    rows: list[dict[str, Any]] = []
    for tier in payload.get('tiers') or []:
        rows.append(
            {
                'id': str(tier.get('id') or '').strip(),
                'title': str(tier.get('title') or '').strip(),
                'status': str(tier.get('status') or '').strip(),
                'release_required': bool(tier.get('releaseRequired')),
                'diagnostic_only': bool(tier.get('diagnosticOnly')),
                'description': str(tier.get('description') or '').strip(),
                'commands': [str(command).strip() for command in list(tier.get('commands') or []) if str(command).strip()],
            }
        )
    return rows


def release_gate_usage_lines(root_dir: Path = ROOT_DIR) -> list[str]:
    """渲染 release gate 帮助文本中的验证层级说明。"""
    lines = ['  验证层级：']
    for row in verification_tier_rows(root_dir):
        marker = '正式门禁' if row['release_required'] else '诊断补充'
        lines.append(f"    - {row['title']}（{marker}）：{row['description']}")
    return lines
