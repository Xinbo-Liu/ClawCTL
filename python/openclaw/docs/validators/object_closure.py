#!/usr/bin/env python3
"""文档对象闭环检查器。"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from openclaw.control_plane.governance_surfaces import load_docs_registry, load_documentation_closure_rules
from openclaw.control_plane.surfaces import load_runtime_service_registry
from openclaw.lib.cli import CliError, FlagSpec, parse_typed_flag_args
from openclaw.docs.support.doc_targets import read_json_object
from openclaw.lib.cli.output import stderr_write, stdout_write
from openclaw.lib.repo.layout import (
    DEFAULT_RUNTIME_CONTROL_PLANE_SERVICE_CONFIG_REL_PATH,
    resolve_default_runtime_control_plane_service_config_path,
)
from openclaw.lib.repo.static_truth import repo_contract_path, repo_contract_relpath, repo_contract_root
from openclaw.lib.runtime.resolver_loader import PathResolverInstance, require_path_resolver

ROOT_DIR = repo_contract_root()
SERVICE_REGISTRY_REL = repo_contract_relpath('runtime.service_registry')
DOCS_REGISTRY_REL = repo_contract_relpath('governance.docs_registry')
DOCUMENTATION_CLOSURE_RULES_REL = repo_contract_relpath('governance.documentation_closure_rules')


def usage() -> str:
    return '\n'.join([
        '用法：',
        '  bash ./scripts/docs/check_documentation_object_closure.sh',
        '  bash ./scripts/docs/check_documentation_object_closure.sh --stdout',
        f'  bash ./scripts/docs/check_documentation_object_closure.sh --config-path {DEFAULT_RUNTIME_CONTROL_PLANE_SERVICE_CONFIG_REL_PATH}',
        '',
        '说明：',
        '  校验活动文档 surface 中的路径对象、runtime service 对象与正式命令入口是否仍与统一规格闭环。',
    ])


def load_rules(config_path: Path | None = None) -> dict[str, Any]:
    return load_documentation_closure_rules(config_path=config_path)


def load_service_registry(config_path: Path | None = None) -> dict[str, dict[str, str]]:
    payload = load_runtime_service_registry(
        repo_contract_path('runtime.service_registry'),
        config_path=config_path,
    )
    targets = payload.get('targets') if isinstance(payload, dict) else None
    if not isinstance(targets, list):
        raise SystemExit('[documentation_object_closure][FAIL] service_registry.targets 必须为数组')
    result: dict[str, dict[str, str]] = {}
    for item in targets:
        if not isinstance(item, dict):
            continue
        target = str(item.get('target') or '').strip()
        if not target:
            continue
        result[target] = {key: str(value or '').strip() for key, value in item.items()}
    return result


def load_documentation_runtime_resolver(config_path: Path | None = None) -> PathResolverInstance:
    resolved_config_path = config_path or resolve_default_runtime_control_plane_service_config_path(ROOT_DIR)
    return require_path_resolver(repo_root=ROOT_DIR, config_path=resolved_config_path)


def load_active_docs_paths(config_path: Path | None = None) -> set[str]:
    payload = load_docs_registry(repo_contract_path('governance.docs_registry'), config_path=config_path)
    pages = payload.get('pages') if isinstance(payload, dict) else None
    if not isinstance(pages, list):
        raise SystemExit('[documentation_object_closure][FAIL] docs registry.pages 必须为数组')
    result: set[str] = set()
    for item in pages:
        if not isinstance(item, dict):
            continue
        path = str(item.get('path') or '').strip()
        if path:
            result.add(path)
    return result


def _normalize_rel_path(config_rel_path: str) -> str:
    value = str(config_rel_path or '').strip().replace('\\', '/')
    while value.startswith('./'):
        value = value[2:]
    return value


def load_config_payload(config_rel_path: str, *, config_path: Path | None = None) -> dict[str, Any]:
    normalized = _normalize_rel_path(config_rel_path)
    if not normalized:
        raise SystemExit('[documentation_object_closure][FAIL] config_rel_path 不能为空')
    if normalized == DOCS_REGISTRY_REL:
        return load_docs_registry(repo_contract_path('governance.docs_registry'), config_path=config_path)
    if normalized == SERVICE_REGISTRY_REL:
        return load_runtime_service_registry(repo_contract_path('runtime.service_registry'), config_path=config_path)
    return read_json_object(ROOT_DIR / normalized, prefix='documentation_object_closure')


def require_key_path(spec: dict[str, Any], *, field_name: str) -> list[str]:
    key_path = spec.get(field_name)
    if not isinstance(key_path, list) or not key_path or not all(isinstance(item, str) and item.strip() for item in key_path):
        raise SystemExit(f'[documentation_object_closure][FAIL] {field_name} 必须为非空字符串数组')
    return [str(item).strip() for item in key_path]


def resolve_nested_value(current: Any, key_path: list[str], *, label: str) -> Any:
    walked: list[str] = []
    for segment in key_path:
        walked.append(segment)
        if isinstance(current, dict):
            current = current.get(segment)
            continue
        if isinstance(current, list):
            if not segment.isdigit():
                raise SystemExit(f'[documentation_object_closure][FAIL] {label} 缺少字段：{".".join(walked)}')
            index = int(segment)
            if index < 0 or index >= len(current):
                raise SystemExit(f'[documentation_object_closure][FAIL] {label} 越界：{".".join(walked)}')
            current = current[index]
            continue
        raise SystemExit(f'[documentation_object_closure][FAIL] {label} 缺少字段：{".".join(walked)}')
    return current


def apply_lookup(payload: Any, spec: dict[str, Any], *, label: str) -> Any:
    lookup_list_key_path = spec.get('lookup_list_key_path')
    if lookup_list_key_path is None:
        return payload
    if not isinstance(lookup_list_key_path, list) or not lookup_list_key_path or not all(isinstance(item, str) and item.strip() for item in lookup_list_key_path):
        raise SystemExit(f'[documentation_object_closure][FAIL] {label} 的 lookup_list_key_path 必须为非空字符串数组')
    match_key_path = spec.get('lookup_match_key_path')
    if not isinstance(match_key_path, list) or not match_key_path or not all(isinstance(item, str) and item.strip() for item in match_key_path):
        raise SystemExit(f'[documentation_object_closure][FAIL] {label} 的 lookup_match_key_path 必须为非空字符串数组')
    match_value = str(spec.get('lookup_match_value') or '').strip()
    if not match_value:
        raise SystemExit(f'[documentation_object_closure][FAIL] {label} 的 lookup_match_value 不能为空')
    items = resolve_nested_value(payload, [str(item).strip() for item in lookup_list_key_path], label=label)
    if not isinstance(items, list):
        raise SystemExit(f'[documentation_object_closure][FAIL] {label} 的 lookup_list_key_path 必须解析为数组')
    parsed_match_key_path = [str(item).strip() for item in match_key_path]
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        value = resolve_nested_value(item, parsed_match_key_path, label=f'{label}[{index}]')
        if str(value or '').strip() == match_value:
            return item
    raise SystemExit(f'[documentation_object_closure][FAIL] {label} 未找到 {".".join(parsed_match_key_path)}={match_value} 的对象')


def load_config_value(spec: dict[str, Any], *, section: str, config_path: Path | None = None) -> tuple[str, Any]:
    config_rel_path = str(spec.get('config_rel_path') or '').strip()
    if not config_rel_path:
        raise SystemExit(f'[documentation_object_closure][FAIL] {section}.config_rel_path 不能为空')
    key_path = require_key_path(spec, field_name='key_path')
    label = str(spec.get('label') or '.'.join(key_path)).strip() or '.'.join(key_path)
    payload = load_config_payload(config_rel_path, config_path=config_path)
    selected = apply_lookup(payload, spec, label=label)
    return label, resolve_nested_value(selected, key_path, label=label)


def resolve_command_ref(spec: dict[str, Any], *, config_path: Path | None = None) -> tuple[str, str]:
    label, value = load_config_value(spec, section='command_refs', config_path=config_path)
    token = str(value or '').strip()
    if not token:
        raise SystemExit(f'[documentation_object_closure][FAIL] {label} 不能为空')
    return label, token


def resolve_string_ref(spec: dict[str, Any], *, config_path: Path | None = None) -> tuple[str, str]:
    label, value = load_config_value(spec, section='string_refs', config_path=config_path)
    token = str(value or '').strip()
    if not token:
        raise SystemExit(f'[documentation_object_closure][FAIL] {label} 不能为空')
    return label, token


def resolve_string_list_ref(spec: dict[str, Any], *, config_path: Path | None = None) -> tuple[str, list[str]]:
    label, value = load_config_value(spec, section='string_list_refs', config_path=config_path)
    item_key_path = spec.get('item_key_path')
    parsed_item_key_path: list[str] | None = None
    if item_key_path is not None:
        if not isinstance(item_key_path, list) or not item_key_path or not all(isinstance(item, str) and item.strip() for item in item_key_path):
            raise SystemExit(f'[documentation_object_closure][FAIL] {label} 的 item_key_path 必须为非空字符串数组')
        parsed_item_key_path = [str(item).strip() for item in item_key_path]

    if isinstance(value, str):
        tokens = [value.strip()] if value.strip() else []
    elif isinstance(value, list):
        raw_items: list[str] = []
        if parsed_item_key_path is None:
            raw_items = [str(item or '').strip() for item in value if str(item or '').strip()]
        else:
            for index, item in enumerate(value):
                if not isinstance(item, dict):
                    raise SystemExit(f'[documentation_object_closure][FAIL] {label}[{index}] 必须为对象，才能配合 item_key_path 使用')
                extracted = resolve_nested_value(item, parsed_item_key_path, label=f'{label}[{index}]')
                token = str(extracted or '').strip()
                if token:
                    raw_items.append(token)
        tokens = raw_items
    else:
        raise SystemExit(f'[documentation_object_closure][FAIL] {label} 必须解析为字符串或字符串数组')
    if not tokens:
        raise SystemExit(f'[documentation_object_closure][FAIL] {label} 不能为空')
    return label, tokens


def normalized_token_candidates(token: str) -> list[str]:
    raw = str(token or '').strip()
    if not raw:
        return []
    candidates: list[str] = []

    def add(value: str) -> None:
        normalized = ' '.join(value.split())
        if normalized and normalized not in candidates:
            candidates.append(normalized)

    add(raw)
    stripped = raw.replace('${offlineFlag}', '').replace('${ offlineFlag }', '')
    add(stripped)
    add(re.sub(r'\s*\[[^\]]+\]', '', stripped))
    return candidates


def has_any_token(content: str, tokens: list[str]) -> bool:
    normalized_content = ' '.join(content.split())
    return any(token in normalized_content for token in tokens)


def placeholderize_host_path(value: str, resolver: PathResolverInstance) -> list[str]:
    host_root = str(resolver.roots.get('host_state_root') or '').rstrip('/')
    normalized = str(value or '').strip()
    if not host_root or not normalized:
        return [normalized] if normalized else []
    candidates: list[str] = [normalized]

    def add(token: str) -> None:
        if token and token not in candidates:
            candidates.append(token)

    if normalized == host_root:
        add('<current-host-state-root>')
        add('${HOST_STATE_ROOT:?HOST_STATE_ROOT_required}')
        return candidates
    prefix = host_root + '/'
    if normalized.startswith(prefix):
        suffix = normalized[len(prefix):]
        add('<current-host-state-root>/' + suffix)
        add('${HOST_STATE_ROOT:?HOST_STATE_ROOT_required}/' + suffix)
    return candidates


def resolve_runtime_path_ref(spec: dict[str, Any], resolver: PathResolverInstance) -> tuple[str, list[str]]:
    entry_id = str(spec.get('entry_id') or '').strip()
    view = str(spec.get('view') or 'host').strip()
    if not entry_id:
        raise SystemExit('[documentation_object_closure][FAIL] runtime_path_refs.entry_id 不能为空')
    if entry_id not in resolver.entries:
        return f'{entry_id}@{view}', []
    value = resolver.resolve_path(entry_id, view)
    candidates = [value]
    if view == 'host':
        for placeholderized in placeholderize_host_path(value, resolver):
            if placeholderized not in candidates:
                candidates.append(placeholderized)
    return f'{entry_id}@{view}', candidates


def resolve_service_target_tokens(spec: dict[str, Any], target_map: dict[str, dict[str, str]]) -> list[tuple[str, str]]:
    target = str(spec.get('target') or '').strip()
    if not target:
        raise SystemExit('[documentation_object_closure][FAIL] service_target_refs.target 不能为空')
    item = target_map.get(target)
    if item is None:
        return []
    fields = spec.get('fields') or ['target', 'service', 'container']
    if not isinstance(fields, list) or not fields:
        raise SystemExit('[documentation_object_closure][FAIL] service_target_refs.fields 必须为非空数组')
    tokens: list[tuple[str, str]] = []
    for field in fields:
        field_name = str(field or '').strip()
        if not field_name:
            raise SystemExit('[documentation_object_closure][FAIL] service_target_refs.fields 中存在空字段')
        value = str(item.get(field_name) or '').strip()
        if not value:
            raise SystemExit(f'[documentation_object_closure][FAIL] service_registry target={target} 缺少字段 {field_name}')
        tokens.append((f'{target}.{field_name}', value))
    return tokens


def check_entry(rule: dict[str, Any], resolver: PathResolverInstance, target_map: dict[str, dict[str, str]], *, config_path: Path | None = None) -> dict[str, Any]:
    rel_path = str(rule.get('path') or '').strip()
    if not rel_path:
        raise SystemExit('[documentation_object_closure][FAIL] entries.path 不能为空')
    file_path = ROOT_DIR / rel_path
    errors: list[str] = []
    checks: list[str] = []
    if not file_path.exists():
        return {'file_path': file_path, 'errors': [f'{rel_path} 不存在'], 'checks': checks}
    content = file_path.read_text(encoding='utf-8')

    for spec in rule.get('command_refs') or []:
        if not isinstance(spec, dict):
            raise SystemExit('[documentation_object_closure][FAIL] command_refs 项必须为对象')
        label, token = resolve_command_ref(spec, config_path=config_path)
        checks.append(f'command:{label}')
        candidates = normalized_token_candidates(token)
        if not has_any_token(content, candidates):
            errors.append(f"{rel_path} 缺少命令真源引用：{label} -> {' | '.join(candidates)}")

    for spec in rule.get('string_refs') or []:
        if not isinstance(spec, dict):
            raise SystemExit('[documentation_object_closure][FAIL] string_refs 项必须为对象')
        label, token = resolve_string_ref(spec, config_path=config_path)
        checks.append(f'string:{label}')
        candidates = normalized_token_candidates(token)
        if not has_any_token(content, candidates):
            errors.append(f"{rel_path} 缺少字符串真源引用：{label} -> {' | '.join(candidates)}")

    for spec in rule.get('string_list_refs') or []:
        if not isinstance(spec, dict):
            raise SystemExit('[documentation_object_closure][FAIL] string_list_refs 项必须为对象')
        label, tokens = resolve_string_list_ref(spec, config_path=config_path)
        for token in tokens:
            checks.append(f'string_list:{label}')
            candidates = normalized_token_candidates(token)
            if not has_any_token(content, candidates):
                errors.append(f"{rel_path} 缺少字符串列表真源引用：{label} -> {' | '.join(candidates)}")

    for spec in rule.get('runtime_path_refs') or []:
        if not isinstance(spec, dict):
            raise SystemExit('[documentation_object_closure][FAIL] runtime_path_refs 项必须为对象')
        label, candidates = resolve_runtime_path_ref(spec, resolver)
        if not candidates:
            continue
        checks.append(f'path:{label}')
        if not any(token in content for token in candidates):
            errors.append(f"{rel_path} 缺少路径对象引用：{label} -> {' | '.join(candidates)}")

    for spec in rule.get('service_target_refs') or []:
        if not isinstance(spec, dict):
            raise SystemExit('[documentation_object_closure][FAIL] service_target_refs 项必须为对象')
        for label, token in resolve_service_target_tokens(spec, target_map):
            checks.append(f'service:{label}')
            if token not in content:
                errors.append(f'{rel_path} 缺少 service 对象引用：{label} -> {token}')

    return {'file_path': file_path, 'errors': errors, 'checks': checks}


def _parse_args(argv: list[str]) -> tuple[bool, Path | None]:
    if any(arg in {'-h', '--help'} for arg in argv):
        stdout_write(f'{usage()}\n')
        raise SystemExit(0)
    try:
        values, _ = parse_typed_flag_args(
            argv,
            specs={
                'stdout': FlagSpec(kind='bool', default=False),
                'config-path': FlagSpec(kind='str', dest='config_path', default=None),
            },
            allow_positionals=False,
        )
    except CliError as exc:
        stderr_write(f'[check_documentation_object_closure][FAIL] {exc}\n')
        stderr_write(f'{usage()}\n')
        raise SystemExit(exc.exit_code) from exc
    config_path_value = values['config_path']
    config_path: Path | None = None
    if config_path_value is not None:
        candidate = Path(config_path_value)
        if not candidate.is_absolute():
            candidate = (ROOT_DIR / candidate).resolve()
        config_path = candidate
    return bool(values['stdout']), config_path


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        stdout, config_path = _parse_args(args)
    except SystemExit as exc:
        code = int(exc.code) if isinstance(exc.code, int) else 1
        return code

    resolved_config_path = config_path or resolve_default_runtime_control_plane_service_config_path(ROOT_DIR)

    try:
        payload = load_rules(config_path=resolved_config_path)
        entries = payload.get('entries')
        if not isinstance(entries, list):
            raise SystemExit('[documentation_object_closure][FAIL] entries 顶层必须为数组')
        active_docs_paths = load_active_docs_paths(resolved_config_path)
        filtered_entries = [
            item for item in entries
            if isinstance(item, dict) and str(item.get('path') or '').strip() in active_docs_paths
        ]
        resolver = load_documentation_runtime_resolver(resolved_config_path)
        target_map = load_service_registry(resolved_config_path)
        results = [check_entry(item, resolver, target_map, config_path=resolved_config_path) for item in filtered_entries]
    except SystemExit as exc:
        stderr_write(f'{exc}\n')
        return 1
    except Exception as exc:
        stderr_write(f'[check_documentation_object_closure][FAIL] {exc}\n')
        return 1

    errors = [error for item in results for error in item['errors']]
    if stdout:
        stdout_write(
            f'[check_documentation_object_closure] config={DOCUMENTATION_CLOSURE_RULES_REL} '
            f'profile={resolved_config_path.relative_to(ROOT_DIR)} count={len(results)}\n'
        )
        for item in results:
            rel = item['file_path'].relative_to(ROOT_DIR)
            stdout_write(f'- {rel} checks={len(item["checks"])} errors={len(item["errors"])}\n')
    if errors:
        stderr_write('[check_documentation_object_closure] 文档对象闭环校验失败：\n')
        for error in errors:
            stderr_write(f'- {error}\n')
        return 1
    stdout_write('[check_documentation_object_closure] 已通过\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
