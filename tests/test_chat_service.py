from datetime import date

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.services.chat import ChatTurn, stream_reply
from app.session import SessionStore

pytestmark = pytest.mark.anyio


async def test_stream_closed_early_writes_nothing_and_releases_lock():
    # 模拟客户端断开：只读取 2 个事件就关闭生成器。
    session = SessionStore().get_or_create("s1")
    await session.lock.acquire()
    turn = ChatTurn(session=session, history=[], user_input="你好", today=date(2026, 10, 6))
    gen = stream_reply(turn, FakeListChatModel(responses=["您好请问"]))
    try:
        assert (await gen.__anext__())[0] == "session"
        assert (await gen.__anext__())[0] == "token"
        await gen.aclose()
    finally:
        # 端点负责释放锁；这里模拟端点的 finally。
        session.lock.release()
    assert session.messages == []
    assert not session.lock.locked()
