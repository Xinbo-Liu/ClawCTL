from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from openclaw.docs.support.doc_targets import read_json_object, require_nested_str
from openclaw.lib.repo.static_truth import repo_contract_path, repo_contract_root


ROOT_DIR = repo_contract_root()
SECTIONS_PATH = repo_contract_path('governance.getting_started_sections')


def fail(message: str, code: int = 2) -> 'NoReturn':
    sys.stderr.write(f'[getting_started_reference][FAIL] {message}\n')
    raise SystemExit(code)


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        fail(f'{path.relative_to(ROOT_DIR)} 顶层必须为对象')
    return payload


def load_surface() -> dict[str, Any]:
    payload = read_json_object(repo_contract_path('governance.getting_started_surface'), prefix='getting_started_reference')
    require_nested_str(payload, ['generated_artifacts', 'quickstart_doc'], prefix='getting_started_reference', label='quickstart_doc')
    require_nested_str(payload, ['generated_artifacts', 'environment_setup_doc'], prefix='getting_started_reference', label='environment_setup_doc')
    return payload


def load_sections() -> dict[str, Any]:
    payload = read_json(SECTIONS_PATH)
    if not isinstance(payload.get('quickstart'), dict):
        fail(f'{SECTIONS_PATH.relative_to(ROOT_DIR)} -> quickstart 顶层必须为对象')
    if not isinstance(payload.get('environment_setup'), dict):
        fail(f'{SECTIONS_PATH.relative_to(ROOT_DIR)} -> environment_setup 顶层必须为对象')
    return payload


def sorted_fields(schema: dict[str, Any]) -> list[dict[str, Any]]:
    groups = {str(group.get('id')): group for group in list(schema.get('groups') or [])}
    return sorted(
        [field for field in list(schema.get('fields') or []) if isinstance(field, dict)],
        key=lambda field: (
            int((groups.get(str(field.get('group'))) or {}).get('doc_order') or 999),
            str(field.get('key') or ''),
        ),
    )


def required_manual_fields(schema: dict[str, Any]) -> list[dict[str, Any]]:
    return [field for field in sorted_fields(schema) if field.get('required') and field.get('manual_required') is True]


def required_manual_field_lines(schema: dict[str, Any]) -> list[str]:
    rows: list[str] = []
    for field in required_manual_fields(schema):
        key = str(field.get('key') or '').strip()
        summary = str(field.get('doc_summary') or '').strip()
        if summary:
            rows.append(f'`{key}`：{summary}')
        else:
            rows.append(f'`{key}`')
    return rows


def ingress_manual_fields(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for field in required_manual_fields(schema):
        if str(field.get('group') or '') == 'ingress':
            rows[str(field.get('key') or '')] = field
    return rows


def conditional_manual_field_lines(schema: dict[str, Any]) -> list[str]:
    rows: list[str] = []
    groups = {str(group.get('id')): group for group in list(schema.get('groups') or [])}
    fields = sorted(
        [field for field in list(schema.get('fields') or []) if isinstance(field, dict)],
        key=lambda field: (
            int((groups.get(str(field.get('group'))) or {}).get('doc_order') or 999),
            str(field.get('key') or ''),
        ),
    )
    for field in fields:
        conditional = dict(field.get('conditional_required') or {})
        if not conditional or field.get('manual_required') is not True:
            continue
        key = str(field.get('key') or '').strip()
        summary = str(field.get('doc_summary') or '').strip()
        if summary:
            rows.append(f'`{key}`：{summary}')
        else:
            rows.append(f'`{key}`')
    return rows
