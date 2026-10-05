#!/usr/bin/env python3
"""提供OpenClaw doctor子系统的生产实现。"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from openclaw.control_plane.manifest_fields import DISPATCH_PROVIDER_REGISTRY_PATHS_KEY

from openclaw.lib.repo.layout import relative_path_within_root, resolve_repo_root
from openclaw.lib.repo.contracts import repo_contract_path

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
DOCS_REGISTRY_CONTRACT_ID = 'governance.docs_registry'
DOCS_REGISTRY_REL_PARTS = ('config', 'governance', 'docs', 'docs_registry.json')

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
        (relative_path_within_root(path, root_dir).as_posix(), path, _read_source(path))
        for path in sorted(base.rglob('*.py'))
        if '__pycache__' not in path.parts
    )


def _file_source_rows(paths: list[Path], root_dir: Path) -> tuple[tuple[str, Path, str], ...]:
    rows: list[tuple[str, Path, str]] = []
    seen: set[str] = set()
    for path in paths:
        if not path.is_file():
            continue
        rel_path = relative_path_within_root(path, root_dir).as_posix()
        if rel_path in seen:
            continue
        seen.add(rel_path)
        rows.append((rel_path, path, _read_source(path)))
    return tuple(rows)


def _read_json_object(path: Path) -> dict[str, Any]:
    """读取 JSON 对象文件并把不可读内容视为空对象。

    参数：
        path（Path）：待读取的 JSON 文件路径。

    返回：
        dict[str, Any]：返回 JSON 根对象；文件缺失、解析失败或根节点非对象时返回空 dict[str, Any]。
    """
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


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
            rel_path = relative_path_within_root(path, root_dir).as_posix()
            if rel_path.startswith('python/openclaw/tests/'):
                continue
            files.append(path)
    return files


def sys_path_mutation_offenders(root_dir: Path = ROOT_DIR) -> list[str]:
    offenders: list[str] = []
    for path in _governance_python_files(root_dir):
        rel_path = relative_path_within_root(path, root_dir).as_posix()
        if rel_path in ALLOWED_SYS_PATH_MUTATION_REL_PATHS:
            continue
        source = path.read_text(encoding='utf-8')
        if 'sys.path.insert(' in source or 'sys.path[:0]' in source:
            offenders.append(rel_path)
    return offenders


def repo_root_resolver_offenders(root_dir: Path = ROOT_DIR) -> list[str]:
    offenders: list[str] = []
    for path in _governance_python_files(root_dir):
        rel_path = relative_path_within_root(path, root_dir).as_posix()
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
    parts = relative_path_within_root(path, root_dir).parts
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
        offenders.append(f'agent authoring surface must not be a Python package -> {relative_path_within_root(path, root_dir).as_posix()}')
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


def _extension_package_dirs(python_root: Path) -> tuple[Path, ...]:
    """列出扩展 Python 根目录下的顶层包目录。

    参数：
        python_root（Path）：受管扩展声明的 Python 根目录。

    返回：
        tuple[Path, ...]：返回包含 __init__.py 的顶层包目录 tuple[Path, ...]；目录缺失时返回空 tuple[Path, ...]。
    """
    if not python_root.is_dir():
        return ()
    return tuple(
        path
        for path in sorted(python_root.iterdir())
        if path.is_dir()
        and path.name != '__pycache__'
        and (path / '__init__.py').is_file()
    )


def _managed_extension_manifest_payload(extension: Any) -> dict[str, Any]:
    """读取受管扩展约定路径上的 manifest 对象。

    参数：
        extension（Any）：受管扩展索引行，需提供 id 与 manifest_dir 属性。

    返回：
        dict[str, Any]：返回 manifest JSON 对象；manifest 缺失或不可读时返回空 dict[str, Any]。
    """
    manifest_path = extension.manifest_dir / f'{extension.id}.json'
    if not manifest_path.is_file():
        return {}
    return _read_json_object(manifest_path)


def _base_docs_registry_path(root_dir: Path) -> Path:
    """解析基座 docs registry 路径。

    参数：
        root_dir（Path）：仓库根目录或最小测试仓库根目录。

    返回：
        Path：完整仓库返回 repo contract 解析的 Path；最小测试仓库缺少 repo contracts 时返回同等相对位置的 Path。
    """
    try:
        return repo_contract_path(DOCS_REGISTRY_CONTRACT_ID, root_dir=root_dir)
    except Exception:
        return root_dir.joinpath(*DOCS_REGISTRY_REL_PARTS)


def public_provider_import_prefixes(root_dir: Path = ROOT_DIR) -> dict[str, tuple[str, ...]]:
    """返回 manifest 声明 provider registry 的扩展公开 provider 包前缀。

    参数：
        root_dir（Path）：仓库根目录，读取受管扩展索引、manifest 与扩展 Python 包。

    返回：
        dict[str, tuple[str, ...]]：返回 extension id 到公开 provider 导入前缀 tuple[str, ...] 的映射。
    """
    from openclaw.lib.repo.managed_extensions import managed_explicit_extensions

    prefixes_by_extension: dict[str, tuple[str, ...]] = {}
    for extension in managed_explicit_extensions(root_dir):
        manifest = _managed_extension_manifest_payload(extension)
        registry = manifest.get('registry') if isinstance(manifest.get('registry'), dict) else {}
        provider_registry_paths = registry.get(DISPATCH_PROVIDER_REGISTRY_PATHS_KEY) or []
        if not isinstance(provider_registry_paths, list) or not provider_registry_paths:
            continue
        prefixes: list[str] = []
        for python_root in extension.python_roots:
            for package_dir in _extension_package_dirs(python_root):
                if (package_dir / 'providers' / '__init__.py').is_file():
                    _append_token(prefixes, f'{package_dir.name}.providers')
        if prefixes:
            prefixes_by_extension[extension.id] = tuple(prefixes)
    return prefixes_by_extension


def _is_declared_public_provider_import(
    import_path: str,
    target_extension: str,
    prefixes_by_extension: dict[str, tuple[str, ...]],
) -> bool:
    """判断跨扩展导入是否命中目标扩展公开 provider 包。

    参数：
        import_path（str）：源码中的完整导入路径。
        target_extension（str）：导入路径所属的目标扩展 id。
        prefixes_by_extension（dict[str, tuple[str, ...]]）：public_provider_import_prefixes 产出的公开前缀映射。

    返回：
        bool：返回 True 表示 import_path 等于或位于目标扩展公开 provider 前缀下；否则返回 False。
    """
    return any(
        import_path == prefix or import_path.startswith(f'{prefix}.')
        for prefix in prefixes_by_extension.get(target_extension, ())
    )


def _extension_required_dependency_ids(extension: Any) -> set[str]:
    """读取扩展 manifest 中声明的必需依赖扩展。

    参数：
        extension（Any）：受管扩展索引行，需提供 id 与 manifest_dir 属性。

    返回：
        set[str]：返回 dependencies 中 optional 不为 True 的 extension id 集合；manifest 缺失时返回空集合。
    """
    manifest = _managed_extension_manifest_payload(extension)
    result: set[str] = set()
    for row in manifest.get('dependencies') or []:
        if not isinstance(row, dict) or row.get('optional') is True:
            continue
        dependency_id = str(row.get('id') or '').strip()
        if dependency_id:
            result.add(dependency_id)
    return result


def _extension_import_paths(source: str) -> tuple[str, ...]:
    """用 Python AST 收集源码中的扩展包绝对导入路径。

    参数：
        source（str）：待检查的 Python 源码文本。

    返回：
        tuple[str, ...]：返回按源码顺序出现的 openclaw_ext_* 导入路径；语法错误时返回空 tuple[str, ...]，
        由 Python 编译/单测门禁负责报告语法问题。
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ()
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(
                alias.name
                for alias in node.names
                if alias.name.startswith('openclaw_ext_')
            )
        elif isinstance(node, ast.ImportFrom):
            module = str(node.module or '').strip()
            if not module.startswith('openclaw_ext_'):
                continue
            module_package = module.split('.', 1)[0]
            if module == module_package:
                imports.extend(
                    f'{module}.{alias.name}'
                    for alias in node.names
                    if alias.name != '*'
                )
                if any(alias.name == '*' for alias in node.names):
                    imports.append(module)
            else:
                imports.append(module)
    return tuple(imports)


def extension_import_boundary_offenders(root_dir: Path = ROOT_DIR) -> list[str]:
    """检查受管扩展是否绕过 manifest 依赖和公开 provider 包直接导入其他扩展。

    参数：
        root_dir（Path）：仓库根目录，读取受管扩展 Python 源码和 manifest 声明。

    返回：
        list[str]：返回违规导入的仓库相对路径与目标扩展说明；空 list[str] 代表跨扩展导入均符合边界。
    """
    from openclaw.lib.repo.managed_extensions import managed_explicit_extensions

    extensions = tuple(managed_explicit_extensions(root_dir))
    package_owner: dict[str, str] = {}
    for extension in extensions:
        for python_root in extension.python_roots:
            for package_dir in _extension_package_dirs(python_root):
                if package_dir.name.startswith('openclaw_ext_'):
                    package_owner[package_dir.name] = extension.id

    prefixes_by_extension = public_provider_import_prefixes(root_dir)
    violations: list[str] = []
    for extension in extensions:
        required_dependency_ids = _extension_required_dependency_ids(extension)
        for python_root in extension.python_roots:
            if not python_root.is_dir():
                continue
            for path in sorted(python_root.rglob('*.py')):
                if '__pycache__' in path.parts:
                    continue
                source = _read_source(path)
                for import_path in _extension_import_paths(source):
                    package_name = import_path.split('.', 1)[0]
                    target_extension = package_owner.get(package_name)
                    if not target_extension or target_extension == extension.id:
                        continue
                    is_public_provider = _is_declared_public_provider_import(import_path, target_extension, prefixes_by_extension)
                    if is_public_provider and target_extension in required_dependency_ids:
                        continue
                    rel_path = relative_path_within_root(path, root_dir).as_posix()
                    if is_public_provider:
                        violations.append(f'{rel_path}: imports {import_path} from {target_extension} without required dependency')
                    else:
                        violations.append(f'{rel_path}: imports {import_path} from {target_extension}')
    return violations


def _registered_base_doc_paths(root_dir: Path) -> list[Path]:
    """返回基座 docs registry 登记的项目级 Markdown 页面。

    参数：
        root_dir（Path）：仓库根目录，读取基座 docs registry。

    返回：
        list[Path]：返回项目级 Markdown 页面 Path 列表；localOnly 和扩展自身文档不进入列表。
    """
    registry = _read_json_object(_base_docs_registry_path(root_dir))
    paths: list[Path] = []
    for page in registry.get('pages') or []:
        if not isinstance(page, dict) or page.get('localOnly') is True:
            continue
        rel_path = str(page.get('path') or '').strip()
        if not rel_path or rel_path.startswith('agent/extensions/'):
            continue
        path = root_dir / rel_path
        if path.suffix.lower() == '.md':
            paths.append(path)
    return paths


def business_name_leak_tokens(root_dir: Path = ROOT_DIR) -> tuple[str, ...]:
    from openclaw.lib.repo.managed_extensions import managed_explicit_extensions

    tokens: list[str] = []
    for extension in managed_explicit_extensions(root_dir):
        _append_token(tokens, extension.id)
        _append_token(tokens, extension.root_dir.name)
        for python_root in extension.python_roots:
            for package_dir in _extension_package_dirs(python_root):
                _append_token(tokens, package_dir.name)
                if package_dir.name.startswith('openclaw_ext_'):
                    _append_token(tokens, package_dir.name.removeprefix('openclaw_ext_'))
    return tuple(tokens)


def business_name_leak_offenders(root_dir: Path = ROOT_DIR) -> list[str]:
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
    docs_registry_path = _base_docs_registry_path(root_dir)
    if docs_registry_path.is_file():
        candidates.append(docs_registry_path)
    candidates.extend(_registered_base_doc_paths(root_dir))

    offenders: list[str] = []
    for path in candidates:
        rel_path = relative_path_within_root(path, root_dir).as_posix()
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
    extension_import_boundaries = extension_import_boundary_offenders(root_dir)
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
    business_name_leak_rows.extend(_file_source_rows([_base_docs_registry_path(root_dir)], root_dir))
    business_name_leak_rows.extend(_file_source_rows(_registered_base_doc_paths(root_dir), root_dir))
    business_name_leaks = []
    if tokens:
        for rel_path, _path, source in business_name_leak_rows:
            if rel_path in BUSINESS_LEAK_ALLOWED_REL_PATHS:
                continue
            leaked = [token for token in tokens if token in source]
            if leaked:
                business_name_leaks.append(f'{rel_path}: {", ".join(leaked)}')
    return {
        'ok': not modules and not lib and not top_level_layout and not layout and not forbidden_packages and not sys_path_mutation and not repo_root_resolvers and not registry_validation_imports and not agent_authoring_package_markers and not extension_import_boundaries and not business_name_leaks,
        'moduleImportOffenders': modules,
        'libReverseDependencyOffenders': lib,
        'topLevelPackageLayoutOffenders': top_level_layout,
        'layoutOffenders': layout,
        'forbiddenTopLevelPackageOffenders': forbidden_packages,
        'sysPathMutationOffenders': sys_path_mutation,
        'repoRootResolverOffenders': repo_root_resolvers,
        'registryValidationImportOffenders': registry_validation_imports,
        'agentAuthoringPackageMarkerOffenders': agent_authoring_package_markers,
        'extensionImportBoundaryOffenders': extension_import_boundaries,
        'businessNameLeakOffenders': business_name_leaks,
    }


def main() -> int:
    payload = build_report(ROOT_DIR)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if bool(payload['ok']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
