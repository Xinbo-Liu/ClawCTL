#!/usr/bin/env python3
"""模块 scaffold 的共享模型与小型辅助函数。"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openclaw.control_plane.registry import CliError
from openclaw.control_plane.runtime.adapter_registry import RuntimeAdapterSpec

MODULE_REF_PATTERN = re.compile(r'^[a-z0-9_]+$')


@dataclass(frozen=True)
class ScaffoldRequest:
    repo_root: Path
    config_path: Path | None
    module_ref: str
    title: str
    owner_domain: str
    module_kind: str
    entrypoint_kind: str
    runtime_adapter_ref: str
    executor_kind: str
    operation_ref: str
    description: str
    version: str
    network: bool
    model_required: bool
    external_dispatch: bool
    filesystem_write: tuple[str, ...]
    with_agents_doc: bool
    with_optional_dirs: bool
    force: bool


@dataclass(frozen=True)
class ScaffoldLayout:
    repo_root: Path
    config_path: Path
    managed_layout: Any
    runtime_spec: RuntimeAdapterSpec
    module_dir: Path
    python_module_dir: Path
    implementation_ref: str
    launcher_path: Path
    test_dir: Path
    change_control_docs: tuple[str, ...]
    module_dir_display: str
    module_manifest_display: str
    launcher_display: str
    group_display: str
    shared_objects_display: str
    python_module_name: str
    implementation_source_display: str
    logic_source_paths: tuple[str, ...]
    implementation_runtime: dict[str, Any]
    smoke_test_text: str


@dataclass(frozen=True)
class ScaffoldPayloads:
    module_payload: dict[str, Any]
    permissions_payload: dict[str, Any]
    tools_payload: dict[str, Any]
    module_readme_text: str
    skills_text: str
    launcher_text: str
    tests_readme_text: str
    agents_text: str | None
    contracts_text: str | None
    constraints_text: str | None
    docs_text: str | None
    python_main_text: str


@dataclass(frozen=True)
class PlannedWrite:
    path: Path
    content: str | dict[str, Any]
    executable: bool = False

    @property
    def is_json(self) -> bool:
        return isinstance(self.content, dict)


def write_text(path: Path, content: str, *, executable: bool = False) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    if executable:
        path.chmod(0o755)
    return str(path)


def write_json(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return str(path)


def display_relative_path(path: str | Path, repo_root: Path) -> str:
    relative = Path(path).resolve().relative_to(repo_root.resolve()).as_posix()
    return relative.replace('/', '\\')


def display_repo_posix_path(path: str | Path, repo_root: Path) -> str:
    return Path(path).resolve().relative_to(repo_root.resolve()).as_posix()


def validate_module_ref(value: str, *, label: str) -> str:
    text = str(value or '').strip()
    if not MODULE_REF_PATTERN.match(text):
        raise CliError(f'{label} 必须匹配 ^[a-z0-9_]+$：{text or "<empty>"}', 2)
    return text


def normalize_owner_domain(value: str) -> str:
    text = str(value or '').strip()
    if not text:
        raise CliError('--owner-domain 不能为空', 2)
    return text


def relative_source_path(base_dir: Path, target_path: Path) -> str:
    return Path(os.path.relpath(target_path.resolve(), base_dir.resolve())).as_posix()


def python_main_template(module_ref: str, title: str, operation_ref: str, *, module_import: str) -> str:
    return f'''#!/usr/bin/env python3
from __future__ import annotations

import sys

HELP = """{module_ref}

用法：
  python -m {module_import} <command>

命令：
  {operation_ref}    执行 {title} 的默认运行命令。
  help               查看帮助。
"""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {{'-h', '--help', 'help'}}:
        print(HELP)
        return 0
    command = str(args.pop(0)).strip()
    if command == '{operation_ref}':
        print('{module_ref}: 默认命令执行完成')
        return 0
    sys.stderr.write(f'[{module_ref}][FAIL] 未知命令：{{command}}\\n')
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
'''


def test_template_python(
    module_ref: str,
    operation_ref: str,
    *,
    module_import: str,
    config_path_repo_rel: str | None = None,
) -> str:
    if config_path_repo_rel:
        config_setup = (
            f"CONFIG_PATH = (REPO_ROOT / {config_path_repo_rel!r}).resolve()\n"
            "EXTRA_ENV = {'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH': str(CONFIG_PATH)}\n"
        )
    else:
        config_setup = "EXTRA_ENV = {}\n"
    return f'''from __future__ import annotations

import unittest
from pathlib import Path

from openclaw.doctor.agent_modules.support import run_python_module
from openclaw.lib.repo.layout import resolve_repo_root

REPO_ROOT = resolve_repo_root(Path(__file__))
{config_setup}


class {module_ref.title().replace('_', '')}SmokeTest(unittest.TestCase):
    def test_help_surface(self) -> None:
        result = run_python_module(REPO_ROOT, '{module_import}', ['--help'], extra_env=EXTRA_ENV)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn('{module_ref}', result.stdout)
        self.assertIn('用法：', result.stdout)
        self.assertIn('{operation_ref}', result.stdout)
        self.assertEqual(result.stderr, '')
'''
