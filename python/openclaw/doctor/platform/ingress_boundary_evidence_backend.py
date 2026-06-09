#!/usr/bin/env python3
"""Structured helpers for ingress boundary evidence shell entrypoints."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from openclaw.doctor.platform.ingress_boundary.compose_contract import compose_contract_summary as _compose_contract_summary
from openclaw.doctor.platform.ingress_boundary.normalization import (
    dump_payload,
    normalize_source_cidrs as _normalize_source_cidrs,
)
from openclaw.doctor.platform.ingress_boundary.summary import (
    evaluate_boundary_evidence as _evaluate_boundary_evidence,
    ingress_boundary_summary as _ingress_boundary_summary,
)


def _command(*args: str) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(args, text=True, capture_output=True, check=False)
    except FileNotFoundError as exc:
        return 127, '', str(exc)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _command_key(args: tuple[str, ...]) -> str:
    return ' '.join(args)


def _snapshot_command_runner(snapshot_path: str):
    snapshot = json.loads(Path(snapshot_path).read_text(encoding='utf-8'))

    def run(*args: str) -> tuple[int, str, str]:
        row = snapshot.get(_command_key(tuple(args)))
        if not isinstance(row, dict):
            return 127, '', f'command snapshot missing: {_command_key(tuple(args))}'
        return int(row.get('rc') or 0), str(row.get('stdout') or '').strip(), str(row.get('stderr') or '').strip()

    return run


def _dump(payload: dict[str, Any]) -> int:
    return dump_payload(payload)


def normalize_source_cidrs(raw: str) -> tuple[dict[str, Any], int]:
    return _normalize_source_cidrs(raw)


def compose_contract_summary(rendered_compose_path: str, expected_ip: str, policy_path: str) -> dict[str, Any]:
    return _compose_contract_summary(rendered_compose_path, expected_ip, policy_path)


def evaluate_boundary_evidence(
    mode: str,
    expected_ip: str,
    expected_host: str,
    allowed_sources_path: str,
    policy_path: str,
    evidence_path: str = '',
    command_snapshot_path: str = '',
) -> dict[str, Any]:
    command_runner = _snapshot_command_runner(command_snapshot_path) if command_snapshot_path else _command
    return _evaluate_boundary_evidence(
        mode,
        expected_ip,
        expected_host,
        allowed_sources_path,
        policy_path,
        evidence_path,
        command_runner=command_runner,
    )


def ingress_boundary_summary(
    compose_json_path: str,
    runtime_json_path: str,
    boundary_json_path: str,
    expected_ip: str,
    policy_path: str,
) -> dict[str, Any]:
    return _ingress_boundary_summary(compose_json_path, runtime_json_path, boundary_json_path, expected_ip, policy_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='python -m openclaw.doctor.platform.ingress_boundary_evidence_backend')
    subparsers = parser.add_subparsers(dest='command', required=True)

    normalize_parser = subparsers.add_parser('normalize-source-cidrs')
    normalize_parser.add_argument('raw')

    compose_parser = subparsers.add_parser('compose-contract')
    compose_parser.add_argument('rendered_compose')
    compose_parser.add_argument('expected_ip')
    compose_parser.add_argument('policy_path')

    boundary_parser = subparsers.add_parser('boundary-evidence')
    boundary_parser.add_argument('mode')
    boundary_parser.add_argument('expected_ip')
    boundary_parser.add_argument('expected_host')
    boundary_parser.add_argument('allowed_sources_json')
    boundary_parser.add_argument('policy_path')
    boundary_parser.add_argument('evidence_path', nargs='?', default='')
    boundary_parser.add_argument('--command-snapshot', default='')

    summary_parser = subparsers.add_parser('summary')
    summary_parser.add_argument('compose_json')
    summary_parser.add_argument('runtime_json')
    summary_parser.add_argument('boundary_json')
    summary_parser.add_argument('expected_ip')
    summary_parser.add_argument('policy_path')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.command == 'normalize-source-cidrs':
        payload, exit_code = normalize_source_cidrs(args.raw)
        _dump(payload)
        return exit_code
    if args.command == 'compose-contract':
        return _dump(compose_contract_summary(args.rendered_compose, args.expected_ip, args.policy_path))
    if args.command == 'boundary-evidence':
        return _dump(
            evaluate_boundary_evidence(
                args.mode,
                args.expected_ip,
                args.expected_host,
                args.allowed_sources_json,
                args.policy_path,
                args.evidence_path,
                args.command_snapshot,
            )
        )
    if args.command == 'summary':
        return _dump(
            ingress_boundary_summary(
                args.compose_json,
                args.runtime_json,
                args.boundary_json,
                args.expected_ip,
                args.policy_path,
            )
        )
    raise SystemExit(2)


if __name__ == '__main__':
    raise SystemExit(main())
