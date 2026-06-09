from __future__ import annotations

import re


SHELL_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SHELL_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SHELL_PYTHON_COMMAND_RE = re.compile(
    r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|[^\s\[\]()]+)\s+|"
    r"env(?:\s+(?:-[^\s]+|[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|[^\s\[\]()]+)))*\s+|"
    r"nohup\s+|"
    r"time(?:\s+-[^\s]+)*\s+|"
    r"(?:builtin\s+)?command(?:\s+--)?\s+|"
    r"exec\s+)*"
    r"(?:python3?|/usr/bin/python3?|/usr/bin/env\s+python3?)(?=$|\s)"
)
SHELL_WRAPPED_PYTHON_COMMAND_RE = re.compile(
    r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|[^\s\[\]()]+)\s+|"
    r"env(?:\s+(?:-[^\s]+|[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|[^\s\[\]()]+)))*\s+|"
    r"nohup\s+|"
    r"time(?:\s+-[^\s]+)*\s+|"
    r"(?:builtin\s+)?command(?:\s+--)?\s+|"
    r"exec\s+)*"
    r"(?:sh|bash)(?:\s+-[^\s]+)*\s+-(?:l?c)\s+(?:\"|')?\s*"
    r"(?:[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|[^\s\[\]()]+)\s+|"
    r"env(?:\s+(?:-[^\s]+|[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|[^\s\[\]()]+)))*\s+|"
    r"nohup\s+|"
    r"time(?:\s+-[^\s]+)*\s+|"
    r"(?:builtin\s+)?command(?:\s+--)?\s+|"
    r"exec\s+)*"
    r"(?:python3?|/usr/bin/python3?|/usr/bin/env\s+python3?)(?=$|\s)"
)
SHELL_LINE_COMMAND_RE = re.compile(
    r"(^|[;&|`{()}]|&&|\|\||\$\(|!\s*|(?:if|then|do|elif|while|until|case)\s+)\s*"
    r"(?:[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|[^\s\[\]()]+)\s+|"
    r"env(?:\s+(?:-[^\s]+|[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|[^\s\[\]()]+)))*\s+|"
    r"nohup\s+|"
    r"time(?:\s+-[^\s]+)*\s+|"
    r"(?:builtin\s+)?command(?:\s+--)?\s+|"
    r"exec\s+)*"
    r"(?:python3?|/usr/bin/python3?|/usr/bin/env\s+python3?)([\s]|$|[<>|&;`)])"
)
SHELL_LINE_WRAPPED_COMMAND_RE = re.compile(
    r"(^|[;&|`{()}]|&&|\|\||\$\(|!\s*|(?:if|then|do|elif|while|until|case)\s+)\s*"
    r"(?:[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|[^\s\[\]()]+)\s+|"
    r"env(?:\s+(?:-[^\s]+|[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|[^\s\[\]()]+)))*\s+|"
    r"nohup\s+|"
    r"time(?:\s+-[^\s]+)*\s+|"
    r"(?:builtin\s+)?command(?:\s+--)?\s+|"
    r"exec\s+)*"
    r"(?:sh|bash)(?:\s+-[^\s]+)*\s+-(?:l?c)\s+(?:\"|')?\s*"
    r"(?:[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|[^\s\[\]()]+)\s+|"
    r"env(?:\s+(?:-[^\s]+|[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|[^\s\[\]()]+)))*\s+|"
    r"nohup\s+|"
    r"time(?:\s+-[^\s]+)*\s+|"
    r"(?:builtin\s+)?command(?:\s+--)?\s+|"
    r"exec\s+)*"
    r"(?:python3?|/usr/bin/python3?|/usr/bin/env\s+python3?)([\s]|$|['\"<>|&;`)])"
)


def trim_tokens(text: str) -> str:
    return text.strip()


def left_trim_tabs(text: str) -> str:
    return text.lstrip('\t')


def should_start_comment(prev_char: str) -> bool:
    return prev_char == '' or bool(re.match(r'[\s;&(|]', prev_char))


def strip_shell_comments(line: str) -> str:
    out: list[str] = []
    prev_char = ''
    single_quote = False
    double_quote = False
    backtick_quote = False
    escaped = False
    for ch in line:
        if escaped:
            out.append(ch)
            escaped = False
            prev_char = ch
            continue
        if ch == '\\':
            out.append(ch)
            if not single_quote:
                escaped = True
            prev_char = ch
            continue
        if backtick_quote:
            out.append(ch)
            if ch == '`':
                backtick_quote = False
            prev_char = ch
            continue
        if single_quote:
            out.append(ch)
            if ch == "'":
                single_quote = False
            prev_char = ch
            continue
        if double_quote:
            out.append(ch)
            if ch == '"':
                double_quote = False
            elif ch == '`':
                backtick_quote = True
            prev_char = ch
            continue
        if ch == '#' and should_start_comment(prev_char):
            break
        out.append(ch)
        if ch == "'":
            single_quote = True
        elif ch == '"':
            double_quote = True
        elif ch == '`':
            backtick_quote = True
        prev_char = ch
    return ''.join(out)


def read_command_substitution(line: str, start_index: int) -> tuple[str, int]:
    out = ['$', '(']
    depth = 1
    i = start_index + 2
    single_quote = False
    double_quote = False
    backtick_quote = False
    escaped = False
    while i < len(line):
        ch = line[i]
        out.append(ch)
        if escaped:
            escaped = False
            i += 1
            continue
        if ch == '\\':
            if not single_quote:
                escaped = True
            i += 1
            continue
        if backtick_quote:
            if ch == '`':
                backtick_quote = False
            i += 1
            continue
        if single_quote:
            if ch == "'":
                single_quote = False
            i += 1
            continue
        if double_quote:
            if ch == '"':
                double_quote = False
            elif ch == '`':
                backtick_quote = True
            i += 1
            continue
        if ch == "'":
            single_quote = True
            i += 1
            continue
        if ch == '"':
            double_quote = True
            i += 1
            continue
        if ch == '`':
            backtick_quote = True
            i += 1
            continue
        if ch == '$' and i + 1 < len(line) and line[i + 1] == '(':
            depth += 1
            out.append('(')
            i += 2
            continue
        if ch == ')':
            depth -= 1
            i += 1
            if depth == 0:
                return ''.join(out), i - 1
            continue
        i += 1
    return ''.join(out), len(line) - 1


def mask_command_substitution(command_sub: str) -> str:
    if not (command_sub.startswith('$(') and command_sub.endswith(')')):
        return command_sub
    inner = command_sub[2:-1]
    return '$(' + mask_non_command_quotes(inner) + ')'


def mask_non_command_quotes(line: str) -> str:
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
            out.append(' ')
            if ch == "'":
                single_quote = False
            i += 1
            continue
        if double_quote:
            if ch == '$' and i + 1 < len(line) and line[i + 1] == '(':
                command_sub, next_index = read_command_substitution(line, i)
                out.append(mask_command_substitution(command_sub))
                i = next_index + 1
                continue
            if ch == '"':
                out.append(' ')
                double_quote = False
            elif ch == '`':
                out.append(ch)
                backtick_quote = True
            else:
                out.append(' ')
            i += 1
            continue
        if ch == "'":
            out.append(' ')
            single_quote = True
            i += 1
            continue
        if ch == '"':
            out.append(' ')
            double_quote = True
            i += 1
            continue
        if ch == '$' and i + 1 < len(line) and line[i + 1] == '(':
            command_sub, next_index = read_command_substitution(line, i)
            out.append(mask_command_substitution(command_sub))
            i = next_index + 1
            continue
        if ch == '`':
            out.append(ch)
            backtick_quote = True
            i += 1
            continue
        out.append(ch)
        i += 1
    return ''.join(out)


def mask_non_command_contexts(line: str) -> str:
    return line.replace('=(', '=[')


def register_heredocs(line: str, queue: list[dict[str, object]]) -> None:
    pattern = re.compile(r"<<-?[\s]*(?:'[^']+'|\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_]*)")
    for match in pattern.finditer(line):
        raw = match.group(0)
        operator_len = 3 if raw.startswith('<<-') else 2
        start_index = match.start()
        prev_char = line[start_index - 1] if start_index > 0 else ''
        next_char = line[start_index + operator_len] if start_index + operator_len < len(line) else ''
        if prev_char == '<' or next_char == '<':
            continue
        marker = re.sub(r'^<<-?[\s]*', '', raw)
        if (marker.startswith("'") and marker.endswith("'")) or (marker.startswith('"') and marker.endswith('"')):
            marker = marker[1:-1]
        queue.append({'marker': marker, 'strip_tabs': raw.startswith('<<-')})


def ends_with_line_continuation(line: str) -> bool:
    single_quote = False
    double_quote = False
    backtick_quote = False
    escaped = False
    last_non_space_index = -1
    for i, ch in enumerate(line):
        if not ch.isspace():
            last_non_space_index = i
        if escaped:
            escaped = False
            continue
        if ch == '\\':
            if not single_quote and not backtick_quote:
                escaped = True
            continue
        if backtick_quote:
            if ch == '`':
                backtick_quote = False
            continue
        if single_quote:
            if ch == "'":
                single_quote = False
            continue
        if double_quote:
            if ch == '"':
                double_quote = False
            elif ch == '`':
                backtick_quote = True
            continue
        if ch == "'":
            single_quote = True
        elif ch == '"':
            double_quote = True
        elif ch == '`':
            backtick_quote = True
    if last_non_space_index < 0 or line[last_non_space_index] != '\\':
        return False
    if single_quote or backtick_quote:
        return False
    slash_count = 0
    i = last_non_space_index
    while i >= 0 and line[i] == '\\':
        slash_count += 1
        i -= 1
    return slash_count % 2 == 1


def drop_trailing_continuation(line: str) -> str:
    return re.sub(r'\\\s*$', '', line)


def append_logical_line(current: str, next_line: str) -> str:
    trimmed_next = re.sub(r'^\s+', '', next_line)
    return f'{current} {trimmed_next}' if current else trimmed_next
