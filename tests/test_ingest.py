from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models import KnowledgeChunk
from app.knowledge import ingest
from app.knowledge import milvus as m
from app.repositories import knowledge
from app.repositories.knowledge import NewChunk
from tests.fakes import entity, unit

pytestmark = pytest.mark.anyio

DOC = "# 退货政策\n\n总则。\n\n## 退货条件\n\n【关键条款】签收后七天内可退。\n\n## 退款\n\n原路退回。\n"


def _write_docs(tmp_path: Path, content_type: str = "policy", name: str = "退货政策.md", text: str = DOC) -> Path:
    d = tmp_path / content_type
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text, encoding="utf-8")
    return tmp_path


async def _rows(db):
    async with db() as s:
        return list((await s.execute(select(KnowledgeChunk).order_by(KnowledgeChunk.id))).scalars())


def test_load_doc_sources_uses_folder_as_content_type(tmp_path):
    srcs = ingest.load_doc_sources(_write_docs(tmp_path))
    assert [(s.title, s.content_type, s.linked, len(s.chunks)) for s in srcs] == [("退货政策", "policy", True, 3)]


def test_load_doc_sources_rejects_unknown_folder(tmp_path):
    with pytest.raises(ValueError):
        ingest.load_doc_sources(_write_docs(tmp_path, content_type="blog"))


async def test_ingest_source_writes_pending_chunks_with_chain(db, tmp_path):
    [src] = ingest.load_doc_sources(_write_docs(tmp_path))
    assert await ingest.ingest_source(src) == 3
    rows = await _rows(db)
    assert [r.vectorize_status for r in rows] == ["pending"] * 3
    assert [r.content_type for r in rows] == ["policy"] * 3
    assert [r.is_key_clause for r in rows] == [False, True, False]
    assert rows[1].section_path == "退货政策 > 退货条件"
    assert [r.prev_chunk_id for r in rows] == [None, rows[0].id, rows[1].id]
    assert [r.next_chunk_id for r in rows] == [rows[1].id, rows[2].id, None]


async def test_ingest_source_skips_existing_document(db, tmp_path):
    [src] = ingest.load_doc_sources(_write_docs(tmp_path))
    await ingest.ingest_source(src)
    assert await ingest.ingest_source(src) == 0
    assert len(await _rows(db)) == 3


async def test_source_match_does_not_hit_prefix_titles(db, tmp_path):
    # 「退货政策补充」不能被当成「退货政策」的一部分。
    _write_docs(tmp_path, name="补充.md", text="# 退货政策补充\n\n补充内容。\n")
    srcs = {s.title: s for s in ingest.load_doc_sources(tmp_path)}
    await ingest.ingest_source(srcs["退货政策补充"])
    [src] = ingest.load_doc_sources(_write_docs(tmp_path / "x"))
    assert await ingest.ingest_source(src) == 3


async def test_faq_source_from_table(db):
    src = await ingest.load_faq_source()
    assert src.title == "常见问答" and src.linked is False
    await ingest.ingest_source(src)
    rows = await _rows(db)
    assert len(rows) == 12
    freight = next(r for r in rows if r.questions == "运费怎么算？")
    assert freight.category == "运费"
    assert freight.section_path == "常见问答 > 运费"
    assert freight.content_type == "faq"
    assert freight.prev_chunk_id is None and freight.next_chunk_id is None


async def test_rebuild_source_deletes_milvus_and_mysql(db, milvus, tmp_path):
    [src] = ingest.load_doc_sources(_write_docs(tmp_path))
    await ingest.ingest_source(src)
    ids = [r.id for r in await _rows(db)]
    await m.upsert_entities([entity(i, unit(n)) for n, i in enumerate(ids)])
    assert await ingest.rebuild_source("退货政策") == 3
    assert await _rows(db) == []
    assert await m.count_vectors() == 0


async def test_ingest_all_rebuild_keeps_mined_chunks(db, milvus, tmp_path):
    from app.repositories import knowledge

    async with db() as s:
        await knowledge.insert_chunks(s, [knowledge.NewChunk("其他", "能开专票吗", "可以。", "对话挖掘 > 其他", "mined", False)])
        await s.commit()
    docs = _write_docs(tmp_path)
    first = await ingest.ingest_all(docs)
    assert first == {"退货政策": 3, "常见问答": 12}
    assert await ingest.ingest_all(docs) == {"退货政策": 0, "常见问答": 0}
    assert await ingest.ingest_all(docs, rebuild=True) == {"退货政策": 3, "常见问答": 12}
    rows = await _rows(db)
    assert sum(r.content_type == "mined" for r in rows) == 1
    assert len(rows) == 16


def test_duplicate_titles_rejected(tmp_path):
    _write_docs(tmp_path, content_type="policy", name="a.md")
    _write_docs(tmp_path, content_type="manual", name="b.md")
    with pytest.raises(ValueError):
        ingest.load_doc_sources(tmp_path)


@pytest.mark.parametrize("title", ["对话挖掘", "常见问答"])
def test_reserved_titles_rejected(tmp_path, title):
    _write_docs(tmp_path, name="保留标题.md", text=f"# {title}\n\n正文。\n")
    with pytest.raises(ValueError, match="保留标题") as exc:
        ingest.load_doc_sources(tmp_path)
    assert "保留标题.md" in str(exc.value)


def test_title_with_path_separator_rejected(tmp_path):
    _write_docs(tmp_path, name="补充.md", text="# 退货政策 > 补充\n\n正文。\n")
    with pytest.raises(ValueError, match="标题不能包含路径分隔符") as exc:
        ingest.load_doc_sources(tmp_path)
    assert "补充.md" in str(exc.value)


@pytest.mark.parametrize("title", ["政策%", "政策_", "政策\\"])
async def test_source_match_escapes_like_characters(db, title):
    from app.repositories import knowledge

    async with db() as s:
        rows = await knowledge.insert_chunks(s, [
            knowledge.NewChunk("政策", "问题", "答案", path, "policy", False)
            for path in (title, f"{title} > 条件", "政策X > 条件", f"{title}补充 > 条件")
        ])
        assert await knowledge.has_source(s, title)
        assert await knowledge.ids_for_source(s, title) == [r.id for r in rows[:2]]
        assert not await knowledge.has_source(s, "不存在")
        await knowledge.delete_ids(s, [r.id for r in rows[:2]])
        assert not await knowledge.has_source(s, title)
        assert await knowledge.ids_for_source(s, title) == []


async def test_repository_pending_and_done_queries(db):
    from app.repositories import knowledge

    async with db() as s:
        rows = await knowledge.insert_chunks(s, [
            knowledge.NewChunk("类别", str(i), "答案", "来源", content_type, False)
            for i, content_type in enumerate(("policy", "policy", "mined"))
        ])
        ids = [r.id for r in rows]
        await s.commit()
        # 提交后先刷新，再读取数据库默认值。
        for row in rows:
            await s.refresh(row)
        assert [r.vectorize_status for r in rows] == ["pending"] * 3

    async with db() as s:
        assert [r.id for r in await knowledge.next_pending(s, 0, 2)] == ids[:2]
        assert [r.id for r in await knowledge.next_pending(s, ids[0], 1)] == ids[1:2]
        assert await knowledge.get_done_by_ids(s, ids) == {}
        await knowledge.mark_done(s, [ids[1]])
        await s.commit()

    async with db() as s:
        done = await knowledge.get_done_by_ids(s, ids + [ids[-1] + 1])
        assert list(done) == [ids[1]]
        assert done[ids[1]].vector_id == str(ids[1])
        assert [r.id for r in await knowledge.next_pending(s, 0, 10)] == [ids[0], ids[2]]
        # MySQL 按 ENUM 定义顺序排序，pending 在 done 前。
        assert await knowledge.status_counts(s) == [
            ("mined", "pending", 1), ("policy", "pending", 1), ("policy", "done", 1),
        ]
        assert await knowledge.get_done_by_ids(s, []) == {}
        await knowledge.mark_done(s, [])
        await knowledge.delete_ids(s, [])
        assert await knowledge.insert_chunks(s, []) == []


async def test_rebuild_failure_preserves_sql_rows_and_can_rerun(db, milvus, tmp_path, monkeypatch):
    [src] = ingest.load_doc_sources(_write_docs(tmp_path))
    await ingest.ingest_source(src)
    ids = [r.id for r in await _rows(db)]
    await m.upsert_entities([entity(i, unit(n)) for n, i in enumerate(ids)])

    async def fail_delete(chunk_ids):
        raise RuntimeError("删除失败")

    with monkeypatch.context() as patch:
        patch.setattr(ingest, "delete_vectors", fail_delete)
        with pytest.raises(RuntimeError, match="删除失败"):
            await ingest.rebuild_source(src.title)
    assert [r.id for r in await _rows(db)] == ids
    assert await m.count_vectors() == 3
    # 模拟向量已删除、SQL 尚未删除时中断。
    await m.delete_vectors(ids)
    assert await ingest.rebuild_source(src.title) == 3
    assert await ingest.rebuild_source(src.title) == 0
    assert await _rows(db) == []
    assert await m.count_vectors() == 0


def test_parse_error_names_the_file(tmp_path):
    _write_docs(tmp_path, name="无标题.md", text="## 没有一级标题\n\n正文。\n")
    with pytest.raises(ValueError, match=r"无标题\.md：文档必须以一级标题开头") as exc:
        ingest.load_doc_sources(tmp_path)
    assert isinstance(exc.value.__cause__, ValueError)


async def test_mark_pending_by_content_type_only_touches_mined(db):
    async with db() as s:
        rows = await knowledge.insert_chunks(s, [
            NewChunk("运费", "q1", "a1", "对话挖掘 > 运费", "mined", False),
            NewChunk("c", "q2", "a2", "退货政策 > x", "policy", False),
        ])
        await knowledge.mark_done(s, [r.id for r in rows])
        await s.commit()
    async with db() as s:
        assert await knowledge.mark_pending_by_content_type(s, "mined") == 1
        await s.commit()
    async with db() as s:
        statuses = {r.content_type: r.vectorize_status for r in await s.scalars(select(KnowledgeChunk))}
    assert statuses == {"mined": "pending", "policy": "done"}
