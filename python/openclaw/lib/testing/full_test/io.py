#!/usr/bin/env python3
"""IO helpers for the full-test surface control plane."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, NoReturn

from openclaw.control_plane.surfaces import load_testing_manifest
from openclaw.docs.support.doc_targets import resolve_target_from_config
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.static_truth import (
    governance_default_path,
    repo_contract_path,
    repo_contract_relpath,
    read_repo_contract_json,
)
from openclaw.lib.summary.io import (
    read_json_if_exists,
    summary_output_profile as load_summary_output_profile,
    write_json as write_summary_json,
    write_text as write_summary_text,
)


ROOT_DIR = resolve_repo_root(Path(__file__))
MANIFEST_PATH = repo_contract_path('runtime.testing_manifest')
SURFACE_PATH = repo_contract_path('governance.full_test_surface')
SUMMARY_MANIFEST_PATH = repo_contract_path('governance.summary_manifest')
SUMMARY_OUTPUT_SURFACE_PATH = repo_contract_path('governance.summary_output_surface')


def fail(message: str, exit_code: int = 2) -> NoReturn:
    sys.stderr.write(f'[full_test_surface_control_plane][FAIL] {message}\n')
    raise SystemExit(exit_code)


def read_manifest() -> dict[str, Any]:
    return load_testing_manifest(MANIFEST_PATH)


def read_surface() -> dict[str, Any]:
    payload = read_repo_contract_json('governance.full_test_surface')
    if not isinstance(payload, dict):
        fail('full_test_surface 顶层必须为对象')
    return payload


def read_summary_manifest() -> dict[str, Any]:
    payload = read_repo_contract_json('governance.summary_manifest')
    if not isinstance(payload, dict):
        fail('summary_manifest 顶层必须为对象')
    return payload


def read_summary_output_surface() -> dict[str, Any]:
    payload = read_repo_contract_json('governance.summary_output_surface')
    if not isinstance(payload, dict):
        fail('summary_output_surface 顶层必须为对象')
    return payload


def summary_output_profile(profile_id: str) -> dict[str, Any]:
    return load_summary_output_profile(profile_id)


def generated_doc_path(surface: dict[str, Any] | None = None) -> Path:
    if surface is not None:
        generated = surface.get('generated_artifacts') or {}
        rel = str(generated.get('full_test_doc') or '').strip()
        if not rel:
            fail('full_test_surface.json 缺少 generated_artifacts.full_test_doc')
        return ROOT_DIR / rel
    target, _ = resolve_target_from_config(
        repo_contract_relpath('governance.full_test_surface'),
        ['generated_artifacts', 'full_test_doc'],
        prefix='full_test_surface_control_plane',
        label='full_test_doc',
    )
    return target


def default_path(key: str, manifest: dict[str, Any] | None = None) -> Path:
    payload = read_manifest() if manifest is None else manifest
    if key in {'latest_json', 'latest_markdown'}:
        rel = governance_default_path(key, profile_id='one_click_test_full', root_dir=ROOT_DIR)
        if rel:
            return ROOT_DIR / rel
    return ROOT_DIR / str(payload['paths'][key])


def read_json(file_path: str | Path) -> Any:
    return json.loads(Path(file_path).read_text(encoding='utf-8'))


def safe_read_json(file_path: str | Path) -> Any | None:
    return read_json_if_exists(file_path)


def write_json(file_path: str | Path, payload: Any) -> None:
    write_summary_json(file_path, payload)


def write_text(file_path: str | Path, text: str) -> None:
    write_summary_text(file_path, text)
