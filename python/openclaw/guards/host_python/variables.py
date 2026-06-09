from __future__ import annotations

import re

from openclaw.guards.host_python.syntax import (
    SHELL_ASSIGNMENT_RE,
    SHELL_VAR_NAME_RE,
    read_command_substitution,
)


def read_variable_reference(line: str, start_index: int) -> tuple[str, int] | None:
    if line[start_index] != '$':
        return None
    if start_index + 1 < len(line) and line[start_index + 1] == '{':
        end_index = start_index + 2
        while end_index < len(line) and re.match(r'[A-Za-z0-9_]', line[end_index]):
            end_index += 1
        if end_index >= len(line) or line[end_index] != '}':
            return None
        name = line[start_index + 2:end_index]
        if not name or not SHELL_VAR_NAME_RE.match(name):
            return None
        return name, end_index
    match = re.match(r'[A-Za-z_][A-Za-z0-9_]*', line[start_index + 1:])
    if not match:
        return None
    name = match.group(0)
    return name, start_index + len(name)


def expand_shell_variables(line: str, env: dict[str, str]) -> str:
    if not line:
        return ''
    out: list[str] = []
    single_quote = False
    double_quote = False
    backtick_quote = False
    escaped = False
    i = 0
    while i < len(line):
        ch = line[i]
        if escaped:
            out.append(ch)
            escaped = False
            i += 1
            continue
        if ch == '\\':
            out.append(ch)
            if not single_quote:
                escaped = True
            i += 1
            continue
        if backtick_quote:
            out.append(ch)
            if ch == '`':
                backtick_quote = False
            i += 1
            continue
        if single_quote:
            out.append(ch)
            if ch == "'":
                single_quote = False
            i += 1
            continue
        if ch == '$' and i + 1 < len(line) and line[i + 1] == '(':
            command_sub, next_index = read_command_substitution(line, i)
            inner = command_sub[2:-1]
            out.append('$(' + expand_shell_variables(inner, env) + ')')
            i = next_index + 1
            continue
        if ch == '$':
            variable_ref = read_variable_reference(line, i)
            if variable_ref is not None:
                name, next_index = variable_ref
                value = env.get(name, line[i:next_index + 1])
                if double_quote:
                    value = (
                        value.replace('\\', '\\\\')
                        .replace('`', '\\`')
                        .replace('"', '\\"')
                        .replace('$', '\\$')
                    )
                out.append(value)
                i = next_index + 1
                continue
        out.append(ch)
        if ch == '"':
            double_quote = not double_quote
        elif ch == "'":
            single_quote = True
        elif ch == '`':
            backtick_quote = True
        i += 1
    return ''.join(out)


def strip_outer_quotes(value: str) -> str | None:
    if len(value) < 2:
        return None
    first = value[0]
    last = value[-1]
    if (first == '"' and last == '"') or (first == "'" and last == "'"):
        return value[1:-1]
    return None


def read_quoted_value(line: str, start_index: int) -> tuple[str, int] | None:
    quote = line[start_index]
    if quote not in ['"', "'"]:
        return None
    out = [quote]
    escaped = False
    for i in range(start_index + 1, len(line)):
        ch = line[i]
        out.append(ch)
        if escaped:
            escaped = False
            continue
        if ch == '\\' and quote == '"':
            escaped = True
            continue
        if ch == quote:
            return ''.join(out), i
    return None


def parse_simple_assignment_value(line: str, start_index: int) -> tuple[str | None, int, bool] | None:
    if start_index >= len(line):
        return None
    current = line[start_index]
    if current == '(':
        return None, len(line) - 1, False
    if current in ['"', "'"]:
        quoted = read_quoted_value(line, start_index)
        if quoted is None:
            return None
        value, next_index = quoted
        return strip_outer_quotes(value), next_index, True
    end_index = start_index
    while end_index < len(line) and not line[end_index].isspace():
        end_index += 1
    raw = line[start_index:end_index]
    if '$(' in raw:
        return None, end_index - 1, False
    return raw, end_index - 1, True


def parse_standalone_assignments(line: str) -> list[dict[str, object]] | None:
    assignments: list[dict[str, object]] = []
    index = 0
    while index < len(line):
        while index < len(line) and line[index].isspace():
            index += 1
        if index >= len(line):
            break
        match = SHELL_ASSIGNMENT_RE.match(line[index:])
        if not match:
            return None
        name = match.group(0)[:-1]
        index += len(match.group(0))
        parsed_value = parse_simple_assignment_value(line, index)
        if parsed_value is None:
            return None
        value, next_index, supported = parsed_value
        assignments.append({'name': name, 'value': value, 'supported': supported})
        index = next_index + 1
    return assignments


def update_shell_variable_env(line: str, env: dict[str, str]) -> None:
    assignments = parse_standalone_assignments(line)
    if not assignments:
        return
    for entry in assignments:
        name = str(entry['name'])
        value = entry['value']
        if entry['supported'] and isinstance(value, str):
            env[name] = value
        else:
            env.pop(name, None)
