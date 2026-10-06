import pytest
from sqlalchemy import select

from app.db.models import Conversation, Faq, KnowledgeChunk, Message, QaExtractionStaging
from tests.conftest import ROOT

pytestmark = pytest.mark.anyio


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
