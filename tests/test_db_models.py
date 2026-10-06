import pytest
from sqlalchemy import select

from app.db.models import Conversation, Faq, Message
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
