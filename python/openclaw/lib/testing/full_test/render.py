#!/usr/bin/env python3
"""渲染 full-test 摘要、目录和终端输出的控制面辅助函数。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.lib.testing.full_test.acceptance import (
    check_catalog,
    execution_order,
    group_catalog,
    normalize_check_csv,
    normalize_required_acceptance_ids,
    parse_result_line,
    read_lines,
    selectable_groups,
    summarize_required_run_ledger,
    validate_check_records,
    validate_group_name,
)
from openclaw.lib.testing.full_test.io import (
    ROOT_DIR,
    default_path,
    fail,
    read_manifest,
    read_surface,
    safe_read_json,
    summary_output_profile,
    write_json,
    write_text,
)
from openclaw.lib.io.json_access import json_array, json_object

TEXT_DETAIL_LIMIT = 1200
MARKDOWN_DETAIL_LIMIT = 1800


def _display_path(path: str | Path | None) -> str | None:
    if not path:
        return None
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(ROOT_DIR))
    except ValueError:
        return str(resolved)


def _compact_detail(detail: Any, *, limit: int) -> str:
    text = str(detail or '')
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f'{text[:limit]}... [detail 已截断 {omitted} 字符；完整内容见机器摘要 JSON]'


def _markdown_cell(value: Any, *, limit: int | None = None) -> str:
    text = str(value or '')
    if limit is not None:
        text = _compact_detail(text, limit=limit)
    return text.replace('|', '\\/').replace('\r', ' ').replace('\n', '<br>')


def _duration_seconds(check: dict[str, Any]) -> int | None:
    value = check.get('duration_seconds')
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _slow_checks(checks: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    timed: list[dict[str, Any]] = []
    for check in checks:
        duration = _duration_seconds(check)
        if duration is None:
            continue
        timed.append({
            'id': check.get('id'),
            'group': check.get('group'),
            'status': check.get('status'),
            'duration_seconds': duration,
        })
    return sorted(timed, key=lambda item: item['duration_seconds'], reverse=True)[:limit]


def build_summary(options: dict[str, Any]) -> dict[str, Any]:
    if not options.get('generatedAt'):
        fail('write-summary 缺少 --generated-at')
    if not options.get('envFile'):
        fail('write-summary 缺少 --env-file')
    if not options.get('outJson'):
        fail('write-summary 缺少 --out-json')
    if not options.get('outMd'):
        fail('write-summary 缺少 --out-md')
    manifest = read_manifest()
    validate_group_name(options.get('group', 'all'), manifest)
    checks = [parse_result_line(line) for line in read_lines(options.get('resultLinesFile', ''))]
    validate_check_records(checks, manifest)
    next_actions = read_lines(options.get('nextActionsFile', ''))
    acceptance = safe_read_json(options.get('acceptanceState', '')) if options.get('acceptanceState') else None
    manifest_run_ledger = summarize_required_run_ledger(manifest)
    acceptance_run_ledger = acceptance.get('run_ledger_snapshot') if isinstance(acceptance, dict) and isinstance(acceptance.get('run_ledger_snapshot'), dict) else None
    run_ledger_snapshot = acceptance_run_ledger or manifest_run_ledger
    acceptance_run_ledger_policy = acceptance.get('run_ledger_policy') if isinstance(acceptance, dict) and isinstance(acceptance.get('run_ledger_policy'), dict) else None

    def status_ids(target: str) -> list[str]:
        return [item['id'] for item in checks if item['status'] == target]

    return_code = int(options.get('returnCode') or 0)
    process_error_blocking = return_code != 0 and not status_ids('FAIL')
    if process_error_blocking:
        checks = [
            *checks,
            {
                'status': 'FAIL',
                'id': 'full_test_process_exit_code',
                'detail': f'full test 进程返回非 0：return_code={return_code}，但结果行未记录 FAIL 检查项。',
                'group': 'process',
            },
        ]
        next_actions = [
            *next_actions,
            'full test 进程返回非 0 但没有记录 FAIL 检查项；查看 latest summary、full test 日志与 deployment_acceptance 写出路径后重新执行默认 full 全量验证。',
        ]
    blocking_checks = status_ids('FAIL')
    slow_checks = _slow_checks(checks)
    duration_seconds_total = sum(_duration_seconds(item) or 0 for item in checks)
    normalized_only = normalize_check_csv(options.get('only', ''), '--only', manifest) if options.get('only') else None
    normalized_skip = normalize_check_csv(options.get('skip', ''), '--skip', manifest) if options.get('skip') else None
    return {
        'schema_version': 2,
        'generated_at': options['generatedAt'],
        'suite': 'one_click_test_full',
        'env_file': options['envFile'],
        'invocation': {
            'group': options.get('group', 'all'),
            'only': normalized_only,
            'skip': normalized_skip,
            'strict': options.get('strict', False),
            'quiet': options.get('quiet', False),
            'json_stdout': options.get('jsonStdout', False),
            'return_code': return_code,
            'summary_json_path': _display_path(options['outJson']),
            'summary_markdown_path': _display_path(options['outMd']),
            'valid_groups': selectable_groups(manifest),
        },
        'summary': {
            'pass': len(status_ids('PASS')),
            'fail': len(blocking_checks),
            'warn': len(status_ids('WARN')),
            'skip': len(status_ids('SKIP')),
            'return_code': return_code,
            'duration_seconds_total': duration_seconds_total,
        },
        'groups': group_catalog(manifest),
        'checks': checks,
        'check_catalog': check_catalog(manifest),
        'blocking_checks': blocking_checks,
        'warning_checks': status_ids('WARN'),
        'skipped_checks': status_ids('SKIP'),
        'slow_checks': slow_checks,
        'next_actions': next_actions,
        'deployment_acceptance': {
            'path': _display_path(options.get('acceptanceState')),
            'exists': acceptance is not None,
            'eligible': (acceptance.get('eligible') is True) if isinstance(acceptance, dict) else None,
            'accepted': (acceptance.get('accepted') is True) if isinstance(acceptance, dict) else None,
            'required_acceptance_ids': normalize_required_acceptance_ids(options.get('requiredAcceptanceIds', '')),
            'required_run_ledger_jobs': list((acceptance.get('required_run_ledger_jobs') if isinstance(acceptance, dict) else None) or run_ledger_snapshot.get('required_jobs') or []),
            'run_ledger_policy': acceptance_run_ledger_policy,
        },
        'run_ledger': run_ledger_snapshot,
    }


def render_markdown(summary: dict[str, Any]) -> str:
    acceptance_desc = '未写出'
    if summary['deployment_acceptance']['exists']:
        acceptance_desc = (
            f"eligible={summary['deployment_acceptance']['eligible']}, "
            f"accepted={summary['deployment_acceptance']['accepted']}"
        )
    run_ledger = json_object(summary.get('run_ledger'))
    run_ledger_policy = json_object(summary['deployment_acceptance'].get('run_ledger_policy'))
    policy_reason = str(run_ledger_policy.get('reason_code') or '').strip()
    required_jobs = list(json_array(run_ledger.get('required_jobs')))
    ledger_status = '未采集'
    if run_ledger.get('exists'):
        ledger_status = (
            f"accepted={run_ledger.get('accepted')}, "
            f"missing={run_ledger.get('missing_jobs') or []}, "
            f"failing={run_ledger.get('failing_jobs') or []}, "
            f"artifact_missing={run_ledger.get('artifact_missing_jobs') or []}, "
            f"artifact_failing={run_ledger.get('artifact_failing_jobs') or []}, "
            f"recovered={run_ledger.get('recovered_jobs') or []}"
        )
    elif run_ledger.get('error'):
        ledger_status = f"采集失败：{run_ledger.get('error')}"
    profile = summary_output_profile('full_test')
    markdown_profile = dict(profile.get('markdown') or {})
    sections = dict(markdown_profile.get('sections') or {})
    lines = [
        f"# {str(markdown_profile.get('title') or 'one_click_test_full 摘要').strip()}",
        '',
        f"- 时间：{summary['generated_at']}",
        f"- 环境文件：`{summary['env_file']}`",
        f"- 检查组：{summary['invocation']['group']}",
        f"- 返回码：{summary['summary']['return_code']}",
        f"- 总耗时（已记录检查项）：{summary['summary'].get('duration_seconds_total') or 0}s",
        f"- 机器摘要：`{summary['invocation']['summary_json_path']}`",
        f"- deployment_acceptance：{acceptance_desc}",
        f"- run_ledger：{ledger_status}",
        '',
        '## 统计',
        '',
        f"- PASS：{summary['summary']['pass']}",
        f"- FAIL：{summary['summary']['fail']}",
        f"- WARN：{summary['summary']['warn']}",
        f"- SKIP：{summary['summary']['skip']}",
        '',
    ]
    for label, ids in [('FAIL', summary['blocking_checks']), ('WARN', summary['warning_checks']), ('SKIP', summary['skipped_checks'])]:
        if not ids:
            continue
        lines.extend([f'## {label}', ''])
        lines.extend([f'- `{item}`' for item in ids])
        lines.append('')
    lines.extend(['## deployment acceptance', ''])
    lines.append('- required checks：' + (', '.join(f"`{item}`" for item in summary['deployment_acceptance']['required_acceptance_ids']) or '无'))
    lines.append('- required run ledger jobs：' + (', '.join(f"`{item}`" for item in summary['deployment_acceptance']['required_run_ledger_jobs']) or '无'))
    lines.append('')
    lines.extend(['## run ledger required jobs', ''])
    if required_jobs:
        lines.append('| job_id | effective_execution_accepted | effective_artifact_accepted | current_status | issues |')
        lines.append('| --- | --- | --- | --- | --- |')
        for raw_job_state in json_array(run_ledger.get('job_states')):
            item = json_object(raw_job_state)
            issues = ', '.join(str(x) for x in json_array(item.get('issues'))) or ''
            lines.append(f"| `{item.get('id')}` | {item.get('effective_execution_accepted')} | {item.get('effective_artifact_accepted')} | {item.get('current_status') or ''} | {issues} |")
        lines.append('')
    else:
        lines.extend(['- 当前未定义 required run ledger jobs。', ''])
    if policy_reason:
        lines.extend(['## run ledger policy', ''])
        lines.append(f"- reason：`{policy_reason}`")
        lines.append(f"- blocking：{run_ledger_policy.get('blocking')}")
        lines.append('')
    if summary.get('slow_checks'):
        lines.extend(['## 慢检查', '', '| 检查项 | 分组 | 状态 | 耗时 |', '| --- | --- | --- | --- |'])
        for item in json_array(summary.get('slow_checks')):
            row = json_object(item)
            lines.append(f"| `{row.get('id')}` | {row.get('group') or ''} | {row.get('status') or ''} | {row.get('duration_seconds')}s |")
        lines.append('')
    lines.extend(['## 检查明细', '', '| 状态 | 检查项 | 分组 | 详情 |', '| --- | --- | --- | --- |'])
    for check in summary['checks']:
        duration = _duration_seconds(check)
        duration_suffix = f"（{duration}s）" if duration is not None else ''
        detail = _markdown_cell(check.get('detail') or '', limit=MARKDOWN_DETAIL_LIMIT)
        lines.append(f"| {check['status']}{duration_suffix} | `{check['id']}` | {check.get('group') or ''} | {detail} |")
    lines.append('')
    if summary['next_actions']:
        lines.extend([f"## {str(sections.get('next_steps') or '下一步动作').strip()}", ''])
        lines.extend([f'- {item}' for item in summary['next_actions']])
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def render_text(summary: dict[str, Any]) -> str:
    run_ledger = json_object(summary.get('run_ledger'))
    run_ledger_policy = json_object(summary['deployment_acceptance'].get('run_ledger_policy'))
    policy_suffix = ''
    if run_ledger_policy.get('reason_code'):
        policy_suffix = f" policy={run_ledger_policy.get('reason_code')} blocking={run_ledger_policy.get('blocking')}"
    profile = summary_output_profile('full_test')
    text_profile = dict(profile.get('text') or {})
    lines = [
        str(text_profile.get('terminal_title') or '=== one_click_test_full 汇总 ==='),
        f"PASS: {summary['summary']['pass']}",
        f"FAIL: {summary['summary']['fail']}",
        f"WARN: {summary['summary']['warn']}",
        f"SKIP: {summary['summary']['skip']}",
        f"RECORDED_DURATION_SECONDS: {summary['summary'].get('duration_seconds_total') or 0}",
        f"deployment_acceptance: eligible={summary['deployment_acceptance']['eligible']} accepted={summary['deployment_acceptance']['accepted']}",
        f"run_ledger: exists={run_ledger.get('exists')} accepted={run_ledger.get('accepted')} missing={run_ledger.get('missing_jobs')} failing={run_ledger.get('failing_jobs')} artifact_missing={run_ledger.get('artifact_missing_jobs')} artifact_failing={run_ledger.get('artifact_failing_jobs')} recovered={run_ledger.get('recovered_jobs')}{policy_suffix}",
    ]
    if summary.get('slow_checks'):
        lines.extend(['', 'slow checks:'])
        for raw_item in json_array(summary.get('slow_checks')):
            item = json_object(raw_item)
            lines.append(f"- {item.get('id')}: {item.get('duration_seconds')}s status={item.get('status')} group={item.get('group')}")
    for check in summary['checks']:
        group_suffix = f" ({check['group']})" if check.get('group') else ''
        duration = _duration_seconds(check)
        duration_suffix = f" duration={duration}s" if duration is not None else ''
        lines.extend(['', f"[{check['status']}] {check['id']}{group_suffix}{duration_suffix}"])
        if check.get('detail'):
            lines.append(f"[detail] {_compact_detail(check['detail'], limit=TEXT_DETAIL_LIMIT)}")
    if run_ledger.get('job_states'):
        lines.extend(['', 'run ledger required jobs:'])
        for raw_job_state in json_array(run_ledger.get('job_states')):
            item = json_object(raw_job_state)
            lines.append(f"- {item.get('id')}: effective_execution_accepted={item.get('effective_execution_accepted')} effective_artifact_accepted={item.get('effective_artifact_accepted')} current_status={item.get('current_status')} issues={json_array(item.get('issues'))}")
    if summary['blocking_checks']:
        lines.extend(['', '失败项:'])
        lines.extend([f'- {item}' for item in summary['blocking_checks']])
    if summary['warning_checks']:
        lines.extend(['', '警告项:'])
        lines.extend([f'- {item}' for item in summary['warning_checks']])
    if summary['skipped_checks']:
        lines.extend(['', '跳过项:'])
        lines.extend([f'- {item}' for item in summary['skipped_checks']])
    if summary['next_actions']:
        lines.extend(['', str(text_profile.get('next_steps_heading') or '下一步动作:')])
        lines.extend([f'{index + 1}. {item}' for index, item in enumerate(summary['next_actions'])])
    return '\n'.join(lines) + '\n'


def write_summary(options: dict[str, Any]) -> dict[str, Any]:
    summary = build_summary(options)
    write_json(options['outJson'], summary)
    markdown = render_markdown(summary)
    write_text(options['outMd'], markdown)
    write_json(default_path('latest_json'), summary)
    write_text(default_path('latest_markdown'), markdown)
    return summary


def append_steps(lines: list[str], steps: list[str]) -> None:
    lines.append('```bash')
    lines.extend(steps)
    lines.append('```')


def render_doc() -> str:
    manifest = read_manifest()
    surface = read_surface()
    groups = group_catalog(manifest)
    checks = check_catalog(manifest)
    selectable = set(selectable_groups(manifest))
    title = str(surface.get('title') or manifest.get('title') or '部署后 full test 摘要参考').strip()
    intro = [str(item).strip() for item in (surface.get('intro') or []) if str(item).strip()]
    lines = [
        f'# {title}',
        '',
    ]
    lines.extend(intro)
    commands = [str(item).strip() for item in (surface.get('control_plane_commands') or []) if str(item).strip()]
    if commands:
        lines.extend(['', '## 控制面入口', ''])
        for command in commands:
            lines.append(f'- `{command}`')
    common_examples = [item for item in (surface.get('common_examples') or []) if isinstance(item, dict)]
    if common_examples:
        lines.extend(['', '## 常见命令例子', ''])
        for example in common_examples:
            example_title = str(example.get('title') or '命令例子').strip()
            lines.append(f'### {example_title}')
            lines.append('')
            append_steps(lines, [str(step) for step in (example.get('steps') or []) if str(step).strip()])
            lines.append('')
            for note in example.get('notes') or []:
                lines.append(f'- {note}')
            if example.get('notes'):
                lines.append('')
    lines.extend(['', '## 默认入口总表', '', '| 主题 | 默认入口 | 什么时候用 |', '| --- | --- | --- |'])
    for entry in manifest.get('entrypoints') or []:
        lines.append(f"| {entry['title']} | `{entry['command']}` | {entry['when']} |")
    lines.extend(['', '## 固定路径', ''])
    for item in [i for i in (surface.get('fixed_paths') or []) if isinstance(i, dict)]:
        key = str(item.get('key') or '').strip()
        label = str(item.get('label') or key).strip()
        if key and key in (manifest.get('paths') or {}):
            lines.append(f"- {label}：`{manifest['paths'][key]}`")
    lines.extend(['', '## 默认执行顺序', ''])
    lines.extend([f'- `{item}`' for item in execution_order(manifest)])
    lines.extend(['', '## 可选检查组', '', '| group | 可直接用于 --group | 说明 |', '| --- | --- | --- |'])
    for group in groups:
        lines.append(f"| `{group['id']}` | {'是' if group['id'] in selectable else '否'} | {group.get('summary') or ''} |")
    lines.extend(['', '## 检查项目录', '', '| 检查项 | 分组 | 说明 |', '| --- | --- | --- |'])
    for check in checks:
        lines.append(f"| `{check['id']}` | `{check['group']}` | {check.get('summary') or ''} |")
    lines.extend(['', '## 摘要主题', ''])
    lines.extend([f'- {item}' for item in manifest.get('summary_topics') or []])
    contract = manifest.get('acceptance_contract') or {}
    lines.extend([
        '',
        '## deployment acceptance 派生检查',
        '',
        f"- 派生检查项：`{contract.get('check_id') or 'deployment_acceptance_contract'}`",
        f"- 派生分组：`{contract.get('group') or 'acceptance'}`",
    ])
    for note in [str(item).strip() for item in (surface.get('acceptance_contract_notes') or []) if str(item).strip()]:
        lines.append(f'- {note}')
    lines.extend(['', '## 使用边界', ''])
    for note in [str(item).strip() for item in (surface.get('usage_boundary') or []) if str(item).strip()]:
        lines.append(f'- {note}')
    return '\n'.join(lines).rstrip() + '\n'
