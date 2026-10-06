import re
from dataclasses import dataclass

from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from app.config import CHUNK_MAX_CHARS, OVERLAP_MAX_CHARS

PATH_SEP = " > "
KEY_CLAUSE_MARK = "【关键条款】"
_HEADERS = [("#", "h1"), ("##", "h2"), ("###", "h3")]
_SENTENCE_ENDS = "。！？"
_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "，", ""]
_TABLE_DIVIDER = re.compile(r"^\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?$")
_KEY_CLAUSE_TOKEN = "\0"


@dataclass(frozen=True)
class Chunk:
    category: str
    questions: str
    answer: str
    section_path: str
    is_key_clause: bool


@dataclass(frozen=True)
class ParsedDoc:
    title: str
    chunks: list[Chunk]


def knowledge_text(category: str, questions: str, answer: str) -> str:
    """生成向量化文本。全项目只用此函数拼接。"""
    return f"{category}\n{questions}\n{answer}"


def parse_markdown(markdown: str) -> ParsedDoc:
    """按标题层级切分文档。文档必须有且只有一个一级标题。"""
    title = _document_title(markdown)
    sections = MarkdownHeaderTextSplitter(_HEADERS, strip_headers=True).split_text(markdown)
    chunks: list[Chunk] = []
    for section in sections:
        headers = [section.metadata[k] for k in ("h1", "h2", "h3") if k in section.metadata]
        if not headers or headers[0] != title:
            raise ValueError("一份文档只能有一个一级标题")
        # 切分器用两个空格加换行连接段落。还原为普通换行。
        body = section.page_content.replace("  \n", "\n").strip()
        if not body:
            continue
        category = PATH_SEP.join(headers[:-1]) or title
        section_path = PATH_SEP.join(headers)
        for text, is_key in _split_body(body):
            answer = text.replace(KEY_CLAUSE_MARK, "").strip()
            if answer:
                chunks.append(Chunk(category, headers[-1], answer, section_path, is_key))
    return ParsedDoc(title, chunks)


def _document_title(markdown: str) -> str:
    """保留标题并关闭合并，避免同名标题和空节绕过校验。"""
    sections = MarkdownHeaderTextSplitter(
        [("#", "h1")], strip_headers=False, return_each_line=True
    ).split_text(markdown)
    if not sections or "h1" not in sections[0].metadata:
        raise ValueError("文档必须以一级标题开头")
    headings = []
    for section in sections:
        first_line = section.page_content.split("\n", 1)[0]
        if first_line == "#" or first_line.startswith("# "):
            headings.append(section.metadata["h1"])
    if len(headings) != 1:
        raise ValueError("一份文档只能有一个一级标题")
    return headings[0]


def _split_body(body: str) -> list[tuple[str, bool]]:
    """返回块文本及关键条款标志。调用方删除文本中的标记。"""
    result: list[tuple[str, bool]] = []
    for kind, block in _blocks(body):
        if kind == "table":
            result += [(t, KEY_CLAUSE_MARK in t) for t in _split_table(block)]
        else:
            result += _with_overlap(_split_text(block))
    return result


def _blocks(body: str) -> list[tuple[str, str]]:
    """按原顺序分离正文块和表格块。"""
    lines = body.split("\n")
    blocks: list[tuple[str, list[str]]] = []
    i = 0
    while i < len(lines):
        is_table_start = (
            lines[i].startswith("|") and i + 1 < len(lines) and _TABLE_DIVIDER.match(lines[i + 1])
        )
        if is_table_start:
            j = i
            while j < len(lines) and lines[j].startswith("|"):
                j += 1
            blocks.append(("table", lines[i:j]))
            i = j
        else:
            if not blocks or blocks[-1][0] != "text":
                blocks.append(("text", []))
            blocks[-1][1].append(lines[i])
            i += 1
    return [(kind, "\n".join(ls).strip()) for kind, ls in blocks if "\n".join(ls).strip()]


def _split_table(table: str) -> list[str]:
    lines = table.split("\n")
    header, rows = lines[:2], lines[2:]
    if len("\n".join(header)) > CHUNK_MAX_CHARS:
        raise ValueError("表头无法满足块长度上限")
    if not rows:
        raise ValueError("表格没有数据行")
    if len(table) <= CHUNK_MAX_CHARS:
        return [table]
    pieces: list[str] = []
    current = list(header)
    for row in rows:
        # 数据行加表头超限时单独成块，保留完整行和表头。
        if len(current) > 2 and len("\n".join(current + [row])) > CHUNK_MAX_CHARS:
            pieces.append("\n".join(current))
            current = list(header)
        current.append(row)
    if len(current) > 2:
        pieces.append("\n".join(current))
    return pieces


def _split_text(text: str) -> list[str]:
    if len(text) <= CHUNK_MAX_CHARS:
        return [text]
    splitter = RecursiveCharacterTextSplitter(
        separators=_SEPARATORS,
        keep_separator="end",
        chunk_size=CHUNK_MAX_CHARS,
        chunk_overlap=0,
        length_function=lambda value: len(value.replace(_KEY_CLAUSE_TOKEN, KEY_CLAUSE_MARK)),
    )
    # 标题切分器已删除不可打印字符。用单字符保护标记，仍按原长度计数。
    protected = text.replace(KEY_CLAUSE_MARK, _KEY_CLAUSE_TOKEN)
    return [piece.replace(_KEY_CLAUSE_TOKEN, KEY_CLAUSE_MARK) for piece in splitter.split_text(protected)]


def _overlap_prefix(prev: str) -> str:
    """取末尾限长文本，从首个句末标点之后开始。无句末标点时返回空串。"""
    tail = prev[-OVERLAP_MAX_CHARS:]
    for idx, ch in enumerate(tail):
        if ch in _SENTENCE_ENDS:
            return tail[idx + 1:].strip()
    return ""


def _with_overlap(pieces: list[str]) -> list[tuple[str, bool]]:
    result = []
    for i, piece in enumerate(pieces):
        prefix = _overlap_prefix(pieces[i - 1]) if i > 0 else ""
        text = f"{prefix}{piece}" if prefix else piece
        result.append((text, KEY_CLAUSE_MARK in piece))
    return result
