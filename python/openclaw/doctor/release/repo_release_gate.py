#!/usr/bin/env python3
"""提供OpenClaw doctor子系统的生产实现。"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterator, Mapping, Sequence

from openclaw.doctor.release.repo_release_gate_support import (
    CheckResult,
    CheckSpec,
    DOCUMENTATION_BATCH_CHECK_IDS,
    GENERATED_DOCS_SYNC_CHECK_ID,
    RELEASE_LANES,
    ROOT_DIR,
    STRICT_MODE,
    agent_governance_batch_check_spec,
    base_checks,
    documentation_batch_check_spec,
    generated_docs_check_spec,
    generated_docs_steps,
    is_agent_governance_batch_spec,
    ordered_check_specs,
    render_json,
    safe_print,
    usage,
)
from openclaw.lib.runtime.execution import build_subprocess_env
from openclaw.lib.runtime.bounded_process import (
    BoundedProcessResult,
    run_bounded_process,
    truncate_process_output,
)


def python_env() -> dict[str, str]:
    return build_subprocess_env(Path(__file__), base_env=os.environ)


def run_command(
    command: Sequence[str],
    *,
    timeout_seconds: float,
    check_id: str,
    extra_env: Mapping[str, str] | None = None,
) -> BoundedProcessResult:
    """按检查项内层时限执行命令，并确保超时时回收整个进程组。

    参数：
        command（Sequence[str]）：无需 shell 解释的命令序列。
        timeout_seconds（float）：检查项允许的最大执行秒数。
        check_id（str）：用于长检查心跳的稳定检查标识。
        extra_env（Mapping[str, str] | None）：叠加到标准发布检查环境的变量。

    返回：
        BoundedProcessResult：命令退出、输出、耗时与超时状态。
    """
    env = python_env()
    if extra_env:
        env.update({str(key): str(value) for key, value in extra_env.items()})
    return run_bounded_process(
        command,
        cwd=ROOT_DIR,
        env=env,
        timeout_seconds=timeout_seconds,
        heartbeat_label=check_id,
    )


def _process_detail(outcome: BoundedProcessResult) -> str:
    """合并并限制子进程诊断文本，附加明确的超时说明。

    参数：
        outcome（BoundedProcessResult）：有界子进程执行结果。

    返回：
        str：适合写入发布报告的截断诊断文本。
    """
    detail = '\n'.join(
        part for part in [str(outcome.stdout or '').strip(), str(outcome.stderr or '').strip()] if part
    ).strip()
    if outcome.timed_out:
        timeout_detail = '[repo_release_gate][FAIL] 检查超过内层时限，已终止并回收子进程组。'
        detail = '\n'.join(part for part in [detail, timeout_detail] if part)
    return truncate_process_output(detail)


def _numeric_failure_reason(payload: Mapping[str, object], prefix: str) -> str:
    for key in ('fail', 'fails', 'failed', 'errors', 'error_count'):
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and value > 0:
            return f'{prefix} {key}={value}'
    summary = payload.get('summary')
    if isinstance(summary, dict):
        nested = _numeric_failure_reason(summary, f'{prefix} summary')
        if nested:
            return nested
    return ''


def _json_payload_failure_reason(payload: object, prefix: str = 'JSON 顶层') -> str:
    if isinstance(payload, list):
        for index, item in enumerate(payload):
            nested = _json_payload_failure_reason(item, f'JSON 列表第 {index + 1} 项')
            if nested:
                return nested
        return ''
    if not isinstance(payload, dict):
        return ''
    status = str(payload.get('status') or '').strip().lower()
    if status in {'fail', 'failed', 'error', 'blocked'}:
        return f'{prefix} status={status}'
    ok_value = payload.get('ok')
    if ok_value is False:
        return f'{prefix} ok=false'
    for key in ('accepted', 'passed', 'success'):
        value = payload.get(key)
        if value is False:
            return f'{prefix} {key}=false'
    numeric_reason = _numeric_failure_reason(payload, prefix)
    if numeric_reason:
        return numeric_reason
    for key in ('results', 'checks', 'items'):
        value = payload.get(key)
        if isinstance(value, list):
            nested = _json_payload_failure_reason(value, f'{prefix} {key}')
            if nested:
                return nested
    return ''


def _iter_json_payloads(detail: str) -> Iterator[object]:
    decoder = json.JSONDecoder()
    raw = str(detail or '')
    index = 0
    while index < len(raw):
        next_object = raw.find('{', index)
        next_array = raw.find('[', index)
        candidates = [item for item in (next_object, next_array) if item >= 0]
        if not candidates:
            return
        start = min(candidates)
        try:
            payload, offset = decoder.raw_decode(raw[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        yield payload
        index = start + max(offset, 1)


def json_semantic_failure_reason(detail: str) -> str:
    """识别退出码为 0 但 JSON 机器状态明确失败的检查结果。

    参数：
        detail（str）：命令标准输出与标准错误合并后的详情文本。

    返回：
        返回 str，空字符串表示没有发现 JSON 语义失败；非空时为失败原因。
    """
    for payload in _iter_json_payloads(detail):
        reason = _json_payload_failure_reason(payload)
        if reason:
            return reason
    return ''


def run_check(
    spec: CheckSpec,
    quiet: bool,
    json_output: bool,
) -> CheckResult:
    if not quiet and not json_output:
        print(f'==> [{spec.check_id}] {spec.title}')
        print(f'    {spec.command_text}')

    outcome = run_command(
        spec.command,
        timeout_seconds=spec.timeout_seconds,
        check_id=spec.check_id,
    )
    detail = _process_detail(outcome)
    semantic_failure_reason = json_semantic_failure_reason(detail) if outcome.exit_code == 0 else ''
    if semantic_failure_reason:
        detail = '\n'.join(
            part
            for part in [
                detail,
                f'[repo_release_gate][FAIL] 命令退出码为 0，但机器可读 JSON 显示失败：{semantic_failure_reason}',
            ]
            if part
        )
    status = 'PASS' if outcome.exit_code == 0 and not semantic_failure_reason and not outcome.timed_out else 'FAIL'
    result = CheckResult(
        spec.check_id,
        spec.title,
        spec.command_text,
        status,
        detail,
        mode=STRICT_MODE,
        lane=spec.lane,
        duration_seconds=outcome.duration_seconds,
        exit_code=outcome.exit_code,
        timed_out=outcome.timed_out,
    )
    if json_output:
        return result
    if status == 'PASS':
        if not quiet:
            print(f'[PASS][{result.mode}] {spec.check_id}')
            if detail:
                safe_print(detail)
            print()
        return result
    print(f'[FAIL][{result.mode}] {spec.check_id}', file=sys.stderr)
    if detail:
        safe_print(detail, err=True)
    return result


def run_generated_docs_check(
    quiet: bool,
    json_output: bool,
) -> CheckResult:
    spec = generated_docs_check_spec()
    if not quiet and not json_output:
        print(f'==> [{spec.check_id}] {spec.title}')
        print(f'    {spec.command_text}')

    details: list[str] = []
    failed = False
    timed_out = False
    exit_code: int | None = 0
    started = time.monotonic()
    for label, command in generated_docs_steps():
        outcome = run_command(
            command,
            timeout_seconds=spec.timeout_seconds,
            check_id=label,
        )
        detail = _process_detail(outcome)
        details.append(f'[{label}]' if not detail else f'[{label}]\n{detail}')
        if outcome.exit_code != 0 or outcome.timed_out:
            failed = True
            if exit_code == 0:
                exit_code = outcome.exit_code
        timed_out = timed_out or outcome.timed_out

    result = CheckResult(
        spec.check_id,
        spec.title,
        spec.command_text,
        'FAIL' if failed else 'PASS',
        '\n'.join(details).strip(),
        mode=STRICT_MODE,
        lane=spec.lane,
        duration_seconds=round(time.monotonic() - started, 3),
        exit_code=exit_code,
        timed_out=timed_out,
    )
    if json_output:
        return result
    if result.status == 'PASS':
        if not quiet:
            print(f'[PASS][{result.mode}] {spec.check_id}')
            if result.detail:
                safe_print(result.detail)
            print()
        return result
    print(f'[FAIL][{result.mode}] {spec.check_id}', file=sys.stderr)
    if result.detail:
        safe_print(result.detail, err=True)
    return result


def _run_child_batch(
    specs: Sequence[CheckSpec],
    *,
    batch_spec: CheckSpec,
    quiet: bool,
    json_output: bool,
    missing_result_detail: str,
) -> list[CheckResult]:
    """执行一个 JSON 批处理，并还原为保持原检查 ID 的结果列表。

    参数：
        specs（Sequence[CheckSpec]）：当前 lane 中需要执行的子检查定义。
        batch_spec（CheckSpec）：内部批处理命令及其时间边界。
        quiet（bool）：是否抑制通过项的人类可读输出。
        json_output（bool）：是否仅保留最终 JSON stdout。
        missing_result_detail（str）：批处理缺失子检查时使用的明确诊断。

    返回：
        list[CheckResult]：与调用方子检查顺序一致的独立结果。

    副作用：
        启动一次批处理子进程，并按输出模式写入子检查诊断。
    """
    outcome = run_command(
        batch_spec.command,
        timeout_seconds=batch_spec.timeout_seconds,
        check_id=batch_spec.check_id,
    )
    process_detail = _process_detail(outcome)
    payload: dict[str, object] | None = None
    try:
        decoded = json.loads(str(outcome.stdout or '').strip())
        if isinstance(decoded, dict):
            payload = decoded
    except json.JSONDecodeError:
        payload = None
    child_rows = {
        str(item.get('id') or ''): item
        for item in list((payload or {}).get('checks') or [])
        if isinstance(item, dict)
    }
    results: list[CheckResult] = []
    for spec in specs:
        row = child_rows.get(spec.check_id)
        if row is None or outcome.timed_out:
            result = CheckResult(
                spec.check_id,
                spec.title,
                spec.command_text,
                'FAIL',
                process_detail or missing_result_detail,
                mode=STRICT_MODE,
                lane=spec.lane,
                duration_seconds=outcome.duration_seconds,
                exit_code=outcome.exit_code,
                timed_out=outcome.timed_out,
            )
        else:
            status = 'PASS' if str(row.get('status') or '').upper() == 'PASS' else 'FAIL'
            result = CheckResult(
                spec.check_id,
                spec.title,
                spec.command_text,
                status,
                truncate_process_output(str(row.get('detail') or '')),
                mode=STRICT_MODE,
                lane=spec.lane,
                duration_seconds=float(row.get('durationSeconds') or 0.0),
                exit_code=int(row.get('exitCode') or 0),
                timed_out=bool(row.get('timedOut')),
            )
        results.append(result)
        if json_output:
            continue
        stream = sys.stderr if result.status == 'FAIL' else sys.stdout
        if result.status == 'FAIL' or not quiet:
            stream.write(f'[{result.status}][{result.mode}] {result.check_id}\n')
            if result.detail:
                stream.write(result.detail.rstrip() + '\n')
    return results


def run_documentation_batch(
    specs: Sequence[CheckSpec],
    *,
    quiet: bool,
    json_output: bool,
) -> list[CheckResult]:
    """一次执行文档 validator，并保持原文档检查 ID。

    参数：
        specs（Sequence[CheckSpec]）：当前 lane 中的文档子检查定义。
        quiet（bool）：是否抑制通过项的人类可读输出。
        json_output（bool）：是否仅保留最终 JSON stdout。

    返回：
        list[CheckResult]：与调用方文档检查顺序一致的独立结果。

    副作用：
        启动一次文档批处理子进程，并按输出模式写入诊断。
    """
    return _run_child_batch(
        specs,
        batch_spec=documentation_batch_check_spec(),
        quiet=quiet,
        json_output=json_output,
        missing_result_detail='[repo_release_gate][FAIL] 文档批处理未返回对应子检查结果。',
    )


def run_agent_governance_batch(
    specs: Sequence[CheckSpec],
    *,
    quiet: bool,
    json_output: bool,
) -> list[CheckResult]:
    """一次执行受管扩展静态治理，并保持 manifest 声明的检查 ID。

    参数：
        specs（Sequence[CheckSpec]）：当前 lane 中可共享上下文的扩展检查定义。
        quiet（bool）：是否抑制通过项的人类可读输出。
        json_output（bool）：是否仅保留最终 JSON stdout。

    返回：
        list[CheckResult]：与调用方扩展检查顺序一致的独立结果。

    副作用：
        启动一次扩展治理批处理子进程，并按输出模式写入诊断。
    """
    return _run_child_batch(
        specs,
        batch_spec=agent_governance_batch_check_spec(),
        quiet=quiet,
        json_output=json_output,
        missing_result_detail='[repo_release_gate][FAIL] 扩展治理批处理未返回对应子检查结果。',
    )


def parse_args(argv: Sequence[str]) -> tuple[bool, bool, tuple[str, ...]]:
    """解析发布门禁输出模式与可重复 lane 选择。

    参数：
        argv（Sequence[str]）：不含程序名的命令行参数。

    返回：
        tuple[bool, bool, tuple[str, ...]]：quiet、JSON 输出开关和去重后的 lane 序列。

    异常：
        SystemExit：帮助请求或参数不合法时按 CLI 约定退出。

    副作用：
        帮助请求或参数错误时由参数解析器写入对应输出流。
    """
    args = list(argv)
    if any(arg in {'-h', '--help'} for arg in args):
        print(usage())
        raise SystemExit(0)
    parser = argparse.ArgumentParser(add_help=False, prog='run_repo_release_gate')
    parser.add_argument('--quiet', action='store_true')
    parser.add_argument('--json', dest='json_output', action='store_true')
    parser.add_argument('--lane', action='append', choices=RELEASE_LANES, default=[])
    values = parser.parse_args(args)
    lanes = tuple(dict.fromkeys(str(item) for item in values.lane))
    return bool(values.quiet), bool(values.json_output), lanes


def _run_specs(
    selected_specs: Sequence[CheckSpec],
    *,
    quiet: bool,
    json_output: bool,
) -> list[CheckResult]:
    """按 lane 内声明顺序执行检查，并复用可批处理的扫描结果。

    参数：
        selected_specs（Sequence[CheckSpec]）：属于同一 lane 的有序检查定义。
        quiet（bool）：是否抑制通过项的人类可读输出。
        json_output（bool）：是否仅收集结果供最终机器报告使用。

    返回：
        list[CheckResult]：与输入检查定义顺序一致的结果。

    副作用：
        启动检查子进程；非 JSON 模式下按调用方输出设置写入诊断。
    """
    results: list[CheckResult] = []
    documentation_check_ids = set(DOCUMENTATION_BATCH_CHECK_IDS)
    documentation_specs = [
        spec for spec in selected_specs if spec.check_id in documentation_check_ids
    ]
    agent_governance_specs = [spec for spec in selected_specs if is_agent_governance_batch_spec(spec)]
    documentation_results_by_id: dict[str, CheckResult] | None = None
    agent_governance_results_by_id: dict[str, CheckResult] | None = None
    for spec in selected_specs:
        if spec.check_id in documentation_check_ids:
            if documentation_results_by_id is None:
                documentation_results_by_id = {
                    item.check_id: item
                    for item in run_documentation_batch(
                        documentation_specs,
                        quiet=quiet,
                        json_output=json_output,
                    )
                }
            results.append(documentation_results_by_id[spec.check_id])
            continue
        if is_agent_governance_batch_spec(spec):
            if agent_governance_results_by_id is None:
                agent_governance_results_by_id = {
                    item.check_id: item
                    for item in run_agent_governance_batch(
                        agent_governance_specs,
                        quiet=quiet,
                        json_output=json_output,
                    )
                }
            results.append(agent_governance_results_by_id[spec.check_id])
            continue
        if spec.check_id == GENERATED_DOCS_SYNC_CHECK_ID:
            results.append(run_generated_docs_check(quiet=quiet, json_output=json_output))
            continue
        results.append(run_check(spec, quiet=quiet, json_output=json_output))
    return results


def _emit_human_results(results: Sequence[CheckResult], *, quiet: bool) -> None:
    """在多 lane 并发结束后按声明顺序输出稳定的人类可读结果。

    参数：
        results（Sequence[CheckResult]）：已经按声明顺序重组的检查结果。
        quiet（bool）：为真时只输出失败项。

    返回：
        None：该函数只负责输出。

    副作用：
        将通过项写入 stdout，将失败状态与详情写入 stderr。
    """
    for result in results:
        if result.status == 'PASS':
            if quiet:
                continue
            print(f'==> [{result.check_id}] {result.title}')
            print(f'    {result.command_text}')
            print(f'[PASS][{result.mode}] {result.check_id}')
            if result.detail:
                safe_print(result.detail)
            print()
            continue
        if not quiet:
            print(f'==> [{result.check_id}] {result.title}')
            print(f'    {result.command_text}')
        print(f'[FAIL][{result.mode}] {result.check_id}', file=sys.stderr)
        if result.detail:
            safe_print(result.detail, err=True)


def _run_selected_specs(
    selected_specs: Sequence[CheckSpec],
    *,
    quiet: bool,
    json_output: bool,
) -> list[CheckResult]:
    """并发执行不同 lane，并按全局声明顺序重组检查结果。

    参数：
        selected_specs（Sequence[CheckSpec]）：跨 lane 的有序检查定义。
        quiet（bool）：是否抑制通过项的人类可读输出。
        json_output（bool）：是否仅生成机器可读输出。

    返回：
        list[CheckResult]：与 ``selected_specs`` 顺序严格一致的结果。

    异常：
        RuntimeError：某个 lane 未返回声明中的检查结果时抛出。

    副作用：
        多个 lane 同时启动检查子进程；每个 lane 内仍保持声明顺序。
    """
    lane_specs = {
        lane: [spec for spec in selected_specs if spec.lane == lane]
        for lane in RELEASE_LANES
    }
    active_lanes = [lane for lane in RELEASE_LANES if lane_specs[lane]]
    if len(active_lanes) <= 1:
        return _run_specs(selected_specs, quiet=quiet, json_output=json_output)

    results_by_id: dict[str, CheckResult] = {}
    with ThreadPoolExecutor(max_workers=len(active_lanes), thread_name_prefix='release-lane') as executor:
        futures = {
            lane: executor.submit(
                _run_specs,
                lane_specs[lane],
                quiet=True,
                json_output=True,
            )
            for lane in active_lanes
        }
        for lane in active_lanes:
            for result in futures[lane].result():
                results_by_id[result.check_id] = result

    missing_ids = [spec.check_id for spec in selected_specs if spec.check_id not in results_by_id]
    if missing_ids:
        raise RuntimeError(f'release lane 未返回检查结果：{", ".join(missing_ids)}')
    results = [results_by_id[spec.check_id] for spec in selected_specs]
    if not json_output:
        _emit_human_results(results, quiet=quiet)
    return results


def main(argv: Sequence[str] | None = None) -> int:
    quiet, json_output, lanes = parse_args(list(sys.argv[1:] if argv is None else argv))
    started = time.monotonic()
    selected_specs = ordered_check_specs(lanes or None)
    results = _run_selected_specs(selected_specs, quiet=quiet, json_output=json_output)
    duration_seconds = round(time.monotonic() - started, 3)

    if json_output:
        safe_print(render_json(results, duration_seconds=duration_seconds))
    else:
        print('=== repo_release_gate 汇总 ===')
        print(f"PASS: {sum(1 for item in results if item.status == 'PASS')}")
        print(f"STRICT_PASS: {sum(1 for item in results if item.status == 'PASS' and item.mode == STRICT_MODE)}")
        print(f"FAIL: {sum(1 for item in results if item.status == 'FAIL')}")
        print(f'TOTAL: {len(results)}')
        print(f'DURATION_SECONDS: {duration_seconds:.3f}')
        if results:
            slowest = max(results, key=lambda item: (item.duration_seconds, item.check_id))
            print(f'SLOWEST: {slowest.check_id} ({slowest.duration_seconds:.3f}s)')
    return 1 if any(item.status == 'FAIL' for item in results) else 0


if __name__ == '__main__':
    raise SystemExit(main())
