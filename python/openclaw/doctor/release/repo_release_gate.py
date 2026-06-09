#!/usr/bin/env python3
"""Cross-platform repo release gate entrypoint."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Sequence

from openclaw.doctor.release.repo_release_gate_support import (
    CheckResult,
    CheckSpec,
    GENERATED_DOCS_SYNC_CHECK_ID,
    ROOT_DIR,
    STRICT_MODE,
    base_checks,
    generated_docs_check_spec,
    generated_docs_steps,
    ordered_check_specs,
    render_json,
    safe_print,
    usage,
)
from openclaw.lib.cli import CliError, FlagSpec, parse_typed_flag_args
from openclaw.lib.runtime.execution import build_subprocess_env


def python_env() -> dict[str, str]:
    return build_subprocess_env(Path(__file__), base_env=os.environ)


def run_command(command: Sequence[str], *, extra_env: Mapping[str, str] | None = None) -> tuple[int, str]:
    env = python_env()
    if extra_env:
        env.update({str(key): str(value) for key, value in extra_env.items()})
    completed = subprocess.run(
        list(command),
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    stdout = str(completed.stdout or '').strip()
    stderr = str(completed.stderr or '').strip()
    detail = '\n'.join(part for part in [stdout, stderr] if part).strip()
    return completed.returncode, detail


def run_check(
    spec: CheckSpec,
    quiet: bool,
    json_output: bool,
) -> CheckResult:
    if not quiet and not json_output:
        print(f'==> [{spec.check_id}] {spec.title}')
        print(f'    {spec.command_text}')

    returncode, detail = run_command(spec.command)
    status = 'PASS' if returncode == 0 else 'FAIL'
    result = CheckResult(spec.check_id, spec.title, spec.command_text, status, detail, mode=STRICT_MODE)
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
    for label, command in generated_docs_steps():
        returncode, detail = run_command(command)
        details.append(f'[{label}]' if not detail else f'[{label}]\n{detail}')
        if returncode != 0:
            failed = True

    result = CheckResult(
        spec.check_id,
        spec.title,
        spec.command_text,
        'FAIL' if failed else 'PASS',
        '\n'.join(details).strip(),
        mode=STRICT_MODE,
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


def parse_args(argv: Sequence[str]) -> tuple[bool, bool]:
    args = list(argv)
    if any(arg in {'-h', '--help'} for arg in args):
        print(usage())
        raise SystemExit(0)
    try:
        values, _ = parse_typed_flag_args(
            args,
            specs={
                'quiet': FlagSpec(kind='bool', default=False),
                'json': FlagSpec(kind='bool', dest='json_output', default=False),
            },
            allow_positionals=False,
        )
    except CliError as exc:
        print(f'[repo_release_gate][FAIL] {exc}', file=sys.stderr)
        print(usage(), file=sys.stderr)
        raise SystemExit(exc.exit_code) from exc
    return bool(values['quiet']), bool(values['json_output'])


def main(argv: Sequence[str] | None = None) -> int:
    quiet, json_output = parse_args(list(sys.argv[1:] if argv is None else argv))
    results: list[CheckResult] = []
    for spec in ordered_check_specs():
        if spec.check_id == GENERATED_DOCS_SYNC_CHECK_ID:
            results.append(run_generated_docs_check(quiet=quiet, json_output=json_output))
            continue
        results.append(run_check(spec, quiet=quiet, json_output=json_output))

    if json_output:
        safe_print(render_json(results))
    else:
        print('=== repo_release_gate 汇总 ===')
        print(f"PASS: {sum(1 for item in results if item.status == 'PASS')}")
        print(f"STRICT_PASS: {sum(1 for item in results if item.status == 'PASS' and item.mode == STRICT_MODE)}")
        print(f"FAIL: {sum(1 for item in results if item.status == 'FAIL')}")
        print(f'TOTAL: {len(results)}')
    return 1 if any(item.status == 'FAIL' for item in results) else 0


if __name__ == '__main__':
    raise SystemExit(main())
