#!/usr/bin/env python3
"""one_click_deploy 失败态最小摘要控制面。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from openclaw.lib.repo.layout import resolve_repo_root
from typing import Any, NoReturn

from openclaw.lib.repo.static_truth import (
    governance_default_path,
    read_repo_contract_json,
    repo_contract_relpath,
)
from openclaw.lib.summary.io import (
    relative_or_self,
    summary_output_profile as summary_profile,
    utc_now_iso,
    write_json,
    write_text,
)
from openclaw.setup.flow import deploy_flow as deploy_flow_control_plane

ROOT_DIR = resolve_repo_root(Path(__file__))
SUPPORTED_FLOW = 'deploy'
FLOW_LABEL = 'one_click_deploy'


def fail(message: str, code: int = 2) -> NoReturn:
    sys.stderr.write(f'[run_failure_control_plane][FAIL] {message}\n')
    raise SystemExit(code)


def read_surface() -> dict[str, Any]:
    payload = read_repo_contract_json('governance.run_failure_surface')
    if not isinstance(payload, dict):
        fail('run_failure_surface.json 顶层必须为对象')
    payload['intro'] = list(payload.get('intro') or [])
    payload['usage_commands'] = list(payload.get('usage_commands') or [])
    payload['boundary'] = list(payload.get('boundary') or [])
    payload['markdown_labels'] = dict(payload.get('markdown_labels') or {})
    payload['text_labels'] = dict(payload.get('text_labels') or {})
    return payload


def read_setup_failure_surface() -> dict[str, Any]:
    payload = read_repo_contract_json('governance.setup_failures')
    if not isinstance(payload, dict):
        fail(f'{repo_contract_relpath("governance.setup_failures")} 顶层必须为对象')
    return payload


def list_str(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def usage() -> str:
    surface = read_surface()
    lines = ['用法：']
    lines.extend([f'  {command}' for command in surface.get('usage_commands') or []])
    lines.append('')
    return '\n'.join(lines)


def parse_args(argv: list[str]) -> dict[str, Any]:
    opts: dict[str, Any] = {
        'flow': '',
        'stage': '',
        'status': 'failed',
        'timestamp': '',
        'log_path': '',
        'summary_json_path': '',
        'summary_md_path': '',
        'out_json': '',
        'out_md': '',
        'exit_code': 1,
        'format': 'text',
        'mode': 'online',
        'resume_from': '',
        'image_archive_path': '',
    }
    index = 0
    while index < len(argv):
        arg = argv[index]
        if not arg.startswith('--'):
            fail(f'未知参数：{arg}')
        index += 1
        if index >= len(argv):
            fail(f'{arg} 缺少参数值')
        value = argv[index]
        index += 1
        match arg:
            case '--flow':
                opts['flow'] = value
            case '--stage':
                opts['stage'] = value
            case '--status':
                opts['status'] = value
            case '--timestamp':
                opts['timestamp'] = value
            case '--log-path':
                opts['log_path'] = Path(value).resolve()
            case '--summary-json-path':
                opts['summary_json_path'] = Path(value).resolve()
            case '--summary-md-path':
                opts['summary_md_path'] = Path(value).resolve()
            case '--out-json':
                opts['out_json'] = Path(value).resolve()
            case '--out-md':
                opts['out_md'] = Path(value).resolve()
            case '--exit-code':
                opts['exit_code'] = int(value)
            case '--format':
                opts['format'] = value
            case '--mode':
                opts['mode'] = value
            case '--resume-from':
                opts['resume_from'] = value
            case '--image-archive-path':
                opts['image_archive_path'] = value
            case _:
                fail(f'未知参数：{arg}')
    if opts['flow'] != SUPPORTED_FLOW:
        fail(f'当前仅支持 --flow {SUPPORTED_FLOW}，收到：{opts["flow"]}')
    if not opts['stage']:
        fail('--stage 缺少阶段名')
    return opts


def default_latest_path(key: str) -> Path:
    rel = governance_default_path(key, profile_id='one_click_deploy', root_dir=ROOT_DIR)
    if not rel:
        fail(f'deploy_success.summary_manifest 缺少路径：{key}')
    return ROOT_DIR / rel


def build_next_steps(options: dict[str, Any]) -> list[str]:
    raw = deploy_flow_control_plane.render_next_commands(options['stage'], {
        'mode': options['mode'],
        'imageArchivePath': options['image_archive_path'] or '',
        'stage': options['stage'],
    })
    return [line for line in raw.splitlines() if line]


def build_setup_failure_bucket(stage: str) -> dict[str, Any]:
    payload = read_setup_failure_surface()
    generated = dict(payload.get('generated_artifacts') or {})
    doc_path = str(generated.get('setup_failure_doc') or '').strip()
    entry = dict((payload.get('entries') or {}).get('one_click_deploy') or {})
    scenarios = entry.get('scenarios') or {}
    if not isinstance(scenarios, dict):
        scenarios = {}
    for scenario_id, info in scenarios.items():
        if not isinstance(info, dict):
            continue
        if stage not in list_str(info.get('stages')):
            continue
        return {
            'doc_path': doc_path,
            'entry_id': 'one_click_deploy',
            'entry_title': str(entry.get('title') or 'one_click_deploy').strip(),
            'scenario_id': str(scenario_id),
            'scenario_title': str(info.get('title') or scenario_id).strip(),
            'when': str(info.get('when') or '').strip(),
            'notes': list_str(info.get('notes')),
            'references': list_str(info.get('references')),
        }
    return {
        'doc_path': doc_path,
        'entry_id': 'one_click_deploy',
        'entry_title': str(entry.get('title') or 'one_click_deploy').strip(),
        'scenario_id': '',
        'scenario_title': '',
        'when': '',
        'notes': [],
        'references': [],
    }


def build_summary(options: dict[str, Any]) -> dict[str, Any]:
    stage_info = deploy_flow_control_plane.stage_info(options['stage'])
    latest_json_path = default_latest_path('latest_json')
    latest_markdown_path = default_latest_path('latest_markdown')
    return {
        'schema_version': 1,
        'generated_at': utc_now_iso(),
        'flow_run': {
            'flow': SUPPORTED_FLOW,
            'label': FLOW_LABEL,
            'status': options['status'],
            'timestamp': options['timestamp'] or None,
            'log_path': relative_or_self(options['log_path'], root_dir=ROOT_DIR),
            'summary_json_path': relative_or_self(options['summary_json_path'] or options['out_json'], root_dir=ROOT_DIR),
            'summary_markdown_path': relative_or_self(options['summary_md_path'] or options['out_md'], root_dir=ROOT_DIR),
            'exit_code': options['exit_code'],
            'mode': options['mode'],
            'resume_from': options['resume_from'] or None,
            'image_archive_path': options['image_archive_path'] or None,
        },
        'fixed_latest_summary': {
            'json_path': relative_or_self(latest_json_path, root_dir=ROOT_DIR),
            'markdown_path': relative_or_self(latest_markdown_path, root_dir=ROOT_DIR),
        },
        'failed_stage': {
            'id': options['stage'],
            'label': stage_info['label'],
        },
        'summary_hint': list(stage_info.get('summary_hint') or []),
        'next_steps': build_next_steps(options),
        'setup_failure_bucket': build_setup_failure_bucket(options['stage']),
    }


def render_markdown(summary: dict[str, Any]) -> str:
    surface = read_surface()
    profile = summary_profile('run_failure')
    labels = dict(profile.get('markdown', {}).get('sections') or {})
    title = str((profile.get('markdown') or {}).get('title') or surface.get('markdown_labels', {}).get('title') or 'one_click_deploy 失败摘要').strip()
    bucket = dict(summary.get('setup_failure_bucket') or {})
    lines = [
        f"# {title}",
        '',
        f"- 时间：{summary['flow_run']['timestamp'] or summary['generated_at']}",
        f"- 状态：{summary['flow_run']['status']}",
        f"- 日志：`{summary['flow_run']['log_path'] or '<missing>'}`",
        f"- 机器摘要：`{summary['flow_run']['summary_json_path'] or '<missing>'}`",
        f"- fixed latest 机器摘要：`{summary['fixed_latest_summary']['json_path'] or '<missing>'}`",
        f"- fixed latest 人工摘要：`{summary['fixed_latest_summary']['markdown_path'] or '<missing>'}`",
        f"- 失败阶段：`{summary['failed_stage']['id']}`",
        f"- 阶段标签：{summary['failed_stage']['label']}",
        f"- 退出码：`{summary['flow_run']['exit_code']}`",
    ]
    if bucket.get('scenario_title'):
        lines.append(f"- setup 主链失败分流：{bucket['scenario_title']} (`{bucket['scenario_id']}`)")
    if bucket.get('doc_path'):
        lines.append(f"- setup 主链失败总览：`{bucket['doc_path']}`")
    lines.extend(['', f"## {labels.get('diagnosis') or surface.get('markdown_labels', {}).get('diagnosis_heading') or '排障结论'}", ''])
    lines.extend(summary['summary_hint'])
    if bucket.get('when'):
        lines.extend(['', f"- 主链分流适用条件：{bucket['when']}"])
    if bucket.get('notes'):
        lines.extend(['', '- 主链分流补充：'])
        lines.extend([f'  - {item}' for item in bucket['notes']])
    lines.extend(['', f"## {labels.get('next_steps') or surface.get('markdown_labels', {}).get('next_steps_heading') or '下一步命令'}", ''])
    lines.extend([f"- `{step}`" for step in summary['next_steps']])
    if bucket.get('references'):
        lines.extend(['', '## 统一参考', ''])
        lines.extend([f"- `{item}`" for item in bucket['references']])
    return '\n'.join(lines)


def render_text(summary: dict[str, Any]) -> str:
    profile = summary_profile('run_failure')
    labels = dict(profile.get('text') or {})
    bucket = dict(summary.get('setup_failure_bucket') or {})
    lines = [
        f"[run_failure] flow={summary['flow_run']['flow']} stage={summary['failed_stage']['id']} exit={summary['flow_run']['exit_code']}",
        f"[run_failure] label={summary['failed_stage']['label']}",
        f"[run_failure] latest_json={summary['fixed_latest_summary']['json_path'] or '<missing>'}",
        f"[run_failure] latest_markdown={summary['fixed_latest_summary']['markdown_path'] or '<missing>'}",
    ]
    if bucket.get('scenario_title'):
        lines.append(f"[run_failure] setup_failure_bucket={bucket['scenario_id']} ({bucket['scenario_title']})")
    if bucket.get('doc_path'):
        lines.append(f"[run_failure] setup_failure_doc={bucket['doc_path']}")
    lines.extend([
        str(labels.get('diagnosis_heading') or '[run_failure] 排障结论：'),
        *[f'  {line}' for line in summary['summary_hint']],
    ])
    if bucket.get('when'):
        lines.append(f'  主链分流适用条件：{bucket["when"]}')
    if bucket.get('notes'):
        lines.append('  主链分流补充：')
        lines.extend([f'    - {item}' for item in bucket['notes']])
    lines.extend([
        str(labels.get('next_steps_heading') or '[run_failure] 下一步动作：'),
        *[f'  - {step}' for step in summary['next_steps']],
    ])
    if bucket.get('references'):
        lines.append('[run_failure] 统一参考：')
        lines.extend([f'  - {item}' for item in bucket['references']])
    return '\n'.join(lines)


def render_doc() -> str:
    surface = read_surface()
    profile = summary_profile('run_failure')
    markdown_profile = dict(profile.get('markdown') or {})
    text_profile = dict(profile.get('text') or {})
    sections = dict(markdown_profile.get('sections') or {})
    lines = [
        f"# {str(markdown_profile.get('title') or surface.get('markdown_labels', {}).get('title') or 'one_click_deploy 失败摘要').strip()}",
        '',
        *surface.get('intro', []),
        '',
        '## 默认入口',
        '',
    ]
    for command in surface.get('usage_commands') or []:
        lines.append(f'- `{command}`')
    lines.extend(['', '## 输出结构', ''])
    lines.extend([
        f"- Markdown 摘要标题：`{str(markdown_profile.get('title') or surface.get('markdown_labels', {}).get('title') or 'one_click_deploy 失败摘要').strip()}`",
        f"- Markdown 固定分节：`{str(sections.get('diagnosis') or surface.get('markdown_labels', {}).get('diagnosis_heading') or '排障结论').strip()}` / `{str(sections.get('next_steps') or surface.get('markdown_labels', {}).get('next_steps_heading') or '下一步命令').strip()}`",
        f"- 纯文本固定分节：`{str(text_profile.get('diagnosis_heading') or surface.get('text_labels', {}).get('diagnosis_heading') or '[run_failure] 排障结论：').strip()}` / `{str(text_profile.get('next_steps_heading') or surface.get('text_labels', {}).get('next_steps_heading') or '[run_failure] 下一步动作：').strip()}`",
        '- `summary_hint` 与阶段级 `next_steps` 均按失败阶段动态生成；setup 主链失败总览与统一第一跳入口回到 `troubleshooting.md`。',
    ])
    lines.extend(['', '## 维护边界', ''])
    lines.extend([f'- {line}' for line in surface.get('boundary') or []])
    return '\n'.join(lines).rstrip() + '\n'


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {'-h', '--help'}:
        sys.stdout.write(usage())
        return 0
    command = args.pop(0)
    if command == 'doc':
        if args:
            fail(f'doc 不接受参数：{" ".join(args)}')
        sys.stdout.write(render_doc())
        return 0
    options = parse_args(args)
    if command == 'failure-summary':
        summary = build_summary(options)
        if options['format'] == 'json':
            sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
        else:
            sys.stdout.write(render_text(summary) + '\n')
        return 0
    if command == 'write-failure-summary':
        if not options['out_json']:
            fail('write-failure-summary 缺少 --out-json')
        if not options['out_md']:
            fail('write-failure-summary 缺少 --out-md')
        summary = build_summary(options)
        write_json(Path(options['out_json']), summary)
        markdown = render_markdown(summary)
        write_text(Path(options['out_md']), markdown)
        write_json(default_latest_path('latest_json'), summary)
        write_text(default_latest_path('latest_markdown'), markdown)
        return 0
    fail(f'未知命令：{command}')

if __name__ == '__main__':
    raise SystemExit(main())
