from __future__ import annotations

import re

from openclaw.guards.host_python.syntax import (
    SHELL_LINE_COMMAND_RE,
    SHELL_LINE_WRAPPED_COMMAND_RE,
    SHELL_PYTHON_COMMAND_RE,
    SHELL_WRAPPED_PYTHON_COMMAND_RE,
    trim_tokens,
)
from openclaw.guards.host_python.variables import expand_shell_variables


def is_python_shell_command_string(value: str) -> bool:
    return bool(value) and (SHELL_PYTHON_COMMAND_RE.search(value) is not None or SHELL_WRAPPED_PYTHON_COMMAND_RE.search(value) is not None)


def resolve_static_command_string(value: str | None, env: dict[str, str]) -> str | None:
    if not isinstance(value, str):
        return None
    expanded = trim_tokens(expand_shell_variables(value, env))
    if not expanded:
        return None
    if is_python_shell_command_string(expanded):
        return expanded
    if expanded.startswith('$(') and expanded.endswith(')'):
        inner = trim_tokens(expanded[2:-1])
        if is_python_shell_command_string(inner):
            return inner
        printf_match = re.match(r"^printf\s+(?:'%s'|\"%s\")\s+(?:'([^']*)'|\"([^\"]*)\")$", inner)
        if printf_match:
            return printf_match.group(1) or printf_match.group(2)
    return None


def contains_host_python_wrapped_command(normalized_line: str, env: dict[str, str]) -> bool:
    wrapper_re = re.compile(
        r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|[^\s\[\]()]+)\s+|"
        r"env(?:\s+(?:-[^\s]+|[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|[^\s\[\]()]+)))*\s+|"
        r"nohup\s+|"
        r"time(?:\s+-[^\s]+)*\s+|"
        r"(?:builtin\s+)?command(?:\s+--)?\s+|"
        r"exec\s+)*"
        r"(?:sh|bash)(?:\s+-[^\s]+)*\s+-(?:l?c)\s+(?:\"([^\"]*)\"|'([^']*)')"
    )
    match = wrapper_re.match(normalized_line)
    if match is None:
        return False
    wrapper_body = match.group(1) or match.group(2) or ''
    resolved = resolve_static_command_string(wrapper_body, env)
    return bool(resolved and is_python_shell_command_string(resolved))


def contains_host_python_command(masked_line: str, normalized_line: str, env: dict[str, str]) -> bool:
    return (
        SHELL_LINE_COMMAND_RE.search(masked_line) is not None
        or SHELL_LINE_WRAPPED_COMMAND_RE.search(normalized_line) is not None
        or contains_host_python_wrapped_command(normalized_line, env)
    )
