#!/usr/bin/env python3
"""Script-catalog helpers for reference_specs surfaces."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from openclaw.docs.support.reference_specs_support.io import (
    AUTO_NOTICE,
    ROOT_DIR,
    SURFACE_LABELS,
    SURFACE_LEVELS,
    SURFACE_TITLES,
    load_specs,
    normalize_spacing,
    read_json,
)
from openclaw.lib.repo.static_truth import repo_contract_path


def script_groups(*, load_specs_fn: Callable[[str], Any] = load_specs) -> list[dict[str, Any]]:
    data = load_specs_fn('script_groups.json')
    if not isinstance(data, list):
        raise ValueError('script_groups.json 顶层必须为数组')
    return data


def get_script_group(
    group_id: str,
    *,
    script_groups_fn: Callable[[], list[dict[str, Any]]] = script_groups,
) -> dict[str, Any] | None:
    return next((group for group in script_groups_fn() if group.get('id') == group_id), None)


def get_all_script_entries(
    *,
    script_groups_fn: Callable[[], list[dict[str, Any]]] = script_groups,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in script_groups_fn():
        for file in group.get('files') or []:
            rows.append(
                {
                    'groupId': group['id'],
                    'name': file['name'],
                    'path': f"scripts/{group['id']}/{file['name']}",
                    'summary': file['summary'],
                    'visibility': str(file.get('visibility') or '').strip(),
                }
            )
    return rows


def get_script_entry_map(
    *,
    get_all_script_entries_fn: Callable[[], list[dict[str, Any]]] = get_all_script_entries,
) -> dict[str, dict[str, Any]]:
    return {entry['path']: entry for entry in get_all_script_entries_fn()}


def get_surface_manifest(*, load_specs_fn: Callable[[str], Any] = load_specs) -> dict[str, Any]:
    data = load_specs_fn('script_surface_manifest.json')
    if not isinstance(data, dict):
        raise ValueError('script_catalog_surface.json visibility 派生面必须为对象')
    return data


def get_script_surface_map(
    *,
    get_surface_manifest_fn: Callable[[], dict[str, Any]] = get_surface_manifest,
) -> dict[str, str]:
    result: dict[str, str] = {}
    manifest = get_surface_manifest_fn()
    for level in SURFACE_LEVELS:
        for relative_path in manifest.get(level) or []:
            result[str(relative_path)] = level
    return result


def validate_script_surface_manifest(
    *,
    get_script_entry_map_fn: Callable[[], dict[str, dict[str, Any]]] = get_script_entry_map,
    get_surface_manifest_fn: Callable[[], dict[str, Any]] = get_surface_manifest,
) -> list[str]:
    errors: list[str] = []
    entry_map = get_script_entry_map_fn()
    seen: dict[str, str] = {}
    manifest = get_surface_manifest_fn()
    for level in SURFACE_LEVELS:
        for relative_path in manifest.get(level) or []:
            if relative_path not in entry_map:
                errors.append(f'script_catalog_surface.json visibility 包含未登记脚本：{relative_path}')
                continue
            if relative_path in seen:
                errors.append(f'script_catalog_surface.json visibility 存在重复分类：{relative_path} 同时属于 {seen[relative_path]} 与 {level}')
                continue
            seen[str(relative_path)] = level
    for relative_path, entry in entry_map.items():
        if entry.get('visibility') not in SURFACE_LEVELS:
            allowed = ', '.join(SURFACE_LEVELS)
            errors.append(f'script_catalog_surface.json {relative_path}.visibility 必须为 {allowed}')
    for relative_path in entry_map:
        if relative_path not in seen:
            errors.append(f'script_catalog_surface.json visibility 缺少脚本分类：{relative_path}')
    return errors


def get_surface_entries(
    level: str,
    group_id: str | None = None,
    *,
    get_surface_manifest_fn: Callable[[], dict[str, Any]] = get_surface_manifest,
    get_all_script_entries_fn: Callable[[], list[dict[str, Any]]] = get_all_script_entries,
) -> list[dict[str, Any]]:
    allowed = set(get_surface_manifest_fn().get(level) or [])
    return [entry for entry in get_all_script_entries_fn() if entry['path'] in allowed and (group_id is None or entry['groupId'] == group_id)]


def render_script_file_list(
    entries: list[dict[str, Any]],
    with_prefix: bool = False,
    with_surface: bool = True,
    *,
    get_script_surface_map_fn: Callable[[], dict[str, str]] = get_script_surface_map,
) -> list[str]:
    surface_map = get_script_surface_map_fn()
    lines: list[str] = []
    for entry in entries:
        ref = f"`{entry['groupId']}/{entry['name']}`" if with_prefix else f"`{entry['name']}`"
        surface_label = SURFACE_LABELS.get(surface_map.get(entry['path']) or '', 'unclassified')
        surface = f"（{surface_label}）" if with_surface else ''
        lines.append(f"- {ref}{surface}：{entry['summary']}")
    return lines


def render_scripts_index_readme(
    *,
    script_groups_fn: Callable[[], list[dict[str, Any]]] = script_groups,
    get_surface_entries_fn: Callable[..., list[dict[str, Any]]] = get_surface_entries,
    render_script_file_list_fn: Callable[..., list[str]] = render_script_file_list,
) -> str:
    lines = [
        '# scripts 目录索引',
        '',
    ]
    if AUTO_NOTICE.strip():
        lines.extend([AUTO_NOTICE.strip(), ''])
    lines.extend([
        '本页只做 `scripts/` 目录内脚本定位：先按项目概览、`docs/getting-started/` 或 `docs/operations/` 确定场景，再回这里找默认入口、补充入口或内部复用。',
        '',
        '> 运行、诊断与 dispatch 运维入口统一回对应的 generic command surface 与专题文档定位。',
        '',
        '## 分组去向',
        '',
    ])
    groups = script_groups_fn()
    for group in groups:
        group_ref = f"`{group['id']}/`"
        lines.append(f"- {group_ref}：{group['purpose']}")
    for group in groups:
        lines.extend(['', f"## {group['id']}/", ''])
        lines.append(f"- 目录职责：{group['purpose']}")
        notes = [str(item) for item in (group.get('notes') or []) if str(item).strip()]
        if notes:
            lines.append('- 维护说明：')
            lines.extend([f"  - {note}" for note in notes])
        for level in SURFACE_LEVELS:
            entries = get_surface_entries_fn(level, group['id'])
            if not entries:
                continue
            lines.extend(['', f"### {SURFACE_TITLES[level]}", ''])
            lines.extend(render_script_file_list_fn(entries, with_prefix=True, with_surface=False))
    lines.extend([
        '',
        '## 使用规则',
        '',
        '- 先由项目概览、`docs/getting-started/quickstart.md` 或 `docs/operations/README.md` 确定场景，再回本页按分组选入口。',
        '- 内部复用脚本只作为实现支撑，不作为人工默认入口。',
        '- 生产计划任务统一由 control-plane scheduler 直接执行标准入口，不从脚本索引反推调度实现。',
        '',
        '## 目录规则',
        '',
        '- 顶层 `scripts/` 统一使用一个 `README.md` 与分组目录。',
        '- 默认入口、补充入口与内部复用入口按同一套分组目录展示。',
        '',
    ])
    return normalize_spacing('\n'.join(lines))


def render_group_surface_section(
    group_id: str,
    level: str,
    *,
    get_surface_entries_fn: Callable[..., list[dict[str, Any]]] = get_surface_entries,
    render_script_file_list_fn: Callable[..., list[str]] = render_script_file_list,
) -> list[str]:
    entries = get_surface_entries_fn(level, group_id)
    if not entries:
        return []
    return [f"## {SURFACE_TITLES[level]}", '', *render_script_file_list_fn(entries, with_surface=False), '']


def render_scripts_group_readme(
    group_id: str,
    *,
    get_script_group_fn: Callable[[str], dict[str, Any] | None] = get_script_group,
    render_group_surface_section_fn: Callable[[str, str], list[str]] = render_group_surface_section,
) -> str:
    group = get_script_group_fn(group_id)
    if not group:
        raise KeyError(f'未知 scripts 分组：{group_id}')
    guidance = '本页只列出本分组脚本。先看默认入口；需要补充动作时再看补充入口；内部复用不作为人工入口。'
    if group_id == 'lib':
        guidance = '本页只回答某个 helper 负责什么；helper 条目不作为人工入口。'
    lines = [
        f"# {group['title']}",
        '',
    ]
    if AUTO_NOTICE.strip():
        lines.extend([AUTO_NOTICE.strip(), ''])
    lines.extend([
        str(group['purpose']),
        '',
        guidance,
        '',
    ])
    lines.extend(render_group_surface_section_fn(group_id, 'default_entrypoint'))
    lines.extend(render_group_surface_section_fn(group_id, 'supplemental_entrypoint'))
    lines.extend(render_group_surface_section_fn(group_id, 'internal_support'))
    notes = list(group.get('notes') or [])
    if notes:
        lines.extend(['## 维护说明', '', *[f'- {note}' for note in notes], ''])
    return normalize_spacing('\n'.join(lines))


def get_script_catalog_doc_layout(
    *,
    read_json_fn: Callable[[Path], Any] = read_json,
) -> dict[str, str]:
    payload = read_json_fn(repo_contract_path('governance.script_catalog_surface'))
    if not isinstance(payload, dict):
        raise ValueError('script_catalog_surface.json 顶层必须为对象')
    generated = payload.get('generated_artifacts') or {}
    if not isinstance(generated, dict):
        raise ValueError('script_catalog_surface.json -> generated_artifacts 顶层必须为对象')
    scripts_index_doc = str(generated.get('scripts_index_doc') or '').strip()
    group_readme_dir = str(generated.get('group_readme_dir') or '').strip()
    group_readme_name = str(generated.get('group_readme_name') or '').strip()
    if not scripts_index_doc:
        raise ValueError('script_catalog_surface.json -> generated_artifacts.scripts_index_doc 不能为空')
    if group_readme_name and Path(group_readme_name).name != group_readme_name:
        raise ValueError('script_catalog_surface.json -> generated_artifacts.group_readme_name 必须为文件名')
    return {
        'scripts_index_doc': scripts_index_doc,
        'group_readme_dir': group_readme_dir,
        'group_readme_name': group_readme_name,
    }


def get_script_doc_targets(
    root_dir: Path | None = None,
    *,
    get_script_catalog_doc_layout_fn: Callable[[], dict[str, str]] = get_script_catalog_doc_layout,
    render_scripts_index_readme_fn: Callable[[], str] = render_scripts_index_readme,
    script_groups_fn: Callable[[], list[dict[str, Any]]] = script_groups,
    render_scripts_group_readme_fn: Callable[[str], str] = render_scripts_group_readme,
) -> dict[Path | str, str]:
    layout = get_script_catalog_doc_layout_fn()
    targets: dict[Path | str, str] = {layout['scripts_index_doc']: render_scripts_index_readme_fn()}
    if layout['group_readme_dir'] and layout['group_readme_name']:
        for group in script_groups_fn():
            targets[f"{layout['group_readme_dir']}/{group['id']}/{layout['group_readme_name']}"] = render_scripts_group_readme_fn(str(group['id']))
    if root_dir is None:
        return targets
    return {(root_dir / str(relative_path)): content for relative_path, content in targets.items()}


def get_expected_script_files(
    *,
    script_groups_fn: Callable[[], list[dict[str, Any]]] = script_groups,
) -> list[dict[str, Any]]:
    return [{'id': group['id'], 'files': sorted([file['name'] for file in group.get('files') or []])} for group in script_groups_fn()]
