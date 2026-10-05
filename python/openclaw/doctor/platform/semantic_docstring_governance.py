#!/usr/bin/env python3
"""仓库生产 Python 语义 docstring 治理入口。"""
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

from openclaw.lib.repo.managed_extensions import managed_explicit_extensions
from openclaw.lib.repo.repo_root import resolve_repo_root


ROOT_DIR = Path(os.environ.get('OPENCLAW_REPO_ROOT') or resolve_repo_root(Path(__file__))).resolve()
DEFAULT_BASELINE_PATH = ROOT_DIR / 'config' / 'governance' / 'validation' / 'repo_prod_semantic_docstring_baseline'
SHARD_INDEX_NAME = '_index.json'
SCOPE_PLATFORM = 'platform'
SCOPE_EXTENSIONS = 'extensions'
SCOPE_REPO_PROD = 'repo-prod'
SCOPES = (SCOPE_PLATFORM, SCOPE_EXTENSIONS, SCOPE_REPO_PROD)
MODES = ('report', 'ratchet', 'enforce')
CHINESE_TEXT_RE = re.compile(r'[\u4e00-\u9fff]')
RETURN_WORDS = ('返回', '输出', '产出', '生成')
LOW_INFORMATION_PATTERNS = (
    '待处理',
    'TODO',
    ''.join(('说明 main', ' 的用途')),
    ''.join(('返回', '结果')),
    ''.join(('返回处理', '后的文本')),
    ''.join(('返回判断', '结果')),
    ''.join(('表示', '用于')),
    ''.join(('用于', '使用')),
    ''.join(('供', '使用')),
)
LOW_INFORMATION_REGEXES = (
    re.compile(r'承载[^。；\n]*参与[^。；\n]*流程'),
    re.compile(r'在[^。；\n]*模块中[a-zA-Z0-9_]+'),
    re.compile(r'在[^。；\n]*领域层中[a-zA-Z0-9_]+'),
)
HIGH_RISK_PLATFORM_PREFIXES = (
    'python/openclaw/cli.py',
    'python/openclaw/cli_registry.py',
    'python/openclaw/control_plane/',
    'python/openclaw/docs/',
    'python/openclaw/doctor/',
    'python/openclaw/guards/',
    'python/openclaw/images/',
    'python/openclaw/internal_api/',
    'python/openclaw/lib/',
    'python/openclaw/release/',
    'python/openclaw/runtime/',
    'python/openclaw/scheduler/',
    'python/openclaw/setup/',
    'python/openclaw/specs/',
)
PLATFORM_EXCLUDED_PARTS = (
    ('python', 'openclaw', 'tests'),
    ('python', 'openclaw', 'testing'),
)
SIDE_EFFECT_CALL_MARKERS = (
    'write_text',
    'write_bytes',
    'mkdir',
    'unlink',
    'rename',
    'replace',
    'rmdir',
    'touch',
    'open',
    'append_jsonl',
    'write_json',
    'run',
    'Popen',
    'serve_forever',
    'send_response',
    'send_header',
    'end_headers',
)
SIDE_EFFECT_NAME_MARKERS = (
    'os.environ',
    'sys.stdout',
    'sys.stderr',
    'subprocess',
)


@dataclass(frozen=True)
class FunctionParam:
    """记录函数签名中的单个业务参数及其类型文本。"""

    name: str
    type_text: str


@dataclass(frozen=True)
class SemanticItem:
    """表示生产语义门禁需要检查的模块、类、函数或方法。"""

    path: str
    qualname: str
    kind: str
    line: int
    required: bool
    requirement_reason: str
    has_docstring: bool
    has_chinese_docstring: bool
    params: tuple[FunctionParam, ...] = ()
    return_type: str = ''
    requires_return_doc: bool = False
    has_raise: bool = False
    has_side_effect: bool = False
    issues: tuple[str, ...] = ()

    @property
    def baseline_key(self) -> str:
        return f'{self.kind}:{self.qualname}'

    def to_json(self) -> dict[str, Any]:
        return {
            'path': self.path,
            'qualname': self.qualname,
            'kind': self.kind,
            'line': self.line,
            'required': self.required,
            'requirementReason': self.requirement_reason,
            'hasDocstring': self.has_docstring,
            'hasChineseDocstring': self.has_chinese_docstring,
            'params': [{'name': item.name, 'typeText': item.type_text} for item in self.params],
            'returnType': self.return_type,
            'requiresReturnDoc': self.requires_return_doc,
            'hasRaise': self.has_raise,
            'hasSideEffect': self.has_side_effect,
            'issues': list(self.issues),
            'baselineKey': self.baseline_key,
        }


def _repo_rel(path: Path, repo_root: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def _has_chinese(value: str | None) -> bool:
    return bool(value and CHINESE_TEXT_RE.search(value))


def _normalize_doc(value: str | None) -> str:
    return ' '.join(str(value or '').strip().split())


def _annotation_text(node: ast.AST | None) -> str:
    if node is None:
        return ''
    try:
        return ast.unparse(node).strip()
    except Exception:
        return ''


def _is_public_name(name: str) -> bool:
    if name in {'__init__', '__enter__', '__exit__', '__call__'}:
        return True
    if name.startswith('__') and name.endswith('__'):
        return False
    return not name.startswith('_')


def _platform_file_excluded(path: Path, repo_root: Path) -> bool:
    rel_parts = Path(_repo_rel(path, repo_root)).parts
    return any(rel_parts[: len(parts)] == parts for parts in PLATFORM_EXCLUDED_PARTS)


def _platform_python_files(repo_root: Path) -> list[Path]:
    base = repo_root / 'python' / 'openclaw'
    if not base.is_dir():
        return []
    return [
        path
        for path in sorted(base.rglob('*.py'))
        if '__pycache__' not in path.parts and not _platform_file_excluded(path, repo_root)
    ]


def _extension_python_files(repo_root: Path) -> list[Path]:
    files: list[Path] = []
    for row in managed_explicit_extensions(repo_root):
        for python_root in row.python_roots:
            if not python_root.is_dir():
                continue
            files.extend(
                path
                for path in sorted(python_root.rglob('*.py'))
                if '__pycache__' not in path.parts
            )
    return sorted({path.resolve() for path in files})


def iter_scope_files(repo_root: Path, scope: str) -> list[Path]:
    if scope == SCOPE_PLATFORM:
        return _platform_python_files(repo_root)
    if scope == SCOPE_EXTENSIONS:
        return _extension_python_files(repo_root)
    if scope == SCOPE_REPO_PROD:
        return sorted({*map(Path.resolve, _platform_python_files(repo_root)), *map(Path.resolve, _extension_python_files(repo_root))})
    raise ValueError(f'未知 docstring governance scope：{scope}')


def _function_params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[FunctionParam, ...]:
    params: list[FunctionParam] = []
    args = node.args
    for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
        if arg.arg in {'self', 'cls'}:
            continue
        params.append(FunctionParam(arg.arg, _annotation_text(arg.annotation)))
    if args.vararg is not None and args.vararg.arg not in {'self', 'cls'}:
        params.append(FunctionParam(args.vararg.arg, _annotation_text(args.vararg.annotation)))
    if args.kwarg is not None and args.kwarg.arg not in {'self', 'cls'}:
        params.append(FunctionParam(args.kwarg.arg, _annotation_text(args.kwarg.annotation)))
    return tuple(params)


def _function_has_value_return(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Return) and child.value is not None:
            if isinstance(child.value, ast.Constant) and child.value.value is None:
                continue
            return True
    return False


def _function_has_raise(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(isinstance(child, ast.Raise) for child in ast.walk(node))


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f'{base}.{node.attr}' if base else node.attr
    return ''


def _function_has_side_effect(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            name = _call_name(child.func)
            if any(name.endswith(marker) or name == marker for marker in SIDE_EFFECT_CALL_MARKERS):
                return True
        if isinstance(child, (ast.Attribute, ast.Name)):
            try:
                text = ast.unparse(child)
            except Exception:
                text = ''
            if any(marker in text for marker in SIDE_EFFECT_NAME_MARKERS):
                return True
    return False


def _return_type(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    text = _annotation_text(node.returns)
    return '' if text in {'', 'None'} else text


def _requires_return_doc(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return bool(_return_type(node) or _function_has_value_return(node))


def _semantic_issues(
    *,
    doc: str | None,
    params: tuple[FunctionParam, ...] = (),
    return_type: str = '',
    requires_return_doc: bool = False,
    has_raise: bool = False,
    has_side_effect: bool = False,
) -> tuple[str, ...]:
    issues: list[str] = []
    normalized = _normalize_doc(doc)
    if not normalized:
        issues.append('missing_docstring')
        return tuple(issues)
    if not _has_chinese(normalized):
        issues.append('missing_chinese_docstring')
    for phrase in LOW_INFORMATION_PATTERNS:
        if phrase in normalized:
            issues.append(f'low_information_phrase:{phrase}')
    for pattern in LOW_INFORMATION_REGEXES:
        match = pattern.search(normalized)
        if match:
            issues.append(f'low_information_template:{match.group(0)}')
    for param in params:
        if param.name not in normalized:
            issues.append(f'missing_param_doc:{param.name}')
        if param.type_text and param.type_text not in normalized:
            issues.append(f'missing_param_type:{param.name}:{param.type_text}')
    if requires_return_doc:
        if not any(word in normalized for word in RETURN_WORDS):
            issues.append('missing_return_doc')
        if return_type and return_type not in normalized:
            issues.append(f'missing_return_type:{return_type}')
    if has_raise and '异常' not in normalized:
        issues.append('missing_exception_doc')
    if has_side_effect and '副作用' not in normalized:
        issues.append('missing_side_effect_doc')
    return tuple(issues)


def _file_is_high_risk(rel_path: str) -> bool:
    if rel_path.startswith('agent/extensions/') and '/python/' in rel_path:
        return True
    return any(rel_path == prefix or rel_path.startswith(prefix) for prefix in HIGH_RISK_PLATFORM_PREFIXES)


def _private_function_required(rel_path: str, node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[bool, str]:
    if not _file_is_high_risk(rel_path):
        return False, 'private_low_risk'
    if _function_params(node) or _requires_return_doc(node) or _function_has_raise(node) or _function_has_side_effect(node):
        return True, 'high_risk_private_with_contract'
    return False, 'private_without_contract'


def _function_item(
    *,
    rel_path: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
    force_public: bool = False,
) -> SemanticItem:
    is_public = force_public or _is_public_name(node.name)
    if is_public:
        required = True
        reason = 'public_function'
    else:
        required, reason = _private_function_required(rel_path, node)
    doc = ast.get_docstring(node)
    params = _function_params(node)
    return_type = _return_type(node)
    requires_return_doc = _requires_return_doc(node)
    has_raise = _function_has_raise(node)
    has_side_effect = _function_has_side_effect(node)
    issues = (
        _semantic_issues(
            doc=doc,
            params=params,
            return_type=return_type,
            requires_return_doc=requires_return_doc,
            has_raise=has_raise,
            has_side_effect=has_side_effect,
        )
        if required
        else ()
    )
    return SemanticItem(
        path=rel_path,
        qualname=qualname,
        kind='function',
        line=node.lineno,
        required=required,
        requirement_reason=reason,
        has_docstring=bool(doc),
        has_chinese_docstring=_has_chinese(doc),
        params=params,
        return_type=return_type,
        requires_return_doc=requires_return_doc,
        has_raise=has_raise,
        has_side_effect=has_side_effect,
        issues=issues,
    )


def collect_file_items(path: Path, repo_root: Path, *, tree: ast.Module | None = None) -> dict[str, Any]:
    rel_path = _repo_rel(path, repo_root)
    if tree is None:
        source = path.read_text(encoding='utf-8')
        tree = ast.parse(source, filename=rel_path)
    module_doc = ast.get_docstring(tree)
    items: list[SemanticItem] = [
        SemanticItem(
            path=rel_path,
            qualname='<module>',
            kind='module',
            line=1,
            required=True,
            requirement_reason='module',
            has_docstring=bool(module_doc),
            has_chinese_docstring=_has_chinese(module_doc),
            issues=_semantic_issues(doc=module_doc),
        )
    ]
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            items.append(_function_item(rel_path=rel_path, node=node, qualname=node.name))
            continue
        if not isinstance(node, ast.ClassDef):
            continue
        class_doc = ast.get_docstring(node)
        class_required = _is_public_name(node.name)
        class_issues = _semantic_issues(doc=class_doc) if class_required else ()
        items.append(
            SemanticItem(
                path=rel_path,
                qualname=node.name,
                kind='class',
                line=node.lineno,
                required=class_required,
                requirement_reason='public_class' if class_required else 'private_class',
                has_docstring=bool(class_doc),
                has_chinese_docstring=_has_chinese(class_doc),
                issues=class_issues,
            )
        )
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                items.append(
                    _function_item(
                        rel_path=rel_path,
                        node=child,
                        qualname=f'{node.name}.{child.name}',
                        force_public=_is_public_name(node.name) and _is_public_name(child.name),
                    )
                )
    required_items = [item for item in items if item.required]
    issue_items = [item for item in required_items if item.issues]
    return {
        'path': rel_path,
        'items': [item.to_json() for item in items],
        'requiredItems': len(required_items),
        'semanticIssueItems': len(issue_items),
        'semanticIssues': sum(len(item.issues) for item in issue_items),
        'moduleHasChineseDocstring': items[0].has_chinese_docstring,
        'issueItemDetails': [item.to_json() for item in issue_items[:50]],
    }


def build_report_from_parsed_files(
    repo_root: Path,
    parsed_files: dict[Path, ast.Module],
    *,
    scope: str = SCOPE_REPO_PROD,
) -> dict[str, Any]:
    """基于共享 AST 缓存构建指定生产范围的语义 docstring 报告。

    参数：
        repo_root（Path）：仓库根目录，用于生成稳定相对路径。
        parsed_files（dict[Path, ast.Module]）：文件绝对路径到已解析 AST 的映射。
        scope（str）：写入报告的生产代码范围标识。

    返回：
        dict[str, Any]：语义缺口统计、逐文件详情与生成时间。

    副作用：
        读取当前 UTC 时间写入报告元数据，不修改仓库文件。
    """
    files = [
        collect_file_items(path, repo_root, tree=tree)
        for path, tree in sorted(parsed_files.items(), key=lambda item: str(item[0]))
    ]
    summary = {
        'files': len(files),
        'requiredItems': sum(int(item['requiredItems']) for item in files),
        'semanticIssueItems': sum(int(item['semanticIssueItems']) for item in files),
        'semanticIssues': sum(int(item['semanticIssues']) for item in files),
        'moduleChineseDocstrings': sum(1 for item in files if item['moduleHasChineseDocstring']),
    }
    return {
        'schemaVersion': 2,
        'kind': 'openclaw_repo_prod_semantic_docstring_report',
        'scope': scope,
        'generatedAt': datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z'),
        'summary': summary,
        'files': files,
    }


def build_report(repo_root: Path = ROOT_DIR, *, scope: str = SCOPE_REPO_PROD) -> dict[str, Any]:
    parsed_files = {
        path: ast.parse(path.read_text(encoding='utf-8'), filename=_repo_rel(path, repo_root))
        for path in iter_scope_files(repo_root, scope)
    }
    return build_report_from_parsed_files(repo_root, parsed_files, scope=scope)


def build_baseline_payload(report: dict[str, Any]) -> dict[str, Any]:
    return {
        'schemaVersion': 2,
        'kind': 'openclaw_repo_prod_semantic_docstring_baseline',
        'policy': {
            'mode': 'semantic-baseline-ratchet',
            'scope': str(report.get('scope') or SCOPE_REPO_PROD),
            'newFilesRequireZeroSemanticIssues': True,
            'trackedItemKinds': ['module', 'class', 'function'],
        },
        'summaryMaximums': {
            'semanticIssueItems': int(report['summary']['semanticIssueItems']),
            'semanticIssues': int(report['summary']['semanticIssues']),
        },
        'fileBaselines': {
            str(item['path']): {
                'semanticIssueItems': int(item['semanticIssueItems']),
                'semanticIssues': int(item['semanticIssues']),
                'moduleHasChineseDocstring': bool(item['moduleHasChineseDocstring']),
                'itemBaselines': {
                    str(detail.get('baselineKey') or f'{detail.get("kind")}:{detail.get("qualname")}'): {
                        'kind': str(detail.get('kind') or ''),
                        'qualname': str(detail.get('qualname') or ''),
                        'issues': list(detail.get('issues') or []),
                    }
                    for detail in list(item.get('items') or [])
                    if bool(detail.get('required'))
                },
            }
            for item in report.get('files') or []
        },
    }


def baseline_shard_key(rel_path: str) -> str:
    parts = [part for part in str(rel_path).replace('\\', '/').split('/') if part]
    if len(parts) >= 3 and parts[0] == 'python' and parts[1] == 'openclaw':
        return 'platform_root' if parts[2].endswith('.py') else f'platform_{parts[2]}'
    if len(parts) >= 3 and parts[0] == 'agent' and parts[1] == 'extensions':
        return f'extension_{re.sub(r"[^A-Za-z0-9_.-]+", "_", parts[2])}'
    return 'root'


def build_sharded_baseline_payloads(report: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    monolithic = build_baseline_payload(report)
    shard_files: dict[str, dict[str, Any]] = {}
    for rel_path, file_baseline in sorted(monolithic['fileBaselines'].items()):
        shard_files.setdefault(baseline_shard_key(rel_path), {})[rel_path] = file_baseline
    shards: dict[str, dict[str, Any]] = {}
    shard_index: list[dict[str, Any]] = []
    for shard_key, file_baselines in sorted(shard_files.items()):
        shard_index.append({'key': shard_key, 'path': f'{shard_key}.json', 'fileCount': len(file_baselines)})
        shards[shard_key] = {
            'schemaVersion': 2,
            'kind': 'openclaw_repo_prod_semantic_docstring_baseline_shard',
            'shardKey': shard_key,
            'policy': dict(monolithic['policy']),
            'fileBaselines': file_baselines,
        }
    return (
        {
            'schemaVersion': 2,
            'kind': 'openclaw_repo_prod_semantic_docstring_baseline_index',
            'policy': dict(monolithic['policy']),
            'summaryMaximums': dict(monolithic['summaryMaximums']),
            'shards': shard_index,
        },
        shards,
    )


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise ValueError(f'语义 docstring 基线根节点必须是对象：{path}')
    return payload


def load_baseline(path: Path = DEFAULT_BASELINE_PATH) -> dict[str, Any]:
    if path.is_dir():
        index_path = path / SHARD_INDEX_NAME
        index_payload = _load_json(index_path)
        file_baselines: dict[str, Any] = {}
        for shard in index_payload.get('shards') or []:
            if not isinstance(shard, dict):
                raise ValueError(f'语义 docstring 分片索引存在非法项：{index_path}')
            shard_key = str(shard.get('key') or '').strip()
            shard_path = str(shard.get('path') or '').strip()
            if not shard_key or shard_path != f'{shard_key}.json':
                raise ValueError(f'语义 docstring 分片路径非法：{shard_path}')
            shard_payload = _load_json(path / shard_path)
            shard_baselines = shard_payload.get('fileBaselines')
            if not isinstance(shard_baselines, dict):
                raise ValueError(f'语义 docstring 分片缺少 fileBaselines：{path / shard_path}')
            expected_count = int(shard.get('fileCount') or -1)
            if expected_count != len(shard_baselines):
                raise ValueError(f'语义 docstring 分片 fileCount 与内容不一致：{path / shard_path}')
            duplicate = sorted(set(file_baselines).intersection(shard_baselines))
            if duplicate:
                raise ValueError(f'语义 docstring 分片存在重复文件：{duplicate[0]}')
            file_baselines.update(shard_baselines)
        return {
            'schemaVersion': 2,
            'kind': 'openclaw_repo_prod_semantic_docstring_baseline',
            'policy': dict(index_payload.get('policy') or {}),
            'summaryMaximums': dict(index_payload.get('summaryMaximums') or {}),
            'fileBaselines': file_baselines,
        }
    payload = _load_json(path)
    if int(payload.get('schemaVersion') or 0) != 2:
        raise ValueError(f'语义 docstring 基线 schemaVersion 不支持：{path}')
    return payload


def compare_with_baseline(report: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    current_by_path = {str(item['path']): item for item in report.get('files') or []}
    baseline_by_path = dict(baseline.get('fileBaselines') or {})
    for rel_path, current in sorted(current_by_path.items()):
        base = baseline_by_path.get(rel_path)
        current_issue_count = int(current['semanticIssues'])
        if base is None:
            if current_issue_count:
                issues.append(f'{rel_path} 新增生产 Python 文件存在语义 docstring 缺口：{current_issue_count}')
            continue
        if current_issue_count > int(base.get('semanticIssues') or 0):
            issues.append(f'{rel_path} semanticIssues 退化：{current_issue_count} > {base.get("semanticIssues")}')
        if int(current['semanticIssueItems']) > int(base.get('semanticIssueItems') or 0):
            issues.append(f'{rel_path} semanticIssueItems 退化：{current["semanticIssueItems"]} > {base.get("semanticIssueItems")}')
        base_items = base.get('itemBaselines') if isinstance(base.get('itemBaselines'), dict) else {}
        current_items = {
            str(detail.get('baselineKey') or f'{detail.get("kind")}:{detail.get("qualname")}'): detail
            for detail in list(current.get('items') or [])
            if bool(detail.get('required'))
        }
        for item_key, detail in sorted(current_items.items()):
            item_issues = list(detail.get('issues') or [])
            if item_key not in base_items and item_issues:
                issues.append(f'{rel_path} 新增生产语义 docstring 缺口：{detail.get("qualname")} -> {", ".join(item_issues[:6])}')
                continue
            base_item_issues = set(base_items.get(item_key, {}).get('issues') or [])
            new_item_issues = [issue for issue in item_issues if issue not in base_item_issues]
            if new_item_issues:
                issues.append(f'{rel_path} 生产语义 docstring 退化：{detail.get("qualname")} -> {", ".join(new_item_issues[:6])}')
    maximums = baseline.get('summaryMaximums') if isinstance(baseline.get('summaryMaximums'), dict) else {}
    for key in ('semanticIssueItems', 'semanticIssues'):
        allowed = int(maximums.get(key) or 0)
        current_value = int(report['summary'].get(key) or 0)
        if current_value > allowed:
            issues.append(f'生产语义 docstring 汇总 {key} 退化：{current_value} > {allowed}')
    return issues


def enforce_issues(report: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for file_row in report.get('files') or []:
        for detail in list(file_row.get('issueItemDetails') or []):
            item_issues = ', '.join(str(item) for item in list(detail.get('issues') or [])[:10])
            issues.append(f'{file_row["path"]}:{detail.get("line")} {detail.get("qualname")} -> {item_issues}')
    return issues


def issue_groups(issues: list[str]) -> dict[str, Any]:
    return {
        'newGapIssues': {'count': sum(1 for issue in issues if '新增' in issue), 'items': [issue for issue in issues if '新增' in issue]},
        'regressionIssues': {'count': sum(1 for issue in issues if '退化' in issue), 'items': [issue for issue in issues if '退化' in issue]},
        'enforceIssues': {'count': sum(1 for issue in issues if '->' in issue), 'items': [issue for issue in issues if '->' in issue][:200]},
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8', newline='\n')


def write_baseline(path: Path, report: dict[str, Any], *, format_name: str = 'auto') -> None:
    selected = 'monolithic' if format_name == 'auto' and path.suffix == '.json' else format_name
    selected = 'sharded' if selected == 'auto' else selected
    if selected == 'monolithic':
        _write_json(path, build_baseline_payload(report))
        return
    if selected != 'sharded':
        raise ValueError(f'未知语义 docstring baseline 写出格式：{format_name}')
    index_payload, shards = build_sharded_baseline_payloads(report)
    path.mkdir(parents=True, exist_ok=True)
    for existing in path.glob('*.json'):
        existing.unlink()
    _write_json(path / SHARD_INDEX_NAME, index_payload)
    for shard_key, shard_payload in shards.items():
        _write_json(path / f'{shard_key}.json', shard_payload)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='检查仓库生产 Python 中文语义 docstring 覆盖情况。')
    parser.add_argument('--repo-root', default=str(ROOT_DIR))
    parser.add_argument('--scope', choices=SCOPES, default=SCOPE_REPO_PROD)
    parser.add_argument('--mode', choices=MODES, default='enforce')
    parser.add_argument('--baseline', default=str(DEFAULT_BASELINE_PATH))
    parser.add_argument('--write-baseline', default='')
    parser.add_argument('--write-baseline-format', choices=('auto', 'monolithic', 'sharded'), default='auto')
    parser.add_argument('--json', action='store_true')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    repo_root = Path(args.repo_root).resolve()
    report = build_report(repo_root, scope=str(args.scope))
    if args.write_baseline:
        write_baseline(Path(args.write_baseline), report, format_name=str(args.write_baseline_format))
        if not args.json:
            print(f'[repo_prod_docstring_governance][OK] 已写出语义基线：{args.write_baseline}')
        return 0

    issues: list[str] = []
    if args.mode == 'ratchet':
        issues = compare_with_baseline(report, load_baseline(Path(args.baseline)))
    elif args.mode == 'enforce':
        issues = enforce_issues(report)

    payload = {
        'status': 'fail' if issues else 'ok',
        'mode': args.mode,
        'scope': args.scope,
        'summary': report['summary'],
        'issues': issues[:1000],
        'issueGroups': issue_groups(issues),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        summary = report['summary']
        print(
            '[repo_prod_docstring_governance] '
            f"scope={args.scope} files={summary['files']} requiredItems={summary['requiredItems']} "
            f"semanticIssueItems={summary['semanticIssueItems']} semanticIssues={summary['semanticIssues']}"
        )
        for issue in issues[:200]:
            print(f'[repo_prod_docstring_governance][FAIL] {issue}', file=sys.stderr)
    return 1 if issues else 0


if __name__ == '__main__':
    raise SystemExit(main())
