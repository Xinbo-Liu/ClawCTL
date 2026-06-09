#!/usr/bin/env python3
"""Shared helpers for summary-oriented control-plane surfaces."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.static_truth import read_repo_contract_json


ROOT_DIR = resolve_repo_root(Path(__file__))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def relative_or_self(file_path: object, *, root_dir: Path = ROOT_DIR) -> str:
    if not file_path:
        return ''
    path = Path(str(file_path).strip())
    try:
        return str(path.resolve().relative_to(root_dir))
    except (OSError, RuntimeError, ValueError):
        return str(path)


def read_json_if_exists(file_path: str | Path) -> Any | None:
    path = Path(file_path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(file_path: str | Path, payload: Any) -> None:
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def write_text(file_path: str | Path, text: str) -> None:
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith('\n') else f'{text}\n', encoding='utf-8')


def summary_output_profile(profile_id: str) -> dict[str, Any]:
    payload = read_repo_contract_json('governance.summary_output_surface')
    if not isinstance(payload, dict):
        raise ValueError('summary_output_surface 顶层必须为对象')
    return dict((payload.get('profiles') or {}).get(profile_id) or {})


def write_summary_outputs(
    *,
    summary: dict[str, Any],
    markdown: str,
    out_json: str | Path,
    out_md: str | Path,
    latest_json: str | Path,
    latest_md: str | Path,
) -> None:
    write_json(out_json, summary)
    write_text(out_md, markdown)
    write_json(latest_json, summary)
    write_text(latest_md, markdown)
