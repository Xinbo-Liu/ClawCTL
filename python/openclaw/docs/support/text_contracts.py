#!/usr/bin/env python3
"""Object-reference support for documentation text contracts."""
from __future__ import annotations

from typing import Any

from openclaw.doctor.release.repo_release_gate_support import ordered_check_specs
from openclaw.lib.repo.contracts import repo_contract_relpath


class TextContractError(ValueError):
    """Raised when a documentation text contract is malformed."""


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _render(spec: dict[str, Any], value: str) -> str:
    template = str(spec.get('render') or '').strip()
    return template.format(value=value) if template else value


def _release_gate_command(check_id: str) -> str:
    for spec in ordered_check_specs():
        if spec.check_id == check_id:
            return spec.command_text
    raise TextContractError(f'未知 release gate check id：{check_id}')


def _runtime_path_tokens(spec: dict[str, Any], resolver: Any | None) -> list[str]:
    if resolver is None:
        raise TextContractError('runtime_path ref 需要 resolver')
    entry_id = str(spec.get('id') or spec.get('pathId') or '').strip()
    if not entry_id:
        raise TextContractError('runtime_path ref 缺少 id')
    entry = resolver.resolve_entry(entry_id)
    paths = entry.get('paths') if isinstance(entry, dict) else None
    if not isinstance(paths, dict):
        raise TextContractError(f'runtime_path ref 无 paths：{entry_id}')
    return [str(value) for value in paths.values() if str(value).strip()]


def resolve_text_ref(spec: dict[str, Any], *, resolver: Any | None = None) -> list[str]:
    if not isinstance(spec, dict):
        raise TextContractError('text ref 项必须为对象')
    kind = str(spec.get('kind') or spec.get('type') or '').strip()
    if not kind:
        raise TextContractError('text ref 缺少 kind')
    if kind == 'literal':
        tokens = _string_list(spec.get('value') if 'value' in spec else spec.get('values'))
    elif kind == 'repo_contract':
        contract_id = str(spec.get('id') or '').strip()
        if not contract_id:
            raise TextContractError('repo_contract ref 缺少 id')
        tokens = [repo_contract_relpath(contract_id)]
    elif kind == 'release_gate_check':
        check_id = str(spec.get('id') or spec.get('checkId') or '').strip()
        if not check_id:
            raise TextContractError('release_gate_check ref 缺少 id')
        tokens = [_release_gate_command(check_id)]
    elif kind == 'script':
        path = str(spec.get('path') or '').strip().replace('\\', '/')
        if not path:
            raise TextContractError('script ref 缺少 path')
        tokens = [f'bash ./{path.lstrip("./")}']
    elif kind == 'doc_page':
        path = str(spec.get('path') or '').strip().replace('\\', '/')
        if not path:
            raise TextContractError('doc_page ref 缺少 path')
        tokens = [path]
    elif kind == 'runtime_path':
        tokens = _runtime_path_tokens(spec, resolver)
    else:
        raise TextContractError(f'未知 text ref kind：{kind}')
    return [_render(spec, token) for token in tokens if token]


def resolve_text_refs(raw_refs: Any, *, resolver: Any | None = None) -> list[str]:
    if raw_refs is None:
        return []
    if not isinstance(raw_refs, list):
        raise TextContractError('text refs 必须为数组')
    tokens: list[str] = []
    for spec in raw_refs:
        tokens.extend(resolve_text_ref(spec, resolver=resolver))
    return tokens


def contract_required_refs(contract: dict[str, Any]) -> list[Any]:
    refs: list[Any] = []
    refs.extend(contract.get('requiredRefs') or [])
    return refs


def contract_forbidden_refs(contract: dict[str, Any]) -> list[Any]:
    refs: list[Any] = []
    refs.extend(contract.get('forbiddenRefs') or [])
    return refs


def check_text_contract(
    *,
    rel_path: str,
    content: str,
    contract: dict[str, Any],
    missing_label: str,
    forbidden_label: str,
    resolver: Any | None = None,
) -> list[str]:
    errors: list[str] = []
    for token in resolve_text_refs(contract_required_refs(contract), resolver=resolver):
        if token not in content:
            errors.append(f'{rel_path} {missing_label}：{token}')
    for token in resolve_text_refs(contract_forbidden_refs(contract), resolver=resolver):
        if token in content:
            errors.append(f'{rel_path} {forbidden_label}：{token}')
    return errors
