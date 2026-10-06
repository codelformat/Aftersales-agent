from datetime import date

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.services.chat import ChatTurn, stream_reply
from app.session import SessionStore

pytestmark = pytest.mark.anyio


async def test_stream_closed_early_writes_nothing():
    # 锁由 app.api.chat.prepare_chat_turn 依赖释放，见 test_lock_held_during_stream_and_released_after。
    # 模拟客户端断开：只读取 2 个事件就关闭生成器。
    session = SessionStore().get_or_create("s1")
    turn = ChatTurn(session=session, history=[], user_input="你好", today=date(2026, 10, 6))
    gen = stream_reply(turn, FakeListChatModel(responses=["您好请问"]))
    assert (await gen.__anext__())[0] == "session"
    assert (await gen.__anext__())[0] == "token"
    await gen.aclose()
    assert session.messages == []
