#!/usr/bin/env python3
"""运行态统一入口控制面。"""
from __future__ import annotations

import json
import sys
from typing import NoReturn

from openclaw.docs.renderers import runtime_surface as runtime_surface_reference
from openclaw.lib.cli.examples import canonical_cli_command, usage_block


def read_manifest() -> dict:
    return runtime_surface_reference.read_manifest()


def usage() -> str:
    return '\n'.join([
        usage_block(
            canonical_cli_command('runtime', 'surface', 'doc'),
            canonical_cli_command('runtime', 'surface', 'json'),
            canonical_cli_command('runtime', 'surface', 'show-index'),
            canonical_cli_command('runtime', 'surface', 'show-entry') + ' --entry <entry_id>',
            canonical_cli_command('runtime', 'surface', 'show-scenario') + ' --scenario <scenario_id>',
            canonical_cli_command('runtime', 'surface', 'show-post-deploy'),
            title='用法：',
            trailing_newline=False,
        ),
        '',
        '说明：',
        '  doc              输出 docs/operations/runtime-service-reference.md 应有内容。',
        '  json             输出运行态统一入口真源 JSON。',
        '  show-index       输出默认入口与常见最短组合索引。',
        '  show-entry       输出单个运行态入口说明。',
        '  show-scenario    输出单个常见最短组合。',
        '  show-post-deploy 输出首次部署后的人工补充核对。',
        '',
    ])


def fail(message: str, exit_code: int = 2) -> NoReturn:
    sys.stderr.write(f"[runtime_surface_control_plane][FAIL] {message}\n")
    raise SystemExit(exit_code)


def _parse_option(args: list[str], name: str) -> str:
    if name not in args:
        fail(f'缺少参数：{name}')
    index = args.index(name)
    if index + 1 >= len(args):
        fail(f'{name} 缺少取值')
    return args[index + 1]


def _entry_map(manifest: dict) -> dict[str, dict]:
    return {entry['entry_id']: entry for entry in manifest.get('entrypoints') or []}


def _scenario_map(manifest: dict) -> dict[str, dict]:
    return {scenario['scenario_id']: scenario for scenario in manifest.get('scenarios') or []}


def show_index(manifest: dict) -> str:
    lines = ['# runtime surface index', '', '## entrypoints']
    for entry in manifest.get('entrypoints') or []:
        lines.append(f"- {entry['entry_id']}: {entry['title']} -> {entry['command']}")
    lines.append('')
    lines.append('## scenarios')
    for scenario in manifest.get('scenarios') or []:
        lines.append(f"- {scenario['scenario_id']}: {scenario['title']}")
    lines.append('')
    lines.append('## post-deploy')
    post_deploy = dict(manifest.get('manual_post_deploy_checks') or {})
    lines.append(f"- {post_deploy.get('title', '首次部署后的人工补充核对')}")
    return '\n'.join(lines).rstrip() + '\n'


def show_entry(manifest: dict, entry_id: str) -> str:
    entry = _entry_map(manifest).get(entry_id)
    if entry is None:
        fail(f'未知 entry_id：{entry_id}')
    lines = [f"# {entry['title']}", '', f"- entry_id: `{entry['entry_id']}`", f"- command: `{entry['command']}`", f"- when: {entry['when']}", '']
    notes = entry.get('notes') or []
    if notes:
        lines.append('## notes')
        for note in notes:
            lines.append(f'- {note}')
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def show_scenario(manifest: dict, scenario_id: str) -> str:
    scenario = _scenario_map(manifest).get(scenario_id)
    if scenario is None:
        fail(f'未知 scenario_id：{scenario_id}')
    lines = [f"# {scenario['title']}", '', f"- scenario_id: `{scenario['scenario_id']}`", '', '```bash']
    lines.extend(scenario.get('steps') or [])
    lines.append('```')
    lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def show_post_deploy(manifest: dict) -> str:
    post_deploy = dict(manifest.get('manual_post_deploy_checks') or {})
    lines = [f"# {post_deploy.get('title', '首次部署后的人工补充核对')}", '']
    intro = str(post_deploy.get('intro') or '').strip()
    if intro:
        lines.append(intro)
        lines.append('')
    lines.append('## artifacts')
    for artifact in post_deploy.get('artifacts') or []:
        lines.append(f'- `{artifact}`')
    lines.append('')
    lines.append('## steps')
    lines.append('```bash')
    lines.extend(post_deploy.get('steps') or [])
    lines.append('```')
    lines.append('')
    lines.append('## points')
    for point in post_deploy.get('points') or []:
        lines.append(f'- {point}')
    lines.append('')
    pairing_note = str(post_deploy.get('pairing_note') or '').strip()
    if pairing_note:
        lines.append('## pairing')
        lines.append(pairing_note)
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {'-h', '--help'}:
        sys.stdout.write(usage())
        return 0
    command = args.pop(0)
    manifest = read_manifest()
    if command == 'doc':
        if args:
            fail(f'doc 不接受参数：{" ".join(args)}')
        sys.stdout.write(runtime_surface_reference.render_doc(manifest))
        return 0
    if command == 'json':
        if args:
            fail(f'json 不接受参数：{" ".join(args)}')
        sys.stdout.write(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        return 0
    if command == 'show-index':
        if args:
            fail(f'show-index 不接受参数：{" ".join(args)}')
        sys.stdout.write(show_index(manifest))
        return 0
    if command == 'show-entry':
        sys.stdout.write(show_entry(manifest, _parse_option(args, '--entry')))
        return 0
    if command == 'show-scenario':
        sys.stdout.write(show_scenario(manifest, _parse_option(args, '--scenario')))
        return 0
    if command == 'show-post-deploy':
        if args:
            fail(f'show-post-deploy 不接受参数：{" ".join(args)}')
        sys.stdout.write(show_post_deploy(manifest))
        return 0
    fail(f'未知命令：{command}')

if __name__ == '__main__':
    raise SystemExit(main())
