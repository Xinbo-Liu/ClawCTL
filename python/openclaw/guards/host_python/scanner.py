from __future__ import annotations

from pathlib import Path
from typing import Iterable

from openclaw.guards.host_python.matching import contains_host_python_command
from openclaw.guards.host_python.syntax import (
    append_logical_line,
    drop_trailing_continuation,
    ends_with_line_continuation,
    left_trim_tabs,
    mask_non_command_contexts,
    mask_non_command_quotes,
    register_heredocs,
    strip_shell_comments,
)
from openclaw.guards.host_python.variables import expand_shell_variables, update_shell_variable_env


def scan_shell_source(source: str, source_path_label: str) -> list[str]:
    violations: list[str] = []
    heredoc_queue: list[dict[str, object]] = []
    lines = source.split('\n')
    env: dict[str, str] = {}
    logical_raw = ''
    logical_comment = ''
    logical_start_line = 1

    def flush_logical_line() -> None:
        nonlocal logical_raw, logical_comment, logical_start_line
        if not logical_raw and not logical_comment:
            return
        expanded = expand_shell_variables(logical_comment, env)
        normalized = mask_non_command_contexts(expanded)
        masked = mask_non_command_quotes(normalized)
        if contains_host_python_command(masked, normalized, env):
            violations.append(f'{source_path_label}:{logical_start_line}:{logical_raw}')
        register_heredocs(logical_comment, heredoc_queue)
        update_shell_variable_env(expanded, env)
        logical_raw = ''
        logical_comment = ''

    for index, raw_line in enumerate(lines):
        if heredoc_queue:
            marker_line = left_trim_tabs(raw_line) if heredoc_queue[0]['strip_tabs'] else raw_line
            if marker_line == heredoc_queue[0]['marker']:
                heredoc_queue.pop(0)
            continue
        comment_stripped = strip_shell_comments(raw_line)
        if not logical_raw:
            logical_start_line = index + 1
        logical_raw = append_logical_line(logical_raw, raw_line)
        logical_comment = append_logical_line(logical_comment, comment_stripped)
        if ends_with_line_continuation(comment_stripped):
            logical_raw = drop_trailing_continuation(logical_raw)
            logical_comment = drop_trailing_continuation(logical_comment)
            continue
        flush_logical_line()
    flush_logical_line()
    return violations


def collect_shell_files(targets: Iterable[str]) -> list[Path]:
    out: list[Path] = []
    seen: set[Path] = set()

    def visit(entry_path: Path) -> None:
        abs_path = entry_path.resolve()
        if abs_path in seen or not abs_path.exists():
            return
        seen.add(abs_path)
        if abs_path.is_dir():
            for child in sorted(abs_path.iterdir()):
                visit(child)
            return
        if abs_path.is_file() and abs_path.suffix == '.sh':
            out.append(abs_path)

    for target in targets:
        visit(Path(target))
    return sorted(out)


def display_path(file_path: Path, repo_root: Path | None) -> str:
    abs_path = file_path.resolve()
    if repo_root is not None:
        try:
            return abs_path.relative_to(repo_root).as_posix()
        except ValueError:
            pass
    return str(abs_path)
