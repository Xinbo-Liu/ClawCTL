#!/usr/bin/env python3
"""Runtime path governance lint."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from openclaw.control_plane.surfaces import load_workspace_templates_manifest
from openclaw.lib.repo.contracts import repo_contract_path, repo_contract_relpath
from openclaw.lib.repo.layout import resolve_default_runtime_control_plane_service_config_path, resolve_repo_root
from openclaw.lib.repo.profiles import control_plane_profile_config_rel_paths
from openclaw.lib.runtime.path_resolver import PathResolver

ROOT_DIR = resolve_repo_root(Path(__file__))
BASE_INTERNAL_VIEWS = ('host', 'gateway', 'scheduler')
SURFACE_SCAN_ROOTS = (
    'README.md',
    'scripts/README.md',
    'agent',
    'config/governance',
    'docs',
    'scripts',
    'tools/agents',
)
SURFACE_SCAN_EXCLUDED_PREFIXES = ('agent/extensions', 'config/governance/validation')
OPTIONAL_LOCAL_PATH_REFS = {
    'deploy/.env',
    'deploy/site.env',
}
ALLOWED_MISSING_REPO_REFS = {
    'agent/extensions/provenance.json',
    'scripts/control_plane/run_registered_agent_runtime.sh',
}
OPTIONAL_LOCAL_PATH_PREFIXES = (
    'deploy/nginx/certs',
)
SURFACE_TEXT_SUFFIXES = {'.md', '.json', '.sh'}
REPO_PATH_REF_RE = re.compile(
    r'(?<![A-Za-z0-9_./-])'
    r'((?:README\.md|docs|agent|config|python|scripts|deploy|LICENSES)/[A-Za-z0-9_./-]+|'
    r'(?:COMMERCIAL_LICENSE|LICENSE|NOTICE|THIRD_PARTY_NOTICES|pyproject)\.?(?:md|toml)?)'
)
RUNTIME_ENV_RE = re.compile(r'(?<![A-Za-z0-9_.-])(runtime(?:\.[A-Za-z0-9_-]+)?\.env)(?![A-Za-z0-9_.-])')


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='lint runtime_paths manifest')
    parser.add_argument('--repo-root', default=None)
    parser.add_argument('--config-path', default=None)
    return parser.parse_args(argv)


def assert_view_contract(resolver: PathResolver) -> None:
    contract = resolver.view_contract
    if not contract:
        raise SystemExit('runtime_paths manifest missing view_contract')
    internal_views = tuple(str(item) for item in (contract.get('internal_view_keys') or ()) if str(item).strip())
    if not internal_views:
        raise SystemExit('runtime_paths view_contract.internal_view_keys must not be empty')
    if internal_views[: len(BASE_INTERNAL_VIEWS)] != BASE_INTERNAL_VIEWS:
        raise SystemExit("runtime_paths view_contract.internal_view_keys must start with ['host','gateway','scheduler']")
    if len(set(internal_views)) != len(internal_views):
        raise SystemExit(f'runtime_paths view_contract.internal_view_keys has duplicates: {list(internal_views)}')

    public_names = contract.get('public_view_names') or {}
    if public_names.get('gateway') != 'gateway':
        raise SystemExit('runtime_paths view_contract.public_view_names.gateway must be gateway')
    if public_names.get('scheduler') != 'scheduler':
        raise SystemExit('runtime_paths view_contract.public_view_names.scheduler must be scheduler')

    forbidden_raw_keys = {'control-plane', 'official-gateway', 'control'}
    for entry_id, entry in resolver.entries.items():
        owner = entry.get('owner') or []
        if isinstance(owner, str):
            owner = [owner]
        if forbidden_raw_keys & set(owner):
            raise SystemExit(f'runtime_paths entry owner must not use public alias as raw key: {entry_id}')
        for block_name in ('paths', 'env_names'):
            block = entry.get(block_name) or {}
            if forbidden_raw_keys & set(block):
                raise SystemExit(f'runtime_paths entry {entry_id}.{block_name} must not use public alias as raw key')


def _path_index_expected_header(resolver: PathResolver) -> str:
    labels = [str(resolver.public_view_names.get(view) or view) for view in resolver.internal_views]
    return '| 逻辑对象 | 逻辑分组 | ' + ' | '.join(labels) + ' |'


def _validate_workspace_templates(repo_root: Path, resolver: PathResolver, config_path: Path) -> None:
    manifest_path = repo_contract_path('workspace_templates.manifest', root_dir=repo_root)
    if not manifest_path.exists():
        raise SystemExit(f'missing {repo_contract_relpath("workspace_templates.manifest", root_dir=repo_root)}')
    workspace_manifest = load_workspace_templates_manifest(manifest_path, config_path=config_path)
    tpl_root = repo_root / 'config' / 'workspace_templates'
    template_dirs = sorted(p.name for p in tpl_root.iterdir() if p.is_dir())
    declared_templates = sorted(
        str(item.get('template') or '')
        for item in workspace_manifest.get('control_plane', [])
        if isinstance(item, dict)
    )
    missing_template_dirs = sorted(set(declared_templates) - set(template_dirs))
    if missing_template_dirs:
        raise SystemExit(f'workspace template dirs missing: {missing_template_dirs}')

    seen_targets: set[str] = set()
    for item in workspace_manifest.get('control_plane', []):
        if not isinstance(item, dict):
            raise SystemExit('workspace template manifest control_plane item must be object')
        template_ref = str(item.get('template') or '').strip()
        target_entry = str(item.get('target_entry') or '').strip()
        if not template_ref:
            raise SystemExit('workspace template manifest item missing template')
        if not target_entry:
            raise SystemExit(f'workspace template manifest missing target_entry: {template_ref}')
        if template_ref not in template_dirs:
            raise SystemExit(f'workspace template dir not found: {template_ref}')
        host_path = resolver.resolve_entry(target_entry)['paths']['host']
        if host_path in seen_targets:
            raise SystemExit(f'duplicate workspace target path in manifest: {host_path}')
        seen_targets.add(host_path)


def _is_excluded_surface_path(rel_path: str) -> bool:
    return any(rel_path == prefix or rel_path.startswith(f'{prefix}/') for prefix in SURFACE_SCAN_EXCLUDED_PREFIXES)


def _iter_surface_files(repo_root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    seen: set[Path] = set()

    def visit(path: Path) -> None:
        if not path.exists():
            return
        if path.is_dir():
            for child in sorted(path.iterdir()):
                visit(child)
            return
        if not path.is_file() or path.suffix.lower() not in SURFACE_TEXT_SUFFIXES:
            return
        rel_path = path.relative_to(repo_root).as_posix()
        if _is_excluded_surface_path(rel_path):
            return
        if path not in seen:
            seen.add(path)
            files.append(path)

    for rel in SURFACE_SCAN_ROOTS:
        visit(repo_root / rel)
    return tuple(files)


def _known_runtime_env_files(resolver: PathResolver) -> set[str]:
    names: set[str] = set()
    for entry in resolver.entries.values():
        paths = entry.get('paths') if isinstance(entry, dict) else {}
        if not isinstance(paths, dict):
            continue
        for value in paths.values():
            text = str(value or '').strip()
            if text:
                names.add(Path(text).name)
    return {name for name in names if name.startswith('runtime.') and name.endswith('.env')}


def _normalized_repo_ref(raw: str) -> str:
    ref = str(raw or '').strip().strip('`"\',;:，。；：）》)]}').replace('\\', '/')
    while ref.endswith('.') and Path(ref[:-1]).suffix:
        ref = ref[:-1]
    return ref


def _profile_registry_paths(repo_root: Path) -> set[str]:
    return set(control_plane_profile_config_rel_paths(repo_root).values())


def _validate_profile_registry_coverage(repo_root: Path) -> list[str]:
    try:
        registered = _profile_registry_paths(repo_root)
    except ValueError:
        return []
    candidates = [
        'config/control_plane/service.json',
        *[
            path.relative_to(repo_root).as_posix()
            for path in sorted((repo_root / 'config' / 'control_plane' / 'profiles').glob('*.service.json'))
        ],
        *[
            path.relative_to(repo_root).as_posix()
            for path in sorted((repo_root / 'agent' / 'extensions').glob('*/config/control_plane/profiles/*.service.json'))
        ],
    ]
    return [rel for rel in candidates if rel not in registered]


def _runtime_image_pin_rel(repo_root: Path) -> str:
    try:
        return repo_contract_relpath('image_pins.runtime', root_dir=repo_root)
    except ValueError:
        return '/'.join(('config', 'image_pins', 'runtime.env'))


def _scan_structured_surface_refs(repo_root: Path, resolver: PathResolver) -> list[str]:
    issues: list[str] = []
    allowed_runtime_envs = _known_runtime_env_files(resolver)
    runtime_image_pin_rel = _runtime_image_pin_rel(repo_root)
    seen_repo_refs: set[str] = set()
    seen_env_refs: set[tuple[str, str]] = set()
    for file_path in _iter_surface_files(repo_root):
        rel_file = file_path.relative_to(repo_root).as_posix()
        text = file_path.read_text(encoding='utf-8')
        for match in REPO_PATH_REF_RE.finditer(text):
            ref = _normalized_repo_ref(match.group(1))
            if not ref:
                continue
            if match.end(1) < len(text) and text[match.end(1)] == '*':
                continue
            if '<' in ref or '>' in ref or '$' in ref or '*' in ref:
                continue
            if ref in OPTIONAL_LOCAL_PATH_REFS:
                continue
            if ref in ALLOWED_MISSING_REPO_REFS:
                continue
            if any(ref == prefix or ref.startswith(f'{prefix}/') for prefix in OPTIONAL_LOCAL_PATH_PREFIXES):
                continue
            if ref.endswith('/') or not Path(ref).suffix:
                continue
            if ref in seen_repo_refs:
                continue
            seen_repo_refs.add(ref)
            if not (repo_root / ref).exists() and not (repo_root / 'scripts' / ref).exists():
                issues.append(f'{rel_file} references missing repository path: {ref}')
        for env_match in RUNTIME_ENV_RE.finditer(text):
            line_start = text.rfind('\n', 0, env_match.start()) + 1
            line_end_raw = text.find('\n', env_match.end())
            line_end = len(text) if line_end_raw < 0 else line_end_raw
            line = text[line_start:line_end]
            if runtime_image_pin_rel in line:
                continue
            env_name = env_match.group(1)
            key = (rel_file, env_name)
            if key in seen_env_refs:
                continue
            seen_env_refs.add(key)
            if env_name not in allowed_runtime_envs:
                issues.append(f'{rel_file} references unknown runtime env file: {env_name}')
    for rel_path in _validate_profile_registry_coverage(repo_root):
        issues.append(f'profile service config is not available as a control-plane profile: {rel_path}')
    return issues


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(args.repo_root).resolve() if args.repo_root else ROOT_DIR
    config_path = Path(args.config_path).resolve() if args.config_path else resolve_default_runtime_control_plane_service_config_path(repo_root)
    resolver = PathResolver.from_repo_root(repo_root, config_path=config_path)
    assert_view_contract(resolver)

    required_roots = {
        'host_gateway_root',
        'host_gateway_logs_root',
        'host_control_plane_root',
        'host_control_plane_setup_root',
        'scheduler_control_plane_root',
        'scheduler_logs_root',
    }
    if any(entry_id.startswith('dispatch_') for entry_id in resolver.entries):
        required_roots.update({'host_control_plane_dispatch_root', 'scheduler_dispatch_root'})
    missing_roots = sorted(required_roots - set(resolver.roots))
    if missing_roots:
        raise SystemExit(f'runtime_paths grouped roots missing: {missing_roots}')

    seen_paths: dict[tuple[str, str], list[str]] = {}
    logical_groups = resolver.logical_groups
    if not logical_groups:
        raise SystemExit('runtime_paths manifest missing logical_groups')
    for entry_id in resolver.entries:
        resolved = resolver.resolve_entry(entry_id)
        logical_group = str(resolved.get('logical_group') or '').strip()
        if not logical_group:
            raise SystemExit(f'runtime_paths entry missing logical_group: {entry_id}')
        if logical_group not in logical_groups:
            raise SystemExit(f'runtime_paths entry logical_group not declared: {entry_id}/{logical_group}')
        for view, value in resolved['paths'].items():
            if value is None:
                continue
            seen_paths.setdefault((view, value), []).append(entry_id)
        for view, env_name in resolved['env_names'].items():
            if not re.fullmatch(r'[A-Z0-9_]+', env_name):
                raise SystemExit(f'invalid env name: {entry_id}/{view}/{env_name}')
    for (view, value), ids in {k: v for k, v in seen_paths.items() if len(v) > 1 and not k[1].endswith('/logs')}.items():
        if len(set(ids)) > 1:
            raise SystemExit(f'duplicate {view} path {value}: {ids}')

    gateway_source_rel = 'config/gateway/openclaw.gateway.json'
    cfg_path = repo_root / gateway_source_rel
    if not cfg_path.exists():
        raise SystemExit(f'missing gateway source: {gateway_source_rel}')
    try:
        json.loads(cfg_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise SystemExit(f'invalid json in {gateway_source_rel}: {exc}') from exc

    _validate_workspace_templates(repo_root, resolver, config_path)

    path_index = resolver.absolute_host_path('path_index_json')
    if path_index.exists():
        obj = json.loads(path_index.read_text(encoding='utf-8'))
        if obj.get('module') != 'runtime_paths':
            raise SystemExit('<current-host-state-root>/path-index.json module mismatch')
    path_index_md = resolver.absolute_host_path('path_index_markdown')
    expected_header = _path_index_expected_header(resolver)
    if path_index_md.exists() and expected_header not in path_index_md.read_text(encoding='utf-8'):
        raise SystemExit('<current-host-state-root>/path-index.md malformed or missing expected view header')

    structured_ref_issues = _scan_structured_surface_refs(repo_root, resolver)
    if structured_ref_issues:
        raise SystemExit(structured_ref_issues[0])
    print('[lint_paths] OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
