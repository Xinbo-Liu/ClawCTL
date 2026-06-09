"""部署唯一顺序基线读取与校验。"""
from __future__ import annotations

import sys
from typing import Any, NoReturn

from openclaw.lib.repo.static_truth import read_repo_contract_json, repo_contract_relpath


def fail(message: str, code: int = 2) -> NoReturn:
    sys.stderr.write(f'[deployment_baseline_surface][FAIL] {message}\n')
    raise SystemExit(code)


def load_baseline() -> dict[str, Any]:
    payload = read_repo_contract_json('governance.default_deployment_flow')
    if not isinstance(payload, dict):
        fail(f'{repo_contract_relpath("governance.default_deployment_flow")} 顶层必须为对象')
    return payload


def generated_artifacts(baseline: dict[str, Any] | None = None) -> dict[str, str | list[str]]:
    payload = load_baseline() if baseline is None else baseline
    raw = payload.get('generated_artifacts') or {}
    if not isinstance(raw, dict):
        fail('generated_artifacts 必须为对象')
    return raw


def setup_entrypoint_doc(baseline: dict[str, Any] | None = None) -> str:
    value = str(generated_artifacts(baseline).get('setup_entrypoint_doc') or '').strip()
    if not value:
        fail('generated_artifacts.setup_entrypoint_doc 不能为空')
    return value


def deployment_doc(baseline: dict[str, Any] | None = None) -> str:
    value = str(generated_artifacts(baseline).get('deployment_doc') or '').strip()
    if not value:
        fail('generated_artifacts.deployment_doc 不能为空')
    return value


def quickstart_docs(baseline: dict[str, Any] | None = None) -> list[str]:
    raw = generated_artifacts(baseline).get('quickstart_docs') or []
    docs = [str(item).strip() for item in list(raw) if str(item).strip()]
    if not docs:
        fail('generated_artifacts.quickstart_docs 不能为空')
    return docs




def prerequisites(baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = load_baseline() if baseline is None else baseline
    raw = payload.get('prerequisites') or {}
    if not isinstance(raw, dict):
        fail('prerequisites 必须为对象')
    return raw


def prerequisite_steps(baseline: dict[str, Any] | None = None) -> list[dict[str, str]]:
    raw_steps = list(prerequisites(baseline).get('steps') or [])
    seen: set[str] = set()
    cleaned: list[dict[str, str]] = []
    for item in raw_steps:
        if not isinstance(item, dict):
            continue
        entry_id = str(item.get('entry_id') or '').strip()
        command = str(item.get('command') or '').strip()
        offline_command = str(item.get('offline_command') or command).strip()
        purpose = str(item.get('purpose') or '').strip()
        if not entry_id or not command:
            fail('prerequisites.steps 存在空 entry_id/command')
        if entry_id in seen:
            fail(f'prerequisites.steps entry_id 重复：{entry_id}')
        seen.add(entry_id)
        cleaned.append({
            'entry_id': entry_id,
            'command': command,
            'offline_command': offline_command,
            'purpose': purpose,
        })
    return cleaned


def prerequisite_title(baseline: dict[str, Any] | None = None) -> str:
    return str(prerequisites(baseline).get('title') or '宿主机前置链').strip()


def prerequisite_notes(baseline: dict[str, Any] | None = None) -> list[str]:
    return [str(item).strip() for item in list(prerequisites(baseline).get('notes') or []) if str(item).strip()]

def default_flow(baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = load_baseline() if baseline is None else baseline
    flow = payload.get('default_flow') or {}
    if not isinstance(flow, dict):
        fail('default_flow 必须为对象')
    return flow


def default_flow_steps(baseline: dict[str, Any] | None = None) -> list[dict[str, str]]:
    flow = default_flow(baseline)
    raw_steps = list(flow.get('steps') or [])
    seen: set[str] = set()
    cleaned: list[dict[str, str]] = []
    for item in raw_steps:
        if not isinstance(item, dict):
            continue
        entry_id = str(item.get('entry_id') or '').strip()
        command = str(item.get('command') or '').strip()
        offline_command = str(item.get('offline_command') or command).strip()
        purpose = str(item.get('purpose') or '').strip()
        if not entry_id or not command:
            fail('default_flow.steps 存在空 entry_id/command')
        if entry_id in seen:
            fail(f'default_flow.steps entry_id 重复：{entry_id}')
        seen.add(entry_id)
        cleaned.append({
            'entry_id': entry_id,
            'command': command,
            'offline_command': offline_command,
            'purpose': purpose,
        })
    if not cleaned:
        fail('default_flow.steps 不能为空')
    return cleaned


def default_flow_notes(baseline: dict[str, Any] | None = None) -> list[str]:
    flow = default_flow(baseline)
    return [str(item).strip() for item in list(flow.get('notes') or []) if str(item).strip()]


def default_flow_title(baseline: dict[str, Any] | None = None) -> str:
    flow = default_flow(baseline)
    return str(flow.get('title') or '默认 one_click 主链').strip()


def default_commands(baseline: dict[str, Any] | None = None, mode: str = 'default') -> list[str]:
    steps = default_flow_steps(baseline)
    key = 'offline_command' if mode == 'offline' else 'command'
    return [step[key] for step in steps]


def entry_ids(baseline: dict[str, Any] | None = None) -> list[str]:
    return [step['entry_id'] for step in default_flow_steps(baseline)]


def next_entry_id(current_entry_id: str, baseline: dict[str, Any] | None = None) -> str:
    ids = entry_ids(baseline)
    try:
        index = ids.index(current_entry_id)
    except ValueError:
        fail(f'默认主路径中不存在 entry_id：{current_entry_id}')
    next_index = index + 1
    return ids[next_index] if next_index < len(ids) else ''


def entry_relations(baseline: dict[str, Any] | None = None) -> dict[str, str]:
    payload = load_baseline() if baseline is None else baseline
    raw = payload.get('entry_relations') or {}
    if not isinstance(raw, dict):
        fail('entry_relations 必须为对象')
    return {str(key): str(value).strip() for key, value in raw.items() if str(value).strip()}


def entry_command(entry_id: str, baseline: dict[str, Any] | None = None, mode: str = 'default') -> str:
    key = 'offline_command' if mode == 'offline' else 'command'
    for step in default_flow_steps(baseline):
        if step['entry_id'] == entry_id:
            return step[key]
    fail(f'默认主路径中不存在 entry_id：{entry_id}')

def post_deploy_default_command(baseline: dict[str, Any] | None = None, mode: str = 'default') -> str:
    relations = entry_relations(baseline)
    direct_command = relations.get('post_deploy_default_command')
    if direct_command:
        return direct_command
    entry_id = relations.get('post_deploy_default_entry_id') or next_entry_id(relations.get('deploy_entry_id', 'one_click_deploy'), baseline)
    return entry_command(entry_id, baseline=baseline, mode=mode)


def deploy_flow(baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = load_baseline() if baseline is None else baseline
    raw = payload.get('deploy_flow') or {}
    if not isinstance(raw, dict):
        fail('deploy_flow 必须为对象')
    return raw


def deploy_stage_order(baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = deploy_flow(baseline).get('stage_order') or {}
    if not isinstance(raw, dict):
        fail('deploy_flow.stage_order 必须为对象')
    return raw
