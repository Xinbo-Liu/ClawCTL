#!/usr/bin/env python3
"""交付包 allowlist/体积治理工具。"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from openclaw.release.bundle_manifest_support import (
    MANIFEST_PATH,
    ROOT_DIR,
    bundle_spec as manifest_bundle_spec,
    load_manifest as manifest_load_manifest,
    must_not_ship_hits as manifest_must_not_ship_hits,
    resolve_bundle_files as manifest_resolve_bundle_files,
)
from openclaw.release.bundle_runtime_checks import (
    artifact_smoke_failures,
    budget_failures,
    build_size_manifest,
    run_artifact_smoke,
)

FIXED_ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)
ARTIFACT_SMOKE_ACTIVE_ENV = 'OPENCLAW_BUNDLE_ARTIFACT_SMOKE_ACTIVE'
LIKELY_BINARY_SUFFIXES = {
    '.7z',
    '.bin',
    '.class',
    '.dll',
    '.dylib',
    '.exe',
    '.gif',
    '.gz',
    '.ico',
    '.jar',
    '.jpeg',
    '.jpg',
    '.pdf',
    '.png',
    '.pyc',
    '.pyo',
    '.so',
    '.tar',
    '.tgz',
    '.woff',
    '.woff2',
    '.zip',
}


class BundleGovernanceError(RuntimeError):
    """交付包治理失败时抛出的错误。"""
    pass


def _fail(prefix: str, message: str) -> None:
    raise SystemExit(f'[{prefix}][FAIL] {message}')


def load_manifest() -> dict[str, Any]:
    return manifest_load_manifest(error_factory=BundleGovernanceError)


def bundle_spec(bundle_id: str, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = load_manifest() if manifest is None else manifest
    return manifest_bundle_spec(bundle_id, payload, error_factory=BundleGovernanceError)


def resolve_bundle_files(bundle_id: str, manifest: dict[str, Any] | None = None) -> list[str]:
    payload = load_manifest() if manifest is None else manifest
    return manifest_resolve_bundle_files(bundle_id, payload, error_factory=BundleGovernanceError)


def must_not_ship_hits(bundle_id: str, file_list: list[str], manifest: dict[str, Any] | None = None) -> list[str]:
    payload = load_manifest() if manifest is None else manifest
    return manifest_must_not_ship_hits(bundle_id, file_list, payload, error_factory=BundleGovernanceError)


def _normalize_bundle_bytes(rel_path: str, data: bytes) -> bytes:
    if b'\r\n' not in data:
        return data
    if b'\x00' in data:
        return data
    suffixes = {suffix.lower() for suffix in Path(rel_path).suffixes}
    if suffixes & LIKELY_BINARY_SUFFIXES:
        return data
    return data.replace(b'\r\n', b'\n')


def _bundle_file_bytes(rel_path: str) -> bytes:
    return _normalize_bundle_bytes(rel_path, (ROOT_DIR / rel_path).read_bytes())


def _git_index_file_modes() -> dict[str, str]:
    """读取 Git index 中的文件模式。

    返回：
        返回仓库相对路径到 Git index mode 的映射；无 Git 目录的源码包返回空映射并使用文件权限。

    副作用：
        调用 `git ls-files --stage -z` 读取工作树 index；工作树存在但 Git 无法读取时抛出治理错误。
    """
    if not (ROOT_DIR / '.git').exists():
        return {}
    try:
        proc = subprocess.run(
            ['git', 'ls-files', '--stage', '-z'],
            cwd=ROOT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError as exc:
        raise BundleGovernanceError('无法读取交付源码的 Git 索引；请准备 Git 后重新导出') from exc
    if proc.returncode != 0:
        raise BundleGovernanceError(f'读取交付源码 Git 索引失败（exit={proc.returncode}）；请检查工作树后重新导出')
    modes: dict[str, str] = {}
    for record in proc.stdout.split(b'\0'):
        if not record:
            continue
        if b'\t' not in record:
            raise BundleGovernanceError('Git 索引记录格式不合法；请检查工作树后重新导出')
        metadata, rel_path = record.split(b'\t', 1)
        parts = metadata.split()
        if len(parts) != 3 or parts[2] != b'0':
            raise BundleGovernanceError('Git 索引含未合并或不合法记录；请解决工作树问题后重新导出')
        modes[rel_path.decode('utf-8', errors='surrogateescape')] = parts[0].decode('ascii')
    return modes


def _zip_external_mode(rel_path: str, git_index_modes: dict[str, str]) -> int:
    """已跟踪普通文件采用索引权限；无 Git 的源码包和未跟踪交付材料保留文件权限。"""
    git_mode = git_index_modes.get(rel_path)
    if git_mode == '100755':
        return 0o100755
    if git_mode == '100644':
        return 0o100644
    return (ROOT_DIR / rel_path).stat().st_mode & 0xFFFF


def compute_bom(file_list: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    git_index_modes = _git_index_file_modes()
    for rel in file_list:
        bundle_bytes = _bundle_file_bytes(rel)
        digest = hashlib.sha256(bundle_bytes).hexdigest()
        rows.append({
            'path': rel,
            'bytes': len(bundle_bytes),
            'mode': oct(_zip_external_mode(rel, git_index_modes) & 0o7777),
            'sha256': digest,
        })
    return rows


def _write_zip(bundle_id: str, file_list: list[str], output_path: Path) -> dict[str, Any]:
    git_index_modes = _git_index_file_modes()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    with zipfile.ZipFile(output_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for rel in file_list:
            src = ROOT_DIR / rel
            info = zipfile.ZipInfo(rel, date_time=FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = _zip_external_mode(rel, git_index_modes) << 16
            archive.writestr(
                info,
                _bundle_file_bytes(rel),
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )
    with zipfile.ZipFile(output_path) as archive:
        infos = archive.infolist()
    payload_bytes = sum(item.compress_size for item in infos)
    uncompressed_bytes = sum(item.file_size for item in infos)
    zip_bytes = output_path.stat().st_size
    return {
        'bundle': bundle_id,
        'outputPath': str(output_path),
        'files': len(file_list),
        'payloadBytes': payload_bytes,
        'uncompressedBytes': uncompressed_bytes,
        'zipBytes': zip_bytes,
        'metadataBytes': zip_bytes - payload_bytes,
    }


def validate_bundle(bundle_id: str, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = load_manifest() if manifest is None else manifest
    file_list = resolve_bundle_files(bundle_id, payload)
    spec = bundle_spec(bundle_id, payload)
    hits = must_not_ship_hits(bundle_id, file_list, payload)
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = Path(tmpdir) / f'{bundle_id}.zip'
        _write_zip(bundle_id, file_list, zip_path)
        size_manifest = build_size_manifest(
            ROOT_DIR,
            bundle_id,
            file_list,
            zip_path,
            spec=spec,
            manifest_path=MANIFEST_PATH,
            must_not_ship_hits=lambda rows: must_not_ship_hits(bundle_id, rows, payload),
        )
        smoke_results = run_artifact_smoke(
            bundle_id,
            spec,
            zip_path,
            artifact_smoke_active_env=ARTIFACT_SMOKE_ACTIVE_ENV,
            error_factory=BundleGovernanceError,
        )
    failures = []
    if hits:
        failures.append(f'命中 must-not-ship 路径：{hits}')
    failures.extend(budget_failures(size_manifest, spec=spec))
    failures.extend(artifact_smoke_failures(smoke_results))
    return {
        'bundle': bundle_id,
        'status': 'ok' if not failures else 'fail',
        'files': len(file_list),
        'mustNotShipHits': hits,
        'size': size_manifest['size'],
        'budget': size_manifest.get('budget') or {},
        'artifactSmoke': smoke_results,
        'failures': failures,
    }


def default_output_name(bundle_id: str, manifest: dict[str, Any] | None = None) -> str:
    payload = load_manifest() if manifest is None else manifest
    spec = bundle_spec(bundle_id, payload)
    prefix = str(spec.get('outputPrefix') or bundle_id.replace('-', '_')).strip()
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    return f'{prefix}_{ts}.zip'


def write_json(path: Path, payload: dict[str, Any] | list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def cmd_list_bundles(_: argparse.Namespace) -> int:
    payload = load_manifest()
    rows = []
    for bundle_id, spec in (payload.get('bundles') or {}).items():
        if not isinstance(spec, dict):
            continue
        rows.append({'bundle': bundle_id, 'outputPrefix': str(spec.get('outputPrefix') or ''), 'description': str(spec.get('description') or '')})
    sys.stdout.write(json.dumps({'bundles': rows}, ensure_ascii=False, indent=2) + '\n')
    return 0


def cmd_manifest(args: argparse.Namespace) -> int:
    payload = load_manifest()
    bundle_id = str(args.bundle or '').strip()
    if not bundle_id:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
        return 0
    spec = bundle_spec(bundle_id, payload)
    resolved = resolve_bundle_files(bundle_id, payload)
    sys.stdout.write(json.dumps({'bundle': bundle_id, 'spec': spec, 'resolvedFiles': resolved}, ensure_ascii=False, indent=2) + '\n')
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    payload = load_manifest()
    bundle_id = str(args.bundle or '').strip()
    if not bundle_id:
        _fail('bundle_governance', 'build 缺少 --bundle')
    file_list = resolve_bundle_files(bundle_id, payload)
    spec = bundle_spec(bundle_id, payload)
    hits = must_not_ship_hits(bundle_id, file_list, payload)
    if hits:
        _fail('bundle_governance', f'bundle {bundle_id} 命中 must-not-ship 路径：{hits}')
    output_path = Path(args.output).resolve() if args.output else ROOT_DIR / 'tmp' / default_output_name(bundle_id, payload)
    stats = _write_zip(bundle_id, file_list, output_path)
    size_manifest = build_size_manifest(
        ROOT_DIR,
        bundle_id,
        file_list,
        output_path,
        spec=spec,
        manifest_path=MANIFEST_PATH,
        must_not_ship_hits=lambda rows: must_not_ship_hits(bundle_id, rows, payload),
    )
    smoke_results = run_artifact_smoke(
        bundle_id,
        spec,
        output_path,
        artifact_smoke_active_env=ARTIFACT_SMOKE_ACTIVE_ENV,
        error_factory=BundleGovernanceError,
    )
    failures = budget_failures(size_manifest, spec=spec)
    failures.extend(artifact_smoke_failures(smoke_results))
    if failures:
        output_path.unlink(missing_ok=True)
        _fail('bundle_governance', f'bundle {bundle_id} 校验失败：{"; ".join(failures)}')
    size_path = Path(args.size_manifest).resolve() if args.size_manifest else output_path.with_suffix('.size-manifest.json')
    bom_path = Path(args.bom).resolve() if args.bom else output_path.with_suffix('.bom.json')
    write_json(size_path, size_manifest)
    write_json(bom_path, {'schemaVersion': 1, 'bundle': bundle_id, 'manifestPath': str(MANIFEST_PATH.relative_to(ROOT_DIR)), 'outputPath': str(output_path), 'files': compute_bom(file_list)})
    sys.stdout.write(json.dumps({'status': 'ok', 'bundle': bundle_id, 'outputPath': str(output_path), 'sizeManifestPath': str(size_path), 'bomPath': str(bom_path), 'stats': stats, 'artifactSmoke': smoke_results}, ensure_ascii=False, indent=2) + '\n')
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    payload = load_manifest()
    bundles = [str(item).strip() for item in (args.bundle or []) if str(item).strip()]
    if args.all or not bundles:
        bundles = sorted((payload.get('bundles') or {}).keys())
    results = [validate_bundle(bundle_id, payload) for bundle_id in bundles]
    status = 'ok' if all(row['status'] == 'ok' for row in results) else 'fail'
    sys.stdout.write(json.dumps({'status': status, 'results': results}, ensure_ascii=False, indent=2) + '\n')
    return 0 if status == 'ok' else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='交付包 allowlist/体积治理工具')
    subparsers = parser.add_subparsers(dest='command', required=True)

    list_parser = subparsers.add_parser('list-bundles', help='列出支持的 bundle 类型')
    list_parser.set_defaults(func=cmd_list_bundles)

    manifest_parser = subparsers.add_parser('manifest', help='输出 bundle 规格与解析结果')
    manifest_parser.add_argument('--bundle', default='')
    manifest_parser.set_defaults(func=cmd_manifest)

    build_parser_cmd = subparsers.add_parser('build', help='按 allowlist 导出 bundle，并写 size manifest / BOM')
    build_parser_cmd.add_argument('--bundle', required=True)
    build_parser_cmd.add_argument('--output', default='')
    build_parser_cmd.add_argument('--size-manifest', default='')
    build_parser_cmd.add_argument('--bom', default='')
    build_parser_cmd.set_defaults(func=cmd_build)

    validate_parser = subparsers.add_parser('validate', help='校验 bundle allowlist、禁止路径与预算')
    validate_parser.add_argument('--bundle', action='append', default=[])
    validate_parser.add_argument('--all', action='store_true')
    validate_parser.set_defaults(func=cmd_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except BundleGovernanceError as exc:
        _fail('bundle_governance', str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
