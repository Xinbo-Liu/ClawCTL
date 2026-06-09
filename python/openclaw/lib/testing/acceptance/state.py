from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, NoReturn

from openclaw.control_plane.surfaces import load_testing_manifest
from openclaw.lib.repo.static_truth import repo_contract_path, repo_contract_root


ROOT_DIR = repo_contract_root()
TESTING_MANIFEST_PATH = repo_contract_path('runtime.testing_manifest')
ACCEPTANCE_SURFACE_PATH = repo_contract_path('governance.acceptance_surface')


def read_manifest(path: Path = TESTING_MANIFEST_PATH) -> dict[str, Any]:
    payload = load_testing_manifest(path)
    acceptance = dict(payload.get('acceptance_reference') or {})
    acceptance['required_checks'] = list(acceptance.get('required_checks') or [])
    acceptance['required_run_ledger_jobs'] = list(acceptance.get('required_run_ledger_jobs') or [])
    return acceptance


def fail(message: str, exit_code: int = 2) -> NoReturn:
    sys.stderr.write(f'[acceptance_surface_control_plane][FAIL] {message}\n')
    raise SystemExit(exit_code)


def read_surface(path: Path = ACCEPTANCE_SURFACE_PATH) -> dict[str, Any]:
    payload = read_json(path)
    if not isinstance(payload, dict):
        fail('acceptance_surface.json 顶层必须为对象')
    payload['usage_commands'] = list(payload.get('usage_commands') or [])
    payload['intro'] = list(payload.get('intro') or [])
    payload['boundary'] = list(payload.get('boundary') or [])
    payload['failure_references'] = list(payload.get('failure_references') or [])
    return payload


def resolve_path(rel_path: str, base_root: Path = ROOT_DIR) -> Path:
    return base_root / rel_path


def read_json(file_path: Path) -> dict[str, Any]:
    return json.loads(file_path.read_text(encoding='utf-8'))


def safe_read_json(file_path: Path) -> dict[str, Any] | None:
    if not file_path.exists():
        return None
    return read_json(file_path)


def default_models_probe_summary(*, reason: str, source_path: Path | None = None) -> dict[str, Any]:
    payload = {
        'ok': True,
        'enabled': False,
        'skipped': True,
        'reason': reason,
    }
    if source_path is not None:
        payload['source_path'] = str(source_path)
    return payload


def write_json(out_path: Path, payload: dict[str, Any]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def parse_required_checks(csv: str) -> list[dict[str, str]]:
    if not csv:
        return []
    items: list[dict[str, str]] = []
    for part in csv.split(','):
        if not part:
            continue
        if '=' not in part:
            fail(f'required check 需要 id=status：{part}')
        check_id, status = part.split('=', 1)
        if not check_id or not status:
            fail(f'required check 需要 id=status：{part}')
        items.append({'id': check_id, 'status': status})
    return items


def write_deployment_acceptance_state(
    *,
    out: str | Path,
    generated_at: str,
    suite: str,
    env_file: str,
    eligible: bool,
    accepted: bool,
    required_checks: str,
) -> None:
    payload = {
        'schema_version': 1,
        'generated_at': generated_at,
        'suite': suite,
        'env_file': env_file,
        'eligible': eligible,
        'accepted': accepted,
        'required_checks': parse_required_checks(required_checks),
    }
    write_json(Path(out), payload)


def parse_kv_args(argv: list[str]) -> dict[str, str]:
    opts: dict[str, str] = {}
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in {'-h', '--help'}:
            opts[arg] = '1'
            index += 1
            continue
        if not arg.startswith('--'):
            fail(f'未知参数：{arg}')
        if index + 1 >= len(argv):
            fail(f'{arg} 缺少参数值')
        opts[arg] = argv[index + 1]
        index += 2
    return opts


def parse_bool(raw: str, flag_name: str) -> bool:
    if raw == 'true':
        return True
    if raw == 'false':
        return False
    fail(f'{flag_name} 只接受 true/false：{raw}')
