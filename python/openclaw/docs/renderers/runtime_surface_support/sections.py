#!/usr/bin/env python3
"""Derived section builders for runtime_surface renderer."""
from __future__ import annotations

from typing import Any


def format_target_list(manifest: dict[str, Any]) -> str:
    targets = [str(item.get('target') or '').strip() for item in (manifest.get('targets') or []) if isinstance(item, dict) and str(item.get('target') or '').strip()]
    if not targets:
        return '`gateway / ingress / internal-api / scheduler`'
    return '`' + ' / '.join(targets) + '`'


def append_steps(lines: list[str], steps: list[str]) -> None:
    lines.append('```bash')
    for step in steps:
        lines.append(step)
    lines.append('```')


def runtime_source_rows(manifest: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    runtime_contract = dict(manifest.get('runtime_contract') or {})
    source_strategy = dict(manifest.get('source_strategy') or {})
    gateway_repos = dict(((runtime_contract.get('upstream_release') or {}).get('image_repositories') or {}))
    images = dict(source_strategy.get('images') or {})
    rows: list[tuple[str, str, str, str]] = []

    gateway_selected = dict((images.get('official_gateway') or {}).get('selected_runtime_source') or {})
    rows.append((
        'official_gateway',
        str(gateway_repos.get('official_release_image_repo') or gateway_repos.get('default_official_gateway_image_repo') or '').strip(),
        str(gateway_selected.get('ref_env') or '').strip(),
        str(gateway_selected.get('pin_file') or '').strip(),
    ))
    for key in ('control_plane_python', 'runtime_python', 'nginx_runtime'):
        image = dict(images.get(key) or {})
        canonical = dict(image.get('canonical_source') or {})
        selected = dict(image.get('selected_runtime_source') or {})
        rows.append((
            key,
            str(canonical.get('repo') or '').strip(),
            str(selected.get('ref_env') or '').strip(),
            str(selected.get('pin_file') or '').strip(),
        ))
    return rows


def runtime_contract_reference_lines(manifest: dict[str, Any]) -> list[str]:
    runtime_contract = dict(manifest.get('runtime_contract') or {})
    source_strategy = dict(manifest.get('source_strategy') or {})
    release_discovery = dict((runtime_contract.get('upstream_release') or {}).get('release_discovery') or {})
    image_repositories = dict((runtime_contract.get('upstream_release') or {}).get('image_repositories') or {})
    runtime_python = dict((source_strategy.get('images') or {}).get('runtime_python') or {})
    nginx_runtime = dict((source_strategy.get('images') or {}).get('nginx_runtime') or {})
    runtime_python_acceleration = dict((runtime_python.get('acceleration_sources') or [{}])[0] or {})
    nginx_runtime_acceleration = dict((nginx_runtime.get('acceleration_sources') or [{}])[0] or {})
    allowed_candidate_image_repos = [
        str(repo).strip()
        for repo in list(image_repositories.get('allowed_candidate_image_repos') or [])
        if str(repo).strip()
    ]
    governance_rules = [
        str(rule).strip()
        for rule in list(source_strategy.get('governance_rules') or [])
        if str(rule).strip()
    ]
    candidate_repos_text = '、'.join(f'`{repo}`' for repo in allowed_candidate_image_repos) or '未声明'
    lines = [
        '### runtime contract 固定事实',
        '',
        f"- GitHub latest release API：`{str(release_discovery.get('github_latest_release_api') or '').strip()}`",
        f"- official release image repo：`{str(image_repositories.get('official_release_image_repo') or '').strip()}`",
        f"- default official gateway image repo：`{str(image_repositories.get('default_official_gateway_image_repo') or '').strip()}`",
        f'- 允许的 Gateway candidate repos：{candidate_repos_text}',
        '',
        '### acceleration source',
        '',
        f"- runtime Python acceleration repo：`{str(runtime_python_acceleration.get('repo') or '').strip()}`",
        f"- Nginx acceleration repo：`{str(nginx_runtime_acceleration.get('repo') or '').strip()}`",
        '',
        '### source strategy 治理规则',
        '',
        *[f'{index}. {rule}' for index, rule in enumerate(governance_rules, start=1)],
        '',
    ]
    return lines
