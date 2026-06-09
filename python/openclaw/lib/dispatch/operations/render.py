from __future__ import annotations

from pathlib import Path

from openclaw.lib.dispatch.operations.state import entries, entry_info, example_rows, string_list


def render_index(*, config_path: Path | None = None, extension_id: str | None = None) -> str:
    lines = ['dispatch operation entries', '']
    for info in entries(config_path=config_path, extension_id=extension_id):
        entry_id = str(info.get('id') or '').strip()
        owner = str(info.get('extensionId') or '').strip()
        owner_suffix = f' [{owner}]' if owner else ''
        lines.append(f'- {entry_id}: {str(info.get("title") or entry_id).strip()}{owner_suffix}')
    return '\n'.join(lines)


def render_entry(entry_id: str, *, config_path: Path | None = None, extension_id: str | None = None) -> str:
    info = entry_info(entry_id, config_path=config_path, extension_id=extension_id)
    lines = [
        f'id: {entry_id}',
        f'extension: {str(info.get("extensionId") or "<base>").strip()}',
        f'title: {str(info.get("title") or entry_id).strip()}',
        f'purpose: {str(info.get("purpose") or "").strip()}',
        f'entry_command: {str(info.get("entry_command") or "").strip()}',
        'steps:',
    ]
    for step in string_list(info.get('steps')):
        lines.append(f'  - {step}')
    prerequisites = string_list(info.get('prerequisites'))
    if prerequisites:
        lines.append('prerequisites:')
        for item in prerequisites:
            lines.append(f'  - {item}')
    examples = example_rows(info.get('example_commands'))
    if examples:
        lines.append('example_commands:')
        for item in examples:
            lines.append(f'  - {item["title"]}: {item["command"]}')
    result_checks = string_list(info.get('result_checks'))
    if result_checks:
        lines.append('result_checks:')
        for item in result_checks:
            lines.append(f'  - {item}')
    common_branches = string_list(info.get('common_branches'))
    if common_branches:
        lines.append('common_branches:')
        for item in common_branches:
            lines.append(f'  - {item}')
    refs = string_list(info.get('references'))
    if refs:
        lines.append('references:')
        for item in refs:
            lines.append(f'  - {item}')
    notes = string_list(info.get('notes'))
    if notes:
        lines.append('notes:')
        for item in notes:
            lines.append(f'  - {item}')
    return '\n'.join(lines)


def render_commands(entry_id: str, *, config_path: Path | None = None, extension_id: str | None = None) -> str:
    info = entry_info(entry_id, config_path=config_path, extension_id=extension_id)
    return '\n'.join(item for item in string_list(info.get('steps')))
