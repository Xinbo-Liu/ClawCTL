#!/usr/bin/env python3
"""平台 Python 注释治理扫描器，按基线递进方式约束公共接口说明。"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT_DIR = Path(os.environ.get('OPENCLAW_REPO_ROOT') or Path(__file__).resolve().parents[4]).resolve()
DEFAULT_BASELINE_PATH = ROOT_DIR / 'config' / 'governance' / 'validation' / 'platform_python_docstring_baseline'
LEGACY_BASELINE_PATH = ROOT_DIR / 'config' / 'governance' / 'validation' / 'platform_python_docstring_baseline.json'
SHARD_INDEX_NAME = '_index.json'
DEFAULT_ROOTS = ('python/openclaw',)
DEFAULT_EXCLUDE_PARTS = (
    ('python', 'openclaw', 'tests'),
    ('python', 'openclaw', 'testing'),
)
HIGH_PRIORITY_PREFIXES = (
    'python/openclaw/control_plane/',
    'python/openclaw/setup/',
    'python/openclaw/doctor/',
    'python/openclaw/lib/repo/',
)
CHINESE_TEXT_RE = re.compile(r'[\u4e00-\u9fff]')


@dataclass(frozen=True)
class PublicItem:
    """表示一个需要新人可理解说明的模块、类、公共函数或公共方法。"""

    qualname: str
    kind: str
    lineno: int
    has_docstring: bool
    has_chinese_docstring: bool

    @property
    def baseline_key(self) -> str:
        return f'{self.kind}:{self.qualname}'

    def to_json(self) -> dict[str, Any]:
        return {
            'qualname': self.qualname,
            'kind': self.kind,
            'line': self.lineno,
            'hasDocstring': self.has_docstring,
            'hasChineseDocstring': self.has_chinese_docstring,
        }


def _repo_rel(path: Path, repo_root: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def _has_chinese(value: str | None) -> bool:
    return bool(value and CHINESE_TEXT_RE.search(value))


def _is_excluded(path: Path, repo_root: Path) -> bool:
    rel_parts = Path(_repo_rel(path, repo_root)).parts
    for excluded in DEFAULT_EXCLUDE_PARTS:
        if rel_parts[: len(excluded)] == excluded:
            return True
    return False


def iter_platform_python_files(repo_root: Path, roots: Iterable[str] = DEFAULT_ROOTS) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        root_path = (repo_root / root).resolve()
        if not root_path.is_dir():
            continue
        for path in sorted(root_path.rglob('*.py')):
            if _is_excluded(path, repo_root):
                continue
            files.append(path)
    return files


def _public_function_items(nodes: Iterable[ast.stmt], prefix: str = '') -> list[PublicItem]:
    items: list[PublicItem] = []
    for node in nodes:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith('_'):
            continue
        doc = ast.get_docstring(node)
        qualname = f'{prefix}.{node.name}' if prefix else node.name
        items.append(
            PublicItem(
                qualname=qualname,
                kind='function',
                lineno=node.lineno,
                has_docstring=bool(doc),
                has_chinese_docstring=_has_chinese(doc),
            )
        )
    return items


def collect_file_metrics(path: Path, repo_root: Path, *, tree: ast.Module | None = None) -> dict[str, Any]:
    rel_path = _repo_rel(path, repo_root)
    if tree is None:
        source = path.read_text(encoding='utf-8')
        tree = ast.parse(source, filename=rel_path)
    module_doc = ast.get_docstring(tree)
    public_items: list[PublicItem] = [
        PublicItem(
            qualname='<module>',
            kind='module',
            lineno=1,
            has_docstring=bool(module_doc),
            has_chinese_docstring=_has_chinese(module_doc),
        )
    ]
    public_items.extend(_public_function_items(tree.body))
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name.startswith('_'):
            continue
        doc = ast.get_docstring(node)
        public_items.append(
            PublicItem(
                qualname=node.name,
                kind='class',
                lineno=node.lineno,
                has_docstring=bool(doc),
                has_chinese_docstring=_has_chinese(doc),
            )
        )
        public_items.extend(_public_function_items(node.body, prefix=node.name))

    missing = [item for item in public_items if not item.has_docstring]
    english_only = [item for item in public_items if item.has_docstring and not item.has_chinese_docstring]
    chinese = [item for item in public_items if item.has_chinese_docstring]
    return {
        'path': rel_path,
        'publicItems': len(public_items),
        'publicMissingDocstrings': len(missing),
        'publicEnglishOnlyDocstrings': len(english_only),
        'publicChineseDocstrings': len(chinese),
        'moduleHasChineseDocstring': public_items[0].has_chinese_docstring,
        'publicItemDetails': [item.to_json() | {'baselineKey': item.baseline_key} for item in public_items],
        'missingItems': [item.to_json() for item in missing[:30]],
        'englishOnlyItems': [item.to_json() for item in english_only[:30]],
    }


def build_report_from_parsed_files(repo_root: Path, parsed_files: dict[Path, ast.Module]) -> dict[str, Any]:
    """基于共享 AST 缓存构建平台 docstring 覆盖报告。

    参数：
        repo_root（Path）：仓库根目录，用于生成稳定相对路径。
        parsed_files（dict[Path, ast.Module]）：文件绝对路径到已解析 AST 的映射。

    返回：
        dict[str, Any]：平台公开项覆盖统计、逐文件详情与生成时间。

    副作用：
        读取当前 UTC 时间写入报告元数据，不修改仓库文件。
    """
    files = [
        collect_file_metrics(path, repo_root, tree=tree)
        for path, tree in sorted(parsed_files.items(), key=lambda item: str(item[0]))
    ]
    summary = {
        'files': len(files),
        'publicItems': sum(int(item['publicItems']) for item in files),
        'publicMissingDocstrings': sum(int(item['publicMissingDocstrings']) for item in files),
        'publicEnglishOnlyDocstrings': sum(int(item['publicEnglishOnlyDocstrings']) for item in files),
        'publicChineseDocstrings': sum(int(item['publicChineseDocstrings']) for item in files),
        'moduleChineseDocstrings': sum(1 for item in files if item['moduleHasChineseDocstring']),
    }
    return {
        'schemaVersion': 1,
        'kind': 'openclaw_platform_docstring_report',
        'generatedAt': datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z'),
        'scope': {
            'roots': list(DEFAULT_ROOTS),
            'excluded': ['/'.join(parts) for parts in DEFAULT_EXCLUDE_PARTS],
        },
        'summary': summary,
        'files': files,
    }


def build_report(repo_root: Path = ROOT_DIR) -> dict[str, Any]:
    parsed_files = {
        path: ast.parse(path.read_text(encoding='utf-8'), filename=_repo_rel(path, repo_root))
        for path in iter_platform_python_files(repo_root)
    }
    return build_report_from_parsed_files(repo_root, parsed_files)


def build_baseline_payload(report: dict[str, Any]) -> dict[str, Any]:
    return {
        'schemaVersion': 1,
        'kind': 'openclaw_platform_docstring_baseline',
        'policy': {
            'mode': 'baseline-ratchet',
            'newFilesRequireChinesePublicDocstrings': True,
            'trackedItemKinds': ['module', 'class', 'function'],
        },
        'scope': dict(report.get('scope') or {}),
        'summaryMinimums': {
            'publicChineseDocstrings': int(report['summary']['publicChineseDocstrings']),
            'moduleChineseDocstrings': int(report['summary']['moduleChineseDocstrings']),
        },
        'fileBaselines': {
            str(item['path']): {
                'publicMissingDocstrings': int(item['publicMissingDocstrings']),
                'publicEnglishOnlyDocstrings': int(item['publicEnglishOnlyDocstrings']),
                'publicChineseDocstrings': int(item['publicChineseDocstrings']),
                'moduleHasChineseDocstring': bool(item['moduleHasChineseDocstring']),
                'publicItemBaselines': {
                    str(detail.get('baselineKey') or f'{detail.get("kind")}:{detail.get("qualname")}'): {
                        'kind': str(detail.get('kind') or ''),
                        'qualname': str(detail.get('qualname') or ''),
                        'hasDocstring': bool(detail.get('hasDocstring')),
                        'hasChineseDocstring': bool(detail.get('hasChineseDocstring')),
                    }
                    for detail in list(item.get('publicItemDetails') or [])
                },
            }
            for item in report.get('files') or []
        },
    }


def baseline_shard_key(rel_path: str) -> str:
    parts = [part for part in str(rel_path).replace('\\', '/').split('/') if part]
    if len(parts) >= 3 and parts[0] == 'python' and parts[1] == 'openclaw':
        if parts[2].endswith('.py'):
            return 'root'
        return re.sub(r'[^A-Za-z0-9_.-]+', '_', parts[2])
    return 'root'


def build_sharded_baseline_payloads(report: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    monolithic = build_baseline_payload(report)
    shard_files: dict[str, dict[str, Any]] = {}
    for rel_path, file_baseline in sorted(monolithic['fileBaselines'].items()):
        shard_key = baseline_shard_key(rel_path)
        shard_files.setdefault(shard_key, {})[rel_path] = file_baseline

    shards: dict[str, dict[str, Any]] = {}
    shard_index: list[dict[str, Any]] = []
    for shard_key, file_baselines in sorted(shard_files.items()):
        shard_path = f'{shard_key}.json'
        shard_index.append({'key': shard_key, 'path': shard_path, 'fileCount': len(file_baselines)})
        shards[shard_key] = {
            'schemaVersion': 1,
            'kind': 'openclaw_platform_docstring_baseline_shard',
            'shardKey': shard_key,
            'policy': dict(monolithic['policy']),
            'scope': dict(monolithic['scope']),
            'fileBaselines': file_baselines,
        }
    index_payload = {
        'schemaVersion': 1,
        'kind': 'openclaw_platform_docstring_baseline_index',
        'policy': dict(monolithic['policy']),
        'scope': dict(monolithic['scope']),
        'summaryMinimums': dict(monolithic['summaryMinimums']),
        'shards': shard_index,
    }
    return index_payload, shards


def load_baseline(path: Path = DEFAULT_BASELINE_PATH) -> dict[str, Any]:
    if path.is_dir():
        return load_sharded_baseline(path)
    return load_monolithic_baseline(path)


def load_monolithic_baseline(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding='utf-8'))
    if int(payload.get('schemaVersion') or 0) != 1:
        raise ValueError(f'平台注释治理基线 schemaVersion 不支持：{path}')
    file_baselines = payload.get('fileBaselines')
    if not isinstance(file_baselines, dict):
        raise ValueError(f'平台注释治理基线缺少 fileBaselines：{path}')
    return payload


def load_sharded_baseline(path: Path) -> dict[str, Any]:
    index_path = path / SHARD_INDEX_NAME
    index_payload = json.loads(index_path.read_text(encoding='utf-8'))
    if int(index_payload.get('schemaVersion') or 0) != 1:
        raise ValueError(f'平台注释治理分片索引 schemaVersion 不支持：{index_path}')
    shards = index_payload.get('shards')
    if not isinstance(shards, list):
        raise ValueError(f'平台注释治理分片索引缺少 shards：{index_path}')
    file_baselines: dict[str, Any] = {}
    for shard in shards:
        if not isinstance(shard, dict):
            raise ValueError(f'平台注释治理分片索引存在非法 shard：{index_path}')
        shard_key = str(shard.get('key') or '').strip()
        if not re.match(r'^[A-Za-z0-9_.-]+$', shard_key):
            raise ValueError(f'平台注释治理分片 key 非法：{shard_key}')
        shard_rel = str(shard.get('path') or '').strip()
        if not shard_rel or '/' in shard_rel or '\\' in shard_rel or shard_rel.startswith('.'):
            raise ValueError(f'平台注释治理分片路径非法：{shard_rel}')
        if shard_rel != f'{shard_key}.json':
            raise ValueError(f'平台注释治理分片路径与 key 不一致：{shard_rel}')
        shard_payload = json.loads((path / shard_rel).read_text(encoding='utf-8'))
        if int(shard_payload.get('schemaVersion') or 0) != 1:
            raise ValueError(f'平台注释治理分片 schemaVersion 不支持：{path / shard_rel}')
        if str(shard_payload.get('shardKey') or '').strip() != shard_key:
            raise ValueError(f'平台注释治理分片 shardKey 与索引不一致：{path / shard_rel}')
        shard_baselines = shard_payload.get('fileBaselines')
        if not isinstance(shard_baselines, dict):
            raise ValueError(f'平台注释治理分片缺少 fileBaselines：{path / shard_rel}')
        expected_file_count = int(shard.get('fileCount') or -1)
        if expected_file_count != len(shard_baselines):
            raise ValueError(f'平台注释治理分片 fileCount 与内容不一致：{path / shard_rel}')
        duplicate_paths = sorted(set(file_baselines).intersection(shard_baselines))
        if duplicate_paths:
            raise ValueError(f'平台注释治理分片存在重复文件基线：{duplicate_paths[0]}')
        file_baselines.update(shard_baselines)
    return {
        'schemaVersion': 1,
        'kind': 'openclaw_platform_docstring_baseline',
        'policy': dict(index_payload.get('policy') or {}),
        'scope': dict(index_payload.get('scope') or {}),
        'summaryMinimums': dict(index_payload.get('summaryMinimums') or {}),
        'fileBaselines': file_baselines,
    }


def compare_with_baseline(report: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    current_by_path = {str(item['path']): item for item in report.get('files') or []}
    baseline_by_path = dict(baseline.get('fileBaselines') or {})
    for rel_path, current in sorted(current_by_path.items()):
        base = baseline_by_path.get(rel_path)
        if base is None:
            if (
                int(current['publicMissingDocstrings']) > 0
                or int(current['publicEnglishOnlyDocstrings']) > 0
                or not bool(current['moduleHasChineseDocstring'])
            ):
                issues.append(f'新增平台 Python 文件缺少中文公共接口说明：{rel_path}')
            continue
        if int(current['publicMissingDocstrings']) > int(base.get('publicMissingDocstrings') or 0):
            issues.append(f'{rel_path} publicMissingDocstrings 退化：{current["publicMissingDocstrings"]} > {base.get("publicMissingDocstrings")}')
        if int(current['publicEnglishOnlyDocstrings']) > int(base.get('publicEnglishOnlyDocstrings') or 0):
            issues.append(f'{rel_path} publicEnglishOnlyDocstrings 退化：{current["publicEnglishOnlyDocstrings"]} > {base.get("publicEnglishOnlyDocstrings")}')
        if int(current['publicChineseDocstrings']) < int(base.get('publicChineseDocstrings') or 0):
            issues.append(f'{rel_path} publicChineseDocstrings 退化：{current["publicChineseDocstrings"]} < {base.get("publicChineseDocstrings")}')
        if bool(base.get('moduleHasChineseDocstring')) and not bool(current['moduleHasChineseDocstring']):
            issues.append(f'{rel_path} 模块中文 docstring 退化为缺失或英文-only')
        base_items = base.get('publicItemBaselines') if isinstance(base.get('publicItemBaselines'), dict) else {}
        current_items = {
            str(detail.get('baselineKey') or f'{detail.get("kind")}:{detail.get("qualname")}'): detail
            for detail in list(current.get('publicItemDetails') or [])
        }
        for item_key, detail in sorted(current_items.items()):
            base_detail = base_items.get(item_key)
            label = str(detail.get('qualname') or item_key)
            if base_detail is None:
                if not bool(detail.get('hasChineseDocstring')):
                    issues.append(f'{rel_path} 新增公共接口缺少中文 docstring：{label}')
                continue
            if bool(base_detail.get('hasChineseDocstring')) and not bool(detail.get('hasChineseDocstring')):
                issues.append(f'{rel_path} 公共接口中文 docstring 退化：{label}')

    minimums = baseline.get('summaryMinimums') if isinstance(baseline.get('summaryMinimums'), dict) else {}
    for key in ('publicChineseDocstrings', 'moduleChineseDocstrings'):
        required = int(minimums.get(key) or 0)
        current_value = int(report['summary'].get(key) or 0)
        if current_value < required:
            issues.append(f'平台注释汇总 {key} 退化：{current_value} < {required}')
    return issues


def _issue_path(issue: str) -> str:
    for prefix in HIGH_PRIORITY_PREFIXES:
        index = issue.find(prefix)
        if index >= 0:
            tail = issue[index:]
            return tail.split()[0].rstrip('：:')
    if issue.startswith('python/'):
        return issue.split()[0].rstrip('：:')
    if '：python/' in issue:
        return issue.split('：', 1)[1].split()[0].rstrip('：:')
    return ''


def issue_groups(issues: list[str]) -> dict[str, Any]:
    new_gap_issues = [issue for issue in issues if '新增' in issue]
    high_priority_issues = [
        issue
        for issue in issues
        if any(_issue_path(issue).startswith(prefix) for prefix in HIGH_PRIORITY_PREFIXES)
    ]
    return {
        'newGapIssues': {'count': len(new_gap_issues), 'items': new_gap_issues},
        'highPriorityIssues': {'count': len(high_priority_issues), 'items': high_priority_issues},
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8', newline='\n')


def write_baseline(path: Path, report: dict[str, Any], *, format_name: str = 'auto') -> None:
    selected_format = format_name
    if selected_format == 'auto':
        selected_format = 'monolithic' if path.suffix == '.json' else 'sharded'
    if selected_format == 'monolithic':
        _write_json(path, build_baseline_payload(report))
        return
    if selected_format != 'sharded':
        raise ValueError(f'未知 baseline 写出格式：{format_name}')
    index_payload, shard_payloads = build_sharded_baseline_payloads(report)
    path.mkdir(parents=True, exist_ok=True)
    for existing in path.glob('*.json'):
        existing.unlink()
    _write_json(path / SHARD_INDEX_NAME, index_payload)
    for shard_key, shard_payload in shard_payloads.items():
        _write_json(path / f'{shard_key}.json', shard_payload)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='检查平台 Python 公共接口中文 docstring 覆盖基线。')
    parser.add_argument('--repo-root', default=str(ROOT_DIR))
    parser.add_argument('--baseline', default=str(DEFAULT_BASELINE_PATH))
    parser.add_argument('--write-baseline', default='')
    parser.add_argument('--write-baseline-format', choices=('auto', 'monolithic', 'sharded'), default='auto')
    parser.add_argument('--mode', choices=('report', 'ratchet'), default='ratchet')
    parser.add_argument('--json', action='store_true')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    repo_root = Path(args.repo_root).resolve()
    report = build_report(repo_root)
    if args.write_baseline:
        write_baseline(Path(args.write_baseline), report, format_name=str(args.write_baseline_format))
        if not args.json:
            print(f'[platform_docstring_governance][OK] 已写出基线：{args.write_baseline}')
        return 0

    issues: list[str] = []
    if args.mode == 'ratchet':
        baseline = load_baseline(Path(args.baseline))
        issues = compare_with_baseline(report, baseline)

    payload = {
        'status': 'fail' if issues else 'ok',
        'mode': args.mode,
        'summary': report['summary'],
        'issues': issues,
        'issueGroups': issue_groups(issues),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        summary = report['summary']
        print(
            '[platform_docstring_governance] '
            f"files={summary['files']} publicItems={summary['publicItems']} "
            f"missing={summary['publicMissingDocstrings']} englishOnly={summary['publicEnglishOnlyDocstrings']} "
            f"chinese={summary['publicChineseDocstrings']}"
        )
        groups = issue_groups(issues)
        print(
            '[platform_docstring_governance] '
            f"newGapIssues={groups['newGapIssues']['count']} "
            f"highPriorityIssues={groups['highPriorityIssues']['count']}"
        )
        for issue in issues:
            print(f'[platform_docstring_governance][FAIL] {issue}', file=sys.stderr)
    return 1 if issues else 0


if __name__ == '__main__':
    raise SystemExit(main())
