#!/usr/bin/env python3
"""Static guard for the CentOS 7 host shell entrypoints that remain supported."""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openclaw.lib.repo.contracts import repo_contract_path
from openclaw.lib.repo.layout import resolve_repo_root

ROOT_DIR = resolve_repo_root(Path(__file__))
DEFAULT_CONFIG_PATH = repo_contract_path('governance.centos7_host_shell_guard')


@dataclass(frozen=True)
class Rule:
    section: str
    rule_id: str
    description: str
    pattern: re.Pattern[str]


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise ValueError(f'CentOS 7 host shell guard config 顶层必须为对象：{path}')
    return payload


def _clean_rel_path(value: object) -> str:
    rel_path = str(value or '').strip().replace('\\', '/').lstrip('./')
    if not rel_path or rel_path.startswith('/') or '..' in rel_path.split('/'):
        raise ValueError(f'非法仓库相对路径：{value}')
    return rel_path


def _resolve_target_paths(root_dir: Path, payload: dict[str, Any]) -> tuple[Path, ...]:
    paths: list[Path] = []
    seen: set[str] = set()
    for item in payload.get('target_paths') or []:
        rel_path = _clean_rel_path(item)
        if rel_path in seen:
            raise ValueError(f'重复 target_paths：{rel_path}')
        seen.add(rel_path)
        path = (root_dir / rel_path).resolve()
        try:
            path.relative_to(root_dir.resolve())
        except ValueError as exc:
            raise ValueError(f'target_paths 越过仓库边界：{rel_path}') from exc
        if not path.is_file():
            raise FileNotFoundError(f'target_paths 不存在：{rel_path}')
        paths.append(path)
    if not paths:
        raise ValueError('target_paths 不能为空')
    return tuple(paths)


def _compile_rules(section: str, payload: dict[str, Any]) -> tuple[Rule, ...]:
    rules: list[Rule] = []
    seen_ids: set[str] = set()
    section_payload = payload.get(section) or {}
    if not isinstance(section_payload, dict):
        raise ValueError(f'{section} 必须为对象')
    for row in section_payload.get('disallowed_patterns') or []:
        if not isinstance(row, dict):
            raise ValueError(f'{section}.disallowed_patterns 项必须为对象')
        rule_id = str(row.get('id') or '').strip()
        description = str(row.get('description') or rule_id).strip()
        regex = str(row.get('regex') or '').strip()
        if not rule_id or not regex:
            raise ValueError(f'{section}.disallowed_patterns 存在空 id/regex')
        if rule_id in seen_ids:
            raise ValueError(f'{section}.disallowed_patterns id 重复：{rule_id}')
        seen_ids.add(rule_id)
        rules.append(Rule(section, rule_id, description, re.compile(regex)))
    return tuple(rules)


def evaluate(root_dir: Path = ROOT_DIR, config_path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    root_dir = Path(root_dir).resolve()
    payload = _read_json(config_path)
    target_paths = _resolve_target_paths(root_dir, payload)
    rules = (*_compile_rules('bash', payload), *_compile_rules('jq', payload))
    findings: list[str] = []
    for path in target_paths:
        rel_path = path.relative_to(root_dir).as_posix()
        for line_number, raw_line in enumerate(path.read_text(encoding='utf-8').splitlines(), start=1):
            for rule in rules:
                if rule.pattern.search(raw_line):
                    findings.append(
                        f'{rel_path}:{line_number}: {rule.section}.{rule.rule_id}: {rule.description}: {raw_line.strip()}'
                    )
    return {
        'ok': not findings,
        'targetCount': len(target_paths),
        'ruleCount': len(rules),
        'findingCount': len(findings),
        'findings': findings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Check retained CentOS 7 host shell entrypoints.')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)

    try:
        report = evaluate(ROOT_DIR, Path(args.config))
    except Exception as exc:
        print(f'[centos7_host_shell_guard][FAIL] {exc}', file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif report['ok']:
        print(
            '[centos7_host_shell_guard] CentOS 7 宿主机入口静态检查已通过：'
            f"targets={report['targetCount']} rules={report['ruleCount']}"
        )
    else:
        print('[centos7_host_shell_guard] CentOS 7 宿主机入口静态检查失败：', file=sys.stderr)
        for item in report['findings']:
            print(f'- {item}', file=sys.stderr)
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
