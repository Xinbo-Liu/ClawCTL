#!/usr/bin/env python3
"""Inventory disallowed keyword/text gates during structured-governance enforcement."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from openclaw.lib.repo.static_truth import repo_contract_path, repo_contract_root

ROOT_DIR = repo_contract_root()
DEFAULT_CONFIG_PATH = repo_contract_path('governance.keyword_gate_inventory')


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise ValueError(f'keyword gate inventory 顶层必须为对象：{path}')
    return payload


def _clean_rel_path(value: object) -> str:
    return str(value or '').strip().replace('\\', '/').lstrip('./')


def _resolve_repo_path(root_dir: Path, rel_path: str) -> Path:
    resolved = (root_dir / rel_path).resolve()
    try:
        resolved.relative_to(root_dir.resolve())
    except ValueError as exc:
        raise ValueError(f'keyword gate inventory 路径越出仓库：{rel_path}') from exc
    return resolved


def _iter_scan_files(root_dir: Path, payload: dict[str, Any]) -> tuple[Path, ...]:
    excludes = {_clean_rel_path(item) for item in payload.get('excludePaths') or [] if _clean_rel_path(item)}
    files: list[Path] = []
    seen: set[Path] = set()
    for row in payload.get('scanRoots') or []:
        if not isinstance(row, dict):
            raise ValueError('scanRoots 项必须为对象')
        rel_root = _clean_rel_path(row.get('path'))
        if not rel_root:
            raise ValueError('scanRoots.path 不能为空')
        suffixes = tuple(str(item) for item in row.get('includeSuffixes') or [])
        base = _resolve_repo_path(root_dir, rel_root)
        if not base.exists():
            continue
        glob_patterns = tuple(f'*{suffix}' for suffix in suffixes) if suffixes else ('*',)
        for glob_pattern in glob_patterns:
            for path in base.rglob(glob_pattern):
                if not path.is_file() or '__pycache__' in path.parts:
                    continue
                if path in seen:
                    continue
                rel_path = path.relative_to(root_dir).as_posix()
                if rel_path in excludes:
                    continue
                if suffixes and path.suffix not in suffixes:
                    continue
                seen.add(path)
                files.append(path)
    return tuple(sorted(files))


def _path_matches_pattern_scope(path: Path, root_dir: Path, pattern_spec: dict[str, Any]) -> bool:
    rel_path = path.relative_to(root_dir).as_posix()
    roots = tuple(_clean_rel_path(item).rstrip('/') for item in pattern_spec.get('scanRoots') or [] if _clean_rel_path(item))
    suffixes = tuple(str(item) for item in pattern_spec.get('includeSuffixes') or [])
    if roots and not any(rel_path == root or rel_path.startswith(f'{root}/') for root in roots):
        return False
    if suffixes and path.suffix not in suffixes:
        return False
    return True


def _compile_patterns(rows: Any, *, field_name: str) -> dict[str, re.Pattern[str]]:
    patterns: dict[str, re.Pattern[str]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            raise ValueError(f'{field_name} 项必须为对象')
        kind = str(row.get('kind') or '').strip()
        regex = str(row.get('regex') or '').strip()
        if not kind or not regex:
            raise ValueError(f'{field_name}.kind / regex 不能为空')
        if kind in patterns:
            raise ValueError(f'{field_name}.kind 重复：{kind}')
        patterns[kind] = re.compile(regex)
    return patterns


def _pattern_specs(rows: Any, *, field_name: str) -> dict[str, dict[str, Any]]:
    specs: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            raise ValueError(f'{field_name} 项必须为对象')
        kind = str(row.get('kind') or '').strip()
        if kind:
            specs[kind] = dict(row)
    return specs


def _current_counts_for_patterns(root_dir: Path, payload: dict[str, Any], field_name: str) -> dict[tuple[str, str], int]:
    patterns = _compile_patterns(payload.get(field_name), field_name=field_name)
    pattern_specs = _pattern_specs(payload.get(field_name), field_name=field_name)
    counts: dict[tuple[str, str], int] = {}
    if not patterns:
        return counts
    for path in _iter_scan_files(root_dir, payload):
        rel_path = path.relative_to(root_dir).as_posix()
        text = path.read_text(encoding='utf-8', errors='ignore')
        for kind, pattern in patterns.items():
            if not _path_matches_pattern_scope(path, root_dir, pattern_specs.get(kind) or {}):
                continue
            count = sum(1 for _ in pattern.finditer(text))
            if count:
                counts[(kind, rel_path)] = count
    return counts


def _current_counts_for_pattern_fields(
    root_dir: Path,
    payload: dict[str, Any],
    field_names: tuple[str, ...],
) -> dict[str, dict[tuple[str, str], int]]:
    patterns_by_field = {
        field_name: _compile_patterns(payload.get(field_name), field_name=field_name)
        for field_name in field_names
    }
    specs_by_field = {
        field_name: _pattern_specs(payload.get(field_name), field_name=field_name)
        for field_name in field_names
    }
    counts_by_field: dict[str, dict[tuple[str, str], int]] = {
        field_name: {}
        for field_name in field_names
    }
    if not any(patterns_by_field.values()):
        return counts_by_field
    for path in _iter_scan_files(root_dir, payload):
        rel_path = path.relative_to(root_dir).as_posix()
        text = path.read_text(encoding='utf-8', errors='ignore')
        for field_name, patterns in patterns_by_field.items():
            counts = counts_by_field[field_name]
            pattern_specs = specs_by_field[field_name]
            for kind, pattern in patterns.items():
                if not _path_matches_pattern_scope(path, root_dir, pattern_specs.get(kind) or {}):
                    continue
                count = sum(1 for _ in pattern.finditer(text))
                if count:
                    counts[(kind, rel_path)] = count
    return counts_by_field


def current_counts(root_dir: Path, payload: dict[str, Any]) -> dict[tuple[str, str], int]:
    return _current_counts_for_patterns(root_dir, payload, 'blockedGatePatterns')


def _entries_from_counts(counts: dict[tuple[str, str], int], pattern_specs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for kind, rel_path in sorted(counts):
        spec = pattern_specs.get(kind) or {}
        rows.append({
            'kind': kind,
            'relPath': rel_path,
            'currentCount': counts[(kind, rel_path)],
            'domain': spec.get('domain') or 'unclassified',
            'classification': spec.get('classification') or 'reported',
            'replacement': spec.get('replacement') or 'structured_governance_contract',
        })
    return rows


def _classified_report(
    root_dir: Path,
    payload: dict[str, Any],
    *,
    field_name: str,
    classification: str,
    counts: dict[tuple[str, str], int] | None = None,
) -> dict[str, Any]:
    if counts is None:
        counts = _current_counts_for_patterns(root_dir, payload, field_name)
    specs = _pattern_specs(payload.get(field_name), field_name=field_name)
    return {
        'classification': classification,
        'patternCount': len(specs),
        'entryCount': len(counts),
        'currentHitCount': sum(counts.values()),
        'entries': _entries_from_counts(counts, specs),
    }


def build_report(
    root_dir: Path = ROOT_DIR,
    config_path: Path = DEFAULT_CONFIG_PATH,
    *,
    include_informational: bool = True,
) -> dict[str, Any]:
    root_dir = root_dir.resolve()
    payload = _load_json(config_path)
    field_names = (
        ('blockedGatePatterns', 'structuredSentinelPatterns', 'behaviorAssertionPatterns')
        if include_informational
        else ('blockedGatePatterns',)
    )
    pattern_counts = _current_counts_for_pattern_fields(root_dir, payload, field_names)
    blocked_patterns = _compile_patterns(payload.get('blockedGatePatterns'), field_name='blockedGatePatterns')
    current = pattern_counts['blockedGatePatterns']
    blocked_specs = _pattern_specs(payload.get('blockedGatePatterns'), field_name='blockedGatePatterns')
    blocked_entries = _entries_from_counts(current, blocked_specs)
    blocked_current_hit_count = sum(current.values())
    mode = str(payload.get('mode') or 'enforce').strip().lower()
    blocked_report = {
        'classification': 'blocked',
        'patternCount': len(blocked_patterns),
        'entryCount': len(blocked_entries),
        'currentHitCount': blocked_current_hit_count,
        'entries': blocked_entries,
    }
    if include_informational:
        sentinel_report = _classified_report(
            root_dir,
            payload,
            field_name='structuredSentinelPatterns',
            classification='sentinel',
            counts=pattern_counts['structuredSentinelPatterns'],
        )
        behavior_report = _classified_report(
            root_dir,
            payload,
            field_name='behaviorAssertionPatterns',
            classification='behavior',
            counts=pattern_counts['behaviorAssertionPatterns'],
        )
    else:
        sentinel_report = {'classification': 'sentinel', 'patternCount': 0, 'entryCount': 0, 'currentHitCount': 0, 'entries': []}
        behavior_report = {'classification': 'behavior', 'patternCount': 0, 'entryCount': 0, 'currentHitCount': 0, 'entries': []}
    return {
        'suite': 'keyword_gate_inventory',
        'status': 'fail' if blocked_current_hit_count else 'ok',
        'mode': mode,
        'summary': {
            'schemaVersion': int(payload.get('schemaVersion') or 1),
            'patternCount': len(blocked_patterns),
            'blockedEntryCount': len(blocked_entries),
            'currentHitCount': blocked_current_hit_count,
            'sentinelEntryCount': sentinel_report['entryCount'],
            'sentinelHitCount': sentinel_report['currentHitCount'],
            'behaviorEntryCount': behavior_report['entryCount'],
            'behaviorHitCount': behavior_report['currentHitCount'],
        },
        'blocked': blocked_report,
        'sentinel': sentinel_report,
        'behavior': behavior_report,
        'blockedGates': blocked_entries,
    }


def _render_text(report: dict[str, Any]) -> str:
    summary = report['summary']
    if report['status'] == 'fail':
        headline = '[keyword_gate_inventory][FAIL] 发现禁止的静态关键词/字面量门禁：'
    else:
        headline = '[keyword_gate_inventory] 已通过；禁止的静态关键词/字面量门禁为 0。'
    lines = [
        headline,
        (
            f"[keyword_gate_inventory] mode={report['mode']} "
            f"blocked_entries={summary['blockedEntryCount']} blocked_hits={summary['currentHitCount']} "
            f"sentinel_hits={summary['sentinelHitCount']} behavior_hits={summary['behaviorHitCount']}"
        ),
    ]
    for item in report.get('blockedGates') or []:
        lines.append(
            f"- [{item['kind']}] {item['relPath']} "
            f"current={item['currentCount']} -> {item['replacement']}"
        )
    return '\n'.join(lines) + '\n'


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Inventory disallowed keyword/text gates.')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--repo-root', default=str(ROOT_DIR))
    parser.add_argument('--config', default=str(DEFAULT_CONFIG_PATH))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        report = build_report(Path(args.repo_root), Path(args.config))
    except Exception as exc:
        if args.json:
            print(json.dumps({'suite': 'keyword_gate_inventory', 'status': 'error', 'detail': str(exc)}, ensure_ascii=False))
        else:
            print(f'[keyword_gate_inventory][FAIL] {exc}', file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        stream = sys.stderr if report['status'] == 'fail' else sys.stdout
        stream.write(_render_text(report))
    return 0 if report['status'] == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
