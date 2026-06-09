#!/usr/bin/env python3
"""Runtime validation helpers for bundle governance."""
from __future__ import annotations

import locale
import os
import subprocess
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Callable


ErrorFactory = Callable[[str], Exception]


def _raise(error_factory: ErrorFactory, message: str) -> None:
    raise error_factory(message)


def _normalize_artifact_smoke_steps(bundle_id: str, spec: dict[str, Any], *, error_factory: ErrorFactory) -> list[dict[str, Any]]:
    """规范化 artifact smoke 步骤。"""
    rows = spec.get('artifactSmoke')
    if rows in (None, ''):
        return []
    if not isinstance(rows, list):
        _raise(error_factory, f'bundle {bundle_id} 的 artifactSmoke 必须为数组')
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            _raise(error_factory, f'bundle {bundle_id} 的 artifactSmoke[{idx}] 必须为对象')
        step_id = str(row.get('id') or f'step_{idx + 1}').strip()
        if not step_id:
            _raise(error_factory, f'bundle {bundle_id} 的 artifactSmoke[{idx}] 缺少 id')
        if step_id in seen_ids:
            _raise(error_factory, f'bundle {bundle_id} 的 artifactSmoke id 重复：{step_id}')
        seen_ids.add(step_id)
        command = row.get('command')
        if not isinstance(command, list) or not command:
            _raise(error_factory, f'bundle {bundle_id} 的 artifactSmoke[{idx}].command 必须为非空数组')
        normalized_command = [str(item or '').strip() for item in command]
        if any(not item for item in normalized_command):
            _raise(error_factory, f'bundle {bundle_id} 的 artifactSmoke[{idx}].command 不允许空参数')
        cwd = str(row.get('cwd') or '.').strip() or '.'
        env_payload = row.get('env') if isinstance(row.get('env'), dict) else {}
        env_map = {str(key).strip(): str(value) for key, value in env_payload.items() if str(key).strip()}
        normalized.append({'id': step_id, 'command': normalized_command, 'cwd': cwd, 'env': env_map})
    return normalized


def _render_artifact_smoke_value(value: str, *, artifact_root: Path, artifact_state_root: Path) -> str:
    """渲染 artifact smoke 参数值。"""
    return str(value).format(artifact_root=str(artifact_root), artifact_state_root=str(artifact_state_root), python=os.sys.executable)


def _output_snippet(text: str, *, limit: int = 4000, tail: int = 1200) -> str:
    """截取输出片段用于错误展示。"""
    normalized = str(text or '').strip()
    if len(normalized) <= limit:
        return normalized
    head_budget = max(0, limit - tail - 32)
    head = normalized[:head_budget].rstrip()
    tail_text = normalized[-tail:].lstrip() if tail > 0 else ''
    omitted = max(0, len(normalized) - len(head) - len(tail_text))
    marker = f' ...<truncated {omitted} chars>... '
    return (head + marker + tail_text).strip()


def _decode_subprocess_output(data: bytes | None) -> str:
    """解码子进程输出。"""
    if not data:
        return ''
    preferred = locale.getpreferredencoding(False) or 'utf-8'
    if data.startswith((b'\xff\xfe', b'\xfe\xff')) or b'\x00' in data[:4]:
        encodings = ['utf-16', 'utf-16-le', 'utf-16-be', 'utf-8', 'utf-8-sig', preferred, 'gbk']
    else:
        encodings = ['utf-8', 'utf-8-sig', preferred, 'gbk', 'utf-16', 'utf-16-le', 'utf-16-be']
    seen: set[str] = set()
    for encoding in encodings:
        normalized = str(encoding or '').strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode('utf-8', errors='replace')


def _prepare_artifact_state_root(artifact_state_root: Path) -> None:
    """创建 artifact smoke 使用的最小运行态 state 骨架。"""
    for rel_path in (
        'tmp',
        'setup',
        'control_plane/dispatch',
        'control_plane/setup/official_cli',
        'control_plane/release/evidence',
    ):
        (artifact_state_root / rel_path).mkdir(parents=True, exist_ok=True)


def artifact_smoke_failures(results: list[dict[str, Any]]) -> list[str]:
    """收集 artifact smoke 失败项。"""
    return [f"artifact smoke 失败：{row.get('id')} (exit={row.get('returncode')})" for row in results if int(row.get('returncode') or 0) != 0]


def run_artifact_smoke(
    bundle_id: str,
    spec: dict[str, Any],
    zip_path: Path,
    *,
    artifact_smoke_active_env: str,
    error_factory: ErrorFactory,
) -> list[dict[str, Any]]:
    """执行 bundle 的 artifact smoke 校验。"""
    if str(os.environ.get(artifact_smoke_active_env) or '').strip().lower() in {'1', 'true', 'yes', 'on'}:
        return []
    steps = _normalize_artifact_smoke_steps(bundle_id, spec, error_factory=error_factory)
    if not steps:
        return []
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        extract_root = Path(tmpdir) / 'artifact'
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_root)
            for item in archive.infolist():
                mode = (item.external_attr >> 16) & 0o7777
                if not mode:
                    continue
                target = (extract_root / item.filename).resolve()
                try:
                    target.relative_to(extract_root.resolve())
                except ValueError:
                    _raise(error_factory, f'bundle {bundle_id} 的 artifactSmoke zip 路径越界：{item.filename}')
                if target.exists():
                    target.chmod(mode)
        artifact_root = extract_root.resolve()
        artifact_state_root = artifact_root / '.artifact_smoke_state'
        artifact_state_root.mkdir(parents=True, exist_ok=True)
        _prepare_artifact_state_root(artifact_state_root)
        for step in steps:
            cwd = Path(_render_artifact_smoke_value(step['cwd'], artifact_root=artifact_root, artifact_state_root=artifact_state_root))
            if not cwd.is_absolute():
                cwd = (artifact_root / cwd).resolve()
            try:
                cwd.relative_to(artifact_root)
            except ValueError as exc:
                _raise(error_factory, f'bundle {bundle_id} 的 artifactSmoke[{step["id"]}] cwd 越界：{cwd}')
            if not cwd.exists() or not cwd.is_dir():
                _raise(error_factory, f'bundle {bundle_id} 的 artifactSmoke[{step["id"]}] cwd 不存在：{cwd}')
            command = [_render_artifact_smoke_value(item, artifact_root=artifact_root, artifact_state_root=artifact_state_root) for item in step['command']]
            env = os.environ.copy()
            env[artifact_smoke_active_env] = '1'
            for key, value in step['env'].items():
                env[key] = _render_artifact_smoke_value(value, artifact_root=artifact_root, artifact_state_root=artifact_state_root)
            proc = subprocess.run(command, cwd=str(cwd), env=env, capture_output=True, text=False, check=False)
            results.append({
                'id': step['id'],
                'cwd': str(cwd.relative_to(artifact_root)),
                'command': command,
                'returncode': int(proc.returncode),
                'stdout': _output_snippet(_decode_subprocess_output(proc.stdout)),
                'stderr': _output_snippet(_decode_subprocess_output(proc.stderr)),
            })
            if proc.returncode != 0:
                break
    return results


def _files_lt_threshold(root_dir: Path, file_list: list[str], threshold: int) -> int:
    """统计小于阈值的文件数量。"""
    return sum(1 for rel in file_list if (root_dir / rel).stat().st_size < threshold)


def build_size_manifest(
    root_dir: Path,
    bundle_id: str,
    file_list: list[str],
    zip_path: Path,
    *,
    spec: dict[str, Any],
    manifest_path: Path,
    must_not_ship_hits: Callable[[list[str]], list[str]],
) -> dict[str, Any]:
    """构建 bundle size manifest。"""
    with zipfile.ZipFile(zip_path) as archive:
        infos = archive.infolist()
    top_level = Counter()
    top_files: list[dict[str, Any]] = []
    by_name = {item.filename: item for item in infos}
    for rel in file_list:
        info = by_name[rel]
        parts = Path(rel).parts
        top_level[parts[0]] += info.compress_size
        top_files.append({'path': rel, 'compressBytes': info.compress_size, 'bytes': info.file_size})
    top_files.sort(key=lambda item: (-item['compressBytes'], item['path']))
    zip_bytes = zip_path.stat().st_size
    payload_bytes = sum(item.compress_size for item in infos)
    return {
        'schemaVersion': 1,
        'bundle': bundle_id,
        'description': str(spec.get('description') or '').strip(),
        'manifestPath': str(manifest_path.relative_to(root_dir)),
        'outputPath': str(zip_path),
        'counts': {
            'files': len(file_list),
            'lt1KiB': _files_lt_threshold(root_dir, file_list, 1024),
            'lt2KiB': _files_lt_threshold(root_dir, file_list, 2048),
            'lt4KiB': _files_lt_threshold(root_dir, file_list, 4096),
            'lt8KiB': _files_lt_threshold(root_dir, file_list, 8192),
        },
        'size': {
            'zipBytes': zip_bytes,
            'payloadBytes': payload_bytes,
            'metadataBytes': zip_bytes - payload_bytes,
            'uncompressedBytes': sum(item.file_size for item in infos),
        },
        'topLevelCompressedBytes': dict(sorted(top_level.items())),
        'topFiles': top_files[:20],
        'mustNotShipHits': must_not_ship_hits(file_list),
        'budget': spec.get('budget') if isinstance(spec.get('budget'), dict) else {},
    }


def budget_failures(size_manifest: dict[str, Any], *, spec: dict[str, Any]) -> list[str]:
    """计算 bundle 体积预算失败项。"""
    budget_payload = spec.get('budget')
    budget: dict[str, Any] = budget_payload if isinstance(budget_payload, dict) else {}
    failures: list[str] = []
    max_zip_bytes = budget.get('maxZipBytes')
    if isinstance(max_zip_bytes, int) and size_manifest['size']['zipBytes'] > max_zip_bytes:
        failures.append(f'zipBytes 超预算：{size_manifest["size"]["zipBytes"]} > {max_zip_bytes}')
    max_files = budget.get('maxFiles')
    if isinstance(max_files, int) and size_manifest['counts']['files'] > max_files:
        failures.append(f'files 超预算：{size_manifest["counts"]["files"]} > {max_files}')
    return failures
