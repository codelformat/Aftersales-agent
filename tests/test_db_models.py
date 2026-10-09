import pytest
from sqlalchemy import select
from sqlalchemy import text as sql_text

from app.db.models import (
    Conversation, FaithCase, Faq, KnowledgeChunk, LowConfidenceQuestion, Message, QaExtractionStaging,
)
from tests.conftest import ROOT, split_sql

pytestmark = pytest.mark.anyio


def test_split_sql_preserves_ch04_comment_semicolons():
    statements = split_sql((ROOT / "db" / "schema_ch04.sql").read_text(encoding="utf-8"))
    assert len(statements) == 3
    assert statements[0] == "SET NAMES utf8mb4"
    assert statements[1].startswith("CREATE TABLE low_confidence_questions")
    assert statements[2].startswith("CREATE TABLE faith_cases")
    assert "COMMENT '评估集题号,如 A43;一题一行'" in statements[2]
    assert statements[2].endswith("COMMENT='ch04 忠实度编造个案台账'")


def test_split_sql_preserves_quoted_and_escaped_delimiters():
    sql = "-- 注释;\nSELECT 'a;b', 'it''s;c', 'it\\'s;d', \"e;f\", `g;h`; SELECT 1;"
    assert split_sql(sql) == [
        "SELECT 'a;b', 'it''s;c', 'it\\'s;d', \"e;f\", `g;h`",
        "SELECT 1",
    ]


async def test_conversation_defaults(db):
    async with db() as s:
        conv = Conversation(user_id="u1")
        s.add(conv)
        await s.commit()
        await s.refresh(conv)
    assert conv.id > 0
    assert conv.status == "进行中"


async def test_message_json_roundtrip(db):
    calls = [{"id": "c1", "name": "query_logistics", "args": {"order_id": "1001"}}]
    async with db() as s:
        conv = Conversation(user_id="u1")
        s.add(conv)
        await s.flush()
        s.add_all([
            Message(conversation_id=conv.id, role="assistant", content=None, tool_calls=calls),
            Message(conversation_id=conv.id, role="tool", content='{"ok": true}', tool_call_id="c1"),
        ])
        await s.commit()
    async with db() as s:
        rows = (await s.execute(select(Message).order_by(Message.id))).scalars().all()
    assert [(r.role, r.content, r.tool_calls, r.tool_call_id) for r in rows] == [
        ("assistant", None, calls, None),
        ("tool", '{"ok": true}', None, "c1"),
    ]


async def test_seed_faq_loaded(db):
    async with db() as s:
        questions = (await s.execute(select(Faq.question))).scalars().all()
    assert len(questions) == 12
    assert "退货政策是什么？" in questions


def test_seed_has_no_you_char_and_no_ascii_semicolon_in_values():
    body = (ROOT / "db" / "seed.sql").read_text(encoding="utf-8")
    values = "\n".join(l for l in body.splitlines() if not l.strip().startswith("--"))
    assert "邮" not in values
    assert values.rstrip().endswith(";")
    assert values.rstrip().rstrip(";").count(";") == 0


async def test_knowledge_chunk_defaults_and_self_pointers(db):
    async with db() as s:
        a = KnowledgeChunk(category="退货政策", questions="退货条件", answer="七天内可退。")
        b = KnowledgeChunk(category="退货政策", questions="退货流程", answer="先申请。")
        s.add_all([a, b])
        await s.flush()
        a.next_chunk_id, b.prev_chunk_id = b.id, a.id
        await s.commit()
        await s.refresh(a)
    assert a.vectorize_status == "pending"
    assert a.is_key_clause is False
    assert a.vector_id is None
    assert a.next_chunk_id == b.id


async def test_staging_defaults(db):
    async with db() as s:
        row = QaExtractionStaging(
            batch_no="20261005-01", source_ref="conversation:1", question="能开专票吗", answer="可以。"
        )
        s.add(row)
        await s.commit()
        await s.refresh(row)
    assert row.status == "extracted"


async def test_low_confidence_question_defaults(db):
    async with db() as s:
        conv = Conversation(user_id="u1")
        s.add(conv)
        await s.flush()
        row = LowConfidenceQuestion(
            conversation_id=conv.id, raw_question="X9 能无线充电吗", source="self_check", reason="证据没写",
        )
        s.add(row)
        await s.commit()
        await s.refresh(row)
        assert row.id > 0 and row.created_at is not None
        assert row.source == "self_check"


async def test_faith_case_defaults_and_utf8_enum(db):
    async with db() as s:
        row = FaithCase(
            eval_id="A01", bucket="A_policy", query="q", answer="a", reason="r",
            citations=[{"n": 1, "chunk_id": 3, "section_path": "p", "question": "q", "answer": "a"}],
        )
        s.add(row)
        await s.commit()
        await s.refresh(row)
        assert (row.strategy, row.status, row.seen_count) == ("hybrid_rerank", "未解决", 1)
        assert row.first_seen_at is not None and row.last_seen_at is not None
        assert row.citations[0]["chunk_id"] == 3
        # HEX 校验存储字节，避免双重编码被反向还原后漏检。
        hexed = await s.scalar(sql_text("SELECT HEX(status) FROM faith_cases WHERE id = :i"), {"i": row.id})
        assert hexed == "E69CAAE8A7A3E586B3"


async def test_conversation_context_columns_and_summaries(db):
    from app.db.models import Conversation, ConversationSummary

    async with db() as s:
        c = Conversation(user_id="u1")
        s.add(c)
        await s.flush()
        s.add(ConversationSummary(
            conversation_id=c.id, seq=1, from_msg_id=1, upto_msg_id=4, content="用户报订单 1001",
        ))
        await s.commit()
        await s.refresh(c)
        assert (c.summary, c.summary_upto_msg_id, c.layer1_from_msg_id) == (None, None, None)
        rows = (await s.scalars(select(ConversationSummary))).all()
        assert [(r.seq, r.from_msg_id, r.upto_msg_id, r.content) for r in rows] == [
            (1, 1, 4, "用户报订单 1001"),
        ]
