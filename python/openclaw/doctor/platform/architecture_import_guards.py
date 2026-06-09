#!/usr/bin/env python3
"""Check import boundaries and Python package layout contracts."""
from __future__ import annotations

import json
import re
from pathlib import Path

from openclaw.lib.repo.layout import resolve_repo_root

ROOT_DIR = resolve_repo_root(Path(__file__))
FORBIDDEN_TOP_LEVEL_PACKAGES = ('domains', 'extensions', 'modules')
ALLOWED_TOP_LEVEL_PACKAGE_DIRS = (
    'control_plane',
    'docs',
    'doctor',
    'guards',
    'images',
    'internal_api',
    'lib',
    'release',
    'runtime',
    'scheduler',
    'setup',
    'specs',
    'testing',
    'tests',
)
ALLOWED_TOP_LEVEL_PACKAGE_FILES = ('__init__.py', 'cli.py', 'cli_registry.py')
ALLOWED_SYS_PATH_MUTATION_REL_PATHS = {
    'openclaw/__init__.py',
    'python/openclaw/doctor/platform/architecture_import_guards.py',
    'python/openclaw/lib/repo/bootstrap.py',
}
ALLOWED_REPO_ROOT_RESOLVER_REL_PATHS = {
    'python/openclaw/doctor/platform/architecture_import_guards.py',
    'python/openclaw/lib/repo/repo_root.py',
}
REGISTRY_VALIDATION_CANONICAL_REL_PATHS = (
    'python/openclaw/control_plane/registry_validation/jobs.py',
    'python/openclaw/control_plane/registry_validation/groups.py',
    'python/openclaw/control_plane/registry_validation/runtime.py',
)
BUSINESS_LEAK_ALLOWED_REL_PATHS = {
    'python/openclaw/doctor/platform/architecture_import_guards.py',
}
BUSINESS_LEAK_CONFIG_REL_PATHS = (
    'config/control_plane/service.json',
    'config/control_plane/profiles/agent_platform.service.json',
    'config/control_plane/extensions.d/agent_platform.json',
    'config/control_plane/extensions.d/agent_platform.runtime_paths.json',
    'config/control_plane/extensions.d/agent_platform.object_families.json',
    'config/control_plane/extensions.d/agent_platform.dispatch_operations_surface.json',
    'config/control_plane/extensions.d/agent_platform.full_test_group_registry.json',
    'config/control_plane/extensions.d/agent_platform.docs_registry.json',
)

PACKAGE_LAYOUT_RULES = (
    {
        'label': 'control_plane',
        'rel_path': 'python/openclaw/control_plane',
        'max_root_files': 14,
        'required_dirs': (
            'agent',
            'api',
            'cli_support',
            'dispatch',
            'extensions',
            'jobs',
            'module_scheduler',
            'modules',
            'registry',
            'registry_loader',
            'registry_validation',
            'runtime',
            'stack',
        ),
    },
    {
        'label': 'lib',
        'rel_path': 'python/openclaw/lib',
        'max_root_files': 1,
        'required_dirs': (
            'channels',
            'cli',
            'control_plane',
            'dispatch',
            'http',
            'io',
            'models',
            'repo',
            'runtime',
            'summary',
            'testing',
        ),
    },
    {
        'label': 'setup',
        'rel_path': 'python/openclaw/setup',
        'max_root_files': 1,
        'required_dirs': ('deploy_env', 'flow', 'network', 'surface'),
    },
    {
        'label': 'doctor',
        'rel_path': 'python/openclaw/doctor',
        'max_root_files': 1,
        'required_dirs': ('agent_governance', 'agent_modules', 'platform', 'release'),
    },
    {
        'label': 'docs',
        'rel_path': 'python/openclaw/docs',
        'max_root_files': 1,
        'required_dirs': ('renderers', 'support', 'validators'),
    },
    {
        'label': 'tests',
        'rel_path': 'python/openclaw/tests',
        'max_root_files': 1,
        'required_dirs': ('control_plane', 'doctor', 'extensions', 'fixtures', 'governance', 'runtime', 'setup', 'support', 'testing'),
    },
)


def _scan(base: Path, pattern: re.Pattern[str]) -> list[str]:
    offenders: list[str] = []
    for path in sorted(base.rglob('*.py')):
        if '__pycache__' in path.parts:
            continue
        if pattern.search(path.read_text(encoding='utf-8')):
            offenders.append(str(path.relative_to(ROOT_DIR)))
    return offenders


def _read_source(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        return path.read_text(encoding='utf-8', errors='ignore')


def _python_source_rows(base: Path, root_dir: Path) -> tuple[tuple[str, Path, str], ...]:
    if not base.exists():
        return ()
    return tuple(
        (path.relative_to(root_dir).as_posix(), path, _read_source(path))
        for path in sorted(base.rglob('*.py'))
        if '__pycache__' not in path.parts
    )


def _file_source_rows(paths: list[Path], root_dir: Path) -> tuple[tuple[str, Path, str], ...]:
    rows: list[tuple[str, Path, str]] = []
    seen: set[str] = set()
    for path in paths:
        if not path.is_file():
            continue
        rel_path = path.relative_to(root_dir).as_posix()
        if rel_path in seen:
            continue
        seen.add(rel_path)
        rows.append((rel_path, path, _read_source(path)))
    return tuple(rows)


def _scan_source_rows(rows: tuple[tuple[str, Path, str], ...], pattern: re.Pattern[str]) -> list[str]:
    return [rel_path for rel_path, _path, source in rows if pattern.search(source)]


def _governance_python_files(root_dir: Path) -> list[Path]:
    files: list[Path] = []
    for base in (root_dir / 'openclaw', root_dir / 'python' / 'openclaw'):
        if not base.exists():
            continue
        for path in sorted(base.rglob('*.py')):
            if '__pycache__' in path.parts:
                continue
            rel_path = path.relative_to(root_dir).as_posix()
            if rel_path.startswith('python/openclaw/tests/'):
                continue
            files.append(path)
    return files


def sys_path_mutation_offenders(root_dir: Path = ROOT_DIR) -> list[str]:
    offenders: list[str] = []
    for path in _governance_python_files(root_dir):
        rel_path = path.relative_to(root_dir).as_posix()
        if rel_path in ALLOWED_SYS_PATH_MUTATION_REL_PATHS:
            continue
        source = path.read_text(encoding='utf-8')
        if 'sys.path.insert(' in source or 'sys.path[:0]' in source:
            offenders.append(rel_path)
    return offenders


def repo_root_resolver_offenders(root_dir: Path = ROOT_DIR) -> list[str]:
    offenders: list[str] = []
    for path in _governance_python_files(root_dir):
        rel_path = path.relative_to(root_dir).as_posix()
        if rel_path in ALLOWED_REPO_ROOT_RESOLVER_REL_PATHS:
            continue
        source = path.read_text(encoding='utf-8')
        if any(
            marker in source
            for marker in (
                'def resolve_repo_root(',
                'def candidate_repo_roots(',
                'def looks_like_repo_root(',
                'REPO_ROOT_ENV_VARS =',
                'REPO_MARKERS =',
            )
        ):
            offenders.append(rel_path)
    return offenders


def layout_offenders(root_dir: Path = ROOT_DIR) -> list[str]:
    offenders: list[str] = []
    for rule in PACKAGE_LAYOUT_RULES:
        base = root_dir / str(rule['rel_path'])
        root_files = sorted(path.name for path in base.iterdir() if path.is_file())
        root_dirs = {path.name for path in base.iterdir() if path.is_dir() and path.name != '__pycache__'}
        max_root_files = int(rule['max_root_files'])
        if len(root_files) > max_root_files:
            offenders.append(
                f'{rule["label"]}: root file budget exceeded ({len(root_files)} > {max_root_files}) -> {", ".join(root_files)}'
            )
        missing_dirs = [name for name in rule['required_dirs'] if name not in root_dirs]
        if missing_dirs:
            offenders.append(f'{rule["label"]}: missing required subpackages -> {", ".join(missing_dirs)}')
        for file_name in root_files:
            if file_name == '__init__.py' or not file_name.endswith('.py'):
                continue
            stem = file_name[:-3]
            for dir_name in rule['required_dirs']:
                if stem.startswith(f'{dir_name}_'):
                    offenders.append(f'{rule["label"]}: flattened root file shadows declared subpackage -> {file_name}')
    return offenders


def top_level_package_layout_offenders(root_dir: Path = ROOT_DIR) -> list[str]:
    package_root = root_dir / 'python' / 'openclaw'
    root_dirs = sorted(path.name for path in package_root.iterdir() if path.is_dir() and path.name != '__pycache__')
    root_files = sorted(path.name for path in package_root.iterdir() if path.is_file())
    offenders: list[str] = []
    unexpected_dirs = [name for name in root_dirs if name not in ALLOWED_TOP_LEVEL_PACKAGE_DIRS]
    missing_dirs = [name for name in ALLOWED_TOP_LEVEL_PACKAGE_DIRS if name not in root_dirs]
    unexpected_files = [name for name in root_files if name not in ALLOWED_TOP_LEVEL_PACKAGE_FILES]
    missing_files = [name for name in ALLOWED_TOP_LEVEL_PACKAGE_FILES if name not in root_files]
    if unexpected_dirs:
        offenders.append(f'top-level package: unexpected subpackages -> {", ".join(unexpected_dirs)}')
    if missing_dirs:
        offenders.append(f'top-level package: missing required subpackages -> {", ".join(missing_dirs)}')
    if unexpected_files:
        offenders.append(f'top-level package: unexpected root files -> {", ".join(unexpected_files)}')
    if missing_files:
        offenders.append(f'top-level package: missing required root files -> {", ".join(missing_files)}')
    return offenders


def forbidden_top_level_package_offenders(root_dir: Path = ROOT_DIR) -> list[str]:
    package_root = root_dir / 'python' / 'openclaw'
    offenders: list[str] = []
    for name in FORBIDDEN_TOP_LEVEL_PACKAGES:
        if (package_root / name).exists():
            offenders.append(f'top-level placeholder package must not exist -> python/openclaw/{name}')
    return offenders


def _is_allowed_agent_package_marker(root_dir: Path, path: Path) -> bool:
    parts = path.relative_to(root_dir).parts
    return (
        len(parts) >= 5
        and parts[0] == 'agent'
        and parts[1] == 'extensions'
        and parts[3] in {'python', 'tests'}
    )


def agent_authoring_package_marker_offenders(root_dir: Path = ROOT_DIR) -> list[str]:
    agent_root = root_dir / 'agent'
    if not agent_root.exists():
        return []
    offenders: list[str] = []
    for path in sorted(agent_root.rglob('__init__.py')):
        if '__pycache__' in path.parts:
            continue
        if _is_allowed_agent_package_marker(root_dir, path):
            continue
        offenders.append(f'agent authoring surface must not be a Python package -> {path.relative_to(root_dir).as_posix()}')
    return offenders


def registry_validation_import_offenders(root_dir: Path = ROOT_DIR) -> list[str]:
    offenders: list[str] = []
    pattern = re.compile(r'(?:from|import)\s+openclaw\.control_plane\.registry\.rules\b')
    for rel_path in REGISTRY_VALIDATION_CANONICAL_REL_PATHS:
        path = root_dir / rel_path
        if not path.exists():
            continue
        if pattern.search(path.read_text(encoding='utf-8')):
            offenders.append(rel_path)
    return offenders


def _append_token(tokens: list[str], token: str) -> None:
    normalized = str(token or '').strip()
    if normalized and normalized not in tokens:
        tokens.append(normalized)


def business_name_leak_tokens(root_dir: Path = ROOT_DIR) -> tuple[str, ...]:
    """从受管扩展索引和扩展 Python 包目录派生业务名泄漏 token。"""
    from openclaw.lib.repo.managed_extensions import managed_explicit_extensions

    tokens: list[str] = []
    for extension in managed_explicit_extensions(root_dir):
        _append_token(tokens, extension.id)
        _append_token(tokens, extension.root_dir.name)
        for python_root in extension.python_roots:
            if not python_root.is_dir():
                continue
            package_dirs = (
                path
                for path in python_root.iterdir()
                if path.is_dir()
                and path.name != '__pycache__'
                and (path / '__init__.py').is_file()
            )
            for package_dir in sorted(package_dirs):
                _append_token(tokens, package_dir.name)
                if package_dir.name.startswith('openclaw_ext_'):
                    _append_token(tokens, package_dir.name.removeprefix('openclaw_ext_'))
    return tuple(tokens)


def business_name_leak_offenders(root_dir: Path = ROOT_DIR) -> list[str]:
    """检查核心实现层是否泄漏具体业务扩展名称。"""
    tokens = business_name_leak_tokens(root_dir)
    if not tokens:
        return []
    candidates: list[Path] = []
    python_root = root_dir / 'python' / 'openclaw'
    if python_root.exists():
        candidates.extend(
            path
            for path in sorted(python_root.rglob('*.py'))
            if '__pycache__' not in path.parts
            and 'tests' not in path.relative_to(python_root).parts
        )
    scripts_root = root_dir / 'scripts'
    if scripts_root.exists():
        candidates.extend(path for path in sorted(scripts_root.rglob('*')) if path.is_file())
    for rel_path in BUSINESS_LEAK_CONFIG_REL_PATHS:
        path = root_dir / rel_path
        if path.is_file():
            candidates.append(path)

    offenders: list[str] = []
    for path in candidates:
        rel_path = path.relative_to(root_dir).as_posix()
        if rel_path in BUSINESS_LEAK_ALLOWED_REL_PATHS:
            continue
        try:
            source = path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            source = path.read_text(encoding='utf-8', errors='ignore')
        leaked = [token for token in tokens if token in source]
        if leaked:
            offenders.append(f'{rel_path}: {", ".join(leaked)}')
    return offenders


def build_report(root_dir: Path = ROOT_DIR) -> dict[str, object]:
    openclaw_rows = _python_source_rows(root_dir / 'python' / 'openclaw', root_dir)
    governance_rows = tuple(row for row in openclaw_rows if not row[0].startswith('python/openclaw/tests/'))
    top_level_openclaw_root = root_dir / 'openclaw'
    if top_level_openclaw_root.exists():
        governance_rows = (*governance_rows, *_python_source_rows(top_level_openclaw_root, root_dir))
    modules = _scan_source_rows(openclaw_rows, re.compile(r'(?:from|import)\s+openclaw\.(?:domains|extensions|modules)\b'))
    lib = _scan_source_rows(
        tuple(row for row in openclaw_rows if row[0].startswith('python/openclaw/lib/')),
        re.compile(r'(?:from|import)\s+openclaw\.(?:domains|extensions|modules)\b'),
    )
    top_level_layout = top_level_package_layout_offenders(root_dir)
    layout = layout_offenders(root_dir)
    forbidden_packages = forbidden_top_level_package_offenders(root_dir)
    sys_path_mutation = [
        rel_path
        for rel_path, _path, source in governance_rows
        if rel_path not in ALLOWED_SYS_PATH_MUTATION_REL_PATHS
        and ('sys.path.insert(' in source or 'sys.path[:0]' in source)
    ]
    repo_root_resolvers = [
        rel_path
        for rel_path, _path, source in governance_rows
        if rel_path not in ALLOWED_REPO_ROOT_RESOLVER_REL_PATHS
        and any(
            marker in source
            for marker in (
                'def resolve_repo_root(',
                'def candidate_repo_roots(',
                'def looks_like_repo_root(',
                'REPO_ROOT_ENV_VARS =',
                'REPO_MARKERS =',
            )
        )
    ]
    registry_validation_rows = _file_source_rows([root_dir / rel_path for rel_path in REGISTRY_VALIDATION_CANONICAL_REL_PATHS], root_dir)
    registry_validation_imports = [
        rel_path
        for rel_path, _path, source in registry_validation_rows
        if re.search(r'(?:from|import)\s+openclaw\.control_plane\.registry\.rules\b', source)
    ]
    agent_authoring_package_markers = agent_authoring_package_marker_offenders(root_dir)
    tokens = business_name_leak_tokens(root_dir)
    business_name_leak_rows: list[tuple[str, Path, str]] = [
        row
        for row in openclaw_rows
        if not row[0].startswith('python/openclaw/tests/')
    ]
    scripts_root = root_dir / 'scripts'
    if scripts_root.exists():
        business_name_leak_rows.extend(
            _file_source_rows([path for path in sorted(scripts_root.rglob('*')) if path.is_file()], root_dir)
        )
    business_name_leak_rows.extend(_file_source_rows([root_dir / rel_path for rel_path in BUSINESS_LEAK_CONFIG_REL_PATHS], root_dir))
    business_name_leaks = []
    if tokens:
        for rel_path, _path, source in business_name_leak_rows:
            if rel_path in BUSINESS_LEAK_ALLOWED_REL_PATHS:
                continue
            leaked = [token for token in tokens if token in source]
            if leaked:
                business_name_leaks.append(f'{rel_path}: {", ".join(leaked)}')
    return {
        'ok': not modules and not lib and not top_level_layout and not layout and not forbidden_packages and not sys_path_mutation and not repo_root_resolvers and not registry_validation_imports and not agent_authoring_package_markers and not business_name_leaks,
        'moduleImportOffenders': modules,
        'libReverseDependencyOffenders': lib,
        'topLevelPackageLayoutOffenders': top_level_layout,
        'layoutOffenders': layout,
        'forbiddenTopLevelPackageOffenders': forbidden_packages,
        'sysPathMutationOffenders': sys_path_mutation,
        'repoRootResolverOffenders': repo_root_resolvers,
        'registryValidationImportOffenders': registry_validation_imports,
        'agentAuthoringPackageMarkerOffenders': agent_authoring_package_markers,
        'businessNameLeakOffenders': business_name_leaks,
    }


def main() -> int:
    payload = build_report(ROOT_DIR)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if bool(payload['ok']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
