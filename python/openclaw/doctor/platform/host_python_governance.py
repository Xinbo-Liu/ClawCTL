#!/usr/bin/env python3
"""Host Python governance scanner and report renderer."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from openclaw.guards.host_python.scanner import scan_shell_source
from openclaw.guards.host_python_doc_guard import collect_files, uncovered_doc_python_commands
from openclaw.lib.repo.static_truth import repo_contract_path, repo_contract_root

ROOT_DIR = repo_contract_root()
CONFIG_PATH = repo_contract_path('governance.host_python_governance')
CATEGORIES = (
    'shell_exec',
    'shell_indirect_exec',
    'doc_example',
    'generated_doc_example',
    'extension_doc_example',
)


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise ValueError(f'JSON 顶层必须为对象：{path}')
    return payload


def _resolve_path(repo_root: Path, raw: str | os.PathLike[str]) -> Path:
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (repo_root / path).resolve()


def _normalize_rel_path(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _signature(path: str, text: str) -> str:
    return f'{path}::{text}'


def _posix_ere_to_python(pattern: str) -> str:
    return (
        pattern
        .replace('[:space:]', r'\s')
        .replace('[:alnum:]', 'A-Za-z0-9')
    )


def _manifest_prefixes(payload: dict[str, Any], *kinds: str) -> tuple[str, ...]:
    manifest = payload.get('shell_scan_manifest')
    if not isinstance(manifest, dict):
        return ()
    prefixes: list[str] = []
    for kind in kinds:
        raw_items = manifest.get(kind)
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            rel_path = str(item or '').strip().replace('\\', '/').lstrip('./')
            if rel_path:
                prefixes.append(rel_path.rstrip('/'))
    return tuple(prefixes)


def _matches_prefix(rel_path: str, prefixes: tuple[str, ...]) -> bool:
    return any(rel_path == prefix or rel_path.startswith(f'{prefix}/') for prefix in prefixes)


def collect_shell_targets(repo_root: Path, payload: dict[str, Any]) -> tuple[Path, ...]:
    skip_prefixes = _manifest_prefixes(payload, 'self', 'skip')
    targets: list[Path] = []
    for path in sorted(repo_root.rglob('*.sh')):
        if '.git' in path.parts:
            continue
        rel_path = _normalize_rel_path(path, repo_root)
        if _matches_prefix(rel_path, skip_prefixes):
            continue
        targets.append(path)
    return tuple(targets)


def _doc_scan_roots(payload: dict[str, Any]) -> list[str]:
    raw = payload.get('doc_scan_roots')
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _category_prefixes(payload: dict[str, Any], category: str) -> tuple[str, ...]:
    roots = payload.get('doc_category_roots')
    if not isinstance(roots, dict):
        return ()
    raw_items = roots.get(category)
    if not isinstance(raw_items, list):
        return ()
    return tuple(str(item).strip().replace('\\', '/').lstrip('./').rstrip('/') for item in raw_items if str(item).strip())


def _doc_allowed_families(payload: dict[str, Any]) -> tuple[str, ...]:
    policy = payload.get('doc_command_policy')
    raw_items = policy.get('allowed_families') if isinstance(policy, dict) else None
    if not isinstance(raw_items, list):
        return ('repo_host', 'unittest_openclaw')
    return tuple(str(item).strip() for item in raw_items if str(item).strip())


def doc_category_for_path(rel_path: str, payload: dict[str, Any]) -> str:
    if _matches_prefix(rel_path, _category_prefixes(payload, 'generated_doc_example')):
        return 'generated_doc_example'
    if _matches_prefix(rel_path, _category_prefixes(payload, 'extension_doc_example')):
        return 'extension_doc_example'
    return 'doc_example'


def _append_unique(violations: list[dict[str, object]], seen: set[tuple[str, str]], item: dict[str, object]) -> None:
    category = str(item['category'])
    signature = str(item['signature'])
    key = (category, signature)
    if key in seen:
        return
    seen.add(key)
    violations.append(item)


def scan_shell_exec(repo_root: Path, payload: dict[str, Any], seen: set[tuple[str, str]]) -> list[dict[str, object]]:
    violations: list[dict[str, object]] = []
    for path in collect_shell_targets(repo_root, payload):
        rel_path = _normalize_rel_path(path, repo_root)
        for raw in scan_shell_source(path.read_text(encoding='utf-8'), rel_path):
            _, line_text = raw.split(':', 1)
            line_no_raw, text = line_text.split(':', 1)
            item = {
                'category': 'shell_exec',
                'path': rel_path,
                'line': int(line_no_raw or '0'),
                'text': text,
                'signature': _signature(rel_path, text),
            }
            _append_unique(violations, seen, item)
    return violations


def scan_shell_indirect(repo_root: Path, payload: dict[str, Any], seen: set[tuple[str, str]]) -> list[dict[str, object]]:
    raw_patterns = payload.get('shell_indirect_patterns')
    if not isinstance(raw_patterns, list):
        return []
    patterns = [
        re.compile(_posix_ere_to_python(str(item.get('pattern') or '')))
        for item in raw_patterns
        if isinstance(item, dict) and str(item.get('pattern') or '').strip()
    ]
    if not patterns:
        return []
    violations: list[dict[str, object]] = []
    for path in collect_shell_targets(repo_root, payload):
        rel_path = _normalize_rel_path(path, repo_root)
        for line_no, line in enumerate(path.read_text(encoding='utf-8').splitlines(), start=1):
            if not any(pattern.search(line) for pattern in patterns):
                continue
            text = line.strip()
            item = {
                'category': 'shell_indirect_exec',
                'path': rel_path,
                'line': line_no,
                'text': text,
                'signature': _signature(rel_path, text),
            }
            _append_unique(violations, seen, item)
    return violations


def scan_doc_examples(repo_root: Path, payload: dict[str, Any], seen: set[tuple[str, str]]) -> list[dict[str, object]]:
    violations: list[dict[str, object]] = []
    allowed_families = _doc_allowed_families(payload)
    for path in collect_files(repo_root, _doc_scan_roots(payload)):
        rel_path = _normalize_rel_path(path, repo_root)
        category = doc_category_for_path(rel_path, payload)
        for line_no, line in enumerate(path.read_text(encoding='utf-8').splitlines(), start=1):
            for command in uncovered_doc_python_commands(line, allowed_families=allowed_families):
                text = command.text
                item = {
                    'category': category,
                    'path': rel_path,
                    'line': line_no,
                    'text': text,
                    'signature': _signature(rel_path, text),
                }
                _append_unique(violations, seen, item)
    return violations


def _baseline_path(repo_root: Path, config: dict[str, Any], override: Path | None) -> Path:
    if override is not None:
        return _resolve_path(repo_root, override)
    raw_path = str(config.get('baselinePath') or '').strip()
    if not raw_path:
        raise ValueError('host python governance 真源缺少 baselinePath')
    return _resolve_path(repo_root, raw_path)


def _baseline_signatures(payload: dict[str, Any]) -> dict[str, set[str]]:
    categories = payload.get('categories')
    if not isinstance(categories, dict):
        return {category: set() for category in CATEGORIES}
    result: dict[str, set[str]] = {}
    for category in CATEGORIES:
        raw_items = categories.get(category)
        result[category] = {str(item).strip() for item in raw_items if str(item).strip()} if isinstance(raw_items, list) else set()
    return result


def build_report(
    repo_root: Path = ROOT_DIR,
    *,
    config_path: Path | None = None,
    baseline_path: Path | None = None,
    mode: str | None = None,
) -> dict[str, object]:
    repo_root = repo_root.resolve()
    config_path = (config_path or CONFIG_PATH).resolve()
    config = _load_json(config_path)
    active_mode = str(mode or config.get('mode') or 'observe').strip()
    if active_mode not in {'observe', 'enforce'}:
        raise ValueError(f'host python governance mode 非法：{active_mode}')
    baseline = _load_json(_baseline_path(repo_root, config, baseline_path))
    baseline_by_category = _baseline_signatures(baseline)
    seen: set[tuple[str, str]] = set()
    violations: list[dict[str, object]] = []
    violations.extend(scan_shell_exec(repo_root, config, seen))
    violations.extend(scan_shell_indirect(repo_root, config, seen))
    violations.extend(scan_doc_examples(repo_root, config, seen))

    new_violations = [
        item for item in violations
        if active_mode == 'enforce' or str(item['signature']) not in baseline_by_category.get(str(item['category']), set())
    ]
    summary_by_category = {
        category: {
            'current': sum(1 for item in violations if item['category'] == category),
            'baseline': len(baseline_by_category.get(category, set())),
        }
        for category in CATEGORIES
    }
    return {
        'suite': 'host_python_governance',
        'status': 'fail' if new_violations else 'ok',
        'mode': active_mode,
        'baselineStatus': 'drift' if new_violations else 'ok',
        'summary': {
            'currentCount': len(violations),
            'baselineCount': sum(len(items) for items in baseline_by_category.values()),
            'newCount': len(new_violations),
            'byCategory': summary_by_category,
        },
        'violations': violations,
        'newViolations': new_violations,
    }


def _usage() -> str:
    return '\n'.join([
        '用法：',
        '  bash ./scripts/doctor/check_host_python_governance.sh [--json]',
        '',
        '内部诊断参数：',
        '  --repo-root <path>',
        '  --config <path>',
        '  --baseline <path>',
        '  --mode <observe|enforce>',
    ])


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--repo-root', default=os.environ.get('OPENCLAW_REPO_ROOT') or str(ROOT_DIR))
    parser.add_argument('--config', default=os.environ.get('OPENCLAW_HOST_PYTHON_GOVERNANCE_CONFIG') or str(CONFIG_PATH))
    parser.add_argument('--baseline', default=os.environ.get('OPENCLAW_HOST_PYTHON_GOVERNANCE_BASELINE') or None)
    parser.add_argument('--mode', default=os.environ.get('OPENCLAW_HOST_PYTHON_GOVERNANCE_MODE') or None)
    parser.add_argument('-h', '--help', action='store_true')
    args, unknown = parser.parse_known_args(argv)
    if unknown:
        raise ValueError(f'未知参数：{unknown[0]}')
    return args


def _render_text(payload: dict[str, object]) -> str:
    summary = payload['summary']
    assert isinstance(summary, dict)
    by_category = summary['byCategory']
    assert isinstance(by_category, dict)
    lines: list[str] = []
    if payload['status'] == 'fail':
        lines.append('[check_host_python_governance][FAIL] 发现未收口的宿主机 Python 面：')
        for item in payload['newViolations']:
            assert isinstance(item, dict)
            lines.append(f"- [{item['category']}] {item['path']}:{item['line']} :: {item['text']}")
    elif payload['mode'] == 'observe' and int(summary['currentCount']) > 0:
        lines.append('[check_host_python_governance] 已通过；当前命中均已登记为观察基线。')
    else:
        lines.append('[check_host_python_governance] 已通过；未发现宿主机 Python 暴露面。')
    lines.append(
        f"[check_host_python_governance] mode={payload['mode']} "
        f"current={summary['currentCount']} baseline={summary['baselineCount']} new={summary['newCount']}"
    )
    for category in CATEGORIES:
        counts = by_category[category]
        lines.append(f"- [{category}] current={counts['current']} baseline={counts['baseline']}")
    return '\n'.join(lines) + '\n'


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(list(sys.argv[1:] if argv is None else argv))
        if args.help:
            sys.stdout.write(_usage() + '\n')
            return 0
        payload = build_report(
            Path(args.repo_root),
            config_path=Path(args.config),
            baseline_path=Path(args.baseline) if args.baseline else None,
            mode=args.mode,
        )
    except Exception as exc:
        if '--json' in (argv if argv is not None else sys.argv[1:]):
            print(json.dumps({'suite': 'host_python_governance', 'status': 'error', 'detail': str(exc)}, ensure_ascii=False))
        else:
            print(f'[check_host_python_governance][FAIL] {exc}', file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        output = _render_text(payload)
        if payload['status'] == 'fail':
            sys.stderr.write(output)
        else:
            sys.stdout.write(output)
    return 0 if payload['status'] == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
