"""解析仓内 Markdown 链接、图片与标题锚点，排除代码示例。"""
from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import re
import unicodedata
from urllib.parse import unquote

from openclaw.docs.support.markdown_tables import _detect_fence, _is_fence_closer

_SCHEME = re.compile(r'^[A-Za-z][A-Za-z0-9+.-]*:')
_DEFINITION = re.compile(r'^ {0,3}\[([^\]]+)\]:\s*(<[^>]+>|\S+)(?:\s+[\'"(].*)?$')
_EMPTY_DEFINITION = re.compile(r'^ {0,3}\[([^\]]+)\]:\s*$')
_DEFINITION_DESTINATION = re.compile(r'^[ \t]*(<[^>]+>|\S+)(?:\s+[\'"(].*)?$')
_DEFINITION_TITLE = re.compile(r'''^[ \t]*(?:"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|\((?:\\.|[^)\\\n])*\))[ \t]*$''')
_ATX = re.compile(r'^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$')
_SETEXT = re.compile(r'^ {0,3}(?:=+|-+)\s*$')


@dataclass(frozen=True)
class MarkdownLink:
    """保存目标、一基行号及规范 LF 正文中的目标源码范围，供诊断与路径治理复用。"""

    target: str
    line: int
    destination_span: tuple[int, int] | None = None


@dataclass(frozen=True)
class MarkdownDocument:
    """保存去除代码示例后解析的链接与可访问锚点。"""

    links: tuple[MarkdownLink, ...]
    anchors: frozenset[str]


def _visible_lines(content: str) -> list[str]:
    """保留原行号，把 fenced/缩进代码和 HTML 注释替换为空白。

    参数：
        content（str）：待解析的 Markdown 正文。
    返回：
        list[str]：与原文行号相同的可见文本行。
    副作用：
        无文件访问；不修改传入正文。
    """
    result: list[str] = []
    fence: tuple[str, int] | None = None
    indented = False
    for line in content.split('\n'):
        if fence:
            result.append(' ' * len(line))
            if _is_fence_closer(line, *fence):
                fence = None
            continue
        detected = _detect_fence(line)
        if detected:
            fence = detected
            result.append(' ' * len(line))
            continue
        # 列表续行有缩进，只有段落边界开始的四空格文本才按代码块处理。
        is_indented = line.startswith(('    ', '\t'))
        if is_indented and (indented or not result or not result[-1].strip()):
            indented = True
            result.append(' ' * len(line))
            continue
        if line.strip():
            indented = False
        result.append(line)
    # inline code 中的注释标记不是 HTML，先以等长遮罩定位可见注释边界。
    searchable = _mask_inline_code('\n'.join(result))
    visible = list('\n'.join(result))
    for match in re.finditer(r'<!--[\s\S]*?(?:-->|\Z)', searchable):
        for index in range(match.start(), match.end()):
            if visible[index] != '\n':
                visible[index] = ' '
    return ''.join(visible).split('\n')


def _mask_inline_code(text: str) -> str:
    """用等长空白遮盖已闭合的 inline code，保留跨行代码的行号。

    参数：
        text（str）：可包含换行的 Markdown 文本。
    返回：
        str：代码区域为空白的等长文本。
    """
    result = list(text)
    index = 0
    while index < len(text):
        if text[index] == '\\':
            index += 2
            continue
        if text[index] != '`':
            index += 1
            continue
        end = index
        while end < len(text) and text[end] == '`':
            end += 1
        delimiter = text[index:end]
        closing = -1
        search = end
        paragraph_end = re.search(r'\n[ \t]*\n', text[end:])
        limit = end + paragraph_end.start() if paragraph_end else len(text)
        while search < len(text):
            candidate = text.find('`', search, limit)
            if candidate < 0:
                break
            run_end = candidate
            while run_end < len(text) and text[run_end] == '`':
                run_end += 1
            if run_end - candidate == len(delimiter):
                closing = candidate
                break
            search = run_end
        if closing < 0:
            index = end
            continue
        result[index:closing + len(delimiter)] = [
            '\n' if char == '\n' else ' ' for char in text[index:closing + len(delimiter)]
        ]
        index = closing + len(delimiter)
    return ''.join(result)


def _balanced_end(text: str, start: int, opening: str, closing: str) -> int:
    """找到支持转义和嵌套括号的闭合位置。

    参数：
        text（str）：待搜索文本。
        start（int）：左括号在文本中的零基位置。
        opening（str）：左括号字符。
        closing（str）：匹配的右括号字符。
    返回：
        int：匹配的右括号位置；未闭合返回 -1。
    """
    depth = 0
    index = start
    angled = False
    while index < len(text):
        char = text[index]
        if char == '\n' and re.match(r'[ \t]*\n', text[index + 1:]):
            return -1
        if char == '\\':
            index += 2
            continue
        if opening == '(' and char == '<':
            angled = True
        elif opening == '(' and char == '>':
            angled = False
        elif not angled:
            if char == opening:
                depth += 1
            elif char == closing:
                depth -= 1
                if depth == 0:
                    return index
        index += 1
    return -1


def _destination(raw: str) -> str:
    """从链接括号正文中提取目标，去掉可选标题与 Markdown 转义。

    参数：
        raw（str）：链接括号或 reference definition 的目标正文。
    返回：
        str：未进行 URL 解码的链接目标。
    """
    value = raw.strip()
    if value.startswith('<'):
        closing = value.find('>')
        value = value[1:closing] if closing >= 0 else value[1:]
    else:
        value = re.split(r'\s+[\'"(]', value, maxsplit=1)[0]
    return unescape(re.sub(r'\\([!"#$%&\'()*+,\-./:;<=>?@\[\]\\^_`{|}~])', r'\1', value))


def _destination_span(raw: str, start: int) -> tuple[int, int]:
    """定位目标本身的源码范围，排除空白、尖括号与可选标题。

    参数：
        raw（str）：链接或 definition 的原始目标片段。
        start（int）：片段在规范 LF 正文中的零基字符起点。
    返回：
        tuple[int, int]：目标源码的半开区间，转义字符仍保留在范围内。
    副作用：
        只计算输入文本的位置，不访问文件或修改正文。
    """
    value = raw.strip()
    begin = start + len(raw) - len(raw.lstrip())
    if value.startswith('<'):
        closing = value.find('>')
        return begin + 1, begin + (closing if closing >= 0 else len(value))
    title = re.search(r'\s+[\'"(]', value)
    return begin, begin + (title.start() if title else len(value))


def _reference_key(value: str) -> str:
    """归一化 reference label 的空白和大小写。

    参数：
        value（str）：原始 reference label。
    返回：
        str：用于同文档 reference 查找的统一键。
    """
    return ' '.join(value.split()).casefold()


def _text_links(content: str, line_number: int, definitions: dict[str, tuple[str, tuple[int, int]]], *, source_offset: int = 0) -> list[MarkdownLink]:
    """提取跨行 inline、图片和 reference 链接，保留起始行号。

    参数：
        content（str）：排除 block code 和 definition 后的可见文本。
        line_number（int）：文本起始的一基行号，用于错误定位。
        definitions（dict[str, tuple[str, tuple[int, int]]]）：reference label 到目标及其源码范围的映射。
        source_offset（int）：当前片段在规范 LF 正文中的零基位置。
    返回：
        list[MarkdownLink]：实际链接；代码、转义括号和未定义 label 不进入结果。
    """
    text = _mask_inline_code(content)
    links: list[MarkdownLink] = []
    index = 0
    while index < len(text):
        if text[index] == '\\':
            index += 2
            continue
        if text[index] != '[':
            index += 1
            continue
        closing = _balanced_end(text, index, '[', ']')
        if closing < 0:
            index += 1
            continue
        label = text[index + 1:closing]
        link_line = line_number + text.count('\n', 0, index)
        if '[' in label:
            links.extend(_text_links(label, link_line, definitions, source_offset=source_offset + index + 1))
        next_index = closing + 1
        target: str | None = None
        span: tuple[int, int] | None = None
        if next_index < len(text) and text[next_index] == '(':
            end = _balanced_end(text, next_index, '(', ')')
            if end >= 0:
                target = _destination(text[next_index + 1:end])
                span = _destination_span(text[next_index + 1:end], source_offset + next_index + 1)
                next_index = end + 1
        elif next_index < len(text) and text[next_index] == '[':
            end = _balanced_end(text, next_index, '[', ']')
            if end >= 0:
                key = text[next_index + 1:end] or label
                destination = definitions.get(_reference_key(key))
                if destination is not None:
                    target, span = destination
                next_index = end + 1
        else:
            destination = definitions.get(_reference_key(label))
            if destination is not None:
                target, span = destination
        if target is not None:
            links.append(MarkdownLink(target, link_line, span))
        index = next_index
    return links


class _HtmlAnchors(HTMLParser):
    """收集可见 HTML 中的显式 id/name 锚点，不执行 HTML。"""

    def __init__(self) -> None:
        """初始化只读 HTML 解析状态。

        返回：
            None：创建空锚点集合。
        副作用：
            在当前实例保存解析结果，不访问文件或网络。
        """
        super().__init__(convert_charrefs=True)
        self.anchors: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """记录元素 id，以及 a 元素旧式 name 属性。

        参数：
            tag（str）：HTML 起始元素名。
            attrs（list[tuple[str, str | None]]）：解析出的属性名称和值。
        返回：
            None：解析结果保存到 anchors。
        副作用：
            向当前实例的锚点集合加入非空属性值。
        """
        for key, value in attrs:
            if value and (key == 'id' or (tag == 'a' and key == 'name')):
                self.anchors.add(value)


def heading_slug(heading: str) -> str:
    """生成 GitHub 风格标题锚点，保留中文并去掉格式标记与标点。

    参数：
        heading（str）：不包含井号的 Markdown 标题正文。
    返回：
        str：尚未增加重复标题后缀的锚点。
    副作用：
        无文件或网络访问，仅转换传入的标题文本。
    """
    text = re.sub(r'<[^>]*>', '', heading)
    text = re.sub(r'!?\[([^\]]+)\]\([^)]*\)', r'\1', text)
    text = unescape(text).lower().replace('`', '').replace('*', '')
    text = ''.join(char for char in text if char in '-_' or unicodedata.category(char)[0] not in 'PS')
    return re.sub(r'\s', '-', text)


def parse_markdown(content: str) -> MarkdownDocument:
    """解析链接、图片与锚点，保留诊断行号并排除代码示例。

    参数：
        content（str）：一个 UTF-8 Markdown 文档的正文；源码范围以规范 LF 换行计算。
    返回：
        MarkdownDocument：实际链接与显式/标题锚点。
    副作用：
        无文件或网络访问。
    """
    lines = _visible_lines(content.replace('\r\n', '\n').replace('\r', '\n'))
    offsets: list[int] = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line) + 1
    # 所有语法识别共用等长可见层；源码目标范围和标题正文仍从原始行取得。
    visible_lines = _mask_inline_code('\n'.join(lines)).split('\n')
    definitions: dict[str, tuple[str, tuple[int, int]]] = {}
    definition_lines: set[int] = set()
    for index, line in enumerate(visible_lines):
        if index in definition_lines:
            continue
        match = _DEFINITION.match(line)
        destination_line = index
        if match:
            has_inline_title = bool(line[match.end(2):].strip())
            raw = lines[index][match.start(2):match.end(2)]
            definitions.setdefault(_reference_key(match[1]), (_destination(raw), _destination_span(raw, offsets[index] + match.start(2))))
            definition_lines.add(index)
        else:
            empty = _EMPTY_DEFINITION.match(line)
            if not empty:
                continue
            definition_lines.add(index)
            destination_line = index + 1
            continuation = _DEFINITION_DESTINATION.match(visible_lines[destination_line]) if destination_line < len(lines) else None
            if continuation is None:
                continue
            has_inline_title = bool(visible_lines[destination_line][continuation.end(1):].strip())
            raw = lines[destination_line][continuation.start(1):continuation.end(1)]
            definitions.setdefault(_reference_key(empty[1]), (_destination(raw), _destination_span(raw, offsets[destination_line] + continuation.start(1))))
            definition_lines.add(destination_line)
        title_line = destination_line + 1
        if not has_inline_title and title_line < len(lines) and _DEFINITION_TITLE.match(visible_lines[title_line]):
            definition_lines.add(title_line)
    link_lines = [
        ' ' * len(line) if index in definition_lines else line
        for index, line in enumerate(visible_lines)
    ]
    links = _text_links('\n'.join(link_lines), 1, definitions)
    headings: list[str] = []
    for index, line in enumerate(link_lines):
        if index in definition_lines:
            continue
        match = _ATX.match(line)
        if match:
            original = _ATX.match(lines[index])
            if original:
                headings.append(original[1])
        elif _SETEXT.match(line) and index and index - 1 not in definition_lines and lines[index - 1].strip():
            headings.append(lines[index - 1].strip())
    anchors: set[str] = set()
    for heading in headings:
        base = heading_slug(heading)
        slug = base
        suffix = 0
        while slug in anchors:
            suffix += 1
            slug = f'{base}-{suffix}'
        anchors.add(slug)
    parser = _HtmlAnchors()
    parser.feed('\n'.join(link_lines))
    anchors.update(parser.anchors)
    return MarkdownDocument(tuple(links), frozenset(anchors))


def resolve_local_link(file_path: Path, target: str, *, root_dir: Path) -> tuple[Path | None, str]:
    """解析本地链接并校验根目录边界，外部协议返回 None。

    参数：
        file_path（Path）：含链接文档路径。
        target（str）：已提取的链接目标。
        root_dir（Path）：本地链接允许访问的仓库根目录。
    返回：
        tuple[Path | None, str]：本地绝对路径和解码后的 fragment。
    异常：
        ValueError：本地链接通过相对路径或符号链接越过仓库根目录。
    """
    if _SCHEME.match(target) or target.startswith('//'):
        return None, ''
    path_part, _, fragment = target.partition('#')
    path_part = unquote(path_part.split('?', 1)[0])
    root = Path(root_dir).resolve()
    if not path_part:
        resolved = Path(file_path).resolve()
    elif path_part.startswith('/'):
        resolved = (root / path_part.lstrip('/')).resolve()
    else:
        resolved = (Path(file_path).parent / path_part).resolve()
    resolved.relative_to(root)
    return resolved, unquote(fragment)


def local_link_errors(file_path: Path, content: str, *, root_dir: Path) -> list[str]:
    """检查一个文档的本地文件与 Markdown fragment，按链接行号报告错误。

    参数：
        file_path（Path）：被检查文档。
        content（str）：文档正文。
        root_dir（Path）：全部文件访问的仓库边界。
    返回：
        list[str]：不存在、越界或 fragment 无效的诊断；外部 URL 不联网检查。
    副作用：
        仅在目标位于仓库内且为 Markdown 时读取目标正文。
    """
    root = Path(root_dir).resolve()
    rel_path = Path(file_path).resolve().relative_to(root).as_posix()
    documents: dict[Path, MarkdownDocument] = {Path(file_path).resolve(): parse_markdown(content)}
    errors: list[str] = []
    for link in documents[Path(file_path).resolve()].links:
        label = f'{rel_path}:{link.line}'
        try:
            resolved, fragment = resolve_local_link(file_path, link.target, root_dir=root)
        except ValueError:
            errors.append(f'{label} 链接越过仓库边界：{link.target}')
            continue
        if resolved is None:
            continue
        if not resolved.exists():
            errors.append(f'{label} 链接目标不存在：{link.target}')
            continue
        if fragment and resolved.suffix.lower() in {'.md', '.markdown', '.mdx'}:
            if resolved not in documents:
                documents[resolved] = parse_markdown(resolved.read_text(encoding='utf-8'))
            if fragment not in documents[resolved].anchors:
                errors.append(f'{label} 链接锚点不存在：{link.target}')
    return errors
