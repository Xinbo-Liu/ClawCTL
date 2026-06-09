from __future__ import annotations

import re

SEPARATOR_CELL_RE = re.compile(r'^:?-{3,}:?$')


def _detect_fence(line: str) -> tuple[str, int] | None:
    stripped = line.lstrip()
    if stripped.startswith('```'):
        marker = '`'
    elif stripped.startswith('~~~'):
        marker = '~'
    else:
        return None
    length = 0
    for char in stripped:
        if char != marker:
            break
        length += 1
    return marker, length


def _is_fence_closer(line: str, marker: str, length: int) -> bool:
    return line.lstrip().startswith(marker * length)


def _split_table_row(line: str) -> list[str]:
    text = line.strip()
    if text.startswith('|'):
        text = text[1:]
    if text.endswith('|'):
        text = text[:-1]

    cells: list[str] = []
    current: list[str] = []
    code_span_length = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == '\\' and index + 1 < len(text):
            current.append(char)
            index += 1
            current.append(text[index])
            index += 1
            continue
        if char == '`':
            run_length = 1
            while index + run_length < len(text) and text[index + run_length] == '`':
                run_length += 1
            current.append('`' * run_length)
            if code_span_length == 0:
                code_span_length = run_length
            elif code_span_length == run_length:
                code_span_length = 0
            index += run_length
            continue
        if char == '|' and code_span_length == 0:
            cells.append(''.join(current).strip())
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    cells.append(''.join(current).strip())
    return cells


def _is_separator_row(cells: list[str]) -> bool:
    return len(cells) > 1 and all(SEPARATOR_CELL_RE.fullmatch(cell.strip()) for cell in cells)


def _looks_like_table_start(header_line: str, separator_line: str) -> bool:
    if '|' not in header_line or '|' not in separator_line:
        return False
    header_cells = _split_table_row(header_line)
    separator_cells = _split_table_row(separator_line)
    return len(header_cells) > 1 and len(header_cells) == len(separator_cells) and _is_separator_row(separator_cells)


def _extract_indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip(' \t'))]


def _display_width(text: str) -> int:
    return len(text)


def _parse_alignment(separator_cell: str) -> str:
    text = separator_cell.strip()
    left = text.startswith(':')
    right = text.endswith(':')
    if left and right:
        return 'center'
    if left:
        return 'left'
    if right:
        return 'right'
    return 'default'


def _pad_cell(text: str, width: int, alignment: str) -> str:
    remaining = max(0, width - _display_width(text))
    if alignment == 'right':
        left = remaining
        right = 0
    elif alignment == 'center':
        left = remaining // 2
        right = remaining - left
    else:
        left = 0
        right = remaining
    return f'{" " * left}{text}{" " * right}'


def _build_separator(width: int, alignment: str) -> str:
    dash_count = max(3, width)
    if alignment == 'center':
        return ':' + '-' * max(1, dash_count - 2) + ':'
    if alignment == 'right':
        return '-' * max(2, dash_count - 1) + ':'
    if alignment == 'left':
        return ':' + '-' * max(2, dash_count - 1)
    return '-' * dash_count


def _format_table_block(lines: list[str], start_index: int) -> tuple[list[str], int] | None:
    if start_index + 1 >= len(lines):
        return None
    header_line = lines[start_index]
    separator_line = lines[start_index + 1]
    if not _looks_like_table_start(header_line, separator_line):
        return None

    indent = _extract_indent(header_line)
    header_cells = _split_table_row(header_line.strip())
    separator_cells = _split_table_row(separator_line.strip())
    alignments = [_parse_alignment(cell) for cell in separator_cells]

    rows = [header_cells]
    cursor = start_index + 2
    while cursor < len(lines):
        line = lines[cursor]
        if not line.strip() or _detect_fence(line) is not None or '|' not in line:
            break
        row_cells = _split_table_row(line.strip())
        if len(row_cells) <= 1 or len(row_cells) != len(header_cells):
            break
        rows.append(row_cells)
        cursor += 1

    widths = [1] * len(header_cells)
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], _display_width(cell))

    formatted_lines = [
        indent + '| ' + ' | '.join(_pad_cell(cell, widths[index], 'left') for index, cell in enumerate(rows[0])) + ' |',
        indent + '|' + '|'.join(_build_separator(widths[index] + 2, alignments[index]) for index in range(len(widths))) + '|',
    ]
    for row in rows[1:]:
        formatted_lines.append(
            indent + '| ' + ' | '.join(_pad_cell(cell, widths[index], alignments[index]) for index, cell in enumerate(row)) + ' |'
        )
    return formatted_lines, cursor


def format_markdown_tables(text: str) -> str:
    newline = '\r\n' if '\r\n' in text else '\n'
    trailing_newline = text.endswith(('\n', '\r'))
    lines = text.splitlines()
    output: list[str] = []
    cursor = 0
    fence_state: tuple[str, int] | None = None

    while cursor < len(lines):
        line = lines[cursor]
        if fence_state is not None:
            output.append(line)
            if _is_fence_closer(line, fence_state[0], fence_state[1]):
                fence_state = None
            cursor += 1
            continue

        new_fence = _detect_fence(line)
        if new_fence is not None:
            output.append(line)
            fence_state = new_fence
            cursor += 1
            continue

        block = _format_table_block(lines, cursor)
        if block is not None:
            output.extend(block[0])
            cursor = block[1]
            continue

        output.append(line)
        cursor += 1

    formatted = newline.join(output)
    if trailing_newline:
        formatted += newline
    return formatted
