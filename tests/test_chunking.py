import pytest

from app.config import CHUNK_MAX_CHARS, OVERLAP_MAX_CHARS
from app.knowledge.chunking import KEY_CLAUSE_MARK, knowledge_text, parse_markdown

SENT = "这是一句用来凑长度的完整句子，内容没有实际含义。"  # 25 字


def test_header_paths_map_to_fields():
    doc = parse_markdown(
        "# 退货政策\n\n总则。\n\n## 退货条件\n\n### 无理由退货\n\n签收后七天内可退。\n"
    )
    assert doc.title == "退货政策"
    first, second = doc.chunks
    assert (first.questions, first.category, first.section_path) == ("退货政策", "退货政策", "退货政策")
    assert first.answer == "总则。"
    assert second.questions == "无理由退货"
    assert second.category == "退货政策 > 退货条件"
    assert second.section_path == "退货政策 > 退货条件 > 无理由退货"
    assert second.answer == "签收后七天内可退。"


def test_faq_heading_is_real_question():
    doc = parse_markdown("# 商品FAQ\n\n## 蓝牙耳机\n\n### 耳机怎么配对？\n\n长按电源键三秒。\n")
    assert doc.chunks[0].questions == "耳机怎么配对？"
    assert doc.chunks[0].category == "商品FAQ > 蓝牙耳机"


def test_paragraphs_keep_line_breaks():
    doc = parse_markdown("# 手册\n\n## 维修\n\n第一段。\n\n第二段。\n")
    assert doc.chunks[0].answer == "第一段。\n第二段。"


def test_empty_section_produces_no_chunk():
    doc = parse_markdown("# 手册\n\n## 空节\n\n## 有内容\n\n内容。\n")
    assert [c.questions for c in doc.chunks] == ["有内容"]


def test_requires_single_h1():
    with pytest.raises(ValueError):
        parse_markdown("## 没有一级标题\n\n内容。\n")
    with pytest.raises(ValueError):
        parse_markdown("# 甲\n\n内容。\n\n# 乙\n\n内容。\n")


@pytest.mark.parametrize(
    "markdown",
    [
        "# 甲\n\n内容。\n\n# 甲\n\n更多内容。\n",
        "# 甲\n\n内容。\n\n# 乙\n",
        "# 甲\n\n# 乙\n\n内容。\n",
    ],
)
def test_duplicate_h1_is_rejected_even_if_same_name_or_empty(markdown):
    with pytest.raises(ValueError, match="一份文档只能有一个一级标题"):
        parse_markdown(markdown)


def test_all_empty_sections_return_title_without_chunks():
    doc = parse_markdown("# 手册\n\n## 空节\n\n### 空子节\n")
    assert doc.title == "手册"
    assert doc.chunks == []


def test_h1_in_fenced_code_is_not_a_document_heading():
    body = "示例。\n```markdown\n# 示例标题\n```"
    doc = parse_markdown(f"# 手册\n\n## 示例\n\n{body}\n")
    assert doc.title == "手册"
    assert doc.chunks[0].answer == body


def _sentences(n: int) -> list[str]:
    return [f"第{i:03d}句用来凑长度的完整内容。" for i in range(n)]  # 每句 16 字，内容互不相同


def test_long_section_split_recursively_with_sentence_overlap():
    sents = _sentences(50)  # 800 字
    doc = parse_markdown("# 手册\n\n## 长节\n\n" + "".join(sents) + "\n")
    chunks = doc.chunks
    assert len(chunks) >= 2
    for c in chunks:
        assert len(c.answer) <= CHUNK_MAX_CHARS + OVERLAP_MAX_CHARS
        assert c.questions == "长节"
        # 每块从句子开头开始，以句号结束。
        assert c.answer.startswith("第") and c.answer.endswith("。")
    for prev, cur in zip(chunks, chunks[1:]):
        # 后一块的第一句出现在前一块中。
        first = cur.answer[: cur.answer.index("。") + 1]
        assert first in prev.answer
    assert all(any(s in c.answer for c in chunks) for s in sents)


def test_no_overlap_when_tail_has_no_sentence_end():
    body = "，".join(["没有句号的分句内容"] * 60)  # 只有逗号，没有句末标点
    doc = parse_markdown(f"# 手册\n\n## 长节\n\n{body}\n")
    chunks = doc.chunks
    assert len(chunks) >= 2
    joined = "".join(c.answer for c in chunks)
    assert len(joined) == len(body)  # 内容不重复，也不丢失


def test_no_overlap_across_sections():
    long = SENT * 20
    doc = parse_markdown(f"# 手册\n\n## 甲\n\n{long}\n\n## 乙\n\n短内容。\n")
    assert doc.chunks[-1].answer == "短内容。"


def _table(rows: int) -> str:
    lines = ["| 品类 | 退货期限 | 说明 |", "|---|---|---|"]
    lines += [f"| 品类{i:02d} | {i} 天 | 这一行是第 {i} 行的说明文字，用来凑长度。 |" for i in range(rows)]
    return "\n".join(lines)


def test_small_table_stays_whole():
    table = _table(3)
    doc = parse_markdown(f"# 政策\n\n## 期限表\n\n{table}\n")
    assert doc.chunks[0].answer == table


def test_small_table_is_separate_from_surrounding_text():
    table = _table(1)
    doc = parse_markdown(f"# 政策\n\n## 期限表\n\n表前说明。\n\n{table}\n\n表后说明。\n")
    assert [c.answer for c in doc.chunks] == ["表前说明。", table, "表后说明。"]


def test_large_table_split_by_rows_with_header_copied():
    table = _table(20)
    doc = parse_markdown(f"# 政策\n\n## 期限表\n\n说明文字。\n\n{table}\n")
    table_chunks = [c for c in doc.chunks if c.answer.startswith("| 品类 |")]
    assert len(table_chunks) >= 2
    data_rows = []
    for c in table_chunks:
        lines = c.answer.split("\n")
        assert lines[0] == "| 品类 | 退货期限 | 说明 |"
        assert lines[1] == "|---|---|---|"
        assert len(c.answer) <= CHUNK_MAX_CHARS
        data_rows += lines[2:]
    assert data_rows == table.split("\n")[2:]  # 行不丢失、不重复，顺序不变


def test_table_only_section_and_oversized_row():
    big_row = "| 超长 | 1 天 | " + "很长的说明" * 100 + " |"
    table = "\n".join(["| 品类 | 退货期限 | 说明 |", "|---|---|---|", "| 甲 | 7 天 | 短 |", big_row, "| 乙 | 15 天 | 短 |"])
    doc = parse_markdown(f"# 政策\n\n## 期限表\n\n{table}\n")
    rows = []
    for c in doc.chunks:
        lines = c.answer.split("\n")
        assert lines[:2] == ["| 品类 | 退货期限 | 说明 |", "|---|---|---|"]
        if big_row in lines:
            assert lines[2:] == [big_row]  # 超长行只与表头同块。
        else:
            assert len(c.answer) <= CHUNK_MAX_CHARS
        rows += lines[2:]
    assert rows == table.split("\n")[2:]


@pytest.mark.parametrize("rows", [[], ["| 短行 |"]])
def test_oversized_header_is_rejected_instead_of_losing_content(rows):
    table = "\n".join(["| " + "甲" * 401 + " |", "|---|", *rows])
    with pytest.raises(ValueError, match="表头无法满足块长度上限"):
        parse_markdown(f"# 政策\n\n## 表\n\n{table}\n")


def test_row_that_fits_alone_but_not_with_header_gets_own_chunk():
    header = ["| 品类 | 退货期限 | 说明 |", "|---|---|---|"]
    long_row = "| " + "甲" * 386 + " |"
    rows = ["| 乙 | 7 天 | 短 |", long_row, "| 丙 | 15 天 | 短 |"]
    assert len(long_row) == 390
    assert len("\n".join(header + [long_row])) > CHUNK_MAX_CHARS
    doc = parse_markdown("# 政策\n\n## 表\n\n" + "\n".join(header + rows) + "\n")
    actual_rows = []
    for chunk in doc.chunks:
        lines = chunk.answer.split("\n")
        assert lines[:2] == header
        actual_rows.extend(lines[2:])
        if long_row in lines:
            assert lines[2:] == [long_row]
            assert len(chunk.answer) > CHUNK_MAX_CHARS
        else:
            assert len(chunk.answer) <= CHUNK_MAX_CHARS
    assert actual_rows == rows


def test_table_without_data_rows_is_rejected():
    with pytest.raises(ValueError, match="表格没有数据行"):
        parse_markdown("# 政策\n\n| 品类 |\n|---|\n")


def test_invalid_table_divider_is_split_as_text():
    body = "| A | B |\n|---|普通文字|\n| " + "甲" * 600 + " |"
    doc = parse_markdown(f"# 手册\n\n## 正文\n\n{body}\n")
    assert len(doc.chunks) >= 2
    assert all(len(c.answer) <= CHUNK_MAX_CHARS for c in doc.chunks)
    assert "".join(c.answer for c in doc.chunks).replace("\n", "") == body.replace("\n", "")


def test_key_clause_flag_and_mark_removed():
    doc = parse_markdown(f"# 政策\n\n## 甲\n\n{KEY_CLAUSE_MARK}签收前不能退货。\n\n## 乙\n\n普通内容。\n")
    assert doc.chunks[0].is_key_clause is True
    assert KEY_CLAUSE_MARK not in doc.chunks[0].answer
    assert doc.chunks[0].answer == "签收前不能退货。"
    assert doc.chunks[1].is_key_clause is False


def test_key_clause_not_propagated_by_overlap():
    body = SENT * 14 + f"{KEY_CLAUSE_MARK}关键句。" + SENT * 14
    doc = parse_markdown(f"# 手册\n\n## 长节\n\n{body}\n")
    flags = [c.is_key_clause for c in doc.chunks]
    assert flags.count(True) == 1
    assert all(KEY_CLAUSE_MARK not in c.answer for c in doc.chunks)


def test_key_clause_in_overlap_keeps_next_chunk_unflagged():
    sents = _sentences(50)
    body = "".join(sents[:24]) + f"{KEY_CLAUSE_MARK}关键句。" + "".join(sents[24:])
    doc = parse_markdown(f"# 手册\n\n## 长节\n\n{body}\n")
    assert doc.chunks[0].is_key_clause is True
    assert "关键句。" in doc.chunks[1].answer
    assert all(c.is_key_clause is False for c in doc.chunks[1:])
    assert all(KEY_CLAUSE_MARK not in c.answer for c in doc.chunks)


@pytest.mark.parametrize("prefix_length", range(395, 400))
def test_key_clause_mark_is_not_split_at_character_boundary(prefix_length):
    body = "甲" * prefix_length + KEY_CLAUSE_MARK + "乙" * 420
    doc = parse_markdown(f"# 手册\n\n## 条款\n\n{body}\n")
    assert sum(c.is_key_clause for c in doc.chunks) == 1
    assert "".join(c.answer for c in doc.chunks) == "甲" * prefix_length + "乙" * 420
    assert all(len(c.answer) <= CHUNK_MAX_CHARS for c in doc.chunks)


def test_knowledge_text_format():
    assert knowledge_text("运费", "运费怎么算？", "满 99 元免运费。") == "运费\n运费怎么算？\n满 99 元免运费。"
