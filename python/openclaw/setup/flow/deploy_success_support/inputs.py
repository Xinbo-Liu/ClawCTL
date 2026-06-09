#!/usr/bin/env python3
"""Input loading helpers for deploy_success summary surface."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from openclaw.lib.repo.static_truth import governance_default_path, read_repo_contract_json


def read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def parse_env_file(path: Path, *, fail: Callable[[str, int], None]) -> dict[str, str]:
    if not path.exists():
        fail(f'deploy env 不存在：{path}', 3)
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        value = value.strip()
        if len(value) >= 2 and ((value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'"))):
            value = value[1:-1]
        result[key.strip()] = value
    return result


def summary_manifest_profile(profile_id: str, *, fail: Callable[[str, int], None]) -> dict[str, Any]:
    payload = read_repo_contract_json('governance.summary_manifest')
    if not isinstance(payload, dict):
        fail('summary_manifest 顶层必须为对象', 2)
    return dict((payload.get('profiles') or {}).get(profile_id) or {})


def default_path(key: str, *, root_dir: Path, profile_id: str = 'one_click_deploy') -> Path:
    return root_dir / governance_default_path(key, profile_id=profile_id, root_dir=root_dir)
