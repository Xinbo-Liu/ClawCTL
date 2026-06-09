"""统一渲染 scripts/README.md。"""
from __future__ import annotations

import sys
from pathlib import Path

from openclaw.docs.support import reference_specs as specs

ROOT_DIR = specs.ROOT_DIR


def collect_script_files(dir_path: Path, base_dir: Path | None = None) -> list[str]:
    base = dir_path if base_dir is None else base_dir
    result: list[str] = []
    for path in sorted(dir_path.iterdir(), key=lambda item: item.name):
        if path.name == 'README.md':
            continue
        if path.is_dir():
            result.extend(collect_script_files(path, base))
            continue
        if path.is_file():
            result.append(path.relative_to(base).as_posix())
    return sorted(result)


def validate_catalog_against_filesystem(root_dir: Path) -> list[str]:
    errors: list[str] = []
    for group in specs.get_expected_script_files():
        dir_path = root_dir / 'scripts' / str(group['id'])
        if not dir_path.exists() or not dir_path.is_dir():
            errors.append(f"缺少 scripts/{group['id']} 目录")
            continue
        actual = collect_script_files(dir_path)
        expected = sorted(list(group['files']))
        actual_only = [name for name in actual if name not in expected]
        expected_only = [name for name in expected if name not in actual]
        if actual_only:
            errors.append(f"scripts/{group['id']} 存在未登记文件：{', '.join(actual_only)}")
        if expected_only:
            errors.append(f"scripts/{group['id']} 缺少已登记文件：{', '.join(expected_only)}")
    expected_groups = {str(group['id']) for group in specs.script_groups()}
    actual_groups = {path.name for path in (root_dir / 'scripts').iterdir() if path.is_dir()}
    stray_groups = sorted([name for name in actual_groups if name != 'node_modules' and name not in expected_groups])
    if stray_groups:
        errors.append(f"scripts 目录存在未登记分组：{', '.join(stray_groups)}")
    return errors


def render_entry(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    mode = 'write'
    if '--check' in args:
        mode = 'check'
    elif '--stdout' in args:
        mode = 'stdout'
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == '--config-path':
            index += 1
            if index >= len(args):
                sys.stderr.write('[render_script_docs] --config-path 缺少路径参数\n')
                return 2
        elif arg.startswith('--config-path=') and not arg.split('=', 1)[1].strip():
            sys.stderr.write('[render_script_docs] --config-path 缺少路径参数\n')
            return 2
        index += 1
    errors = validate_catalog_against_filesystem(ROOT_DIR) + specs.validate_script_surface_manifest()
    rendered_targets = specs.get_script_doc_targets(ROOT_DIR)
    normalized_targets = [(Path(target), content) for target, content in rendered_targets.items()]
    mismatches = [path for path, content in normalized_targets if (path.read_text(encoding='utf-8') if path.exists() else None) != content]
    if mode == 'stdout':
        for path, content in normalized_targets:
            sys.stdout.write(f'===== {path.relative_to(ROOT_DIR)} =====\n{content.rstrip()}\n')
        return 0 if not errors and not mismatches else 1
    if errors:
        sys.stderr.write('[render_script_docs] 脚本清单与目录不一致：\n- ' + '\n- '.join(errors) + '\n')
        return 1
    if mode == 'check':
        if not mismatches:
            sys.stdout.write('[render_script_docs] 已同步\n')
            return 0
        sys.stderr.write('[render_script_docs] scripts/README.md 未同步：\n- ' + '\n- '.join(str(path.relative_to(ROOT_DIR)) for path in mismatches) + '\n')
        return 1
    for path, content in normalized_targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8', newline='\n')
    sys.stdout.write('[render_script_docs] 已写入统一 scripts/README.md\n')
    return 0


def main(argv: list[str] | None = None) -> int:
    return render_entry(argv)


if __name__ == '__main__':
    raise SystemExit(main())
