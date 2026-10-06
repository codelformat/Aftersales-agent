import uuid

import pytest
from langchain_core.messages import HumanMessage

from app.session import SessionStore


def test_create_with_generated_id():
    s = SessionStore().get_or_create(None)
    uuid.UUID(s.id)
    assert s.messages == []


def test_create_with_given_id_then_reuse():
    store = SessionStore()
    s1 = store.get_or_create("demo1")
    s1.messages.append(HumanMessage("q"))
    s2 = store.get_or_create("demo1")
    assert s2 is s1
    assert store.get("demo1") is s1
    assert store.get("nope") is None


@pytest.mark.anyio
async def test_lock_state_is_visible():
    s = SessionStore().get_or_create("x")
    assert not s.lock.locked()
    await s.lock.acquire()
    assert s.lock.locked()
    s.lock.release()
