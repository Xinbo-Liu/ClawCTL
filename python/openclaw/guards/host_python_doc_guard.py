#!/usr/bin/env python3
"""扫描活动文档 / 配置文本中的宿主机 Python 暴露面。"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from dataclasses import dataclass

from openclaw.lib.cli.examples import canonical_cli_command
from openclaw.lib.repo.static_truth import repo_contract_path, repo_contract_root

ROOT_DIR = repo_contract_root()
CONFIG_PATH = repo_contract_path('governance.host_python_governance')
TEXT_FILE_RE = re.compile(r'(?:^|/)(?:README(?:\.[^./]+)?|[^/]+\.(?:md|markdown|mdx|txt|rst|adoc|json|ya?ml))$', re.IGNORECASE)
PYTHON_MODULE_COMMAND_RE = re.compile(
    r'(?P<prefix>(?:^|[\s>`;-])(?:PYTHONPATH=python\s+)?)'
    r'(?P<python>python3?)\s+-m\s+'
    r'(?:(?P<runner>unittest)\s+)?'
    r'(?P<module>[A-Za-z_][A-Za-z0-9_.-]*)'
)


@dataclass(frozen=True)
class DocPythonCommand:
    module: str
    runner: str
    text: str


def default_scan_roots() -> list[str]:
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except Exception:
        return [
            'README.md',
            'config',
            'docs/README.md',
            'docs/architecture',
            'docs/getting-started',
            'docs/operations',
            'release',
            'scripts/README.md',
            'tools',
            'agent/extensions',
        ]
    raw = payload.get('doc_scan_roots') if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _allowed_doc_command_families() -> tuple[str, ...]:
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except Exception:
        return ('repo_host', 'unittest_openclaw')
    policy = payload.get('doc_command_policy') if isinstance(payload, dict) else None
    raw = policy.get('allowed_families') if isinstance(policy, dict) else None
    if not isinstance(raw, list):
        return ('repo_host', 'unittest_openclaw')
    return tuple(str(item).strip() for item in raw if str(item).strip())


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=canonical_cli_command('guards', 'host-python-doc'),
        description='扫描活动文档/配置文本中是否出现未收口的宿主机 Python 模块入口。',
        add_help=True,
    )
    parser.add_argument('--repo-root', default=str(Path.cwd()))
    parser.add_argument('paths', nargs='*')
    return parser.parse_args(argv)


def is_text_candidate(rel_path: str) -> bool:
    return TEXT_FILE_RE.search(rel_path.replace('\\', '/')) is not None


def collect_files(repo_root: Path, rel_paths: list[str]) -> list[Path]:
    files: list[Path] = []

    def visit(abs_path: Path) -> None:
        if not abs_path.exists():
            return
        if abs_path.is_dir():
            for entry in sorted(abs_path.iterdir()):
                visit(entry)
            return
        rel_path = abs_path.relative_to(repo_root).as_posix()
        if is_text_candidate(rel_path):
            files.append(abs_path)

    for rel in rel_paths:
        visit((repo_root / rel).resolve() if not Path(rel).is_absolute() else Path(rel).resolve())
    return files


def iter_doc_python_commands(line: str) -> tuple[DocPythonCommand, ...]:
    commands: list[DocPythonCommand] = []
    for match in PYTHON_MODULE_COMMAND_RE.finditer(line):
        module = str(match.group('module') or '').strip()
        if not module:
            continue
        command_text = line[match.start('python'):match.end('module')].strip()
        commands.append(
            DocPythonCommand(
                module=module,
                runner=str(match.group('runner') or '').strip(),
                text=command_text,
            )
        )
    return tuple(commands)


def _has_uncovered_doc_pattern_match(line: str) -> bool:
    return bool(uncovered_doc_python_commands(line))


def _is_allowed_doc_command(command: DocPythonCommand, allowed_families: tuple[str, ...]) -> bool:
    module = command.module
    runner = command.runner
    if 'repo_host' in allowed_families and not runner and module == 'openclaw.testing.repo_host':
        return True
    if 'unittest_openclaw' in allowed_families and runner == 'unittest' and (
        module == 'openclaw' or module.startswith('openclaw.')
    ):
        return True
    return False


def uncovered_doc_python_commands(line: str, allowed_families: tuple[str, ...] | None = None) -> tuple[DocPythonCommand, ...]:
    active_allowed_families = _allowed_doc_command_families() if allowed_families is None else allowed_families
    return tuple(
        command
        for command in iter_doc_python_commands(line)
        if (
            command.module.startswith('openclaw.')
            or command.module.startswith('openclaw_ext_')
            or command.module == 'python.openclaw'
            or command.module.startswith('python.openclaw.')
        )
        and not _is_allowed_doc_command(command, active_allowed_families)
    )


def scan_file(repo_root: Path, abs_path: Path) -> list[str]:
    rel_path = abs_path.relative_to(repo_root).as_posix()
    hits: list[str] = []
    for index, line in enumerate(abs_path.read_text(encoding='utf-8').splitlines(), start=1):
        for command in uncovered_doc_python_commands(line):
            hits.append(f'{rel_path}:{index}:{command.text}')
    return hits


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    repo_root = Path(args.repo_root).resolve()
    scan_roots = args.paths or default_scan_roots()
    files = collect_files(repo_root, scan_roots)
    hits: list[str] = []
    for file_path in files:
        hits.extend(scan_file(repo_root, file_path))
    if hits:
        sys.stdout.write('\n'.join(hits) + '\n')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
